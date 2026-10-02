"""M3 项目实体与三条审定路径（第二批）。

依据 UG-DAP-09（9 步）、UG-DAP-04（11 步）、UG-DAP-07（13 步）、UG-DAW-002 第 5 章，
设计输入第 8.2／8.3 节，判据 M3-1。

【判据 M3-1：按类型参数化，不写死状态机】
三条路径不是两条。步骤连同"适用于哪几类项目"一起存在 `das_project_step` 里，
服务层不认识任何具体步骤编号——它只问数据库"这一步适用于这类项目吗"。把分支写进代码
的后果是第三类迟早会沿着 STC 的分支走到"签符合性声明"，而那一签就是对委托方的产品
作了设计批准：这是越权，不是流程瑕疵。

【两处岗位门槛来自不同文件】
- 立项（建项目记录并分配编号）：**资料管理负责人**。UG-DAW-002 第 5 章原文：
  项目编号由资料管理负责人在立项时分配并登记。
- 批准立项：**责任经理**。UG-DAP-09 第 1 步原文：评估资源，责任经理批准立项。
两个人、两份依据，所以是两道独立的门槛而不是一道。

【符合性声明的签署人检查到哪为止】
UG-DAP-07 第 11 步允许"责任经理**或其授权人员**"签署。签署人不是在任责任经理时，
系统只要求写明纸面授权依据，**不冒充校验该授权覆盖了符合性声明这件事**——授权的
第三维校验的是 DCMS 文档种类，不是 UG-DAM-01-附2 的 5 类适航签署事项
（判据 I10.FILE_TYPE 语义不符，待澄清项第 1 条未关闭）。由授权人员签署的声明进
`das_compliance_statement_delegated`，交独立监督按 UG-DAP-13 核对。

【这里没有"产品范围"的结构化字段】
判据 I10.PRODUCT_SCOPE 要的"具体产品／型号／件号范围"须由项目与件号的结构化关联表达，
那是下一片工作。所以独立性登记册里 I10.PRODUCT_SCOPE 仍如实记为未实现——
不因为建了项目表就改成"已实现"。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

HANDOVER_CN = {
    "AIRWORTHINESS": "移交持续适航（UG-DAP-11）",
    "PRODUCTION": "生产协调（UG-DAP-10）",
    "ARCHIVE": "归档（UG-DAP-01）",
}
POSITION_CN = {"DCM": "设计资料管理负责人", "AM": "责任经理",
               "AWM": "适航管理负责人", "PM": "项目负责人"}


def _holds(conn: psycopg.Connection, user_id, code: str) -> bool:
    return scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code=%s""",
                  (user_id, code)) > 0


def _require(conn: psycopg.Connection, actor: dict, code: str, what: str,
             basis: str) -> None:
    if not _holds(conn, actor["user_id"], code):
        raise PermissionError(
            f"{what}须由在任{POSITION_CN[code]}执行（{basis}）。"
            f"请先按 UG-DAP-03 完成任命。")


def types(conn: psycopg.Connection) -> list[dict]:
    """三条审定路径（设计输入第 8.2 节）。"""
    return fetch_all(conn, "SELECT * FROM das_project_type ORDER BY code")


def steps(conn: psycopg.Connection, type_code: str | None = None) -> list[dict]:
    """33 个步骤。给定类型时只返回适用于该类型的。"""
    if type_code:
        return fetch_all(conn, """SELECT * FROM das_project_step
                                   WHERE %s = ANY (applies_to)
                                   ORDER BY procedure_ref, step_no""", (type_code,))
    return fetch_all(conn, """SELECT * FROM das_project_step
                               ORDER BY procedure_ref, step_no""")


def step_coverage(conn: psycopg.Connection) -> dict:
    """判据 M3-1 的证据。

    `exclusive` 比 `counts` 要紧: STC 与 PMA 都是 30 步, 条数一样 ——
    只有按**集合**比才看得出 STC 独有第 4 步、PMA 独有第 3 步。
    拿条数去证明参数化起作用了, 这两类会显示为完全一样。
    """
    return {
        "counts": fetch_all(conn, "SELECT * FROM das_project_step_coverage"),
        "exclusive": fetch_all(conn, "SELECT * FROM das_project_type_exclusive"),
        "unassigned": fetch_all(conn, "SELECT * FROM das_project_step_unassigned"),
        "inferred": fetch_all(conn, "SELECT * FROM das_project_step_inferred"),
    }


def projects(conn: psycopg.Connection, *, type_code: str | None = None,
             open_only: bool = False) -> list[dict]:
    sql = """
        SELECT p.*, t.name_cn AS type_name, t.yields_design_approval,
               (SELECT count(*) FROM das_project_step s
                 WHERE p.type_code = ANY (s.applies_to))            AS steps_total,
               (SELECT count(*) FROM das_project_step_done d
                 WHERE d.project_id = p.id)                         AS steps_done,
               (SELECT count(*) FROM das_project_handover h
                 WHERE h.project_id = p.id)                         AS handed_over,
               (SELECT a.certificate_no FROM das_design_approval a
                 WHERE a.project_id = p.id ORDER BY a.issued_on DESC LIMIT 1)
                                                                    AS certificate_no
          FROM das_project p JOIN das_project_type t ON t.code = p.type_code
         WHERE (%s::text IS NULL OR p.type_code = %s)
           AND (NOT %s OR p.closed_on IS NULL)
         ORDER BY p.project_no
    """
    return fetch_all(conn, sql, (type_code, type_code, open_only))


def project(conn: psycopg.Connection, project_no: str) -> dict:
    row = fetch_one(conn, """SELECT p.*, t.name_cn AS type_name, t.path_note,
                                    t.yields_design_approval
                               FROM das_project p
                               JOIN das_project_type t ON t.code = p.type_code
                              WHERE p.project_no = %s""", (project_no,))
    if not row:
        raise LookupError(f"没有编号为 {project_no} 的项目")
    row["progress"] = fetch_all(conn, """SELECT * FROM das_project_progress
                                          WHERE project_id = %s""", (row["id"],))
    row["statements"] = fetch_all(conn, """SELECT * FROM das_compliance_statement
                                            WHERE project_id = %s
                                            ORDER BY signed_on""", (row["id"],))
    row["approvals"] = fetch_all(conn, """SELECT * FROM das_design_approval
                                           WHERE project_id = %s
                                           ORDER BY issued_on""", (row["id"],))
    row["handovers"] = fetch_all(conn, """SELECT * FROM das_project_handover
                                           WHERE project_id = %s""", (row["id"],))
    row["pma_quality"] = fetch_one(conn, """SELECT * FROM das_pma_quality_confirm
                                             WHERE project_id = %s""", (row["id"],))
    return row


def _by_no(conn: psycopg.Connection, project_no: str) -> dict:
    row = fetch_one(conn, """SELECT p.id, p.project_no, p.type_code, p.approved_on,
                                    p.closed_on, t.name_cn AS type_name
                               FROM das_project p
                               JOIN das_project_type t ON t.code = p.type_code
                              WHERE p.project_no = %s""", (project_no,))
    if not row:
        raise LookupError(f"没有编号为 {project_no} 的项目")
    return row


def create(conn: psycopg.Connection, *, project_no: str, type_code: str, name_cn: str,
           aircraft_type: str | None = None, product_desc: str | None = None,
           caac_project_no: str | None = None, certification_basis: str | None = None,
           initiated_on: dt.date | None = None, actor: dict) -> dict:
    """立项：建项目记录并登记编号（须在任资料管理负责人）。

    编号格式与前缀由 DCMS-INV-091 按 UG-DAW-002 第 5 章校验 —— 前缀是类型的一部分,
    前缀对不上的项目在台账里会被归错类。
    """
    _require(conn, actor, "DCM", "立项并分配项目编号",
             "UG-DAW-002 第 5 章：项目编号由资料管理负责人在立项时分配并登记")
    if not scalar(conn, "SELECT count(*) FROM das_project_type WHERE code=%s",
                  (type_code,)):
        raise LookupError(f"没有 {type_code} 这个项目类型")
    if not (name_cn or "").strip():
        raise ValueError("项目名称不得为空")
    row = fetch_one(conn, """
        INSERT INTO das_project (project_no, type_code, name_cn, aircraft_type,
                                 product_desc, caac_project_no, certification_basis,
                                 initiated_on, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, project_no, type_code, name_cn, initiated_on
    """, (project_no.strip(), type_code, name_cn.strip(),
          (aircraft_type or "").strip() or None, (product_desc or "").strip() or None,
          (caac_project_no or "").strip() or None,
          (certification_basis or "").strip() or None,
          initiated_on or dt.date.today(), actor["user_id"]))
    audit.write(conn, action="DAS_PROJECT_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_PROJECT",
                object_id=str(row["id"]), object_code=row["project_no"],
                new_value={"type": type_code, "name": name_cn,
                           "caac_project_no": caac_project_no},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def approve(conn: psycopg.Connection, *, project_no: str, approval_ref: str,
            approved_on: dt.date | None = None, actor: dict) -> dict:
    """批准立项（须在任责任经理，UG-DAP-09 第 1 步）。"""
    _require(conn, actor, "AM", "批准立项",
             "UG-DAP-09 第 1 步：评估资源，责任经理批准立项")
    p = _by_no(conn, project_no)
    if p["approved_on"]:
        raise ValueError(f"项目 {project_no} 已于 {p['approved_on']} 批准立项，不重复批准")
    if not (approval_ref or "").strip():
        raise ValueError("批准依据（立项单编号）不得为空")
    execute(conn, """UPDATE das_project SET approved_by=%s, approved_on=%s,
                            approval_ref=%s WHERE id=%s""",
            (actor["user_id"], approved_on or dt.date.today(), approval_ref.strip(),
             p["id"]))
    audit.write(conn, action="DAS_PROJECT_APPROVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_PROJECT",
                object_id=str(p["id"]), object_code=project_no,
                new_value={"approval_ref": approval_ref.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, """SELECT project_no, approved_on, approval_ref
                                FROM das_project WHERE id=%s""", (p["id"],))


def complete_step(conn: psycopg.Connection, *, project_no: str, step_code: str,
                  record_ref: str, completed_on: dt.date | None = None,
                  note: str | None = None, actor: dict) -> dict:
    """登记一个步骤完成。

    **服务层不认识任何具体步骤编号**（判据 M3-1）: 适用性、前置步骤、实质证据表
    全部从 das_project_step 读。下面这段话是把数据库的判断翻成人话,
    不是另写一份判断 —— 两处各判一遍迟早会不一致。
    """
    _require(conn, actor, "PM", "登记项目步骤完成",
             "UG-DAP-04／07／09 第 6 章：步骤责任人以项目负责人为主")
    p = _by_no(conn, project_no)
    step = fetch_one(conn, """SELECT code, name_cn, applies_to, requires_step,
                                     evidence_table, procedure_ref, step_no
                                FROM das_project_step WHERE code=%s""", (step_code,))
    if not step:
        raise LookupError(f"没有编号为 {step_code} 的步骤")
    if not step["applies_to"]:
        raise ValueError(
            f"步骤「{step['name_cn']}」不属于任何产品项目——它是首次申请设计机构许可证"
            f"本身，延续与维持归 M10（UG-DAP-15）。登记在产品项目下，"
            f"这个步骤对每个项目都完不成。")
    if p["type_code"] not in step["applies_to"]:
        applies = "、".join(step["applies_to"])
        raise ValueError(
            f"步骤「{step['name_cn']}」不适用于{p['type_name']}（适用：{applies}）。"
            f"判据 M3-1：状态机按项目类型参数化，第三类不得复用前两类的分支——"
            f"沿着别的类型的分支走下去，迟早会走到「签符合性声明」，"
            f"而那一签就是对委托方的产品作了设计批准。")
    if not (record_ref or "").strip():
        raise ValueError("对应记录或表单编号不得为空：没有记录编号的「已完成」查不到证据")
    if scalar(conn, """SELECT count(*) FROM das_project_step_done
                        WHERE project_id=%s AND step_code=%s""", (p["id"], step_code)):
        raise ValueError(f"步骤「{step['name_cn']}」已登记完成，不重复登记")
    row = fetch_one(conn, """
        INSERT INTO das_project_step_done
               (project_id, step_code, completed_on, record_ref, note, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, step_code, completed_on, record_ref
    """, (p["id"], step_code, completed_on or dt.date.today(), record_ref.strip(),
          note, actor["user_id"]))
    audit.write(conn, action="DAS_PROJECT_STEP_DONE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_PROJECT_STEP",
                object_id=str(row["id"]), object_code=f"{project_no}/{step_code}",
                new_value={"record_ref": record_ref.strip(),
                           "completed_on": str(row["completed_on"])},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def confirm_pma_quality(conn: psycopg.Connection, *, project_no: str, manual_ref: str,
                        established: bool, covers_project: bool,
                        materials_submitted: bool, interface_ref: str,
                        confirmed_on: dt.date | None = None, note: str | None = None,
                        actor: dict) -> dict:
    """PMA 生产质量系统确认（UG-DAP-09 第 3 步，仅 PMA，须在任适航管理负责人）。

    四项有一项为否就不是"确认具备"（DCMS-INV-093）。把"不具备"写成"已确认"的下一步
    就是提交 PMA 申请, 而 CCAR-21.303 要求申请人表明具有符合 21.137 的质量系统。
    """
    _require(conn, actor, "AWM", "确认 PMA 生产质量系统",
             "UG-DAP-09 第 3 步：适航管理负责人、生产质量系统负责人")
    p = _by_no(conn, project_no)
    if p["type_code"] != "PMA":
        raise ValueError(
            f"生产质量系统确认只适用于 PMA 项目（UG-DAP-09 第 3 步：仅 PMA 项目），"
            f"{project_no} 是{p['type_name']}。")
    for name, val in (("质量手册编号与版次", manual_ref),
                      ("设计与生产接口依据", interface_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空：确认记录要在局方面前拿得出来")
    if not (established and covers_project and materials_submitted):
        no = [n for n, v in (("已建立并处于受控状态", established),
                             ("覆盖本项目的零部件类别", covers_project),
                             ("质量手册及相关资料已一并提交", materials_submitted))
              if not v]
        raise ValueError(
            f"「{'』『'.join(no)}」为否，不算确认具备（UG-DAP-09 第 3 步）。"
            f"有一项为否就登记成确认记录，等于把「不具备」写成了「已确认」——"
            f"而下一步就是提交 PMA 申请。")
    row = fetch_one(conn, """
        INSERT INTO das_pma_quality_confirm
               (project_id, manual_ref, established, covers_project,
                materials_submitted, interface_ref, confirmed_by, confirmed_on, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING project_id, manual_ref, confirmed_on
    """, (p["id"], manual_ref.strip(), established, covers_project, materials_submitted,
          interface_ref.strip(), actor["user_id"], confirmed_on or dt.date.today(), note))
    audit.write(conn, action="DAS_PMA_QUALITY_CONFIRM", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_PROJECT",
                object_id=str(p["id"]), object_code=project_no,
                new_value={"manual_ref": manual_ref.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def sign_statement(conn: psycopg.Connection, *, project_no: str, statement_no: str,
                   completion_confirm_ref: str, verification_docs_ref: str,
                   signed_under_authority_ref: str | None = None,
                   signed_on: dt.date | None = None, note: str | None = None,
                   actor: dict) -> dict:
    """签署符合性声明（UG-DAP-07 第 11 步，表-21-160／UG-DAF-03）。

    第三类项目签不了（DCMS-INV-094）—— 签了就是对委托方的产品作了设计批准。
    签署人不是在任责任经理时须写明纸面授权依据; 系统**不冒充校验**该授权覆盖了
    符合性声明这件事（判据 I10.FILE_TYPE 语义不符, 待澄清项第 1 条）。
    """
    p = _by_no(conn, project_no)
    signs = scalar(conn, """SELECT signs_compliance_statement FROM das_project_type
                             WHERE code=%s""", (p["type_code"],))
    if not signs:
        raise ValueError(
            f"{p['type_name']}不得签符合性声明。UG-DAP-07 第 12 步 d)："
            f"本单位不对委托方的产品作设计批准，只提供经核查的符合性验证资料"
            f"（AP-21-18 7.1(5)）。签了就是越权作了设计批准，不是流程瑕疵。")
    is_am = _holds(conn, actor["user_id"], "AM")
    if not is_am and not (signed_under_authority_ref or "").strip():
        raise PermissionError(
            "签署人不是在任责任经理时，须写明纸面授权依据"
            "（UG-DAP-07 第 11 步：由责任经理**或其授权人员**签署）。"
            "系统此刻判不了该授权是否覆盖符合性声明这件事——授权的第三维校验的是"
            "DCMS 文档种类，不是 UG-DAM-01-附2 的 5 类适航签署事项"
            "（待澄清项第 1 条）。所以这里要的是说得出依据，由独立监督核对。")
    for name, val in (("完成确认书编号", completion_confirm_ref),
                      ("所引用验证文件及版次", verification_docs_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（UG-DAP-07 第 11 步：核对声明与所引用"
                             f"验证文件的版次一致）")
    if "-SM-" not in (statement_no or ""):
        raise ValueError(
            "声明编号不合 UG-DAW-002 的规则：设计机构许可证编号-SM-年份-流水号。"
            "许可证编号部分**不校验**——本单位的 DOA 尚在申请中，编号还不存在，"
            "按一个不存在的编号去校验只会逼人编一个填进去。")
    row = fetch_one(conn, """
        INSERT INTO das_compliance_statement
               (project_id, statement_no, signed_by, signed_on, completion_confirm_ref,
                verification_docs_ref, signed_under_authority_ref, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, statement_no, signed_on
    """, (p["id"], statement_no.strip(), actor["user_id"], signed_on or dt.date.today(),
          completion_confirm_ref.strip(), verification_docs_ref.strip(),
          (signed_under_authority_ref or "").strip() or None, note))
    audit.write(conn, action="DAS_COMPLIANCE_STATEMENT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_COMPLIANCE_STATEMENT",
                object_id=str(row["id"]), object_code=statement_no,
                new_value={"project": project_no, "as_am": is_am,
                           "authority_ref": signed_under_authority_ref},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def record_approval(conn: psycopg.Connection, *, project_no: str, certificate_no: str,
                    issued_on: dt.date, product_scope_ref: str,
                    renew_due_on: dt.date | None = None, note: str | None = None,
                    actor: dict) -> dict:
    """登记设计批准（设计输入第 8.3 节）。

    类别由项目类型决定, 不由调用方给 —— 让调用方自己填, 填成别的类别就会出现
    一个 PMA 项目挂着 STC 证件的记录。
    """
    _require(conn, actor, "AWM", "登记设计批准",
             "UG-DAP-09 第 8 步：取证准备与提交由适航管理负责人归口")
    p = _by_no(conn, project_no)
    yields = scalar(conn, """SELECT yields_design_approval FROM das_project_type
                              WHERE code=%s""", (p["type_code"],))
    if not yields:
        raise ValueError(
            f"{p['type_name']}不产出设计批准。UG-DAP-07 第 12 步 d)："
            f"本单位不对委托方的产品作设计批准。证件属于委托方，不属于本单位。")
    if not scalar(conn, """SELECT count(*) FROM das_compliance_statement
                            WHERE project_id=%s""", (p["id"],)):
        raise ValueError(
            "本项目还没有已签署的符合性声明，不得登记设计批准。"
            "UG-DAP-09 第 8 步：取证准备与提交要汇总符合性文件、声明、构型状态。"
            "没有声明的设计批准，取证顺序是倒的。")
    if p["type_code"] == "PMA" and renew_due_on is None:
        raise ValueError(
            "PMA 项目单每 2 年延续（设计输入第 8.3 节，延续归 M10），"
            "须写明延续到期日。不写就等于把一个两年后失效的东西记成长期有效。")
    if not (product_scope_ref or "").strip():
        raise ValueError("批准覆盖的产品／型号／件号范围不得为空（纸面依据）")
    row = fetch_one(conn, """
        INSERT INTO das_design_approval
               (project_id, approval_kind, certificate_no, issued_on, renew_due_on,
                product_scope_ref, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, certificate_no, approval_kind, issued_on, renew_due_on,
                  statement_id
    """, (p["id"], p["type_code"], certificate_no.strip(), issued_on, renew_due_on,
          product_scope_ref.strip(), note))
    audit.write(conn, action="DAS_DESIGN_APPROVAL", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DESIGN_APPROVAL",
                object_id=str(row["id"]), object_code=certificate_no,
                new_value={"project": project_no, "kind": p["type_code"],
                           "renew_due_on": str(renew_due_on) if renew_due_on else None},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def record_handover(conn: psycopg.Connection, *, project_no: str, item_code: str,
                    target_ref: str, received_by: str,
                    handed_on: dt.date | None = None, note: str | None = None,
                    actor: dict) -> dict:
    """登记转段移交的一项（UG-DAP-09 第 9 步）。"""
    _require(conn, actor, "PM", "登记转段移交",
             "UG-DAP-09 第 9 步：批准后转段由项目负责人办理")
    if item_code not in HANDOVER_CN:
        raise ValueError(f"移交项须为 {'/'.join(HANDOVER_CN)} 之一")
    p = _by_no(conn, project_no)
    if not (target_ref or "").strip():
        raise ValueError("移交依据与记录编号不得为空：没有编号的「已移交」查不到承接人")
    if scalar(conn, """SELECT count(*) FROM das_project_handover
                        WHERE project_id=%s AND item_code=%s""", (p["id"], item_code)):
        raise ValueError(f"{HANDOVER_CN[item_code]}已登记移交，不重复登记")
    row = fetch_one(conn, """
        INSERT INTO das_project_handover
               (project_id, item_code, target_ref, received_by, handed_on, note)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, item_code, target_ref, handed_on
    """, (p["id"], item_code, target_ref.strip(), received_by,
          handed_on or dt.date.today(), note))
    audit.write(conn, action="DAS_PROJECT_HANDOVER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_PROJECT",
                object_id=str(p["id"]), object_code=f"{project_no}/{item_code}",
                new_value={"target_ref": target_ref.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def close(conn: psycopg.Connection, *, project_no: str,
          closed_on: dt.date | None = None, actor: dict) -> dict:
    """结项（转段三项齐备，UG-DAP-09 第 9 步）。

    闸门在数据库里（DCMS-INV-097）, 这里先把还差哪几项列出来 ——
    报一句"条件不满足"等于让人去猜。
    """
    _require(conn, actor, "PM", "结项", "UG-DAP-09 第 9 步")
    p = _by_no(conn, project_no)
    if p["closed_on"]:
        raise ValueError(f"项目 {project_no} 已于 {p['closed_on']} 结项")
    done = {r["item_code"] for r in fetch_all(
        conn, "SELECT item_code FROM das_project_handover WHERE project_id=%s",
        (p["id"],))}
    missing = [cn for code, cn in HANDOVER_CN.items() if code not in done]
    if missing:
        raise ValueError(
            f"转段清单还差：{'、'.join(missing)}。"
            f"UG-DAP-09 第 9 步「批准后转段」要移交持续适航、生产协调、归档三项——"
            f"项目做到转段为止，没移交就结项等于证后活动没有承接人。")
    execute(conn, "UPDATE das_project SET closed_on=%s WHERE id=%s",
            (closed_on or dt.date.today(), p["id"]))
    audit.write(conn, action="DAS_PROJECT_CLOSE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_PROJECT",
                object_id=str(p["id"]), object_code=project_no,
                new_value={"closed_on": str(closed_on or dt.date.today())},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT project_no, closed_on FROM das_project WHERE id=%s",
                     (p["id"],))


def oversight(conn: psycopg.Connection) -> dict:
    """交独立监督核对的清单，均不阻断。

    `delegated` 由"授权人员"而非在任责任经理签署的符合性声明 —— UG-DAP-07 第 11 步
    明文允许，但系统判不了该授权是否覆盖这件事（待澄清项第 1 条）。
    `handover_pending` 已取证但转段未齐的项目。
    `renewal` 设计批准的延续到期（PMA 项目单每 2 年，归 M10）。
    """
    return {
        "delegated": fetch_all(conn,
                               "SELECT * FROM das_compliance_statement_delegated"),
        "handover_pending": fetch_all(conn,
                                      "SELECT * FROM das_project_handover_pending"),
        "renewal": fetch_all(conn, "SELECT * FROM das_design_approval_renewal"),
    }
