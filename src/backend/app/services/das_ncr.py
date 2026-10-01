"""不符合项与纠正措施（M7），含工作日日历（基础能力的一部分）。

依据 UG-DAP-14 第 6 章步骤 1～10、第 7 章关键控制点，判据 M7-1、M7-2、I6、I7。

三处与直觉相反、但必须这么做的地方：

  · **没有"关闭"这个动作, 只有两条各自独立的关闭记录。**
    局方开具的不符合项要"局方评估其合理性并验证后方可关闭"(步骤 8)。所以这里
    提供的是 close_internally() 与 record_caac_closure() 两个函数, 谁也写不到
    对方的字段; "是否已关闭"由视图推导。做一个 close() 并在里面判来源, 就等于
    把那支笔交回给了内部操作(判据 M7-1)。

  · **时限从局方发布之日算, 不是收到之日。**两个日期都存, 起算只认 issued_on。
    按收到之日算会把法规期限整体往后挪 —— 核对时发现差 2 天就是这么来的。

  · **一类问题的 21 个工作日在日历没加载时算不出来, 这时返回 NULL 并说明原因。**
    估一个出来比空着危险: 它看上去精确, 而错的是一条法规时限。日历缺口由
    calendar_gaps() 列出, 让"今年的节假日通知有没有加载"成为看得见的待办。

还有一处是**系统不假装能判的**: 第 7 章把"预防措施与纠正措施内容雷同"视为未采取
预防措施。完全相同、互为子串由数据库拒绝; 语义上近似要人看, 由 preventive_review()
列出线索供独立验证人判断, 不下结论。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

SOURCES = {
    "CAAC": "局方开具（审定信函／表-21-165）",
    "INTERNAL_AUDIT": "内部审核",
    "SUPERVISION": "独立监督",
    "INSPECTION": "内部检查",
    "SUPPLIER": "供应商",
    "OCCURRENCE": "事件调查（UG-DAP-12 步骤 8 转入）",
}
CLASSES = {"CLASS_1": "一类问题", "CLASS_2": "二类问题", "OBSERVATION": "观察项"}
RC_LEVELS = {
    "PROCEDURE": "程序层面", "MANAGEMENT": "管理层面", "TRAINING": "培训层面",
    "RESOURCE": "资源层面", "SUPPLIER": "供应商层面", "HUMAN_ERROR_ONLY": "仅人为失误",
}
EXT_GROUNDS = ("IMPROPER_MAINTENANCE", "ABNORMAL_USE", "ALREADY_REPORTED")


# ---------------------------------------------------------------- 工作日日历

def calendar_gaps(conn: psycopg.Connection) -> list[dict]:
    """前后三年的日历加载情况。declared=false 的年份算不出工作日时限。"""
    return fetch_all(conn, "SELECT * FROM das_work_calendar_gap")


def calendar_days(conn: psycopg.Connection, year: int | None = None) -> list[dict]:
    if year:
        return fetch_all(conn, """
            SELECT * FROM das_work_calendar
             WHERE extract(year FROM day)::int = %s ORDER BY day""", (year,))
    return fetch_all(conn, "SELECT * FROM das_work_calendar ORDER BY day")


def declare_calendar_year(conn: psycopg.Connection, *, year: int, source_ref: str,
                          actor: dict) -> dict:
    """声明某一年的节假日安排已按权威来源加载完整。

    这一步不是形式: 没有它, "例外日表里没有这一年"与"这一年没人录过"看着一样,
    而法规时限不能建立在这种歧义上。source_ref 要写得出文号。
    """
    if not (source_ref or "").strip():
        raise ValueError(
            "须写明依据来源（如国务院办公厅关于某年部分节假日安排的通知）。"
            "工作日时限是法规时限的一部分, 它依赖的日历必须可追溯到权威发布。")
    if scalar(conn, "SELECT count(*) FROM das_work_calendar_year WHERE calendar_year=%s",
              (year,)):
        raise ValueError(f"{year} 年已声明加载。日历内容可继续补录例外日。")
    row = fetch_one(conn, """
        INSERT INTO das_work_calendar_year (calendar_year, source_ref, declared_by)
        VALUES (%s,%s,%s) RETURNING calendar_year, source_ref, declared_at
    """, (year, source_ref.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_CALENDAR_DECLARE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_WORK_CALENDAR_YEAR",
                object_id=str(year), object_code=str(year),
                new_value={"source_ref": source_ref}, reason=source_ref,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def add_calendar_day(conn: psycopg.Connection, *, day: dt.date, kind: str,
                     name_cn: str | None, source_ref: str, actor: dict) -> dict:
    if kind not in ("HOLIDAY", "MAKEUP"):
        raise ValueError("类别须为 HOLIDAY 法定节假日（不上班）或 MAKEUP 调休上班日（上班）")
    if not (source_ref or "").strip():
        raise ValueError("须写明依据文号")
    return fetch_one(conn, """
        INSERT INTO das_work_calendar (day, kind, name_cn, source_ref, loaded_by)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (day) DO UPDATE SET kind=EXCLUDED.kind, name_cn=EXCLUDED.name_cn,
                                        source_ref=EXCLUDED.source_ref,
                                        loaded_by=EXCLUDED.loaded_by, loaded_at=now()
        RETURNING day, kind, name_cn
    """, (day, kind, name_cn, source_ref.strip(), actor["user_id"]))


# ---------------------------------------------------------------- 查询

def register_list(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_ncr_register")


def deadlines(conn: psycopg.Connection, only_open: bool = False) -> list[dict]:
    if only_open:
        return fetch_all(conn, "SELECT * FROM das_ncr_deadline WHERE NOT is_closed"
                               " ORDER BY deadline_on NULLS LAST")
    return fetch_all(conn, "SELECT * FROM das_ncr_deadline ORDER BY deadline_on NULLS LAST")


def overdue(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_ncr_overdue")


def escalation_risk(conn: psycopg.Connection) -> list[dict]:
    """二类临近时限而缺 CAR 或缺答复的。第 7 章: 未按要求提交或落实将上升为一类。"""
    return fetch_all(conn, "SELECT * FROM das_ncr_escalation_risk")


def recurrence(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_ncr_recurrence")


def observations_open(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_ncr_observation_open")


def preventive_review(conn: psycopg.Connection) -> list[dict]:
    """预防措施待人工复核的线索。系统不判语义雷同, 只给线索。"""
    return fetch_all(conn, "SELECT * FROM das_car_preventive_review")


def quarterly(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_ncr_quarterly")


def detail(conn: psycopg.Connection, ncr_id: int) -> dict:
    row = fetch_one(conn, """
        SELECT n.*, d.deadline_on, d.deadline_note, d.is_closed,
               u.full_name AS responsible_name
          FROM das_ncr n
          LEFT JOIN das_ncr_deadline d ON d.id = n.id
          LEFT JOIN app_user u ON u.id = n.responsible_user
         WHERE n.id=%s""", (ncr_id,))
    if row is None:
        raise LookupError("不符合项不存在")
    row["containments"] = fetch_all(conn, """
        SELECT c.*, u.full_name AS decided_by_name FROM das_ncr_containment c
          JOIN app_user u ON u.id = c.decided_by WHERE c.ncr_id=%s ORDER BY c.decided_at""",
                                    (ncr_id,))
    row["root_causes"] = fetch_all(conn, """
        SELECT r.*, u.full_name AS analysed_by_name FROM das_ncr_root_cause r
          JOIN app_user u ON u.id = r.analysed_by WHERE r.ncr_id=%s ORDER BY r.analysed_at""",
                                   (ncr_id,))
    row["car"] = fetch_one(conn, "SELECT * FROM das_car WHERE ncr_id=%s", (ncr_id,))
    row["extensions"] = fetch_all(conn, """
        SELECT * FROM das_ncr_extension WHERE ncr_id=%s ORDER BY requested_on""", (ncr_id,))
    row["caac_replies"] = fetch_all(conn, """
        SELECT * FROM das_ncr_caac_reply WHERE ncr_id=%s ORDER BY submitted_on""", (ncr_id,))
    row["closure"] = fetch_one(conn, """
        SELECT k.*, v.full_name AS verifier_name, g.full_name AS designated_by_name
          FROM das_ncr_closure k
          JOIN app_user v ON v.id = k.verifier
          LEFT JOIN app_user g ON g.id = k.designated_by
         WHERE k.ncr_id=%s""", (ncr_id,))
    row["observation_response"] = fetch_one(conn, """
        SELECT * FROM das_ncr_observation_response WHERE ncr_id=%s""", (ncr_id,))
    return row


# ---------------------------------------------------------------- 步骤 1、4

def register(conn: psycopg.Connection, *, source: str, ncr_class: str | None,
             fact: str, basis_clause: str, evidence: str, issued_on: dt.date | None,
             received_on: dt.date | None, source_ref: str | None, ncr_no: str | None,
             responsible_user: str | None, responsible_dept: str | None,
             occurrence_id: int | None, concerns_ism: bool, actor: dict) -> dict:
    if source not in SOURCES:
        raise ValueError("来源须取自：" + "、".join(f"{k} {v}" for k, v in SOURCES.items()))
    for name, val in (("事实", fact), ("依据条款", basis_clause), ("证据", evidence)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（UG-DAP-14 步骤 4: 事实、依据条款、证据、来源）")
    if source == "CAAC":
        if ncr_class not in CLASSES:
            raise ValueError(
                "局方开具的不符合项须写明分类：" + "、".join(f"{k} {v}" for k, v in CLASSES.items())
                + "。不写分类就定不出整改时限是 21 个工作日还是 3 个月。")
        if issued_on is None:
            raise ValueError(
                "须填写局方发布记录表的日期。UG-DAP-14 第 7 章: 整改时限自局方发布记录表"
                "之日起算, 不是收到之日 —— 没有这个日期就无从起算。")
    elif ncr_class is not None:
        raise ValueError("一类／二类／观察项是局方的分类, 内部来源的不符合项不套用")
    if issued_on and issued_on > dt.date.today():
        raise ValueError("发布日期不得晚于今天")
    if received_on and issued_on and received_on < issued_on:
        raise ValueError("收到日期不得早于发布日期")

    row = fetch_one(conn, """
        INSERT INTO das_ncr (ncr_no, source, ncr_class, fact, basis_clause, evidence,
                             issued_on, received_on, source_ref, responsible_user,
                             responsible_dept, occurrence_id, concerns_ism, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, ncr_no, source, ncr_class, issued_on, registered_at
    """, (ncr_no, source, ncr_class, fact.strip(), basis_clause.strip(), evidence.strip(),
          issued_on, received_on, source_ref, responsible_user, responsible_dept,
          occurrence_id, concerns_ism, actor["user_id"]))
    audit.write(conn, action="DAS_NCR_REGISTER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(row["id"]), object_code=ncr_no,
                new_value={"source": source, "class": ncr_class,
                           "basis": basis_clause, "issued_on": str(issued_on)},
                reason=source_ref, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    # 当场把时限和重复情况返回, 免得还要再查一次才知道要紧不要紧。
    row["deadline"] = fetch_one(conn, "SELECT deadline_on, deadline_note"
                                      " FROM das_ncr_deadline WHERE id=%s", (row["id"],))
    row["recurrence_within_12m"] = scalar(conn, """
        SELECT count(*) FROM das_ncr WHERE id <> %s AND basis_clause = %s
           AND registered_at >= now() - interval '12 months'""",
                                          (row["id"], basis_clause.strip()))
    return row


def contain(conn: psycopg.Connection, *, ncr_id: int, measures: str,
            issued_docs_impact: str | None, actor: dict) -> dict:
    if not (measures or "").strip():
        raise ValueError("遏制措施不得为空（UG-DAP-14 步骤 5）")
    if scalar(conn, "SELECT count(*) FROM das_ncr WHERE id=%s", (ncr_id,)) == 0:
        raise LookupError("不符合项不存在")
    return fetch_one(conn, """
        INSERT INTO das_ncr_containment (ncr_id, measures, issued_docs_impact, decided_by)
        VALUES (%s,%s,%s,%s) RETURNING id, ncr_id, decided_at
    """, (ncr_id, measures.strip(), issued_docs_impact, actor["user_id"]))


def analyse(conn: psycopg.Connection, *, ncr_id: int, level: str, analysis: str,
            actor: dict) -> dict:
    """根本原因分析（步骤 6）。可记多条; 只有"仅人为失误"一条时不得关闭。"""
    if level not in RC_LEVELS:
        raise ValueError("层面须取自：" + "、".join(f"{k} {v}" for k, v in RC_LEVELS.items()))
    if not (analysis or "").strip():
        raise ValueError("分析内容不得为空")
    if scalar(conn, "SELECT count(*) FROM das_ncr WHERE id=%s", (ncr_id,)) == 0:
        raise LookupError("不符合项不存在")
    return fetch_one(conn, """
        INSERT INTO das_ncr_root_cause (ncr_id, level, analysis, analysed_by)
        VALUES (%s,%s,%s,%s) RETURNING id, ncr_id, level, analysed_at
    """, (ncr_id, level, analysis.strip(), actor["user_id"]))


# ---------------------------------------------------------------- 步骤 7

def draft_car(conn: psycopg.Connection, *, ncr_id: int, correction: str,
              corrective_action: str, preventive_action: str,
              correction_owner: str, corrective_owner: str, preventive_owner: str,
              correction_due: dt.date, corrective_due: dt.date, preventive_due: dt.date,
              actor: dict) -> dict:
    """UG-DAF-05C。三栏分别填写, 内容不得雷同（第 7 章）。

    雷同的机械部分由 DCMS-INV-050 拒绝。这里只补一条服务层的提示: 三个负责人都是
    同一人时并不违规(5～8 人编制下常态), 但会进 preventive_review 供验证人留意。
    """
    for name, val in (("纠正", correction), ("纠正措施", corrective_action),
                      ("预防措施", preventive_action)):
        if not (val or "").strip():
            raise ValueError(
                f"{name}不得为空。UG-DAP-14 步骤 7 要求三者分别填写: 纠正消除已发生的"
                "不符合本身, 纠正措施针对根本原因, 预防措施举一反三排查同类。")
    if scalar(conn, "SELECT count(*) FROM das_ncr WHERE id=%s", (ncr_id,)) == 0:
        raise LookupError("不符合项不存在")
    if scalar(conn, "SELECT count(*) FROM das_car WHERE ncr_id=%s", (ncr_id,)):
        raise ValueError("该不符合项已有 UG-DAF-05C。措施调整请在表上说明并留痕。")
    row = fetch_one(conn, """
        INSERT INTO das_car (ncr_id, correction, corrective_action, preventive_action,
                             correction_owner, corrective_owner, preventive_owner,
                             correction_due, corrective_due, preventive_due, drafted_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, ncr_id, drafted_at
    """, (ncr_id, correction.strip(), corrective_action.strip(), preventive_action.strip(),
          correction_owner, corrective_owner, preventive_owner,
          correction_due, corrective_due, preventive_due, actor["user_id"]))
    audit.write(conn, action="DAS_CAR_DRAFT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(ncr_id), new_value={"car_id": row["id"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


# ---------------------------------------------------------------- 步骤 2 延期

def request_extension(conn: psycopg.Connection, *, ncr_id: int, requested_to: dt.date,
                      reason: str, actor: dict) -> dict:
    """二类问题的延期申请。一类不得延期, 由 DCMS-INV-044 拒绝。"""
    if not (reason or "").strip():
        raise ValueError("延期理由不得为空")
    if scalar(conn, "SELECT count(*) FROM das_ncr WHERE id=%s", (ncr_id,)) == 0:
        raise LookupError("不符合项不存在")
    row = fetch_one(conn, """
        INSERT INTO das_ncr_extension (ncr_id, requested_to, reason, requested_by)
        VALUES (%s,%s,%s,%s) RETURNING id, ncr_id, requested_on, requested_to
    """, (ncr_id, requested_to, reason.strip(), actor["user_id"]))
    row["note"] = ("延期申请已登记, 但时限尚未顺延。第 7 章要求延期计划**事先**得到"
                   "局方同意, 同意记录登记后时限才改变。")
    return row


def record_extension_agreement(conn: psycopg.Connection, *, extension_id: int,
                               agreed_on: dt.date, agreed_ref: str, actor: dict) -> dict:
    if not (agreed_ref or "").strip():
        raise ValueError("须写明局方同意的依据（函件号、邮件日期等）, 否则无从核对")
    n = execute(conn, """
        UPDATE das_ncr_extension
           SET caac_agreed=true, caac_agreed_on=%s, caac_agreed_ref=%s
         WHERE id=%s AND NOT caac_agreed""", (agreed_on, agreed_ref.strip(), extension_id))
    if not n:
        raise ValueError("延期申请不存在或已登记局方同意")
    row = fetch_one(conn, "SELECT ncr_id, requested_to FROM das_ncr_extension WHERE id=%s",
                    (extension_id,))
    audit.write(conn, action="DAS_NCR_EXTENSION_AGREED", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(row["ncr_id"]), object_code=agreed_ref,
                new_value={"requested_to": str(row["requested_to"]),
                           "agreed_on": str(agreed_on)}, reason=agreed_ref,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT deadline_on, deadline_note FROM das_ncr_deadline"
                           " WHERE id=%s", (row["ncr_id"],))


# ---------------------------------------------------------------- 步骤 8

def submit_caac_reply(conn: psycopg.Connection, *, ncr_id: int, form_ref: str,
                      correction_completed_on: dt.date, corrective_completed_on: dt.date,
                      confirmed_by: str, submitted_on: dt.date | None, actor: dict) -> dict:
    """提交表-21-166（步骤 8）。两个完成时间都必填, 缺一即答复不完整。"""
    if not (form_ref or "").strip():
        raise ValueError("须写明表-21-166 的编号")
    n = fetch_one(conn, "SELECT id, source FROM das_ncr WHERE id=%s", (ncr_id,))
    if n is None:
        raise LookupError("不符合项不存在")
    if n["source"] != "CAAC":
        raise ValueError(
            "表-21-166 是对局方开具不符合项的答复。内部不符合项不向局方提交答复, "
            "闭环走内部关闭验证（步骤 9）。")
    row = fetch_one(conn, """
        INSERT INTO das_ncr_caac_reply (ncr_id, form_ref, correction_completed_on,
                                        corrective_completed_on, confirmed_by,
                                        submitted_on, submitted_by)
        VALUES (%s,%s,%s,%s,%s,COALESCE(%s, current_date),%s)
        RETURNING id, ncr_id, form_ref, submitted_on
    """, (ncr_id, form_ref.strip(), correction_completed_on, corrective_completed_on,
          confirmed_by, submitted_on, actor["user_id"]))
    audit.write(conn, action="DAS_NCR_CAAC_REPLY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(ncr_id), object_code=form_ref,
                new_value={"form_ref": form_ref}, reason=form_ref,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    row["note"] = ("答复已提交。局方评估其合理性并验证后方可关闭 —— 内部动作不把状态"
                   "推进到已关闭（判据 M7-1）。")
    return row


# ---------------------------------------------------------------- 步骤 3、9

def respond_observation(conn: psycopg.Connection, *, ncr_id: int, assessment: str,
                        disposition: str, to_management_review: bool, actor: dict) -> dict:
    for name, val in (("评估", assessment), ("处理意见", disposition)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（UG-DAP-14 步骤 3）")
    n = fetch_one(conn, "SELECT id, ncr_class FROM das_ncr WHERE id=%s", (ncr_id,))
    if n is None:
        raise LookupError("不符合项不存在")
    if n["ncr_class"] != "OBSERVATION":
        raise ValueError("该记录不是观察项。不符合项的闭环走纠正措施与关闭验证, 不是处理意见。")
    return fetch_one(conn, """
        INSERT INTO das_ncr_observation_response
               (ncr_id, assessment, disposition, to_management_review, responded_by)
        VALUES (%s,%s,%s,%s,%s) RETURNING id, ncr_id, responded_at
    """, (ncr_id, assessment.strip(), disposition.strip(), to_management_review,
          actor["user_id"]))


def verify_closure(conn: psycopg.Connection, *, ncr_id: int, evidence: str,
                   effectiveness: str, designated_by: str | None,
                   designation_basis: str | None, verifier_qualification: str | None,
                   independence_note: str | None, actor: dict) -> dict:
    """内部关闭验证（步骤 9）。签署人即当前操作人——验证是本人的签署行为。

    "验证人不得是整改责任人""涉及独立监督职能自身须有责任经理指定""原因不止于人为
    失误""12 个月内重复须先升级"四条都在数据库触发器里: 它们是记录之间的关系,
    放在库里才拦得住绕过服务层的写入。
    """
    for name, val in (("实施证据", evidence), ("有效性结论", effectiveness)):
        if not (val or "").strip():
            raise ValueError(
                f'{name}不得为空。UG-DAP-14 步骤 9: 核对实施证据和有效性, '
                '仅有"已整改"声明不得关闭（判据 M7-2）。')
    if scalar(conn, "SELECT count(*) FROM das_ncr WHERE id=%s", (ncr_id,)) == 0:
        raise LookupError("不符合项不存在")
    if scalar(conn, "SELECT count(*) FROM das_ncr_closure WHERE ncr_id=%s", (ncr_id,)):
        raise ValueError("该不符合项已有关闭验证记录。验证记录不可改写。")
    row = fetch_one(conn, """
        INSERT INTO das_ncr_closure (ncr_id, verifier, evidence, effectiveness,
                                     designated_by, designation_basis,
                                     verifier_qualification, independence_note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, ncr_id, verified_at
    """, (ncr_id, actor["user_id"], evidence.strip(), effectiveness.strip(),
          designated_by, designation_basis, verifier_qualification, independence_note))
    audit.write(conn, action="DAS_NCR_VERIFY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(ncr_id), new_value={"closure_id": row["id"]},
                reason=effectiveness, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def close_internally(conn: psycopg.Connection, *, ncr_id: int, actor: dict) -> dict:
    """写入内部关闭时间。**局方开具项不因此变为已关闭**（判据 M7-1）。"""
    n = execute(conn, """UPDATE das_ncr SET internal_closed_at=now()
                          WHERE id=%s AND internal_closed_at IS NULL""", (ncr_id,))
    if not n:
        raise ValueError("不符合项不存在或已登记内部关闭")
    row = fetch_one(conn, "SELECT * FROM das_ncr_deadline WHERE id=%s", (ncr_id,))
    audit.write(conn, action="DAS_NCR_CLOSE_INTERNAL", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(ncr_id), new_value={"is_closed": row["is_closed"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    if not row["is_closed"]:
        row["note"] = ("内部关闭已登记, 但本项为局方开具, 仍须局方确认关闭才算闭环"
                       "（UG-DAP-14 步骤 8: 局方评估其合理性并验证后方可关闭）。")
    return row


def record_caac_closure(conn: psycopg.Connection, *, ncr_id: int, closed_ref: str,
                        actor: dict) -> dict:
    """登记局方的关闭确认。与 close_internally 是两支笔, 互相写不到（判据 M7-1）。"""
    if not (closed_ref or "").strip():
        raise ValueError(
            "须写明局方关闭确认的依据（函件号、监督检查关闭通知等）。"
            "没有依据的「局方已关闭」等于内部自己宣布关闭。")
    n = fetch_one(conn, "SELECT id, source FROM das_ncr WHERE id=%s", (ncr_id,))
    if n is None:
        raise LookupError("不符合项不存在")
    if n["source"] != "CAAC":
        raise ValueError("只有局方开具的不符合项才有局方关闭确认")
    k = execute(conn, """UPDATE das_ncr SET caac_closed_at=now(), caac_closed_ref=%s
                          WHERE id=%s AND caac_closed_at IS NULL""",
                (closed_ref.strip(), ncr_id))
    if not k:
        raise ValueError("该项已登记局方关闭")
    audit.write(conn, action="DAS_NCR_CLOSE_CAAC", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(ncr_id), object_code=closed_ref,
                new_value={"caac_closed_ref": closed_ref}, reason=closed_ref,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_ncr_deadline WHERE id=%s", (ncr_id,))


def mark_systemic(conn: psycopg.Connection, *, ncr_id: int, actor: dict) -> dict:
    """升级为系统性纠正措施（第 7 章：12 个月内重复发生的）。"""
    n = execute(conn, "UPDATE das_ncr SET systemic=true WHERE id=%s AND NOT systemic",
                (ncr_id,))
    if not n:
        raise ValueError("不符合项不存在或已标记为系统性")
    audit.write(conn, action="DAS_NCR_SYSTEMIC", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_NCR",
                object_id=str(ncr_id), new_value={"systemic": True},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return {"id": ncr_id, "systemic": True,
            "note": "已升级为系统性纠正措施。第 7 章同时要求重新分析根本原因。"}
