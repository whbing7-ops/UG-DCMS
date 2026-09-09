"""外部件与软件对象 — SRS-EXT-001~003 / SRS-SW-001/002 / INV-016 017 018。

两类对象的共同点是: **身份与技术状态分离**。

外部件的身份是「哪个来源的哪个件号」(Namespace + External P/N, INV-016), 技术状态是
「供应商在某个时点给出的那一版规格」。供应商改版不改变件号, 因此不能把供应商版本写进
身份 —— 那样每次改版都会变成一个新对象, 历史引用全部断裂。

软件同理: 软件编号是身份, 版本是技术状态(INV-018)。

**技术状态必须显式接受才能进基线**(INV-017)。收到供应商文件不等于认可它 —— 中间要有
一次人为确认。这条由数据库触发器在基线发布时强制, 本模块提供接受的入口与留痕。
"""
from __future__ import annotations

import hashlib

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar


# ---------------------------------------------------------------------
# 外部件
# ---------------------------------------------------------------------
def list_external(conn: psycopg.Connection, namespace_code: str | None = None,
                  q: str | None = None) -> list[dict]:
    return fetch_all(conn, """
        SELECT ep.id, d.object_code, ep.external_part_number, ep.name_cn,
               n.code AS namespace_code, n.name_cn AS namespace_name,
               m.code AS manufacturer_code, ep.lifecycle_status,
               ep.external_class_code, ec.name_cn AS external_class_name,
               (SELECT count(*) FROM external_technical_state ets
                 WHERE ets.external_part_id = ep.id) AS state_count,
               (SELECT max(state_sequence) FROM external_technical_state ets
                 WHERE ets.external_part_id = ep.id AND ets.status = 'ACCEPTED')
                 AS accepted_state
          FROM external_part ep
          JOIN design_object d ON d.id = ep.design_object_id
          JOIN namespace n ON n.id = ep.namespace_id
          LEFT JOIN manufacturer m ON m.id = ep.manufacturer_id
          JOIN external_part_class ec ON ec.code=ep.external_class_code
         WHERE (%s::text IS NULL OR n.code = %s)
           AND (%s::text IS NULL OR ep.external_part_number ILIKE %s
                OR ep.name_cn ILIKE %s)
         ORDER BY n.code, ep.external_part_number
    """, (namespace_code, namespace_code, q,
          f"%{q}%" if q else None, f"%{q}%" if q else None))


def create_external(conn: psycopg.Connection, *, namespace_code: str,
                    external_part_number: str, name_cn: str, name_en: str | None,
                    manufacturer_code: str | None, external_class_code: str,
                    project_code: str | None, project_applicability: str | None,
                    project_evaluation_basis: str | None,
                    actor: dict) -> dict:
    ns = fetch_one(conn, "SELECT id, code FROM namespace WHERE code = %s AND status='ACTIVE'",
                   (namespace_code,))
    if ns is None:
        raise LookupError(f"来源命名空间不存在或已废止: {namespace_code}")
    mf = None
    if manufacturer_code:
        mf = fetch_one(conn, "SELECT id FROM manufacturer WHERE code = %s", (manufacturer_code,))
        if mf is None:
            raise LookupError(f"制造商不存在: {manufacturer_code}")
    cls = fetch_one(conn, "SELECT code FROM external_part_class WHERE code=%s AND status='ACTIVE'",
                    (external_class_code,))
    if cls is None:
        raise LookupError(f"外部件分类不存在或已停用: {external_class_code}")
    if project_code and (not project_applicability or not project_evaluation_basis):
        raise ValueError("创建项目准入记录时，必须同时填写项目适用范围和评价依据")

    # 对象编码取 NAMESPACE::EXT_PN —— 同一件号在不同来源下是不同对象(INV-016)
    object_code = f"{ns['code']}::{external_part_number.strip()}"
    obj = fetch_one(conn, """
        INSERT INTO design_object (object_type, object_code, display_name, created_by, updated_by)
        VALUES ('EXTERNAL_PART', %s, %s, %s, %s) RETURNING id
    """, (object_code, name_cn, actor["user_id"], actor["user_id"]))

    row = fetch_one(conn, """
        INSERT INTO external_part (design_object_id, namespace_id, external_part_number,
                                   manufacturer_id, name_cn, name_en, external_class_code,
                                   created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, external_part_number, name_cn, lifecycle_status
    """, (obj["id"], ns["id"], external_part_number.strip(),
          mf["id"] if mf else None, name_cn, name_en, external_class_code,
          actor["user_id"], actor["user_id"]))

    if project_code:
        execute(conn, """
            INSERT INTO external_part_project_control
              (external_part_id, project_code, applicability, evaluation_basis, created_by)
            VALUES (%s,%s,%s,%s,%s)
        """, (row["id"], project_code.strip(), project_applicability,
              project_evaluation_basis, actor["user_id"]))

    audit.write(conn, action="EXTERNAL_PART_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="EXTERNAL_PART",
                object_id=str(row["id"]), object_code=object_code,
                new_value={"namespace": ns["code"], "external_pn": external_part_number,
                           "name_cn": name_cn, "manufacturer": manufacturer_code,
                           "external_class_code": external_class_code,
                           "project_code": project_code},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    row["object_code"] = object_code
    return row


def get_external(conn: psycopg.Connection, object_code: str) -> dict | None:
    ep = fetch_one(conn, """
        SELECT ep.*, d.object_code, d.lifecycle_status AS object_status,
               n.code AS namespace_code, n.name_cn AS namespace_name,
               m.code AS manufacturer_code, m.name_cn AS manufacturer_name,
               ec.name_cn AS external_class_name, ec.definition AS external_class_definition
          FROM external_part ep
          JOIN design_object d ON d.id = ep.design_object_id
          JOIN namespace n ON n.id = ep.namespace_id
          LEFT JOIN manufacturer m ON m.id = ep.manufacturer_id
          JOIN external_part_class ec ON ec.code=ep.external_class_code
         WHERE d.object_code = %s
    """, (object_code,))
    if ep is None:
        return None
    ep["technical_states"] = technical_states(conn, str(ep["id"]))
    ep["project_controls"] = fetch_all(conn, """
        SELECT c.id, c.project_code, c.status, c.applicability, c.evaluation_basis,
               c.approved_at, u.full_name AS approved_by_name,
               aps.assignee_user_id AS approval_assignee_user_id
          FROM external_part_project_control c
          LEFT JOIN app_user u ON u.id=c.approved_by
          LEFT JOIN approval_step aps ON aps.approval_request_id=c.approval_request_id
            AND aps.decision='PENDING'
         WHERE c.external_part_id=%s ORDER BY project_code
    """, (ep["id"],))
    return ep


def technical_states(conn: psycopg.Connection, external_part_id: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT ets.id, ets.state_sequence, ets.supplier_revision, ets.supplier_document,
               ets.supplier_document_date, ets.hash_sha256, ets.status, ets.notes,
               ets.accepted_at, u.username AS accepted_by_username,
               aps.assignee_user_id AS approval_assignee_user_id,
               (SELECT count(*) FROM baseline_item bi
                 WHERE bi.external_technical_state_id = ets.id) AS baseline_refs
          FROM external_technical_state ets
          LEFT JOIN app_user u ON u.id = ets.accepted_by
          LEFT JOIN approval_step aps ON aps.approval_request_id=ets.approval_request_id
            AND aps.decision='PENDING'
         WHERE ets.external_part_id = %s
         ORDER BY ets.state_sequence DESC
    """, (external_part_id,))


def add_technical_state(conn: psycopg.Connection, object_code: str, *,
                        supplier_revision: str, supplier_document: str | None,
                        supplier_document_date: str | None, notes: str | None,
                        actor: dict) -> dict:
    """登记一个新的供应商技术状态。默认 DRAFT —— 收到不等于接受。"""
    ep = fetch_one(conn, """
        SELECT ep.id, ep.external_part_number FROM external_part ep
          JOIN design_object d ON d.id = ep.design_object_id WHERE d.object_code = %s
    """, (object_code,))
    if ep is None:
        raise LookupError(f"外部件不存在: {object_code}")

    seq = scalar(conn, """
        SELECT COALESCE(max(state_sequence), 0) + 1 FROM external_technical_state
         WHERE external_part_id = %s
    """, (ep["id"],)) or 1

    # 摘要按「供应商版本 + 文件号 + 日期」计算, 用于日后核对这条记录描述的是不是同一版
    digest = hashlib.sha256(
        f"{supplier_revision}|{supplier_document or ''}|{supplier_document_date or ''}"
        .encode()).hexdigest()

    row = fetch_one(conn, """
        INSERT INTO external_technical_state
            (external_part_id, state_sequence, supplier_revision, supplier_document,
             supplier_document_date, hash_sha256, notes, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, state_sequence, supplier_revision, status
    """, (ep["id"], seq, supplier_revision, supplier_document,
          supplier_document_date or None, digest, notes, actor["user_id"]))

    audit.write(conn, action="EXTERNAL_STATE_ADD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="EXTERNAL_TECHNICAL_STATE",
                object_id=str(row["id"]), object_code=object_code,
                new_value={"state_sequence": seq, "supplier_revision": supplier_revision,
                           "supplier_document": supplier_document, "status": "DRAFT"},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def accept_technical_state(conn: psycopg.Connection, state_id: str, comments: str,
                           actor: dict) -> dict:
    """接受技术状态 — INV-017 的前置动作。

    接受是一次明确的技术判断: 确认这一版供应商规格满足我方设计要求。基线只能引用
    已接受的状态, 数据库触发器在基线发布时会再验一次。

    接受新状态时, 上一个已接受状态转为 SUPERSEDED —— 同一外部件同时只应有一个
    「当前认可版本」, 否则引用时无从判断该用哪个。
    """
    st = fetch_one(conn, """
        SELECT ets.*, ep.external_part_number, d.object_code
          FROM external_technical_state ets
          JOIN external_part ep ON ep.id = ets.external_part_id
          JOIN design_object d ON d.id = ep.design_object_id
         WHERE ets.id = %s
    """, (state_id,))
    if st is None:
        raise LookupError("技术状态不存在")
    if st["status"] != "IN_REVIEW":
        raise ValueError(f"技术状态为 {st['status']}，只有审核中状态可接受")
    from . import approvals
    approvals.require_assignee(conn, str(st["approval_request_id"]), str(actor["user_id"]))

    prev = fetch_one(conn, """
        SELECT id, state_sequence FROM external_technical_state
         WHERE external_part_id = %s AND status = 'ACCEPTED'
    """, (st["external_part_id"],))
    if prev is not None:
        execute(conn, "UPDATE external_technical_state SET status='SUPERSEDED' WHERE id=%s",
                (prev["id"],))

    row = fetch_one(conn, """
        UPDATE external_technical_state
           SET status='ACCEPTED', accepted_by=%s, accepted_at=now(),
               notes = COALESCE(NULLIF(%s,''), notes)
         WHERE id=%s
        RETURNING id, state_sequence, supplier_revision, status, accepted_at
    """, (actor["user_id"], comments, state_id))
    execute(conn, """UPDATE approval_step SET decision='APPROVED', decided_by=%s,
      acted_at=now(), comments=%s WHERE approval_request_id=%s AND decision='PENDING'""",
      (actor["user_id"], comments, st["approval_request_id"]))
    execute(conn, "UPDATE approval_request SET status='APPROVED',closed_at=now() WHERE id=%s",
            (st["approval_request_id"],))

    execute(conn, """
        UPDATE design_object SET lifecycle_status='RELEASED', updated_by=%s
         WHERE id = (SELECT design_object_id FROM external_part WHERE id=%s)
           AND lifecycle_status = 'DRAFT'
    """, (actor["user_id"], st["external_part_id"]))

    audit.write(conn, action="EXTERNAL_STATE_ACCEPT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="EXTERNAL_TECHNICAL_STATE",
                object_id=state_id, object_code=st["object_code"],
                old_value={"status": st["status"]},
                new_value={"status": "ACCEPTED",
                           "superseded": prev["state_sequence"] if prev else None},
                reason=comments, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def submit_technical_state(conn: psycopg.Connection, state_id: str,
                           approver_user_id: str, actor: dict) -> dict:
    from . import approvals
    st=fetch_one(conn,"""SELECT ets.*,d.object_code FROM external_technical_state ets
      JOIN external_part ep ON ep.id=ets.external_part_id
      JOIN design_object d ON d.id=ep.design_object_id WHERE ets.id=%s""",(state_id,))
    if not st: raise LookupError("技术状态不存在")
    if st["status"]!="DRAFT": raise ValueError("只有草稿技术状态可提交")
    approver=approvals.require_approver(conn,approver_user_id,actor["user_id"])
    number=approvals.next_request_number(conn)
    req=fetch_one(conn,"""INSERT INTO approval_request(request_number,request_type,object_type,
      object_id,object_code,title,requester_id) VALUES(%s,'EXTERNAL_TS_ACCEPT',
      'EXTERNAL_TECHNICAL_STATE',%s,%s,%s,%s) RETURNING id,request_number""",
      (number,state_id,st["object_code"],f"接受外部件技术状态 {st['object_code']} TS{st['state_sequence']}",actor["user_id"]))
    execute(conn,"""INSERT INTO approval_step(approval_request_id,step_order,step_name,
      required_role_code,assignee_user_id,is_final) VALUES(%s,1,'技术状态接受','APPROVER',%s,true)""",
      (req["id"],approver["id"]))
    execute(conn,"UPDATE external_technical_state SET status='IN_REVIEW',approval_request_id=%s WHERE id=%s",
            (req["id"],state_id))
    return req


def reject_technical_state(conn: psycopg.Connection, state_id: str, reason: str,
                           actor: dict) -> None:
    st = fetch_one(conn, """
        SELECT ets.status, d.object_code FROM external_technical_state ets
          JOIN external_part ep ON ep.id = ets.external_part_id
          JOIN design_object d ON d.id = ep.design_object_id WHERE ets.id = %s
    """, (state_id,))
    if st is None:
        raise LookupError("技术状态不存在")
    if st["status"] != "DRAFT":
        raise ValueError(f"技术状态为 {st['status']}, 只有 DRAFT 可拒绝")
    execute(conn, "UPDATE external_technical_state SET status='REJECTED' WHERE id=%s",
            (state_id,))
    audit.write(conn, action="EXTERNAL_STATE_REJECT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="EXTERNAL_TECHNICAL_STATE",
                object_id=state_id, object_code=st["object_code"],
                new_value={"status": "REJECTED"}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


def add_project_control(conn: psycopg.Connection, object_code: str, project_code: str,
                        applicability: str, evaluation_basis: str | None,
                        actor: dict) -> dict:
    ep=fetch_one(conn,"""SELECT ep.id FROM external_part ep JOIN design_object d
      ON d.id=ep.design_object_id WHERE d.object_code=%s""",(object_code,))
    if not ep: raise LookupError("外部件不存在")
    row=fetch_one(conn,"""INSERT INTO external_part_project_control
      (external_part_id,project_code,applicability,evaluation_basis,created_by)
      VALUES(%s,%s,%s,%s,%s) RETURNING *""",
      (ep["id"],project_code.strip(),applicability.strip(),evaluation_basis,actor["user_id"]))
    return row


def submit_project_control(conn: psycopg.Connection, control_id: str,
                           approver_user_id: str, actor: dict) -> dict:
    from . import approvals
    c=fetch_one(conn,"""SELECT c.*,d.object_code FROM external_part_project_control c
      JOIN external_part ep ON ep.id=c.external_part_id
      JOIN design_object d ON d.id=ep.design_object_id WHERE c.id=%s""",(control_id,))
    if not c: raise LookupError("项目准入记录不存在")
    if c["status"]!="DRAFT": raise ValueError("只有草稿状态可提交")
    ep=fetch_one(conn,"SELECT external_class_code FROM external_part WHERE id=%s",(c["external_part_id"],))
    if ep["external_class_code"]=='E99': raise ValueError("待分类外部件不得提交项目准入，请先确定正式分类")
    if not c["evaluation_basis"]: raise ValueError("项目准入必须填写评价依据")
    accepted=scalar(conn,"SELECT count(*) FROM external_technical_state WHERE external_part_id=%s AND status='ACCEPTED'",(c["external_part_id"],))
    if not accepted: raise ValueError("至少有一个已接受的供应商技术状态后才能提交项目准入")
    approver=approvals.require_approver(conn,approver_user_id,actor["user_id"])
    request_number=approvals.next_request_number(conn)
    req=fetch_one(conn,"""INSERT INTO approval_request(request_number,request_type,
      object_type,object_id,object_code,title,requester_id,payload)
      VALUES(%s,'EXTERNAL_PROJECT_APPROVAL','EXTERNAL_PROJECT_CONTROL',%s,%s,%s,%s,
      jsonb_build_object('project_code',%s,'applicability',%s,'evaluation_basis',%s))
      RETURNING id,request_number""",(request_number,control_id,c["object_code"],
      f"外部件项目准入 {c['project_code']} {c['object_code']}",actor["user_id"],
      c["project_code"],c["applicability"],c["evaluation_basis"]))
    execute(conn,"""INSERT INTO approval_step(approval_request_id,step_order,step_name,
      required_role_code,assignee_user_id,is_final) VALUES(%s,1,'项目准入批准','APPROVER',%s,true)""",
      (req["id"],approver["id"]))
    execute(conn,"UPDATE external_part_project_control SET status='IN_REVIEW',approval_request_id=%s WHERE id=%s",(req["id"],control_id))
    return req


def approve_project_control(conn: psycopg.Connection, control_id: str, comments: str,
                            actor: dict) -> dict:
    from . import approvals
    c=fetch_one(conn,"SELECT * FROM external_part_project_control WHERE id=%s",(control_id,))
    if not c: raise LookupError("项目准入记录不存在")
    if c["status"]!="IN_REVIEW": raise ValueError("只有审核中状态可批准")
    approvals.require_assignee(conn,str(c["approval_request_id"]),str(actor["user_id"]))
    execute(conn,"""UPDATE approval_step SET decision='APPROVED',decided_by=%s,
      acted_at=now(),comments=%s WHERE approval_request_id=%s AND is_final""",
      (actor["user_id"],comments,c["approval_request_id"]))
    execute(conn,"UPDATE approval_request SET status='APPROVED',closed_at=now() WHERE id=%s",(c["approval_request_id"],))
    return fetch_one(conn,"""UPDATE external_part_project_control SET status='APPROVED',
      approved_by=%s,approved_at=now() WHERE id=%s RETURNING *""",(actor["user_id"],control_id))


# ---------------------------------------------------------------------
# 软件对象
# ---------------------------------------------------------------------
def list_software(conn: psycopg.Connection, q: str | None = None) -> list[dict]:
    return fetch_all(conn, """
        SELECT so.id, d.object_code, so.software_number, so.name_cn, so.software_type,
               so.lifecycle_status, sv.version AS current_version,
               (SELECT count(*) FROM software_version x WHERE x.software_object_id = so.id)
                 AS version_count
          FROM software_object so
          JOIN design_object d ON d.id = so.design_object_id
          LEFT JOIN software_version sv ON sv.id = so.current_version_id
         WHERE (%s::text IS NULL OR so.software_number ILIKE %s OR so.name_cn ILIKE %s)
         ORDER BY so.software_number
    """, (q, f"%{q}%" if q else None, f"%{q}%" if q else None))


def create_software(conn: psycopg.Connection, *, software_number: str, name_cn: str,
                    name_en: str | None, software_type: str, actor: dict) -> dict:
    obj = fetch_one(conn, """
        INSERT INTO design_object (object_type, object_code, display_name, created_by, updated_by)
        VALUES ('SOFTWARE', %s, %s, %s, %s) RETURNING id
    """, (software_number, name_cn, actor["user_id"], actor["user_id"]))
    row = fetch_one(conn, """
        INSERT INTO software_object (design_object_id, software_number, name_cn, name_en,
                                     software_type, created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, software_number, name_cn, software_type, lifecycle_status
    """, (obj["id"], software_number, name_cn, name_en, software_type,
          actor["user_id"], actor["user_id"]))
    audit.write(conn, action="SOFTWARE_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="SOFTWARE_OBJECT",
                object_id=str(row["id"]), object_code=software_number,
                new_value={"name_cn": name_cn, "software_type": software_type},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def get_software(conn: psycopg.Connection, software_number: str) -> dict | None:
    so = fetch_one(conn, """
        SELECT so.*, d.object_code FROM software_object so
          JOIN design_object d ON d.id = so.design_object_id
         WHERE so.software_number = %s
    """, (software_number,))
    if so is None:
        return None
    so["versions"] = fetch_all(conn, """
        SELECT sv.id, sv.version, sv.build, sv.hash_sha256, sv.status, sv.released_at,
               sv.notes, aps.assignee_user_id AS approval_assignee_user_id,
               (SELECT count(*) FROM baseline_item bi WHERE bi.software_version_id = sv.id)
                 AS baseline_refs
          FROM software_version sv
          LEFT JOIN approval_step aps ON aps.approval_request_id=sv.approval_request_id
            AND aps.decision='PENDING'
         WHERE sv.software_object_id = %s
         ORDER BY sv.created_at DESC
    """, (so["id"],))
    return so


def add_version(conn: psycopg.Connection, software_number: str, *, version: str,
                build: str, hash_sha256: str | None, notes: str | None,
                actor: dict) -> dict:
    so = fetch_one(conn, "SELECT id FROM software_object WHERE software_number = %s",
                   (software_number,))
    if so is None:
        raise LookupError(f"软件对象不存在: {software_number}")
    row = fetch_one(conn, """
        INSERT INTO software_version (software_object_id, version, build, hash_sha256,
                                      notes, created_by)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, version, build, status
    """, (so["id"], version, build or "", hash_sha256, notes, actor["user_id"]))
    audit.write(conn, action="SOFTWARE_VERSION_ADD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="SOFTWARE_VERSION",
                object_id=str(row["id"]), object_code=f"{software_number} {version}",
                new_value={"version": version, "build": build, "hash_sha256": hash_sha256},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def release_version(conn: psycopg.Connection, version_id: str, comments: str,
                    actor: dict) -> dict:
    """发布软件版本。发布后 hash 冻结(与文件版次同理), 上一个发布版转 SUPERSEDED。"""
    sv = fetch_one(conn, """
        SELECT sv.*, so.software_number, so.id AS so_id FROM software_version sv
          JOIN software_object so ON so.id = sv.software_object_id WHERE sv.id = %s
    """, (version_id,))
    if sv is None:
        raise LookupError("软件版本不存在")
    if sv["status"] != "IN_REVIEW":
        raise ValueError(f"软件版本为 {sv['status']}，只有审核中状态可发布")
    from . import approvals
    approvals.require_assignee(conn,str(sv["approval_request_id"]),str(actor["user_id"]))
    if not sv["hash_sha256"]:
        raise ValueError("发布前必须登记软件包的 SHA-256, 否则无法核对交付物是否被替换")

    prev = fetch_one(conn, """
        SELECT id FROM software_version WHERE software_object_id=%s AND status='RELEASED'
    """, (sv["so_id"],))
    if prev is not None:
        execute(conn, "UPDATE software_version SET status='SUPERSEDED' WHERE id=%s",
                (prev["id"],))

    row = fetch_one(conn, """
        UPDATE software_version SET status='RELEASED', released_at=now(), released_by=%s
         WHERE id=%s RETURNING id, version, build, status, released_at
    """, (actor["user_id"], version_id))
    execute(conn,"""UPDATE approval_step SET decision='APPROVED',decided_by=%s,
      acted_at=now(),comments=%s WHERE approval_request_id=%s AND decision='PENDING'""",
      (actor["user_id"],comments,sv["approval_request_id"]))
    execute(conn,"UPDATE approval_request SET status='APPROVED',closed_at=now() WHERE id=%s",
            (sv["approval_request_id"],))
    execute(conn, """
        UPDATE software_object SET current_version_id=%s, lifecycle_status='RELEASED',
               updated_by=%s WHERE id=%s
    """, (version_id, actor["user_id"], sv["so_id"]))
    execute(conn, """
        UPDATE design_object SET lifecycle_status='RELEASED', updated_by=%s
         WHERE id=(SELECT design_object_id FROM software_object WHERE id=%s)
    """, (actor["user_id"], sv["so_id"]))

    audit.write(conn, action="SOFTWARE_VERSION_RELEASE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="SOFTWARE_VERSION",
                object_id=version_id,
                object_code=f"{sv['software_number']} {sv['version']}",
                new_value={"status": "RELEASED", "hash_sha256": sv["hash_sha256"]},
                reason=comments, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def submit_version(conn: psycopg.Connection, version_id: str,
                   approver_user_id: str, actor: dict) -> dict:
    from . import approvals
    sv=fetch_one(conn,"""SELECT sv.*,so.software_number FROM software_version sv
      JOIN software_object so ON so.id=sv.software_object_id WHERE sv.id=%s""",(version_id,))
    if not sv: raise LookupError("软件版本不存在")
    if sv["status"]!="DRAFT": raise ValueError("只有草稿软件版本可提交")
    if not sv["hash_sha256"]: raise ValueError("提交前必须登记软件包 SHA-256")
    approver=approvals.require_approver(conn,approver_user_id,actor["user_id"])
    number=approvals.next_request_number(conn)
    req=fetch_one(conn,"""INSERT INTO approval_request(request_number,request_type,object_type,
      object_id,object_code,title,requester_id) VALUES(%s,'CHANGE_PACKAGE','SOFTWARE_VERSION',
      %s,%s,%s,%s) RETURNING id,request_number""",(number,version_id,
      f"{sv['software_number']} {sv['version']}",f"发布软件版本 {sv['software_number']} {sv['version']}",actor["user_id"]))
    execute(conn,"""INSERT INTO approval_step(approval_request_id,step_order,step_name,
      required_role_code,assignee_user_id,is_final) VALUES(%s,1,'软件版本发布','APPROVER',%s,true)""",
      (req["id"],approver["id"]))
    execute(conn,"UPDATE software_version SET status='IN_REVIEW',approval_request_id=%s WHERE id=%s",
            (req["id"],version_id))
    return req
