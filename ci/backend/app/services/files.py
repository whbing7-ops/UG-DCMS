"""设计文件与版次 — SRS-FILE-001~005 / INV-006/007/014 / AC-FILE-01 / AC-DATA-05 / AT-007。

三条容易被误解的规则:

**文件身份与版次分离**(INV-006)。design_file 是"这份文件是什么", file_revision 是
"它在某一时刻的内容"。图号不变、内容改了, 是新版次而不是新文件。

**已发布版次不得覆盖**(INV-007)。Rev.00 发布之后, 无论是改变更说明、换附件还是退回
工作状态, 都被数据库触发器拒绝。要改就出 Rev.01 —— 这正是版次存在的意义。

**文件发新版次不自动改变任何 P/N 的技术状态**(INV-014)。这是最容易被想当然做错的一条:
直觉上"图纸升版了, 用它的零件当然跟着升", 但适航构型管理的要求恰恰相反 —— P/N 锁定
的是某个确定版次, 要跟随新版次必须显式发布新基线。所以本模块的任何函数都不会去碰
part_number.current_baseline_id, test_release_does_not_touch_part_baseline 守着这一点。
"""
from __future__ import annotations

import psycopg

from .. import audit, storage
from ..db import execute, fetch_all, fetch_one, scalar

# 新文件版次为两位数字序列; 历史文件兼容字母版次(ERD §9)
def next_revision_number(seq: int) -> str:
    return f"{seq - 1:02d}"


# ---------------------------------------------------------------------
# 设计文件
# ---------------------------------------------------------------------
def create_file(conn: psycopg.Connection, *, file_number: str, file_type_code: str,
                title_cn: str, title_en: str | None, actor: dict) -> dict:
    row = fetch_one(conn, """
        INSERT INTO design_file (file_number, file_type_code, title_cn, title_en,
                                 owner_user_id, created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, file_number, file_type_code, title_cn, title_en, status
    """, (file_number, file_type_code, title_cn, title_en,
          actor["user_id"], actor["user_id"], actor["user_id"]))
    audit.write(conn, action="FILE_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_FILE",
                object_id=str(row["id"]), object_code=file_number,
                new_value={"file_number": file_number, "file_type": file_type_code,
                           "title_cn": title_cn},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def get_file(conn: psycopg.Connection, file_number: str) -> dict | None:
    return fetch_one(conn, """
        SELECT df.*, fr.revision_number AS current_released_revision
          FROM design_file df
          LEFT JOIN file_revision fr ON fr.id = df.current_released_revision_id
         WHERE df.file_number = %s
    """, (file_number,))


def list_files(conn: psycopg.Connection, file_type_code: str | None = None,
               q: str | None = None) -> list[dict]:
    return fetch_all(conn, """
        SELECT df.id, df.file_number, df.file_type_code, df.title_cn, df.status,
               fr.revision_number AS current_released_revision,
               (SELECT count(*) FROM file_revision x WHERE x.design_file_id = df.id)
                 AS revision_count
          FROM design_file df
          LEFT JOIN file_revision fr ON fr.id = df.current_released_revision_id
         WHERE (%s::text IS NULL OR df.file_type_code = %s)
           AND (%s::text IS NULL OR df.file_number ILIKE %s OR df.title_cn ILIKE %s)
         ORDER BY df.file_number
    """, (file_type_code, file_type_code, q,
          f"%{q}%" if q else None, f"%{q}%" if q else None))


# ---------------------------------------------------------------------
# 版次
# ---------------------------------------------------------------------
def list_revisions(conn: psycopg.Connection, file_id: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT fr.id, fr.revision_number, fr.revision_sequence, fr.status,
               fr.revision_date, fr.change_summary, fr.released_at, fr.superseded_at,
               (SELECT count(*) FROM revision_attachment ra
                 WHERE ra.file_revision_id = fr.id) AS attachment_count
          FROM file_revision fr WHERE fr.design_file_id = %s
         ORDER BY fr.revision_sequence
    """, (file_id,))


def create_revision(conn: psycopg.Connection, file_number: str,
                    change_summary: str, actor: dict) -> dict:
    """新建工作版次。

    同一文件同时只允许一个未发布版次 —— 两个并行的工作版次会让"下一版是什么"
    变成开放问题, 且两人各改各的最终必然冲突。
    """
    df = get_file(conn, file_number)
    if df is None:
        raise LookupError(f"设计文件不存在: {file_number}")
    if df["status"] != "ACTIVE":
        raise ValueError(f"文件状态为 {df['status']}, 不可新建版次")

    open_rev = fetch_one(conn, """
        SELECT revision_number, status FROM file_revision
         WHERE design_file_id = %s AND status IN ('WORKING', 'IN_REVIEW')
    """, (df["id"],))
    if open_rev is not None:
        raise ValueError(f"该文件已有未发布版次 Rev.{open_rev['revision_number']}"
                         f"(状态 {open_rev['status']}), 请先完成或取消该版次")

    seq = (scalar(conn, "SELECT COALESCE(max(revision_sequence),0)+1 FROM file_revision "
                        "WHERE design_file_id = %s", (df["id"],)) or 1)
    row = fetch_one(conn, """
        INSERT INTO file_revision (design_file_id, revision_number, revision_sequence,
                                   change_summary, prepared_by, created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, revision_number, revision_sequence, status, change_summary
    """, (df["id"], next_revision_number(seq), seq, change_summary,
          actor["user_id"], actor["user_id"], actor["user_id"]))
    audit.write(conn, action="REVISION_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="FILE_REVISION",
                object_id=str(row["id"]),
                object_code=f"{file_number} Rev.{row['revision_number']}",
                new_value={"change_summary": change_summary},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def get_revision(conn: psycopg.Connection, revision_id: str) -> dict | None:
    return fetch_one(conn, """
        SELECT fr.*, df.file_number, df.title_cn, df.file_type_code
          FROM file_revision fr JOIN design_file df ON df.id = fr.design_file_id
         WHERE fr.id = %s
    """, (revision_id,))


def submit_revision(conn: psycopg.Connection, revision_id: str, actor: dict) -> dict:
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] != "WORKING":
        raise ValueError(f"版次状态为 {rev['status']}, 只有 WORKING 可提交审核")
    if not attachments(conn, revision_id):
        raise ValueError("版次尚无任何附件, 不得提交审核")

    seq = scalar(conn, """
        SELECT COALESCE(max(substring(request_number from 9)::int), 0) + 1
          FROM approval_request WHERE request_number LIKE 'AR-2026-%'
    """) or 1
    req = fetch_one(conn, """
        INSERT INTO approval_request
            (request_number, request_type, object_type, object_id, object_code,
             title, requester_id)
        VALUES (%s,'FILE_REVISION_RELEASE','FILE_REVISION',%s,%s,%s,%s)
        RETURNING id, request_number
    """, (f"AR-2026-{seq:06d}", revision_id,
          f"{rev['file_number']} Rev.{rev['revision_number']}",
          f"发布 {rev['file_number']} Rev.{rev['revision_number']}", actor["user_id"]))
    execute(conn, """
        INSERT INTO approval_step (approval_request_id, step_order, step_name,
                                   required_role_code, is_final)
        VALUES (%s, 1, '版次批准', 'APPROVER', true)
    """, (req["id"],))
    execute(conn, """
        UPDATE file_revision SET status='IN_REVIEW', approval_request_id=%s,
               checked_by=%s, updated_by=%s WHERE id=%s
    """, (req["id"], actor["user_id"], actor["user_id"], revision_id))

    audit.write(conn, action="REVISION_SUBMIT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="FILE_REVISION",
                object_id=revision_id,
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                new_value={"approval_request": req["request_number"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return req


def release_revision(conn: psycopg.Connection, revision_id: str, comments: str,
                     actor: dict) -> dict:
    """批准并发布版次。

    发布后本版次内容即冻结(INV-007), 上一发布版次转为 SUPERSEDED。
    **不触碰任何 P/N 的 current_baseline_id** — INV-014。
    """
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] != "IN_REVIEW":
        raise ValueError(f"版次状态为 {rev['status']}, 只有 IN_REVIEW 可发布")

    # INV-025 由 trg_approval_separation 在此拦下自批
    execute(conn, """
        UPDATE approval_step SET decision='APPROVED', decided_by=%s, acted_at=now(),
               comments=%s WHERE approval_request_id=%s AND is_final
    """, (actor["user_id"], comments, rev["approval_request_id"]))
    execute(conn, "UPDATE approval_request SET status='APPROVED', closed_at=now() WHERE id=%s",
            (rev["approval_request_id"],))

    prev = fetch_one(conn, """
        SELECT id, revision_number FROM file_revision
         WHERE design_file_id=%s AND status='RELEASED'
    """, (rev["design_file_id"],))

    row = fetch_one(conn, """
        UPDATE file_revision
           SET status='RELEASED', approved_by=%s, released_at=now(),
               revision_date=COALESCE(revision_date, current_date), updated_by=%s
         WHERE id=%s
        RETURNING id, revision_number, status, released_at
    """, (actor["user_id"], actor["user_id"], revision_id))

    if prev is not None:
        execute(conn, "UPDATE file_revision SET status='SUPERSEDED', superseded_at=now() "
                      "WHERE id=%s", (prev["id"],))

    execute(conn, "UPDATE design_file SET current_released_revision_id=%s, updated_by=%s "
                  "WHERE id=%s", (revision_id, actor["user_id"], rev["design_file_id"]))

    audit.write(conn, action="REVISION_RELEASE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="FILE_REVISION",
                object_id=revision_id,
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                old_value={"status": "IN_REVIEW"},
                new_value={"status": "RELEASED",
                           "superseded": prev["revision_number"] if prev else None,
                           "note": "按 INV-014, 本次发布不改变任何 P/N 的当前技术状态"},
                reason=comments, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def cancel_revision(conn: psycopg.Connection, revision_id: str, reason: str,
                    actor: dict) -> None:
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] not in ("WORKING", "IN_REVIEW"):
        raise ValueError(f"版次状态为 {rev['status']}, 不可取消")
    execute(conn, "UPDATE file_revision SET status='CANCELLED', updated_by=%s WHERE id=%s",
            (actor["user_id"], revision_id))
    audit.write(conn, action="REVISION_CANCEL", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="FILE_REVISION",
                object_id=revision_id,
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                reason=reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))


# ---------------------------------------------------------------------
# 附件
# ---------------------------------------------------------------------
def attachments(conn: psycopg.Connection, revision_id: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT id, attachment_role, filename, storage_key, mime_type, size_bytes,
               sha256, integrity_status, integrity_checked_at, uploaded_at
          FROM revision_attachment WHERE file_revision_id = %s
         ORDER BY attachment_role, filename
    """, (revision_id,))


def upload_attachment(conn: psycopg.Connection, revision_id: str, *, role: str,
                      filename: str, mime_type: str, content: bytes,
                      actor: dict) -> dict:
    """为工作版次上传附件。已发布版次由 trg_revision_attachment_guard 拒绝。"""
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] not in ("WORKING", "IN_REVIEW"):
        raise ValueError(f"版次状态为 {rev['status']}, 不得追加或替换附件 (INV-007)。"
                         f"内容变化必须新建版次")

    key = storage.make_key(rev["file_number"], rev["revision_number"], role, filename)
    stored = storage.save(key, content)

    row = fetch_one(conn, """
        INSERT INTO revision_attachment
            (file_revision_id, attachment_role, filename, storage_key, mime_type,
             size_bytes, sha256, uploaded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, attachment_role, filename, storage_key, size_bytes, sha256
    """, (revision_id, role, filename, stored.storage_key, mime_type,
          stored.size_bytes, stored.sha256, actor["user_id"]))

    audit.write(conn, action="ATTACHMENT_UPLOAD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="REVISION_ATTACHMENT",
                object_id=str(row["id"]),
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                new_value={"role": role, "filename": filename,
                           "size_bytes": stored.size_bytes, "sha256": stored.sha256},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def delete_attachment(conn: psycopg.Connection, attachment_id: str, actor: dict, reason: str | None = None) -> None:
    row = fetch_one(conn, """
        SELECT ra.*, fr.status AS rev_status, df.file_number, fr.revision_number
          FROM revision_attachment ra
          JOIN file_revision fr ON fr.id = ra.file_revision_id
          JOIN design_file df ON df.id = fr.design_file_id
         WHERE ra.id = %s
    """, (attachment_id,))
    if row is None:
        raise LookupError("附件不存在")
    # 已发布版次的删除由数据库触发器拒绝; 这里先给出可读提示
    if row["rev_status"] in ("RELEASED", "SUPERSEDED"):
        raise ValueError(f"版次 Rev.{row['revision_number']} 已发布, 附件不得删除 (INV-007)")
    execute(conn, "DELETE FROM revision_attachment WHERE id = %s", (attachment_id,))
    # 物理文件保留: 数据库记录已删, 但物理文件留待运维按备份策略清理。
    # 立即删除物理文件会让"误删后能否恢复"完全取决于备份时点。
    audit.write(conn, action="ATTACHMENT_DELETE", user_id=str(actor["user_id"]),
                reason=reason,
                username=actor["username"], object_type="REVISION_ATTACHMENT",
                object_id=attachment_id,
                object_code=f"{row['file_number']} Rev.{row['revision_number']}",
                old_value={"filename": row["filename"], "sha256": row["sha256"],
                           "storage_key": row["storage_key"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


# ---------------------------------------------------------------------
# 完整性巡检 — AC-DATA-05 / AT-007 / DQ-FILE-001
# ---------------------------------------------------------------------
def check_integrity(conn: psycopg.Connection, *, revision_id: str | None = None,
                    actor: dict | None = None) -> dict:
    """重算物理文件摘要并与登记值比对。

    三种结果:
      OK       物理文件存在且摘要一致
      MISMATCH 文件存在但内容已变 —— 有人绕过系统直接改了存储上的文件
      MISSING  文件不见了

    MISMATCH 与 MISSING 都是 ERROR 级数据质量问题(DQ-FILE-001), 阻止相关基线发布。
    巡检结果回写 integrity_status 字段, 这也是已发布版次唯一允许被更新的字段 ——
    触发器 trg_revision_attachment_guard 对此开了口子, 否则巡检结果根本写不回去。
    """
    rows = fetch_all(conn, """
        SELECT ra.id, ra.storage_key, ra.sha256, ra.filename, ra.size_bytes,
               fr.status AS rev_status, fr.revision_number, df.file_number
          FROM revision_attachment ra
          JOIN file_revision fr ON fr.id = ra.file_revision_id
          JOIN design_file df ON df.id = fr.design_file_id
         WHERE (%s::uuid IS NULL OR ra.file_revision_id = %s)
         ORDER BY df.file_number, fr.revision_sequence
    """, (revision_id, revision_id))

    checked = {"OK": 0, "MISMATCH": 0, "MISSING": 0}
    problems: list[dict] = []

    for r in rows:
        actual = storage.compute_sha256(r["storage_key"])
        if actual is None:
            state = "MISSING"
        elif actual != r["sha256"]:
            state = "MISMATCH"
        else:
            state = "OK"
        checked[state] += 1

        execute(conn, """
            UPDATE revision_attachment
               SET integrity_status = %s, integrity_checked_at = now()
             WHERE id = %s
        """, (state, r["id"]))

        if state != "OK":
            problems.append({
                "attachment_id": str(r["id"]),
                "file_number": r["file_number"],
                "revision_number": r["revision_number"],
                "filename": r["filename"],
                "storage_key": r["storage_key"],
                "expected_sha256": r["sha256"],
                "actual_sha256": actual,
                "state": state,
            })
            _record_quality_issue(conn, r, state)

    if actor is not None:
        audit.write(conn, action="INTEGRITY_CHECK", user_id=str(actor["user_id"]),
                    username=actor["username"], object_type="REVISION_ATTACHMENT",
                    object_id=revision_id,
                    new_value={"checked": sum(checked.values()), **checked},
                    session_id=str(actor.get("session_id")),
                    client_ip=actor.get("client_ip"))

    return {"checked": sum(checked.values()), **checked, "problems": problems,
            "passed": checked["MISMATCH"] == 0 and checked["MISSING"] == 0}


def _record_quality_issue(conn: psycopg.Connection, row: dict, state: str) -> None:
    """登记数据质量问题, 使其进入待处置清单而不是只出现在一次巡检的返回值里。"""
    exists = scalar(conn, """
        SELECT EXISTS (SELECT 1 FROM data_quality_issue
                        WHERE rule_code='DQ-FILE-001' AND object_id=%s AND status='OPEN')
    """, (row["id"],))
    if exists:
        return
    execute(conn, """
        INSERT INTO data_quality_issue
            (rule_code, object_type, object_id, severity, message, status)
        VALUES ('DQ-FILE-001','REVISION_ATTACHMENT',%s,'ERROR',%s,'OPEN')
    """, (row["id"],
          f"{row['file_number']} Rev.{row['revision_number']} 的附件 "
          f"{row['filename']} 完整性异常: {state}"))


def open_integrity_issues(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """
        SELECT id, rule_code, object_type, object_id, severity, message,
               status, detected_at
          FROM data_quality_issue
         WHERE rule_code = 'DQ-FILE-001' AND status = 'OPEN'
         ORDER BY detected_at DESC
    """)


# ---------------------------------------------------------------------
# 设计定义关系 — INV-022 的数据基础
# ---------------------------------------------------------------------
def link_definition(conn: psycopg.Connection, object_code: str, file_number: str,
                    relation_type: str, note: str | None, actor: dict) -> dict:
    obj = fetch_one(conn, "SELECT id FROM design_object WHERE object_code=%s", (object_code,))
    if obj is None:
        raise LookupError(f"设计对象不存在: {object_code}")
    df = fetch_one(conn, "SELECT id FROM design_file WHERE file_number=%s", (file_number,))
    if df is None:
        raise LookupError(f"设计文件不存在: {file_number}")

    row = fetch_one(conn, """
        INSERT INTO design_definition_link
            (design_object_id, design_file_id, relation_type, applicability_note, created_by)
        VALUES (%s,%s,%s,%s,%s)
        RETURNING id, relation_type, is_active
    """, (obj["id"], df["id"], relation_type, note, actor["user_id"]))
    audit.write(conn, action="DEFINITION_LINK", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_DEFINITION_LINK",
                object_id=str(row["id"]), object_code=object_code,
                new_value={"file_number": file_number, "relation_type": relation_type},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def definitions_of(conn: psycopg.Connection, object_code: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT dl.id, dl.relation_type, dl.applicability_note, dl.is_active,
               df.file_number, df.title_cn, df.file_type_code,
               fr.revision_number AS current_released_revision
          FROM design_definition_link dl
          JOIN design_object d ON d.id = dl.design_object_id
          JOIN design_file df ON df.id = dl.design_file_id
          LEFT JOIN file_revision fr ON fr.id = df.current_released_revision_id
         WHERE d.object_code = %s
         ORDER BY dl.relation_type, df.file_number
    """, (object_code,))
