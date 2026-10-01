"""故障、失效、缺陷和不安全事件报告（M6）。

依据 UG-DAP-12 第 6 章步骤 1～10、第 7 章关键控制点，判据 L1／M6-1／M6-2／M6-3。

这个模块里有三处是**刻意不做校验**的，比做了更重要：

  · **报告内容缺项不拦报送（判据 M6-2）。**序列号、型别、件号、故障性质、时间地点、
    初步原因分析六项，"信息不全的先报已知部分"。在 submit() 里加一道"必须填齐"看着
    严谨, 实际是拿数据完整性去换 48 小时超期 —— 那是把法规时限让给了表单校验。
    缺哪几项由 das_occurrence_content_gaps 列出来, 报送时记在 content_gaps 上跟踪。

  · **系统不判"是否属于 21.5 范围"。**13 种情形的逐项判断是人的工作, 系统只保证
    判完了 13 项、保证"有疑问时不能结论为不属范围"(DCMS-INV-040／041)。做成关键词
    自动匹配会造出一种假覆盖 —— 这个错在符合性检查单上已经犯过一次。

  · **超期不自动做任何事。**到期和超期只在视图里显形, 不发邮件、不改状态、不阻断。
    超了 48 小时是一个已发生的事实, 系统的职责是让它无法被忽略, 而不是替人补救。

另有一处与直觉相反: **登记时间晚不影响计时**。48 小时从"确认存在"起算, 晚登记只是
另一条偏离(das_occurrence_registration_deviation), 不动时限。反过来, 不填确认存在
时间就成了绕过计时的路, 所以 das_occurrence_clock_pending 把这类单独列出来。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import fetch_all, fetch_one, scalar

GROUNDS = {
    "IMPROPER_MAINTENANCE": "已确认是由于不恰当的维修造成的",
    "ABNORMAL_USE": "已确认是由于非正常的使用造成的",
    "ALREADY_REPORTED": "已知使用人或其他人已就同一事件向局方提交报告",
}
CONCLUSIONS = {
    "REPORTABLE": "属 21.5 报告范围",
    "EXEMPT": "免于报告",
    "OUT_OF_SCOPE": "不属 21.5 报告范围",
    "UNDETERMINED": "尚无法确定",
}
VERDICTS = ("YES", "NO", "UNSURE")
DISCLOSURES = ("NAMED", "CONFIDENTIAL", "ANONYMOUS")
VIA = ("PHONE", "WRITTEN", "EMAIL", "OTHER")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """把不带时区的时间按本机时区补齐。

    客户端送 "2026-10-01T09:00:00"(无时区)时 Pydantic 给出朴素 datetime, 与
    带时区的 now() 一比就抛 TypeError —— 时限模块里这等于把 48 小时的校验整段打掉。
    本系统是单地点部署, 库里是 timestamptz, 按本机时区补齐即为用户填写时的本意。
    """
    if value is not None and value.tzinfo is None:
        return value.astimezone()      # 朴素值按本机时区解读, 返回带时区的同一时刻
    return value


# ---------------------------------------------------------------- 配置与查询

def categories(conn: psycopg.Connection) -> list[dict]:
    """CCAR-21.5（二）13 种应报告情形。规章改版时在系统内维护, 不改代码。"""
    return fetch_all(conn, "SELECT * FROM das_occurrence_category ORDER BY seq")


def register_list(conn: psycopg.Connection) -> list[dict]:
    """事件台账。保密件与匿名件在此不显示报告人（步骤 9）。"""
    return fetch_all(conn, "SELECT * FROM das_occurrence_register")


def deadlines(conn: psycopg.Connection, state: str | None = None) -> list[dict]:
    if state:
        return fetch_all(conn, "SELECT * FROM das_occurrence_deadline WHERE clock_state=%s"
                               " ORDER BY deadline_at NULLS LAST", (state,))
    return fetch_all(conn, "SELECT * FROM das_occurrence_deadline"
                           " ORDER BY deadline_at NULLS LAST")


def clock_pending(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_occurrence_clock_pending")


def registration_deviations(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_occurrence_registration_deviation")


def content_gaps(conn: psycopg.Connection, occurrence_id: int | None = None) -> list[dict]:
    if occurrence_id:
        return fetch_all(conn, "SELECT * FROM das_occurrence_content_gaps WHERE id=%s",
                         (occurrence_id,))
    return fetch_all(conn, "SELECT * FROM das_occurrence_content_gaps"
                           " WHERE cardinality(missing) > 0 ORDER BY id")


def feedback_due(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_occurrence_feedback_due")


def detail(conn: psycopg.Connection, occurrence_id: int) -> dict:
    row = fetch_one(conn, """
        SELECT o.*, t.full_name AS triage_by_name
          FROM das_occurrence o
          LEFT JOIN app_user t ON t.id = o.triage_by
         WHERE o.id=%s""", (occurrence_id,))
    if row is None:
        raise LookupError("事件报告不存在")
    # 步骤 9: 报告人信息保密。匿名件本来就没有; 保密件有但不往外给。
    if row["disclosure"] != "NAMED":
        row["reporter_user_id"] = None
        row["reporter_name_text"] = None
    row["triage_items"] = fetch_all(conn, """
        SELECT i.category_seq, c.description, i.verdict, i.note, i.assessed_at,
               u.full_name AS assessed_by_name
          FROM das_occurrence_triage_item i
          JOIN das_occurrence_category c ON c.seq = i.category_seq
          JOIN app_user u ON u.id = i.assessed_by
         WHERE i.occurrence_id=%s ORDER BY i.category_seq""", (occurrence_id,))
    row["exemption"] = fetch_one(conn, """
        SELECT e.*, u.full_name AS signed_by_name FROM das_occurrence_exemption e
          JOIN app_user u ON u.id = e.signed_by WHERE e.occurrence_id=%s""", (occurrence_id,))
    row["submissions"] = fetch_all(conn, """
        SELECT s.*, u.full_name AS submitted_by_name FROM das_occurrence_submission s
          JOIN app_user u ON u.id = s.submitted_by
         WHERE s.occurrence_id=%s ORDER BY s.submitted_at""", (occurrence_id,))
    row["investigations"] = fetch_all(conn, """
        SELECT i.*, u.full_name AS organised_by_name FROM das_occurrence_investigation i
          JOIN app_user u ON u.id = i.organised_by
         WHERE i.occurrence_id=%s ORDER BY i.completed_at""", (occurrence_id,))
    row["feedbacks"] = fetch_all(conn, """
        SELECT f.*, u.full_name AS given_by_name FROM das_occurrence_feedback f
          JOIN app_user u ON u.id = f.given_by
         WHERE f.occurrence_id=%s ORDER BY f.given_at""", (occurrence_id,))
    row["time_changes"] = fetch_all(conn, """
        SELECT c.*, u.full_name AS changed_by_name FROM das_occurrence_time_change c
          JOIN app_user u ON u.id = c.changed_by
         WHERE c.occurrence_id=%s ORDER BY c.changed_at""", (occurrence_id,))
    return row


# ---------------------------------------------------------------- 步骤 1、2

def register(conn: psycopg.Connection, *, description: str, received_at: dt.datetime,
             received_via: str, disclosure: str, reporter_user_id: str | None,
             reporter_name_text: str | None, report_no: str | None,
             occurrence_at: dt.datetime | None, occurrence_place: str | None,
             aircraft_serial: str | None, product_model: str | None, part_marking: str | None,
             failure_nature: str | None, preliminary_cause: str | None,
             backfilled: bool, actor: dict) -> dict:
    """登记（步骤 2）。报告内容六项一律可空——判据 M6-2, 见模块说明。"""
    if not (description or "").strip():
        raise ValueError("事件描述不得为空: 没有描述的报告单没有内容可判")
    if disclosure not in DISCLOSURES:
        raise ValueError("报告人公开方式须为 NAMED 具名 / CONFIDENTIAL 保密 / ANONYMOUS 匿名")
    if received_via not in VIA:
        raise ValueError("接收方式须取自：" + "、".join(VIA))
    received_at = _aware(received_at)
    occurrence_at = _aware(occurrence_at)
    if received_at > _now():
        raise ValueError("首次接收时间不得晚于当前时间: 记录的是已发生的事")
    if disclosure == "ANONYMOUS":
        if reporter_user_id or (reporter_name_text or "").strip():
            raise ValueError(
                "匿名提交不得留下报告人。UG-DAP-12 步骤 1 允许匿名或保密两种方式: "
                "保密是有报告人但不公开, 匿名是没有报告人可记, 二者不可混用。")
        reporter_user_id, reporter_name_text = None, None
    elif not reporter_user_id and not (reporter_name_text or "").strip():
        raise ValueError("具名或保密提交须写明报告人（系统账号或外部报告人姓名之一）")

    row = fetch_one(conn, """
        INSERT INTO das_occurrence
               (report_no, description, received_at, received_via, disclosure,
                reporter_user_id, reporter_name_text, occurrence_at, occurrence_place,
                aircraft_serial, product_model, part_marking, failure_nature,
                preliminary_cause, backfilled_at, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, report_no, received_at, registered_at, disclosure
    """, (report_no, description.strip(), received_at, received_via, disclosure,
          reporter_user_id, (reporter_name_text or None), occurrence_at, occurrence_place,
          aircraft_serial, product_model, part_marking, failure_nature,
          preliminary_cause, (_now() if backfilled else None), actor["user_id"]))

    # 登记晚于接收超过 4 小时本身是一条偏离(步骤 2)。这里只提示, 不拒绝:
    # 拒绝登记会让一条已经迟到的报告干脆进不了系统, 那更糟。
    lag = (row["registered_at"] - row["received_at"]).total_seconds() / 3600.0
    audit.write(conn, action="DAS_OCCURRENCE_REGISTER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_OCCURRENCE",
                object_id=str(row["id"]), object_code=report_no,
                new_value={"received_at": str(received_at), "via": received_via,
                           "disclosure": disclosure, "lag_hours": round(lag, 2)},
                reason=report_no, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    row["registration_lag_hours"] = round(lag, 2)
    row["registration_deviation"] = lag > 4
    return row


def assess_category(conn: psycopg.Connection, *, occurrence_id: int, category_seq: int,
                    verdict: str, note: str | None, actor: dict) -> dict:
    """对 13 种情形中的一项作判断（步骤 2 的"逐项判断"）。"""
    if verdict not in VERDICTS:
        raise ValueError("判断结果须为 YES 符合 / NO 不符合 / UNSURE 无法确定")
    if verdict in ("YES", "UNSURE") and not (note or "").strip():
        raise ValueError(
            '判为"符合"或"无法确定"的, 须写明理由。这两种都会把结论推向应报告, '
            "依据要留得下来（UG-DAP-12 第 7 章: 有疑问时按应报告处理）。")
    if scalar(conn, "SELECT count(*) FROM das_occurrence WHERE id=%s", (occurrence_id,)) == 0:
        raise LookupError("事件报告不存在")
    if scalar(conn, "SELECT count(*) FROM das_occurrence_triage_item"
                    " WHERE occurrence_id=%s AND category_seq=%s",
              (occurrence_id, category_seq)):
        raise ValueError(f"第 {category_seq} 种情形已判断过。初判记录不可改写, "
                         "需要改判请在报告单上说明并重新初判。")
    return fetch_one(conn, """
        INSERT INTO das_occurrence_triage_item
               (occurrence_id, category_seq, verdict, note, assessed_by)
        VALUES (%s,%s,%s,%s,%s) RETURNING occurrence_id, category_seq, verdict
    """, (occurrence_id, category_seq, verdict, note, actor["user_id"]))


def triage_progress(conn: psycopg.Connection, occurrence_id: int) -> dict:
    row = fetch_one(conn, "SELECT * FROM das_occurrence_triage_progress WHERE id=%s",
                    (occurrence_id,))
    if row is None:
        raise LookupError("事件报告不存在")
    return row


def conclude(conn: psycopg.Connection, *, occurrence_id: int, conclusion: str,
             containment_needed: bool, containment_note: str | None, actor: dict) -> dict:
    """下初判结论（步骤 2）。

    逐项是否判完、有疑问能不能结论为不属范围、免报有没有判定行, 都由数据库触发器
    (DCMS-INV-040／041／042)把关 —— 那三条是记录之间的关系, 放在库里才拦得住绕过
    服务层的写入。
    """
    if conclusion not in CONCLUSIONS:
        raise ValueError("初判结论须取自：" + "、".join(f"{k} {v}" for k, v in CONCLUSIONS.items()))
    if containment_needed and not (containment_note or "").strip():
        raise ValueError("判定需要立即遏制的, 须写明遏制措施（UG-DAP-12 步骤 2、4）")
    row = fetch_one(conn, """
        UPDATE das_occurrence
           SET triage_conclusion=%s, triage_by=%s, triaged_at=now(),
               containment_needed=%s, containment_note=%s
         WHERE id=%s
        RETURNING id, report_no, triage_conclusion, triaged_at
    """, (conclusion, actor["user_id"], containment_needed, containment_note, occurrence_id))
    if row is None:
        raise LookupError("事件报告不存在")
    audit.write(conn, action="DAS_OCCURRENCE_TRIAGE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_OCCURRENCE",
                object_id=str(occurrence_id), object_code=row["report_no"],
                new_value={"conclusion": conclusion, "containment": containment_needed},
                reason=containment_note, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


# ---------------------------------------------------------------- 步骤 3

def exempt(conn: psycopg.Connection, *, occurrence_id: int, ground: str, rationale: str,
           evidence: str, actor: dict) -> dict:
    """免于报告的判定（步骤 3，判据 M6-3）。签署人须为适航管理负责人。"""
    if ground not in GROUNDS:
        raise ValueError("免报情形须取自 UG-DAP-12 步骤 3 的三种：" +
                         "；".join(f"{k} {v}" for k, v in GROUNDS.items()))
    for name, val in (("判定理由", rationale), ("证据", evidence)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空。免于报告不得作为默认值或静默跳过"
                             "（判据 M6-3；第 7 章: 免于报告必须有书面判定理由, 不得口头决定）。")
    # 判据 M6-3 的"适航管理负责人签署": 用岗位任命判, 不用账号角色判 ——
    # 角色是系统权限, 岗位才是体系文件里那个人(见 das_appointments 的模块说明)。
    if scalar(conn, """
            SELECT count(*) FROM das_appointment_in_force
             WHERE user_id=%s AND position_code='AWM'""", (actor["user_id"],)) == 0:
        raise ValueError(
            "免于报告的判定须由适航管理负责人签署（UG-DAP-12 步骤 3）。"
            "当前操作人没有在任的适航管理负责人任命——请先按 UG-DAP-03 完成任命。")
    if scalar(conn, "SELECT count(*) FROM das_occurrence WHERE id=%s", (occurrence_id,)) == 0:
        raise LookupError("事件报告不存在")
    if scalar(conn, "SELECT count(*) FROM das_occurrence_exemption WHERE occurrence_id=%s",
              (occurrence_id,)):
        raise ValueError("该报告已有免报判定。判定不可改写, 改判请在报告单上另行说明。")
    row = fetch_one(conn, """
        INSERT INTO das_occurrence_exemption
               (occurrence_id, ground, rationale, evidence, signed_by)
        VALUES (%s,%s,%s,%s,%s) RETURNING id, occurrence_id, ground, signed_at
    """, (occurrence_id, ground, rationale.strip(), evidence.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_OCCURRENCE_EXEMPT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_OCCURRENCE",
                object_id=str(occurrence_id), new_value={"ground": ground},
                reason=rationale, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


# ---------------------------------------------------------------- 确认存在时间

def set_confirmed(conn: psycopg.Connection, *, occurrence_id: int,
                  confirmed_at: dt.datetime, reason: str | None, actor: dict) -> dict:
    """写入或更正"确认存在时间"——48 小时的唯一起算点（判据 M6-1）。

    首次写入不需要理由; 改写必须给理由, 并先落一条变更行, 否则数据库拒绝
    (DCMS-INV-039)。把它往后挪就能让一条已超期的记录重新"未到期", 所以痕迹必须在。
    """
    cur = fetch_one(conn, "SELECT id, report_no, received_at, confirmed_at"
                          " FROM das_occurrence WHERE id=%s", (occurrence_id,))
    if cur is None:
        raise LookupError("事件报告不存在")
    confirmed_at = _aware(confirmed_at)
    if confirmed_at > _now():
        raise ValueError("确认存在时间不得晚于当前时间: 记录的是已发生的事")
    if confirmed_at < cur["received_at"]:
        raise ValueError(
            f'确认存在时间不得早于首次接收时间（{cur["received_at"]:%Y-%m-%d %H:%M}）: '
            "确认是本单位的动作, 不可能早于收到报告。")
    if cur["confirmed_at"] is not None:
        if not (reason or "").strip():
            raise ValueError(
                "确认存在时间已填, 改写须写明理由。它是 48 小时法定时限的起算点, "
                "往后挪就会让一条已超期的记录重新显示为未到期, 所以改动必须留痕。")
        fetch_one(conn, """
            INSERT INTO das_occurrence_time_change
                   (occurrence_id, field_name, old_value, new_value, reason, changed_by)
            VALUES (%s,'confirmed_at',%s,%s,%s,%s) RETURNING id
        """, (occurrence_id, cur["confirmed_at"], confirmed_at, reason.strip(),
              actor["user_id"]))
    row = fetch_one(conn, """
        UPDATE das_occurrence SET confirmed_at=%s WHERE id=%s
        RETURNING id, report_no, confirmed_at
    """, (confirmed_at, occurrence_id))
    audit.write(conn, action="DAS_OCCURRENCE_CONFIRM", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_OCCURRENCE",
                object_id=str(occurrence_id), object_code=cur["report_no"],
                old_value={"confirmed_at": str(cur["confirmed_at"])} if cur["confirmed_at"] else None,
                new_value={"confirmed_at": str(confirmed_at)}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


# ---------------------------------------------------------------- 步骤 5

def submit(conn: psycopg.Connection, *, occurrence_id: int, channel: str,
           channel_note: str | None, submitted_at: dt.datetime | None, reference_no: str | None,
           receipt_ref: str | None, is_supplement: bool, actor: dict) -> dict:
    """向局方报送（步骤 5）。

    **这里刻意不校验报告内容是否填齐**（判据 M6-2）。"信息不全的先报已知部分",
    加一道必填校验就等于用表单完整性换 48 小时超期。缺哪几项随本次报送记在
    content_gaps 上, 对应程序里"注明后续补充"。
    """
    occ = fetch_one(conn, "SELECT id, report_no, confirmed_at FROM das_occurrence WHERE id=%s",
                    (occurrence_id,))
    if occ is None:
        raise LookupError("事件报告不存在")
    if not (channel or "").strip():
        raise ValueError("须写明报送途径（AMOS 或与主管监察员约定的方式）")
    if channel != "AMOS" and not (channel_note or "").strip():
        raise ValueError(
            "AMOS 以外的途径须写明实际使用的方式（UG-DAP-12 步骤 5: "
            "AMOS 不可用或主管监察员另有要求的, 按约定方式报送, 并在 UG-DAF-08 上注明）。")
    at = _aware(submitted_at) or _now()
    if at > _now():
        raise ValueError("报送时间不得晚于当前时间")

    gaps = fetch_one(conn, "SELECT missing FROM das_occurrence_content_gaps WHERE id=%s",
                     (occurrence_id,))
    missing = list(gaps["missing"] or []) if gaps else []
    row = fetch_one(conn, """
        INSERT INTO das_occurrence_submission
               (occurrence_id, channel, channel_note, submitted_at, submitted_by,
                reference_no, receipt_ref, is_supplement, content_gaps)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, occurrence_id, submitted_at, is_supplement
    """, (occurrence_id, channel.strip(), channel_note, at, actor["user_id"],
          reference_no, receipt_ref, is_supplement,
          ("、".join(missing) if missing else None)))
    if not is_supplement:
        fetch_one(conn, "UPDATE das_occurrence SET reported_at=COALESCE(reported_at,%s)"
                        " WHERE id=%s RETURNING id", (at, occurrence_id))
    audit.write(conn, action="DAS_OCCURRENCE_SUBMIT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_OCCURRENCE",
                object_id=str(occurrence_id), object_code=reference_no,
                new_value={"channel": channel, "submitted_at": str(at),
                           "supplement": is_supplement, "content_gaps": missing},
                reason=reference_no, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    row["content_gaps"] = missing
    # 报送成功后把时限状态一并返回, 让超期当场可见, 而不是等人去翻视图。
    row["clock"] = fetch_one(conn, "SELECT clock_state, elapsed_hours, deadline_at"
                                   " FROM das_occurrence_deadline WHERE id=%s", (occurrence_id,))
    return row


# ---------------------------------------------------------------- 步骤 7、9

def investigate(conn: psycopg.Connection, *, occurrence_id: int, findings: str,
                root_cause: str, report_ref: str | None, actor: dict) -> dict:
    for name, val in (("调查发现", findings), ("根本原因", root_cause)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（UG-DAP-12 步骤 7: 查明事实和根本原因）")
    if scalar(conn, "SELECT count(*) FROM das_occurrence WHERE id=%s", (occurrence_id,)) == 0:
        raise LookupError("事件报告不存在")
    return fetch_one(conn, """
        INSERT INTO das_occurrence_investigation
               (occurrence_id, findings, root_cause, report_ref, organised_by)
        VALUES (%s,%s,%s,%s,%s) RETURNING id, occurrence_id, completed_at
    """, (occurrence_id, findings.strip(), root_cause.strip(), report_ref, actor["user_id"]))


def give_feedback(conn: psycopg.Connection, *, occurrence_id: int, content: str,
                  actor: dict) -> dict:
    """向报告人反馈（步骤 9）。匿名件没有反馈对象。"""
    if not (content or "").strip():
        raise ValueError("反馈内容不得为空")
    occ = fetch_one(conn, "SELECT id, disclosure FROM das_occurrence WHERE id=%s",
                    (occurrence_id,))
    if occ is None:
        raise LookupError("事件报告不存在")
    if occ["disclosure"] == "ANONYMOUS":
        raise ValueError(
            "匿名提交没有可反馈的对象。UG-DAP-12 步骤 9 的反馈对保密件仍然适用——"
            "保密是不公开报告人, 不是找不到报告人。")
    return fetch_one(conn, """
        INSERT INTO das_occurrence_feedback (occurrence_id, content, given_by)
        VALUES (%s,%s,%s) RETURNING id, occurrence_id, given_at
    """, (occurrence_id, content.strip(), actor["user_id"]))
