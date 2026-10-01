"""独立监督与内部审核（M2）。

依据 UG-DAP-13 第 6 章步骤 1～10、第 7 章关键控制点，判据 L4、L5、L10、M2-1、M2-2、I1、I6、I7。

【判据 M2-2：写入授权来自岗位任命，不来自账号角色】
这是本模块与其他模块最大的不同。别的模块把写权限挂在 `DAS_*_MANAGE` 上，由
CONFIGURATION_MANAGER 持有；这里不能这么做 —— 挂在角色上，拥有该角色的人（包括
`admin`）就能写监督记录，而 I6／I7 要求**被监督对象无写权限，admin 亦然**。

所以本模块的端点只要求"已登录"，真正的闸门是 `_require_position()`：

  · 排计划、实施审核、记录发现、出报告 —— 须在任的独立监督负责人（ISM）
  · 批准计划 —— 须在任的责任经理（AM）
  · 确认审核员资格 —— 须在任的适航管理负责人（AWM，步骤 4）
  · 对独立监督职能自身的审核 —— 由 AM 组织，实施人**不得**持 ISM 任命（步骤 10）

副作用是: 没有人被任命为独立监督负责人之前, 谁也写不了监督记录。这不是缺陷 ——
没有在任的独立监督负责人, 本来就不存在"独立监督"这回事。

【判据 M2-1：两类活动分开，连覆盖率都分开算】
独立监督覆盖的是符合性检查单的适用项；质量系统内部审核覆盖的是部门与过程。
一份计划只能是一类，合并编制表现为两份计划共用 document_ref。两类的周期状态与
覆盖率各算一套，没有一个合并后的"内审覆盖率" —— 合并出来的那个数字对局方没有意义。

【判据 L10：24 个月不在这里出现】
AP-21-18 4.2 的 24 个月是局方对本单位开展计划性监督的周期。它与本模块的 12 个月
一旦混用，内部监督就晚一年。计划周期上限由 DCMS-INV-054 挡住，填不进去。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

KINDS = {"DAS_SUPERVISION": "设计保证系统独立监督", "QMS_AUDIT": "质量系统内部审核"}
SCOPE_KINDS = {"DEPARTMENT": "部门", "PROCESS": "过程", "CHECKLIST": "检查单条目",
               "FUNCTION": "职能"}
VERDICTS = {"CONFORM": "符合", "NONCONFORM": "不符合", "OBSERVATION": "观察"}
TRIGGERS = {
    "SERIOUS_SERVICE_PROBLEM": "严重使用问题",
    "MAJOR_CHANGE": "过程或系统的重大更改",
    "NEW_CAPABILITY": "新增生产或设计能力",
    "SITE_RELOCATION": "场所迁移",
    "KEY_PERSONNEL_CHANGE": "关键人员变更",
}
POSITION_CN = {"ISM": "独立监督负责人", "AM": "责任经理", "AWM": "适航管理负责人"}


def _require_position(conn: psycopg.Connection, actor: dict, code: str) -> None:
    """按**岗位任命**判写入授权，不按账号角色（判据 M2-2）。"""
    if scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                        WHERE user_id=%s AND position_code=%s""",
              (actor["user_id"], code)) == 0:
        raise PermissionError(
            f"本操作须由在任的{POSITION_CN[code]}执行。"
            f"UG-DAP-13 是独立权限域（判据 M2-2、I6／I7）: 写入授权来自岗位任命, "
            f"不来自账号角色 —— 被监督对象无写权限, 系统管理员角色也不例外。"
            f"请先按 UG-DAP-03 完成任命。")


# ---------------------------------------------------------------- 审核员（步骤 4）

def auditors(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_auditor_roster")


def qualify_auditor(conn: psycopg.Connection, *, user_id: str | None,
                    external_name: str | None, external_org: str | None,
                    method_training_ref: str, ccar21_training_ref: str,
                    manual_training_ref: str, external_evidence_ref: str | None,
                    valid_from: dt.date | None, valid_to: dt.date | None,
                    actor: dict) -> dict:
    """确认审核员资格。步骤 4：资格由适航管理负责人确认。

    三项培训证据分列而不是一个"已培训"布尔值: 只学过审核方法而没学过 CCAR-21 的人,
    审出来的结论正是第 7 章说的"审核依据不全"。
    """
    _require_position(conn, actor, "AWM")
    for name, val in (("审核方法培训证据", method_training_ref),
                      ("CCAR-21 培训证据", ccar21_training_ref),
                      ("本单位体系文件培训证据", manual_training_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（UG-DAP-13 步骤 4: 审核方法、CCAR-21 "
                             "及本单位体系文件三项均须有资格记录）")
    if user_id and (external_name or external_org):
        raise ValueError("本单位审核员与外单位审核员二者只能填一个")
    if not user_id:
        if not (external_name or "").strip() or not (external_org or "").strip():
            raise ValueError("外单位审核员须写明姓名与所属单位")
        if not (external_evidence_ref or "").strip():
            raise ValueError(
                "外单位审核员须保存资格证明（UG-DAP-13 步骤 4 末句）。"
                "没有留存的资格证明, 这名审核员的结论就没有依据。")
    row = fetch_one(conn, """
        INSERT INTO das_auditor (user_id, external_name, external_org,
                                 method_training_ref, ccar21_training_ref,
                                 manual_training_ref, external_evidence_ref,
                                 confirmed_by, valid_from, valid_to)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s, current_date),%s)
        RETURNING id, user_id, external_name, valid_from, valid_to
    """, (user_id, external_name, external_org, method_training_ref.strip(),
          ccar21_training_ref.strip(), manual_training_ref.strip(),
          external_evidence_ref, actor["user_id"], valid_from, valid_to))
    audit.write(conn, action="DAS_AUDITOR_QUALIFY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_AUDITOR",
                object_id=str(row["id"]), object_code=external_name,
                new_value={"user_id": user_id, "external": external_name},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def revoke_auditor(conn: psycopg.Connection, *, auditor_id: int, reason: str,
                   actor: dict) -> None:
    _require_position(conn, actor, "AWM")
    if not (reason or "").strip():
        raise ValueError("撤销审核员资格须写明理由")
    n = execute(conn, """UPDATE das_auditor SET revoked_at=now(), revoke_reason=%s
                          WHERE id=%s AND revoked_at IS NULL""", (reason.strip(), auditor_id))
    if not n:
        raise ValueError("审核员资格记录不存在或已撤销")


# ---------------------------------------------------------------- 计划（步骤 1～3）

def plans(conn: psycopg.Connection, kind: str | None = None) -> list[dict]:
    if kind:
        return fetch_all(conn, """
            SELECT p.*, u.full_name AS prepared_by_name, a.full_name AS approved_by_name
              FROM das_audit_plan p JOIN app_user u ON u.id = p.prepared_by
              LEFT JOIN app_user a ON a.id = p.approved_by
             WHERE p.kind=%s ORDER BY p.period_from DESC""", (kind,))
    return fetch_all(conn, """
        SELECT p.*, u.full_name AS prepared_by_name, a.full_name AS approved_by_name
          FROM das_audit_plan p JOIN app_user u ON u.id = p.prepared_by
          LEFT JOIN app_user a ON a.id = p.approved_by
         ORDER BY p.period_from DESC, p.kind""")


def cycle_status(conn: psycopg.Connection) -> list[dict]:
    """两类活动的周期状态。**分别**算，没有合并后的那个数字（判据 M2-1）。"""
    return fetch_all(conn, "SELECT * FROM das_audit_cycle_status ORDER BY kind")


def create_plan(conn: psycopg.Connection, *, kind: str, period_from: dt.date,
                period_to: dt.date, scope_note: str, document_ref: str | None,
                supersedes: int | None, change_reason: str | None, actor: dict) -> dict:
    _require_position(conn, actor, "ISM")
    if kind not in KINDS:
        raise ValueError("计划类别须为：" + "、".join(f"{k} {v}" for k, v in KINDS.items())
                         + "。两类活动的覆盖对象不同, 一份计划只能是其中一类（判据 M2-1）。")
    if not (scope_note or "").strip():
        raise ValueError(
            "须写明本类活动的覆盖范围。UG-DAP-13 步骤 1: 两类计划可合并编制, "
            "但周期和覆盖范围须**分别标明** —— 不写范围就等于合并掉了这个区分。")
    if period_to <= period_from:
        raise ValueError("周期结束日须晚于起始日")
    if supersedes and not (change_reason or "").strip():
        raise ValueError("计划变更须说明原因并重新批准（UG-DAP-13 步骤 3）")
    row = fetch_one(conn, """
        INSERT INTO das_audit_plan (kind, document_ref, period_from, period_to,
                                    scope_note, prepared_by, supersedes, change_reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, kind, period_from, period_to, document_ref
    """, (kind, document_ref, period_from, period_to, scope_note.strip(),
          actor["user_id"], supersedes, change_reason))
    audit.write(conn, action="DAS_AUDIT_PLAN", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_AUDIT_PLAN",
                object_id=str(row["id"]), object_code=document_ref,
                new_value={"kind": kind, "period": f"{period_from}~{period_to}"},
                reason=change_reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    row["note"] = "计划已编制。经责任经理批准后方可实施（步骤 3）。"
    return row


def approve_plan(conn: psycopg.Connection, *, plan_id: int, actor: dict) -> dict:
    """批准计划。步骤 3：年度审核计划经责任经理批准后实施。"""
    _require_position(conn, actor, "AM")
    n = execute(conn, """UPDATE das_audit_plan SET approved_by=%s, approved_at=now()
                          WHERE id=%s AND approved_at IS NULL""", (actor["user_id"], plan_id))
    if not n:
        raise ValueError("计划不存在或已批准")
    audit.write(conn, action="DAS_AUDIT_PLAN_APPROVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_AUDIT_PLAN",
                object_id=str(plan_id), new_value={"approved": True},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT id, kind, approved_at FROM das_audit_plan WHERE id=%s",
                     (plan_id,))


def add_plan_item(conn: psycopg.Connection, *, plan_id: int, seq: int, scope_kind: str,
                  scope_ref: str, scope_owner: str | None, planned_from: dt.date | None,
                  planned_to: dt.date | None, actor: dict) -> dict:
    _require_position(conn, actor, "ISM")
    if scope_kind not in SCOPE_KINDS:
        raise ValueError("范围类别须取自：" + "、".join(f"{k} {v}" for k, v in SCOPE_KINDS.items()))
    if not (scope_ref or "").strip():
        raise ValueError("范围标识不得为空")
    if scalar(conn, "SELECT count(*) FROM das_audit_plan WHERE id=%s", (plan_id,)) == 0:
        raise LookupError("计划不存在")
    return fetch_one(conn, """
        INSERT INTO das_audit_plan_item (plan_id, seq, scope_kind, scope_ref,
                                         scope_owner, planned_from, planned_to)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, plan_id, seq, scope_ref
    """, (plan_id, seq, scope_kind, scope_ref.strip(), scope_owner, planned_from, planned_to))


# ---------------------------------------------------------------- 专项审核（步骤 2）

def triggers_due(conn: psycopg.Connection) -> list[dict]:
    """触发了专项审核却没启动的。第 7 章：为不符合项。"""
    return fetch_all(conn, "SELECT * FROM das_special_audit_due")


def record_trigger(conn: psycopg.Connection, *, trigger_kind: str, occurred_on: dt.date,
                   description: str, actor: dict) -> dict:
    """登记专项审核的触发事件。

    为什么要单独登记触发事件: 第 7 章把"触发情形发生而未启动"定为不符合项。
    只在计划里排审核是查不出这件事的 —— 没排的那次本来就不在计划里。
    """
    _require_position(conn, actor, "ISM")
    if trigger_kind not in TRIGGERS:
        raise ValueError("触发情形须取自 UG-DAP-13 步骤 2 的五种："
                         + "、".join(f"{k} {v}" for k, v in TRIGGERS.items()))
    if not (description or "").strip():
        raise ValueError("触发事件须写明情况")
    if occurred_on > dt.date.today():
        raise ValueError("发生日期不得晚于今天")
    row = fetch_one(conn, """
        INSERT INTO das_special_audit_trigger (trigger_kind, occurred_on, description,
                                               recorded_by)
        VALUES (%s,%s,%s,%s) RETURNING id, trigger_kind, occurred_on
    """, (trigger_kind, occurred_on, description.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_SPECIAL_AUDIT_TRIGGER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_SPECIAL_AUDIT_TRIGGER",
                object_id=str(row["id"]), new_value={"kind": trigger_kind},
                reason=description, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    row["note"] = f"已登记。{TRIGGERS[trigger_kind]}属应启动专项审核的情形, 未启动的为不符合项。"
    return row


# ---------------------------------------------------------------- 审核（步骤 5～7）

def register_list(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_audit_register")


def detail(conn: psycopg.Connection, audit_id: int) -> dict:
    row = fetch_one(conn, "SELECT * FROM das_audit_register WHERE id=%s", (audit_id,))
    if row is None:
        raise LookupError("审核记录不存在")
    row["auditors"] = fetch_all(conn, """
        SELECT r.* FROM das_audit_auditor x
          JOIN das_auditor_roster r ON r.id = x.auditor_id
         WHERE x.audit_id=%s""", (audit_id,))
    # 不叫 findings: das_audit_register 里 findings 是**条数**, 在这里用列表盖掉它,
    # 调用方拿到的是数字还是列表就要看运气。计数和明细并存, 各用各的名字。
    row["findings_detail"] = fetch_all(conn, """
        SELECT f.*, u.full_name AS recorded_by_name, n.ncr_no
          FROM das_audit_finding f JOIN app_user u ON u.id = f.recorded_by
          LEFT JOIN das_ncr n ON n.id = f.ncr_id
         WHERE f.audit_id=%s ORDER BY f.clause_ref, f.id""", (audit_id,))
    row["checklist_coverage"] = fetch_all(conn, """
        SELECT i.seq, i.req_code, i.req_name FROM das_audit_checklist_coverage c
          JOIN das_checklist_item i ON i.id = c.checklist_item_id
         WHERE c.audit_id=%s ORDER BY i.seq""", (audit_id,))
    return row


def conduct(conn: psycopg.Connection, *, kind: str, plan_item_id: int | None,
            trigger_id: int | None, scope_ref: str, scope_owner: str | None,
            criteria_ccar21: bool, criteria_ap2118_d: bool, criteria_checklist: bool,
            criteria_manual: bool, criteria_other: str | None, lead_auditor: int,
            conducted_from: dt.date, conducted_to: dt.date, audit_ref: str | None,
            actor: dict) -> dict:
    """登记一次审核的实施（步骤 5、6）。

    准则四项分列, 不是一段自由文本。第 7 章把"仅覆盖 ISO9001/AS9100 而未覆盖
    CCAR-21 适用要求"直接定为审核依据不全 —— 写成自由文本就判不出来了。
    """
    _require_position(conn, actor, "ISM")
    if kind not in KINDS:
        raise ValueError("审核类别须为：" + "、".join(f"{k} {v}" for k, v in KINDS.items()))
    if plan_item_id is None and trigger_id is None:
        raise ValueError(
            "审核须挂在计划条目或专项审核触发事件上。两者都不挂, 这次审核既不在"
            "计划内也没有触发依据, 覆盖率和计划执行率都统计不进去。")
    if not (scope_ref or "").strip():
        raise ValueError("审核范围不得为空")
    if conducted_to < conducted_from:
        raise ValueError("审核结束日不得早于开始日")
    if conducted_from > dt.date.today():
        raise ValueError("审核实施日期不得晚于今天: 记录的是已发生的事")

    row = fetch_one(conn, """
        INSERT INTO das_audit (audit_ref, kind, plan_item_id, trigger_id, scope_ref,
                               scope_owner, criteria_ccar21, criteria_ap2118_d,
                               criteria_checklist, criteria_manual, criteria_other,
                               lead_auditor, conducted_from, conducted_to, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, audit_ref, kind, conducted_from, conducted_to
    """, (audit_ref, kind, plan_item_id, trigger_id, scope_ref.strip(), scope_owner,
          criteria_ccar21, criteria_ap2118_d, criteria_checklist, criteria_manual,
          criteria_other, lead_auditor, conducted_from, conducted_to, actor["user_id"]))
    audit.write(conn, action="DAS_AUDIT_CONDUCT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_AUDIT",
                object_id=str(row["id"]), object_code=audit_ref,
                new_value={"kind": kind, "scope": scope_ref,
                           "criteria": {"ccar21": criteria_ccar21,
                                        "checklist": criteria_checklist}},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def assign_auditor(conn: psycopg.Connection, *, audit_id: int, auditor_id: int,
                   actor: dict) -> dict:
    """把审核员加入某次审核。"审核员不得审核其本人负责的活动"由触发器判。"""
    _require_position(conn, actor, "ISM")
    if scalar(conn, "SELECT count(*) FROM das_audit WHERE id=%s", (audit_id,)) == 0:
        raise LookupError("审核记录不存在")
    a = fetch_one(conn, "SELECT * FROM das_auditor_roster WHERE id=%s", (auditor_id,))
    if a is None:
        raise LookupError("审核员资格记录不存在")
    if not a["in_force"]:
        raise ValueError(f'{a["auditor_name"]} 的审核员资格当前无效, 不得参加审核'
                         "（UG-DAP-13 步骤 4）")
    return fetch_one(conn, """
        INSERT INTO das_audit_auditor (audit_id, auditor_id) VALUES (%s,%s)
        ON CONFLICT DO NOTHING RETURNING audit_id, auditor_id
    """, (audit_id, auditor_id)) or {"audit_id": audit_id, "auditor_id": auditor_id}


def record_finding(conn: psycopg.Connection, *, audit_id: int, clause_ref: str,
                   verdict: str, fact: str, evidence: str,
                   confirmed_with_auditee: bool, auditee_rep: str | None,
                   ncr_id: int | None, actor: dict) -> dict:
    """记录一条审核发现（步骤 6）。

    verdict 有 CONFORM, 不是只记不符合: 步骤 6 要求"如实记载, 包括符合项和不符合项"。
    只记不符合的审核记录, 看不出审了哪些条款、哪些审过而没问题, 覆盖率也无从统计。
    """
    _require_position(conn, actor, "ISM")
    if verdict not in VERDICTS:
        raise ValueError("结论须取自：" + "、".join(f"{k} {v}" for k, v in VERDICTS.items()))
    for name, val in (("条款/检查项", clause_ref), ("事实", fact), ("证据", evidence)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    if verdict == "NONCONFORM":
        if not confirmed_with_auditee or not (auditee_rep or "").strip():
            raise ValueError(
                "不符合发现须当场与受审部门确认事实和证据, 并写明确认人"
                "（UG-DAP-13 步骤 6）。事后补确认的, 受审部门已无从核对当时的证据。")
    if scalar(conn, "SELECT count(*) FROM das_audit WHERE id=%s", (audit_id,)) == 0:
        raise LookupError("审核记录不存在")
    return fetch_one(conn, """
        INSERT INTO das_audit_finding (audit_id, clause_ref, verdict, fact, evidence,
                                       confirmed_with_auditee, auditee_rep, ncr_id,
                                       recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, audit_id, clause_ref, verdict
    """, (audit_id, clause_ref.strip(), verdict, fact.strip(), evidence.strip(),
          confirmed_with_auditee, auditee_rep, ncr_id, actor["user_id"]))


def cover_checklist_items(conn: psycopg.Connection, *, audit_id: int,
                          checklist_item_ids: list[int], actor: dict) -> dict:
    """登记本次独立监督覆盖了检查单的哪些条目（步骤 1a、9）。"""
    _require_position(conn, actor, "ISM")
    a = fetch_one(conn, "SELECT id, kind FROM das_audit WHERE id=%s", (audit_id,))
    if a is None:
        raise LookupError("审核记录不存在")
    if a["kind"] != "DAS_SUPERVISION":
        raise ValueError(
            "符合性检查单的覆盖只属于设计保证系统独立监督。质量系统内部审核覆盖的是"
            "部门与过程, 两类活动的覆盖对象不同, 不得互相充当（判据 M2-1）。")
    if not checklist_item_ids:
        raise ValueError("须至少指定一条检查单条目")
    n = 0
    for item_id in checklist_item_ids:
        n += execute(conn, """
            INSERT INTO das_audit_checklist_coverage (audit_id, checklist_item_id)
            VALUES (%s,%s) ON CONFLICT DO NOTHING""", (audit_id, item_id))
    return {"audit_id": audit_id, "added": n,
            "coverage": fetch_one(conn, "SELECT * FROM das_audit_checklist_coverage_stat")}


def issue_report(conn: psycopg.Connection, *, audit_id: int, report_ref: str,
                 conclusion: str, issued_to_am: str, issued_to_action_owner: str | None,
                 actor: dict) -> dict:
    """出具审核报告（步骤 7）。直接报送责任经理，不经被审核部门转交。"""
    _require_position(conn, actor, "ISM")
    for name, val in (("报告编号", report_ref), ("结论", conclusion)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    if scalar(conn, "SELECT count(*) FROM das_audit WHERE id=%s", (audit_id,)) == 0:
        raise LookupError("审核记录不存在")
    if scalar(conn, "SELECT count(*) FROM das_audit_report WHERE audit_id=%s", (audit_id,)):
        raise ValueError("该次审核已出具报告。报告不可改写, 更正请另出一份并说明。")
    row = fetch_one(conn, """
        INSERT INTO das_audit_report (audit_id, report_ref, conclusion, issued_to_am,
                                      issued_to_action_owner, issued_by)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id, audit_id, report_ref, issued_to_am_at
    """, (audit_id, report_ref.strip(), conclusion.strip(), issued_to_am,
          issued_to_action_owner, actor["user_id"]))
    audit.write(conn, action="DAS_AUDIT_REPORT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_AUDIT",
                object_id=str(audit_id), object_code=report_ref,
                new_value={"report_ref": report_ref}, reason=conclusion,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


# ---------------------------------------------------------------- 覆盖率（步骤 9）

def checklist_coverage(conn: psycopg.Connection) -> dict:
    return {
        "stat": fetch_one(conn, "SELECT * FROM das_audit_checklist_coverage_stat"),
        "uncovered": fetch_all(conn, "SELECT * FROM das_audit_checklist_uncovered"),
    }


# ---------------------------------------------------------------- 步骤 10

def ism_function_audit_due(conn: psycopg.Connection) -> dict:
    return fetch_one(conn, "SELECT * FROM das_ism_function_audit_due")


def ism_function_audits(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """
        SELECT f.*, o.full_name AS organised_by_name,
               COALESCE(a.full_name, f.external_name) AS auditor_name
          FROM das_ism_function_audit f
          JOIN app_user o ON o.id = f.organised_by
          LEFT JOIN app_user a ON a.id = f.auditor_user_id
         ORDER BY f.period_to DESC""")


def record_ism_function_audit(conn: psycopg.Connection, *, report_ref: str,
                              period_from: dt.date, period_to: dt.date,
                              auditor_user_id: str | None, external_name: str | None,
                              external_org: str | None, is_external: bool,
                              designation_basis: str | None, chaired_by_am: bool,
                              auditor_capability_note: str | None,
                              finding_plan_completeness: str, finding_execution_rate: str,
                              finding_adequacy: str, finding_car_closure: str,
                              finding_auditor_qual: str, finding_independence: str,
                              actor: dict) -> dict:
    """对独立监督职能自身的审核（步骤 10）。由责任经理组织。

    这是整份程序里最容易被做成形式的一条。第 7 章: "由该职能自行出具的自查结论
    不作为符合性证据"。所以:
      · 组织人须是在任责任经理（触发器 DCMS-INV-060）;
      · 实施人不得持独立监督负责人任命（同上）;
      · 不是外部审核员时, 须有责任经理的指定依据、能力说明, 且由责任经理亲自主持;
      · 六项内容逐项留结论 —— 做成一段自由文本, "审了没审"就看不出来。
    """
    _require_position(conn, actor, "AM")
    if not (report_ref or "").strip():
        raise ValueError("报告编号不得为空")
    if period_to <= period_from:
        raise ValueError("周期结束日须晚于起始日")
    if is_external:
        if not (external_name or "").strip() or not (external_org or "").strip():
            raise ValueError("外部审核员须写明姓名与所属单位, 并保存其资格证明")
    else:
        if not auditor_user_id:
            raise ValueError("非外部审核员时须指定本单位人员")
        if not (designation_basis or "").strip() or not (auditor_capability_note or "").strip():
            raise ValueError(
                "确无外部资源时, 须由责任经理指定一名未参与独立监督活动、且具备审核"
                "能力的人员, 并记录指定依据与能力说明（UG-DAP-13 步骤 10）。")
        if not chaired_by_am:
            raise ValueError(
                "由本单位指定人员实施时, 须由责任经理亲自主持（UG-DAP-13 步骤 10）。"
                "不主持就把这次审核交回给了被审对象所在的管理链条。")
    fields = {
        "监督计划的完整性和覆盖率": finding_plan_completeness,
        "计划的执行率": finding_execution_rate,
        "发现问题的充分性": finding_adequacy,
        "NCR／CAR 的跟踪闭环质量": finding_car_closure,
        "审核员资格的保持": finding_auditor_qual,
        "独立性的实际保持情况": finding_independence,
    }
    missing = [k for k, v in fields.items() if not (v or "").strip()]
    if missing:
        raise ValueError("步骤 10 的六项内容须逐项留结论, 缺：" + "、".join(missing))

    row = fetch_one(conn, """
        INSERT INTO das_ism_function_audit
               (report_ref, period_from, period_to, organised_by, auditor_user_id,
                external_name, external_org, is_external, designation_basis,
                chaired_by_am, auditor_capability_note,
                finding_plan_completeness, finding_execution_rate, finding_adequacy,
                finding_car_closure, finding_auditor_qual, finding_independence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, report_ref, period_from, period_to, is_external
    """, (report_ref.strip(), period_from, period_to, actor["user_id"], auditor_user_id,
          external_name, external_org, is_external, designation_basis, chaired_by_am,
          auditor_capability_note, finding_plan_completeness.strip(),
          finding_execution_rate.strip(), finding_adequacy.strip(),
          finding_car_closure.strip(), finding_auditor_qual.strip(),
          finding_independence.strip()))
    audit.write(conn, action="DAS_ISM_FUNCTION_AUDIT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_ISM_FUNCTION_AUDIT",
                object_id=str(row["id"]), object_code=report_ref,
                new_value={"is_external": is_external,
                           "period": f"{period_from}~{period_to}"},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row
