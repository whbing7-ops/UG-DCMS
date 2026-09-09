"""设计基线 — SRS-BL-001~007 / AC-BL-01/02 / AT-003/004 /
   INV-011 012 013 015 017 022。

基线是整套系统的收口环节: P/N、文件版次、BOM 快照、外部件技术状态、软件版本
在这里汇合成一个"某个 P/N 在某一时刻的完整技术状态"。

四条约束决定了本模块的写法:

**基线只引用确定版次, 不存在 latest/current 引用**(INV-012)。baseline_item 的四个
目标列都是具体版次的外键, 表结构上就没有"跟随最新"的位置。这是基线可复现的前提 ——
若允许浮动引用, 两年后重新查这条基线会得到与当初不同的内容。

**已发布基线完全冻结**(INV-011)。发布之后除了 status 转 SUPERSEDED 和 is_current
切换, 任何字段与任何明细都改不动, 由数据库触发器保证。要改就发新基线。

**发布前置校验在数据库层**(AC-DATA-03/04, INV-017, INV-022)。
trg_baseline_release_validate 在状态转 RELEASED 时检查: 引用的文件版次必须已发布、
外部技术状态必须 ACCEPTED、软件版本必须已发布、必须恰好一个 PRIMARY_DEFINITION、
最多一个 BOM 快照。应用层的 validate() 只是把同样的检查提前到界面上, 让使用者在
点发布之前就知道哪里不合格 —— 但真正的把关在数据库。

**每个已发布 P/N 有且仅有一个当前基线**(INV-013)。唯一性由局部唯一索引保证,
归属正确性由延迟约束触发器保证 —— 延迟是为了让"发布新基线 → 旧基线让位 →
切换 current"能在单个事务里完成。
"""
from __future__ import annotations

import hashlib
import json

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

ITEM_ROLES = {"PRIMARY_DEFINITION", "SUPPORTING_DEFINITION",
              "INTERFACE_DEFINITION", "QUALIFICATION_EVIDENCE"}


def _part(conn: psycopg.Connection, full_pn: str) -> dict:
    row = fetch_one(conn, """
        SELECT pn.*, d.object_code, d.id AS design_object_id
          FROM part_number pn JOIN design_object d ON d.id = pn.design_object_id
         WHERE pn.full_part_number = %s
    """, (full_pn,))
    if row is None:
        raise LookupError(f"P/N 不存在: {full_pn}")
    return row


# ---------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------
def create_baseline(conn: psycopg.Connection, full_pn: str, reason: str,
                    actor: dict, copy_from_current: bool = False,
                    baseline_type: str = "DESIGN", scope_note: str = "",
                    change_reference: str | None = None,
                    project_code: str = "GENERAL") -> dict:
    """建立 DRAFT 基线。

    copy_from_current 会把当前基线的明细整份复制过来 —— 实际改版多数只动一两项,
    从零开始拼装既费事又容易漏掉本该保留的项。复制来的是同样的确定版次引用,
    使用者再逐项替换需要更新的部分。
    """
    pn = _part(conn, full_pn)
    open_bl = fetch_one(conn, """
        SELECT baseline_code, status FROM design_baseline
         WHERE part_number_id = %s AND status IN ('DRAFT', 'IN_REVIEW')
    """, (pn["id"],))
    if open_bl is not None:
        raise ValueError(f"该 P/N 已有未发布基线 {open_bl['baseline_code']}"
                         f"(状态 {open_bl['status']}), 请先完成或取消")

    seq = (scalar(conn, "SELECT COALESCE(max(baseline_sequence),0)+1 FROM design_baseline "
                        "WHERE part_number_id=%s", (pn["id"],)) or 1)
    prefix = scalar(conn, "SELECT value FROM system_setting WHERE key='baseline_code_prefix'") or "BL-"

    source = None
    if copy_from_current:
        source = fetch_one(conn, """
            SELECT id, baseline_code FROM design_baseline
             WHERE part_number_id=%s AND is_current
        """, (pn["id"],))
        if source is None:
            raise ValueError("该 P/N 尚无当前基线, 无法复制")

    bl = fetch_one(conn, """
        INSERT INTO design_baseline (part_number_id, baseline_sequence, baseline_code,
                                     reason, baseline_type, scope_note, change_reference, project_code,
                                     copied_from_baseline_id, prepared_by,
                                     created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, baseline_code, baseline_sequence, status, reason
    """, (pn["id"], seq, f"{prefix}{seq:03d}", reason, baseline_type,
          scope_note.strip(), change_reference or None, project_code.strip(),
          source["id"] if source else None,
          actor["user_id"], actor["user_id"], actor["user_id"]))

    copied = 0
    if source is not None:
        copied = execute(conn, """
            INSERT INTO baseline_item (design_baseline_id, item_type, file_revision_id,
                                       bom_snapshot_id, external_technical_state_id,
                                       software_version_id, item_role, sequence, notes)
            SELECT %s, item_type, file_revision_id, bom_snapshot_id,
                   external_technical_state_id, software_version_id, item_role,
                   sequence, notes
              FROM baseline_item WHERE design_baseline_id = %s
        """, (bl["id"], source["id"]))

    audit.write(conn, action="BASELINE_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_BASELINE",
                object_id=str(bl["id"]), object_code=f"{full_pn} {bl['baseline_code']}",
                new_value={"reason": reason,
                           "baseline_type": baseline_type, "scope_note": scope_note,
                           "change_reference": change_reference,
                           "project_code": project_code,
                           "copied_from": source["baseline_code"] if source else None,
                           "items_copied": copied},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    bl["items_copied"] = copied
    return bl


def get_baseline(conn: psycopg.Connection, baseline_id: str) -> dict | None:
    bl = fetch_one(conn, """
        SELECT db.*, pn.full_part_number, pn.formal_name_cn,
               aps.assignee_user_id AS approval_assignee_user_id
          FROM design_baseline db JOIN part_number pn ON pn.id = db.part_number_id
          LEFT JOIN approval_step aps ON aps.approval_request_id=db.approval_request_id
            AND aps.decision='PENDING'
         WHERE db.id = %s
    """, (baseline_id,))
    if bl is not None:
        bl["items"] = list_items(conn, baseline_id)
    return bl


def list_baselines(conn: psycopg.Connection, full_pn: str) -> list[dict]:
    pn = _part(conn, full_pn)
    return fetch_all(conn, """
        SELECT id, baseline_code, baseline_sequence, status, is_current, reason,
               content_hash, released_at, superseded_at,
               (SELECT count(*) FROM baseline_item bi WHERE bi.design_baseline_id = db.id)
                 AS item_count
          FROM design_baseline db WHERE part_number_id = %s
         ORDER BY baseline_sequence
    """, (pn["id"],))


def list_items(conn: psycopg.Connection, baseline_id: str) -> list[dict]:
    """展开明细并把四类目标统一成可读形式。

    统一形式很重要: 界面和比较逻辑都不该关心"这一项存在哪一列", 否则每处都要写
    一遍四分支判断。
    """
    return fetch_all(conn, """
        SELECT bi.id, bi.item_type, bi.item_role, bi.sequence, bi.notes,
               CASE bi.item_type
                 WHEN 'FILE_REVISION' THEN df.file_number || ' Rev.' || fr.revision_number
                 WHEN 'BOM_SNAPSHOT' THEN bs.snapshot_number
                 WHEN 'EXTERNAL_TECHNICAL_STATE' THEN
                      ns.code || '::' || ep.external_part_number || ' TS' || ets.state_sequence
                 WHEN 'SOFTWARE_VERSION' THEN so.software_number || ' ' || sv.version
               END AS item_label,
               CASE bi.item_type
                 WHEN 'FILE_REVISION' THEN fr.status
                 WHEN 'BOM_SNAPSHOT' THEN 'FROZEN'
                 WHEN 'EXTERNAL_TECHNICAL_STATE' THEN ets.status
                 WHEN 'SOFTWARE_VERSION' THEN sv.status
               END AS item_status,
               CASE bi.item_type
                 WHEN 'BOM_SNAPSHOT' THEN bs.hash_sha256
                 WHEN 'EXTERNAL_TECHNICAL_STATE' THEN ets.hash_sha256
                 WHEN 'SOFTWARE_VERSION' THEN sv.hash_sha256
               END AS item_hash,
               df.file_number, fr.revision_number, bs.snapshot_number,
               ep.external_part_number, ets.supplier_revision,
               so.software_number, sv.version
          FROM baseline_item bi
          LEFT JOIN file_revision fr ON fr.id = bi.file_revision_id
          LEFT JOIN design_file df ON df.id = fr.design_file_id
          LEFT JOIN bom_snapshot bs ON bs.id = bi.bom_snapshot_id
          LEFT JOIN external_technical_state ets ON ets.id = bi.external_technical_state_id
          LEFT JOIN external_part ep ON ep.id = ets.external_part_id
          LEFT JOIN namespace ns ON ns.id = ep.namespace_id
          LEFT JOIN software_version sv ON sv.id = bi.software_version_id
          LEFT JOIN software_object so ON so.id = sv.software_object_id
         WHERE bi.design_baseline_id = %s
         ORDER BY bi.sequence, bi.item_type
    """, (baseline_id,))


# ---------------------------------------------------------------------
# 明细维护
# ---------------------------------------------------------------------
def add_item(conn: psycopg.Connection, baseline_id: str, *, item_type: str,
             target: str, item_role: str | None, notes: str | None,
             actor: dict) -> dict:
    """向 DRAFT 基线添加一项。target 用可读标识而非内部 ID, 便于脚本与导入使用。"""
    bl = fetch_one(conn, "SELECT status, baseline_code, approval_request_id FROM design_baseline WHERE id=%s",
                   (baseline_id,))
    if bl is None:
        raise LookupError("基线不存在")
    if bl["status"] != "DRAFT":
        raise ValueError(f"基线状态为 {bl['status']}, 明细不可修改 (INV-011)")
    if item_role is not None and item_role not in ITEM_ROLES:
        raise ValueError(f"item_role 须为: {', '.join(sorted(ITEM_ROLES))}")

    cols = {"file_revision_id": None, "bom_snapshot_id": None,
            "external_technical_state_id": None, "software_version_id": None}

    if item_type == "FILE_REVISION":
        # target 形如 "UG-A10001M001 Rev.00"
        num, _, rev = target.partition(" Rev.")
        row = fetch_one(conn, """
            SELECT fr.id FROM file_revision fr JOIN design_file df ON df.id=fr.design_file_id
             WHERE df.file_number=%s AND fr.revision_number=%s
        """, (num.strip(), rev.strip()))
        if row is None:
            raise LookupError(f"文件版次不存在: {target}")
        cols["file_revision_id"] = row["id"]
    elif item_type == "BOM_SNAPSHOT":
        row = fetch_one(conn, "SELECT id FROM bom_snapshot WHERE snapshot_number=%s",
                        (target.strip(),))
        if row is None:
            raise LookupError(f"BOM 快照不存在: {target}")
        cols["bom_snapshot_id"] = row["id"]
    elif item_type == "EXTERNAL_TECHNICAL_STATE":
        # target 形如 "MOLEX::43025-0400 TS1"
        head, _, ts = target.partition(" TS")
        ns, _, epn = head.partition("::")
        row = fetch_one(conn, """
            SELECT ets.id FROM external_technical_state ets
              JOIN external_part ep ON ep.id = ets.external_part_id
              JOIN namespace n ON n.id = ep.namespace_id
             WHERE n.code=%s AND ep.external_part_number=%s AND ets.state_sequence=%s
        """, (ns.strip(), epn.strip(), int(ts or 1)))
        if row is None:
            raise LookupError(f"外部技术状态不存在: {target}")
        cols["external_technical_state_id"] = row["id"]
    elif item_type == "SOFTWARE_VERSION":
        num, _, ver = target.rpartition(" ")
        row = fetch_one(conn, """
            SELECT sv.id FROM software_version sv
              JOIN software_object so ON so.id = sv.software_object_id
             WHERE so.software_number=%s AND sv.version=%s
        """, (num.strip(), ver.strip()))
        if row is None:
            raise LookupError(f"软件版本不存在: {target}")
        cols["software_version_id"] = row["id"]
    else:
        raise ValueError(f"未知明细类型: {item_type}")

    seq = scalar(conn, "SELECT COALESCE(max(sequence),0)+10 FROM baseline_item "
                       "WHERE design_baseline_id=%s", (baseline_id,)) or 10

    row = fetch_one(conn, """
        INSERT INTO baseline_item (design_baseline_id, item_type, file_revision_id,
                                   bom_snapshot_id, external_technical_state_id,
                                   software_version_id, item_role, sequence, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, item_type, item_role, sequence
    """, (baseline_id, item_type, cols["file_revision_id"], cols["bom_snapshot_id"],
          cols["external_technical_state_id"], cols["software_version_id"],
          item_role, seq, notes))

    audit.write(conn, action="BASELINE_ITEM_ADD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_BASELINE",
                object_id=baseline_id, object_code=bl["baseline_code"],
                new_value={"item_type": item_type, "target": target,
                           "item_role": item_role},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def remove_item(conn: psycopg.Connection, item_id: str, actor: dict) -> None:
    row = fetch_one(conn, """
        SELECT bi.*, db.status, db.baseline_code FROM baseline_item bi
          JOIN design_baseline db ON db.id = bi.design_baseline_id WHERE bi.id=%s
    """, (item_id,))
    if row is None:
        raise LookupError("基线明细不存在")
    if row["status"] != "DRAFT":
        raise ValueError(f"基线状态为 {row['status']}, 明细不可删除 (INV-011)")
    execute(conn, "DELETE FROM baseline_item WHERE id=%s", (item_id,))
    audit.write(conn, action="BASELINE_ITEM_REMOVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_BASELINE",
                object_id=str(row["design_baseline_id"]), object_code=row["baseline_code"],
                old_value={"item_type": row["item_type"], "item_role": row["item_role"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


# ---------------------------------------------------------------------
# 校验 — 与数据库触发器同规则, 提前到界面
# ---------------------------------------------------------------------
def validate(conn: psycopg.Connection, baseline_id: str) -> dict:
    """发布前置校验。

    这些检查在数据库触发器里也有一份。重复不是浪费: 触发器负责"绝对拦住",
    本函数负责"提前告诉使用者哪里不合格", 二者目的不同。若只有触发器,
    使用者要点了发布才知道错在哪; 若只有应用层, 绕过应用就失效了。
    """
    bl = get_baseline(conn, baseline_id)
    if bl is None:
        raise LookupError("基线不存在")

    errors: list[str] = []
    warnings: list[str] = []
    items = bl["items"]

    if not items:
        errors.append("基线无任何明细, 不得发布")

    # AC-DATA-03: 文件版次必须已发布
    for it in items:
        if it["item_type"] == "FILE_REVISION" and it["item_status"] not in ("RELEASED", "SUPERSEDED"):
            errors.append(f"引用了未发布版次 {it['item_label']} (状态 {it['item_status']}) "
                          f"— AC-DATA-03")
        if it["item_type"] == "FILE_REVISION" and it["item_status"] == "SUPERSEDED":
            warnings.append(f"{it['item_label']} 已被更新版次取代, 请确认这是有意锁定")
        # AC-DATA-04 / INV-017
        if it["item_type"] == "EXTERNAL_TECHNICAL_STATE" and it["item_status"] != "ACCEPTED":
            errors.append(f"外部技术状态 {it['item_label']} 状态为 {it['item_status']}, "
                          f"必须为 ACCEPTED — INV-017")
        if it["item_type"] == "SOFTWARE_VERSION" and it["item_status"] not in ("RELEASED", "SUPERSEDED"):
            errors.append(f"软件版本 {it['item_label']} 尚未发布")

    # INV-022: 恰好一个主设计定义
    primaries = [i for i in items
                 if i["item_type"] == "FILE_REVISION" and i["item_role"] == "PRIMARY_DEFINITION"]
    if not primaries:
        errors.append("缺少 PRIMARY_DEFINITION 主设计定义 — INV-022")
    elif len(primaries) > 1:
        errors.append(f"存在 {len(primaries)} 个主设计定义, 只允许 1 个 — INV-022")

    # INV-015: 最多一个 BOM 快照
    snaps = [i for i in items if i["item_type"] == "BOM_SNAPSHOT"]
    if len(snaps) > 1:
        errors.append(f"存在 {len(snaps)} 个 BOM 快照, 只允许 1 个")
    if not snaps:
        warnings.append("基线未锁定 BOM 快照; 若该 P/N 为组件, 应确认是否遗漏")

    # 附件完整性 — DQ-FILE-001 阻止发布
    bad = fetch_all(conn, """
        SELECT df.file_number, fr.revision_number, ra.filename, ra.integrity_status
          FROM baseline_item bi
          JOIN file_revision fr ON fr.id = bi.file_revision_id
          JOIN design_file df ON df.id = fr.design_file_id
          JOIN revision_attachment ra ON ra.file_revision_id = fr.id
         WHERE bi.design_baseline_id = %s AND ra.integrity_status IN ('MISMATCH','MISSING')
    """, (baseline_id,))
    for b in bad:
        errors.append(f"{b['file_number']} Rev.{b['revision_number']} 的附件 "
                      f"{b['filename']} 完整性异常 ({b['integrity_status']}) — DQ-FILE-001")

    unapproved_external = fetch_all(conn, """
        SELECT ep.external_part_number
          FROM baseline_item bi
          JOIN external_technical_state ets ON ets.id=bi.external_technical_state_id
          JOIN external_part ep ON ep.id=ets.external_part_id
         WHERE bi.design_baseline_id=%s AND NOT EXISTS (
           SELECT 1 FROM external_part_project_control pc
            WHERE pc.external_part_id=ep.id AND pc.project_code=%s AND pc.status='APPROVED')
    """, (baseline_id, bl["project_code"]))
    for x in unapproved_external:
        errors.append(f"外部件 {x['external_part_number']} 尚未获得项目 {bl['project_code']} 的准入批准")

    result = {"errors": errors, "warnings": warnings, "item_count": len(items),
              "passed": not errors}
    execute(conn, "UPDATE design_baseline SET validation_result=%s::jsonb WHERE id=%s",
            (json.dumps(result, ensure_ascii=False), baseline_id))
    return result


def content_hash(conn: psycopg.Connection, baseline_id: str) -> str:
    """基线内容摘要 — SRS-BL-007。

    只把"锁定了哪些确定版次"纳入摘要, 不含创建人、时间等元数据 ——
    同样内容的两条基线应得到同样的摘要, 才能用于比对与复现验证。
    """
    items = list_items(conn, baseline_id)
    payload = json.dumps(
        sorted([{"type": i["item_type"], "label": i["item_label"],
                 "role": i["item_role"]} for i in items],
               key=lambda x: (x["type"], x["label"] or "")),
        ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------
# 提交与发布
# ---------------------------------------------------------------------
def submit(conn: psycopg.Connection, baseline_id: str, approver_user_id: str,
           actor: dict) -> dict:
    bl = get_baseline(conn, baseline_id)
    if bl is None:
        raise LookupError("基线不存在")
    if bl["status"] != "DRAFT":
        raise ValueError(f"基线状态为 {bl['status']}, 只有 DRAFT 可提交")
    res = validate(conn, baseline_id)
    if not res["passed"]:
        raise ValueError("基线校验未通过: " + "; ".join(res["errors"]))
    from . import approvals
    approver = approvals.require_approver(conn, approver_user_id, actor["user_id"],
                                          "CONFIGURATION_MANAGER")

    request_number = approvals.next_request_number(conn)
    req = fetch_one(conn, """
        INSERT INTO approval_request (request_number, request_type, object_type,
                                      object_id, object_code, title, requester_id)
        VALUES (%s,'BASELINE_RELEASE','DESIGN_BASELINE',%s,%s,%s,%s)
        RETURNING id, request_number
    """, (request_number, baseline_id,
          f"{bl['full_part_number']} {bl['baseline_code']}",
          f"发布基线 {bl['full_part_number']} {bl['baseline_code']}", actor["user_id"]))
    execute(conn, """
        INSERT INTO approval_step (approval_request_id, step_order, step_name,
                                   required_role_code, assignee_user_id, is_final)
        VALUES (%s,1,'基线批准','CONFIGURATION_MANAGER',%s,true)
    """, (req["id"], approver["id"]))
    execute(conn, "UPDATE design_baseline SET status='IN_REVIEW', approval_request_id=%s, "
                  "updated_by=%s WHERE id=%s", (req["id"], actor["user_id"], baseline_id))

    audit.write(conn, action="BASELINE_SUBMIT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_BASELINE",
                object_id=baseline_id, object_code=bl["baseline_code"],
                new_value={"approval_request": req["request_number"],
                           "warnings": res["warnings"],
                           "approver": approver["username"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return req


def release(conn: psycopg.Connection, baseline_id: str, comments: str,
            actor: dict, make_current: bool = True) -> dict:
    """批准并发布基线, 可选同时置为当前基线。

    顺序: 审批决定 → 旧基线让位 → 本基线转 RELEASED → 置 current → 回写 P/N。
    全部在一个事务内; part_number 的归属校验是延迟约束触发器, 到事务提交时才判定,
    因此中间态(旧基线已让位、新基线尚未成为 current)不会被误判为违规。
    """
    bl = get_baseline(conn, baseline_id)
    if bl is None:
        raise LookupError("基线不存在")
    if bl["status"] != "IN_REVIEW":
        raise ValueError(f"基线状态为 {bl['status']}, 只有 IN_REVIEW 可发布")
    from . import approvals
    approvals.require_assignee(conn, str(bl["approval_request_id"]), str(actor["user_id"]))

    # INV-025 由 trg_approval_separation 拦截自批
    execute(conn, """
        UPDATE approval_step SET decision='APPROVED', decided_by=%s, acted_at=now(),
               comments=%s WHERE approval_request_id=%s AND is_final
    """, (actor["user_id"], comments, bl["approval_request_id"]))
    execute(conn, "UPDATE approval_request SET status='APPROVED', closed_at=now() WHERE id=%s",
            (bl["approval_request_id"],))

    prev = fetch_one(conn, """
        SELECT id, baseline_code FROM design_baseline
         WHERE part_number_id=%s AND is_current
    """, (bl["part_number_id"],))
    if prev is not None and make_current:
        # 先让位: uq_baseline_one_current 是局部唯一索引, 两条 is_current 会直接冲突
        execute(conn, """
            UPDATE design_baseline SET is_current=false, status='SUPERSEDED',
                   superseded_at=now() WHERE id=%s
        """, (prev["id"],))

    digest = content_hash(conn, baseline_id)
    # trg_baseline_release_validate 在此对 AC-DATA-03/04、INV-017/022 做最终把关
    row = fetch_one(conn, """
        UPDATE design_baseline
           SET status='RELEASED', approved_by=%s, released_at=now(),
               content_hash=%s, is_current=%s, updated_by=%s
         WHERE id=%s
        RETURNING id, baseline_code, status, is_current, content_hash, released_at
    """, (actor["user_id"], digest, make_current, actor["user_id"], baseline_id))

    if make_current:
        execute(conn, """
            UPDATE part_number SET current_baseline_id=%s, lifecycle_status='RELEASED',
                   updated_by=%s WHERE id=%s
        """, (baseline_id, actor["user_id"], bl["part_number_id"]))
        execute(conn, """
            UPDATE design_object SET lifecycle_status='RELEASED', updated_by=%s
             WHERE id=(SELECT design_object_id FROM part_number WHERE id=%s)
        """, (actor["user_id"], bl["part_number_id"]))

    audit.write(conn, action="BASELINE_RELEASE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_BASELINE",
                object_id=baseline_id,
                object_code=f"{bl['full_part_number']} {bl['baseline_code']}",
                old_value={"status": "IN_REVIEW"},
                new_value={"status": "RELEASED", "is_current": make_current,
                           "content_hash": digest,
                           "superseded": prev["baseline_code"] if prev else None},
                reason=comments, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def cancel(conn: psycopg.Connection, baseline_id: str, reason: str, actor: dict) -> None:
    bl = fetch_one(conn, "SELECT status, baseline_code, approval_request_id FROM design_baseline WHERE id=%s",
                   (baseline_id,))
    if bl is None:
        raise LookupError("基线不存在")
    if bl["status"] not in ("DRAFT", "IN_REVIEW"):
        raise ValueError(f"基线状态为 {bl['status']}, 不可取消 (INV-011)")
    if bl.get("approval_request_id"):
        execute(conn,"UPDATE approval_request SET status='CANCELLED',closed_at=now() WHERE id=%s AND status='PENDING'",(bl["approval_request_id"],))
    execute(conn, "UPDATE design_baseline SET status='CANCELLED', approval_request_id=NULL, updated_by=%s WHERE id=%s",
            (actor["user_id"], baseline_id))
    audit.write(conn, action="BASELINE_CANCEL", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_BASELINE",
                object_id=baseline_id, object_code=bl["baseline_code"], reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


# ---------------------------------------------------------------------
# 比较 — AC-BL-02
# ---------------------------------------------------------------------
def compare(conn: psycopg.Connection, a_id: str, b_id: str) -> dict:
    """比较两条基线。按"类型 + 角色"配对, 使"同一角色换了版次"能被识别为变更,
    而不是报成一增一删 —— 后者需要人工再判断哪两项是同一件事。
    """
    a, b = get_baseline(conn, a_id), get_baseline(conn, b_id)
    if a is None or b is None:
        raise LookupError("基线不存在")

    def key(i: dict) -> tuple:
        if i["item_type"] == "FILE_REVISION":
            return ("FILE_REVISION", i["file_number"], i["item_role"])
        if i["item_type"] == "BOM_SNAPSHOT":
            return ("BOM_SNAPSHOT", "", "")
        if i["item_type"] == "EXTERNAL_TECHNICAL_STATE":
            return ("EXTERNAL_TECHNICAL_STATE", i["external_part_number"], "")
        return ("SOFTWARE_VERSION", i["software_number"], "")

    ai = {key(i): i for i in a["items"]}
    bi = {key(i): i for i in b["items"]}

    added = [bi[k] for k in bi.keys() - ai.keys()]
    removed = [ai[k] for k in ai.keys() - bi.keys()]
    changed = [{"item_type": k[0], "subject": k[1], "item_role": k[2],
                "from": ai[k]["item_label"], "to": bi[k]["item_label"]}
               for k in ai.keys() & bi.keys()
               if ai[k]["item_label"] != bi[k]["item_label"]]

    return {
        "from": {"baseline_code": a["baseline_code"], "status": a["status"],
                 "content_hash": a["content_hash"]},
        "to": {"baseline_code": b["baseline_code"], "status": b["status"],
               "content_hash": b["content_hash"]},
        "added": added, "removed": removed, "changed": changed,
        "identical": not (added or removed or changed),
    }


def current_configuration(conn: psycopg.Connection, full_pn: str) -> dict:
    """某 P/N 的当前技术状态全貌 — AC-BL-01 的查询侧。"""
    pn = _part(conn, full_pn)
    if pn["current_baseline_id"] is None:
        return {"full_part_number": full_pn, "lifecycle_status": pn["lifecycle_status"],
                "current_baseline": None, "items": []}
    bl = get_baseline(conn, str(pn["current_baseline_id"]))
    return {
        "full_part_number": full_pn,
        "formal_name_cn": pn["formal_name_cn"],
        "lifecycle_status": pn["lifecycle_status"],
        "current_baseline": {"baseline_code": bl["baseline_code"],
                             "released_at": bl["released_at"],
                             "content_hash": bl["content_hash"],
                             "reason": bl["reason"]},
        "items": bl["items"],
    }
