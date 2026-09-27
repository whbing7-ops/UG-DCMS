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

import io
import re
import zipfile

import psycopg

from .. import audit, storage
from ..db import execute, fetch_all, fetch_one, scalar
from ..rbac import Perm, has

# 生产/采购只见已发布(含已被取代)版次; 版次状态见 file_revision.status
PUBLISHED_STATUSES = ("RELEASED", "SUPERSEDED")


def access_for(user: dict | None) -> dict:
    """按角色得出资料访问范围。user 为 None(内部调用)时不设限。"""
    if user is None:
        return {"native": True, "unreleased": True}
    roles = list(user.get("roles") or [])
    return {"native": has(roles, Perm.DOWNLOAD_NATIVE), "unreleased": has(roles, Perm.READ_UNRELEASED)}


def attachment_allowed(role: str, revision_status: str, access: dict | None) -> bool:
    if access is None:
        return True
    if role == "PRIMARY_NATIVE" and not access["native"]:
        return False
    if revision_status not in PUBLISHED_STATUSES and not access["unreleased"]:
        return False
    return True

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


def page_files(conn: psycopg.Connection, file_type_code: str | None, q: str | None,
               page: int, page_size: int, status: str = "", stage: str = "") -> dict:
    """文件列表。status: ACTIVE/OBSOLETE; stage: RELEASED(有发布版次) / OPEN(有编制或审核中版次) / NONE(尚无发布版次)。"""
    like = f"%{q}%" if q else None
    args = (file_type_code, file_type_code, q, like, like, status, status, stage, stage, stage, stage)
    where = """WHERE (%s::text IS NULL OR df.file_type_code=%s)
      AND (%s::text IS NULL OR df.file_number ILIKE %s OR df.title_cn ILIKE %s)
      AND (%s='' OR df.status=%s)
      AND (%s='' OR (%s='RELEASED' AND df.current_released_revision_id IS NOT NULL)
                 OR (%s='NONE' AND df.current_released_revision_id IS NULL)
                 OR (%s='OPEN' AND EXISTS (SELECT 1 FROM file_revision o WHERE o.design_file_id=df.id
                                            AND o.status IN ('WORKING','IN_REVIEW'))))"""
    total = scalar(conn, f"SELECT count(*) FROM design_file df {where}", args) or 0
    items = fetch_all(conn, f"""SELECT df.id,df.file_number,df.file_type_code,df.title_cn,df.status,
      fr.revision_number AS current_released_revision, fr.released_at,
      (SELECT o.revision_number||':'||o.status FROM file_revision o WHERE o.design_file_id=df.id
          AND o.status IN ('WORKING','IN_REVIEW') LIMIT 1) AS open_revision,
      (SELECT count(*) FROM file_revision x WHERE x.design_file_id=df.id) AS revision_count
      FROM design_file df LEFT JOIN file_revision fr ON fr.id=df.current_released_revision_id
      {where} ORDER BY df.file_number LIMIT %s OFFSET %s""",
      args + (page_size, (page - 1) * page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def update_file(conn: psycopg.Connection, file_number: str, *, title_cn: str,
                title_en: str | None, actor: dict) -> dict:
    """修改文件名称。文件号与类型是身份的一部分, 不可改。"""
    old = get_file(conn, file_number)
    if old is None:
        raise LookupError(f"设计文件不存在: {file_number}")
    if old["status"] != "ACTIVE":
        raise ValueError("已作废的文件不可修改名称, 请先恢复")
    row = fetch_one(conn, """
        UPDATE design_file SET title_cn=%s, title_en=%s, updated_by=%s
         WHERE id=%s RETURNING id, file_number, title_cn, title_en, status
    """, (title_cn, title_en, actor["user_id"], old["id"]))
    audit.write(conn, action="FILE_UPDATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_FILE",
                object_id=str(old["id"]), object_code=file_number,
                old_value={"title_cn": old["title_cn"], "title_en": old["title_en"]},
                new_value={"title_cn": title_cn, "title_en": title_en},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def set_file_status(conn: psycopg.Connection, file_number: str, target: str,
                    reason: str, actor: dict) -> dict:
    """作废或恢复设计文件。

    作废只是不再允许出新版次, 已发布版次和已冻结基线不受影响(INV-014 同一思路)。
    仍是 Released 对象有效主设计定义的文件不得作废(INV-022), 否则该对象的
    下一次基线发布会被数据库拦下, 而错误要到那时才暴露。
    """
    if target not in ("ACTIVE", "OBSOLETE"):
        raise ValueError("目标状态无效")
    df = get_file(conn, file_number)
    if df is None:
        raise LookupError(f"设计文件不存在: {file_number}")
    if df["status"] == target:
        raise ValueError("文件已经是该状态")
    if target == "OBSOLETE":
        open_rev = fetch_one(conn, """SELECT revision_number, status FROM file_revision
            WHERE design_file_id=%s AND status IN ('WORKING','IN_REVIEW')""", (df["id"],))
        if open_rev:
            raise ValueError(f"存在未完成版次 Rev.{open_rev['revision_number']}"
                             f"({open_rev['status']}), 请先发布或取消")
        blocking = fetch_all(conn, """
            SELECT d.object_code FROM design_definition_link dl
              JOIN design_object d ON d.id=dl.design_object_id
             WHERE dl.design_file_id=%s AND dl.is_active
               AND dl.relation_type='PRIMARY_DEFINITION' AND d.lifecycle_status='RELEASED'
             ORDER BY d.object_code LIMIT 5""", (df["id"],))
        if blocking:
            raise ValueError("仍是已发布对象的主设计定义: "
                             + "、".join(b["object_code"] for b in blocking)
                             + "。请先为这些对象指定新的主设计定义")
    row = fetch_one(conn, "UPDATE design_file SET status=%s, updated_by=%s WHERE id=%s "
                          "RETURNING id, file_number, status", (target, actor["user_id"], df["id"]))
    audit.write(conn, action="FILE_OBSOLETE" if target == "OBSOLETE" else "FILE_REACTIVATE",
                user_id=str(actor["user_id"]), username=actor["username"],
                object_type="DESIGN_FILE", object_id=str(df["id"]), object_code=file_number,
                old_value={"status": df["status"]}, new_value={"status": target},
                reason=reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def file_where_used(conn: psycopg.Connection, file_number: str) -> dict:
    """文件被谁引用: 设计定义关系(逻辑关系) + 基线项(锁定到具体版次的硬引用)。"""
    df = get_file(conn, file_number)
    if df is None:
        raise LookupError(f"设计文件不存在: {file_number}")
    objects = fetch_all(conn, """
        SELECT dl.id AS link_id, dl.relation_type, dl.applicability_note, dl.is_active,
               d.object_code, d.display_name, d.object_type, d.lifecycle_status
          FROM design_definition_link dl JOIN design_object d ON d.id=dl.design_object_id
         WHERE dl.design_file_id=%s
         ORDER BY dl.is_active DESC, dl.relation_type, d.object_code LIMIT 500
    """, (df["id"],))
    baselines = fetch_all(conn, """
        SELECT pn.full_part_number, pn.formal_name_cn, b.baseline_code, b.status AS baseline_status,
               b.is_current, fr.revision_number, bi.item_role
          FROM baseline_item bi
          JOIN file_revision fr ON fr.id=bi.file_revision_id
          JOIN design_baseline b ON b.id=bi.design_baseline_id
          JOIN part_number pn ON pn.id=b.part_number_id
         WHERE fr.design_file_id=%s
         ORDER BY b.is_current DESC, pn.full_part_number, b.baseline_sequence DESC LIMIT 500
    """, (df["id"],))
    return {"file_number": file_number, "objects": objects, "baselines": baselines}


def file_impact(conn: psycopg.Connection, file_number: str, max_objects: int = 30) -> dict:
    """变更影响分析: 这份文件出新版次会波及谁。

    在 where-used 的基础上补两层: 关联对象被哪些上层组件使用(工作 BOM, 逐级向上),
    以及"当前基线锁定的是不是最新发布版次"。后者是 INV-014 的直接后果 —— 新版次发布
    不会自动改变任何 P/N 的技术状态, 落后的基线必须显式发布新基线才会跟随, 这里把它们列出来。
    """
    from . import bom as bom_svc
    usage = file_where_used(conn, file_number)
    df = get_file(conn, file_number)
    latest = df["current_released_revision"]

    objects = []
    active = [o for o in usage["objects"] if o["is_active"]]
    for o in active[:max_objects]:
        oid = scalar(conn, "SELECT id FROM design_object WHERE object_code=%s", (o["object_code"],))
        up = bom_svc.where_used(conn, str(oid), include_snapshots=False)["working"] if oid else []
        objects.append({**o, "upstream": [
            {"parent_object_code": u["parent_object_code"], "parent_name": u["parent_name"],
             "parent_status": u["parent_status"], "level": u["level"]} for u in up[:50]]})

    baselines = []
    for b in usage["baselines"]:
        baselines.append({**b, "behind": bool(latest) and b["revision_number"] != latest
                          and b["baseline_status"] == "RELEASED"})
    behind_current = [b for b in baselines if b["is_current"] and b["behind"]]
    parents = {u["parent_object_code"] for o in objects for u in o["upstream"]}
    return {
        "file_number": file_number, "latest_released_revision": latest,
        "objects": objects, "objects_truncated": len(active) > max_objects,
        "baselines": baselines,
        "summary": {"objects": len(active), "upstream_parents": len(parents),
                    "baselines": len(baselines), "current_baselines_behind": len(behind_current)},
        "note": ("新版次发布不会自动改变任何件号的当前技术状态(INV-014)。"
                 "落后的当前基线需要显式发布新基线才会采用新版次。"),
    }


_UNIQUE_ROLES = {"PRIMARY_NATIVE", "RELEASED_PDF"}


def compare_revisions(conn: psycopg.Connection, file_number: str, rev_a: str, rev_b: str,
                      access: dict | None = None) -> dict:
    """比较同一文件两个版次的附件差异(按 SHA-256 判定内容是否变化)。"""
    df = get_file(conn, file_number)
    if df is None:
        raise LookupError(f"设计文件不存在: {file_number}")
    revs = {}
    for rid in (rev_a, rev_b):
        r = fetch_one(conn, """SELECT id, revision_number, status, change_summary, released_at
            FROM file_revision WHERE id=%s AND design_file_id=%s""", (rid, df["id"]))
        if r is None or (access is not None and not access["unreleased"]
                         and r["status"] not in PUBLISHED_STATUSES):
            raise LookupError("版次不存在或不属于该文件")
        r["attachments"] = attachments(conn, rid, access)
        revs[rid] = r

    def key(a):
        if a["attachment_role"] in _UNIQUE_ROLES:
            return (a["attachment_role"], "")
        return (a["attachment_role"], a["filename"])

    ma = {key(a): a for a in revs[rev_a]["attachments"]}
    mb = {key(a): a for a in revs[rev_b]["attachments"]}

    def brief(x):
        return None if x is None else {"filename": x["filename"], "size_bytes": x["size_bytes"],
                                       "sha256": x["sha256"]}
    rows = []
    for k in sorted(set(ma) | set(mb)):
        a, b = ma.get(k), mb.get(k)
        state = ("ADDED" if a is None else "REMOVED" if b is None
                 else "SAME" if a["sha256"] == b["sha256"] else "CHANGED")
        rows.append({"role": k[0], "state": state, "a": brief(a), "b": brief(b)})

    def head(r):
        return {c: r[c] for c in ("id", "revision_number", "status", "change_summary", "released_at")}
    return {"a": head(revs[rev_a]), "b": head(revs[rev_b]), "rows": rows,
            "changed": sum(1 for r in rows if r["state"] != "SAME")}


# ---------------------------------------------------------------------
# 版次
# ---------------------------------------------------------------------
def list_revisions(conn: psycopg.Connection, file_id: str, access: dict | None = None) -> list[dict]:
    rows = _list_revisions(conn, file_id)
    if access is not None and not access["unreleased"]:
        rows = [r for r in rows if r["status"] in PUBLISHED_STATUSES]
    return rows


def _list_revisions(conn: psycopg.Connection, file_id: str) -> list[dict]:
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
        SELECT fr.*, df.file_number, df.title_cn, df.file_type_code,
               aps.assignee_user_id AS approval_assignee_user_id,
               aps.step_order AS approval_step_order, aps.step_name AS approval_step_name,
               aps.is_final AS approval_step_final
          FROM file_revision fr JOIN design_file df ON df.id = fr.design_file_id
          LEFT JOIN current_pending_step aps ON aps.approval_request_id=fr.approval_request_id
         WHERE fr.id = %s
    """, (revision_id,))


# ---------------------------------------------------------------------
# 三级签署: 编制 → 审核 → 批准
# ---------------------------------------------------------------------
LEVEL_MEANING = {"PREPARE": "编制", "REVIEW": "审核", "APPROVE": "批准"}


def content_digest(conn: psycopg.Connection, revision_id: str) -> str:
    """被签署内容的摘要: 版次、变更说明、全部附件(用途/文件名/SHA-256)。

    签署记录里存这个摘要, 使"签的是哪一份内容"可以事后核对; 版次在审核中内容已冻结,
    所以三级签署看到的应是同一摘要。
    """
    import hashlib
    import json
    rev = fetch_one(conn, "SELECT id, revision_number, change_summary FROM file_revision WHERE id=%s", (revision_id,))
    atts = fetch_all(conn, """SELECT attachment_role, filename, sha256 FROM revision_attachment
                               WHERE file_revision_id=%s ORDER BY attachment_role, filename, sha256""", (revision_id,))
    payload = {"revision_id": str(rev["id"]), "revision_number": rev["revision_number"],
               "change_summary": rev["change_summary"] or "",
               "attachments": [[a["attachment_role"], a["filename"], a["sha256"]] for a in atts]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _record_signature(conn: psycopg.Connection, rev: dict, request_id: str, round_no: int, level: str,
                      signer: dict, comments: str | None, method: str = "PASSWORD_REENTRY") -> None:
    """写一条不可变签署记录。姓名与账户取签署当下的值。"""
    who = fetch_one(conn, "SELECT username, full_name FROM app_user WHERE id=%s", (signer["user_id"],))
    execute(conn, """
        INSERT INTO signature_record (object_type, object_id, approval_request_id, submission_round, level, meaning,
                                      user_id, username, full_name, comments, content_sha256, auth_method, client_ip)
        VALUES ('FILE_REVISION',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (rev["id"], request_id, round_no, level, LEVEL_MEANING[level], signer["user_id"], who["username"],
          who["full_name"], comments, content_digest(conn, str(rev["id"])), method, signer.get("client_ip")))


def signoff(conn: psycopg.Connection, revision_id: str) -> list[dict]:
    """三级签署栏: 当前(或最近一轮)编制/审核/批准各是谁、何时、什么状态。"""
    rev = get_revision(conn, revision_id)
    if rev is None or rev.get("approval_request_id") is None:
        # 编制中或已取消: 若有历史签署, 取最近一轮
        rows = fetch_all(conn, """SELECT * FROM signature_record WHERE object_type='FILE_REVISION' AND object_id=%s
                                   ORDER BY submission_round DESC, signed_at""", (revision_id,))
        if not rows:
            return []
        last = rows[0]["submission_round"]
        done = {r["level"]: r for r in rows if r["submission_round"] == last}
        return [_level_row(lv, done.get(lv), None) for lv in ("PREPARE", "REVIEW", "APPROVE")]
    req = fetch_one(conn, "SELECT submission_round FROM approval_request WHERE id=%s", (rev["approval_request_id"],))
    rows = fetch_all(conn, """SELECT * FROM signature_record WHERE object_type='FILE_REVISION' AND object_id=%s
                               AND submission_round=%s""", (revision_id, req["submission_round"]))
    done = {r["level"]: r for r in rows}
    steps = fetch_all(conn, """SELECT s.step_order, s.step_name, s.is_final, s.decision, s.assignee_user_id,
                                      au.full_name AS assignee_name
                                 FROM current_approval_step s LEFT JOIN app_user au ON au.id=s.assignee_user_id
                                WHERE s.approval_request_id=%s ORDER BY s.step_order""", (rev["approval_request_id"],))
    pending = {"REVIEW": None, "APPROVE": None}
    if len(steps) >= 2:
        pending["REVIEW"], pending["APPROVE"] = steps[0], steps[-1]
    elif len(steps) == 1:
        pending["APPROVE"] = steps[0]                      # 旧流程: 只有批准一级
    return [_level_row("PREPARE", done.get("PREPARE"), None),
            _level_row("REVIEW", done.get("REVIEW"), pending["REVIEW"], legacy=len(steps) == 1),
            _level_row("APPROVE", done.get("APPROVE"), pending["APPROVE"])]


def _level_row(level: str, sig: dict | None, step: dict | None, legacy: bool = False) -> dict:
    if sig:
        state = "SIGNED"
    elif legacy:
        state = "NOT_APPLICABLE"                            # 旧单级流程没有审核这一级, 不伪造
    elif step and step["decision"] == "PENDING":
        state = "PENDING"
    elif step:
        state = step["decision"]
    else:
        state = "NONE"
    return {"level": level, "meaning": LEVEL_MEANING[level], "state": state,
            "signer_name": sig["full_name"] if sig else (step["assignee_name"] if step else None),
            "signer_username": sig["username"] if sig else None,
            "signed_at": sig["signed_at"] if sig else None, "comments": sig["comments"] if sig else None,
            "auth_method": sig["auth_method"] if sig else None,
            "content_sha256": sig["content_sha256"] if sig else None}


def signer_candidates(conn: psycopg.Connection, revision_id: str, level: str, actor: dict,
                      exclude: list[str] | None = None) -> list[dict]:
    """提交时可选的审核人/批准人: 已授权(此刻有效)且不是编制人本人。"""
    from . import signers
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    return signers.candidates(conn, level, rev["file_type_code"], [str(actor["user_id"])] + list(exclude or []))


def submit_revision(conn: psycopg.Connection, revision_id: str, approver_user_id: str,
                    actor: dict, reviewer_user_id: str | None = None, password: str | None = None) -> dict:
    """编制人提交: 指定审核人和批准人, 并以口令确认完成"编制"签署。"""
    from . import approvals, auth, drafts, signers
    drafts.require_editable(conn,'FILE_REVISION',revision_id,actor)
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] != "WORKING":
        raise ValueError(f"版次状态为 {rev['status']}, 只有 WORKING 可提交审核")
    if not attachments(conn, revision_id):
        raise ValueError("版次尚无任何附件, 不得提交审核")
    if not reviewer_user_id or not approver_user_id:
        raise ValueError("必须指定审核人和批准人")
    ids = {str(actor["user_id"]), str(reviewer_user_id), str(approver_user_id)}
    if len(ids) != 3:
        raise ValueError("编制人、审核人、批准人必须是三个不同的人")
    for uid, level in ((reviewer_user_id, "REVIEW"), (approver_user_id, "APPROVE")):
        if not signers.is_authorized(conn, str(uid), level, rev["file_type_code"]):
            raise ValueError(f"所选{signers.LEVELS[level]}人不在有权签署人清单内(或授权已过期/撤销), 请重新选择")
    auth.verify_signature_password(conn, actor, password)

    req = approvals.open_request(conn,kind='FILE_REVISION',oid=revision_id,request_type='FILE_REVISION_RELEASE',code=f"{rev['file_number']} Rev.{rev['revision_number']}",title=f"发布 {rev['file_number']} Rev.{rev['revision_number']}",actor=actor)
    for order, name, uid, final in ((1, "审核", reviewer_user_id, False), (2, "批准", approver_user_id, True)):
        execute(conn, """
            INSERT INTO approval_step (approval_request_id, step_order, step_name, assignee_user_id, is_final)
            VALUES (%s, %s, %s, %s, %s)
        """, (req["id"], order, name, uid, final))
    execute(conn, """
        UPDATE file_revision SET status='IN_REVIEW', approval_request_id=%s, prepared_by=%s,
               checked_by=NULL, approved_by=NULL, updated_by=%s WHERE id=%s
    """, (req["id"], actor["user_id"], actor["user_id"], revision_id))
    _record_signature(conn, rev, str(req["id"]), req["submission_round"], "PREPARE", actor, None)

    names = {str(r["id"]): r["username"] for r in fetch_all(conn, "SELECT id, username FROM app_user WHERE id = ANY(%s::uuid[])",
                                                            ([str(reviewer_user_id), str(approver_user_id)],))}
    audit.write(conn, action="REVISION_SUBMIT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="FILE_REVISION",
                object_id=revision_id,
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                new_value={"approval_request": req["request_number"], "signature": "PREPARE",
                           "reviewer": names[str(reviewer_user_id)], "approver": names[str(approver_user_id)]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return req


def _current_steps(conn: psycopg.Connection, request_id: str) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM current_approval_step WHERE approval_request_id=%s ORDER BY step_order",
                     (request_id,))


def review_revision(conn: psycopg.Connection, revision_id: str, comments: str, password: str | None,
                    actor: dict) -> dict:
    """审核人签署"审核": 第一级通过, 之后才轮到批准人。"""
    from . import approvals, auth, drafts, signers
    drafts.lock_object(conn,'FILE_REVISION',revision_id)
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] != "IN_REVIEW":
        raise ValueError(f"版次状态为 {rev['status']}, 只有 IN_REVIEW 可审核")
    approvals.require_assignee(conn, str(rev["approval_request_id"]), str(actor["user_id"]))
    steps = _current_steps(conn, str(rev["approval_request_id"]))
    step = next((s for s in steps if s["decision"] == "PENDING"), None)
    if step is None or step["is_final"] or len(steps) < 2:
        raise ValueError("当前不是审核步骤")
    if not signers.is_authorized(conn, str(actor["user_id"]), "REVIEW", rev["file_type_code"]):
        raise ValueError("您不在该类文件的有权审核人清单内(或授权已过期/撤销), 不能签署")
    auth.verify_signature_password(conn, actor, password)

    req = fetch_one(conn, "SELECT submission_round FROM approval_request WHERE id=%s", (rev["approval_request_id"],))
    execute(conn, """UPDATE current_approval_step SET decision='APPROVED', decided_by=%s, acted_at=now(), comments=%s
                      WHERE id=%s""", (actor["user_id"], comments, step["id"]))
    execute(conn, "UPDATE file_revision SET checked_by=%s, updated_by=%s WHERE id=%s",
            (actor["user_id"], actor["user_id"], revision_id))
    _record_signature(conn, rev, str(rev["approval_request_id"]), req["submission_round"], "REVIEW", actor, comments)
    audit.write(conn, action="REVISION_REVIEW", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="FILE_REVISION", object_id=revision_id,
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                old_value={"status": "IN_REVIEW", "step": "审核"}, new_value={"status": "IN_REVIEW", "next": "批准", "signature": "REVIEW"},
                reason=comments, session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return {"status": "IN_REVIEW", "next_step": "批准"}


def _finalize_release(conn: psycopg.Connection, rev: dict, comments: str, actor: dict) -> dict:
    """发布收尾: 冻结版次、取代旧发布版次、更新文件当前版次。**不触碰任何 P/N 的 current_baseline_id**(INV-014)。"""
    execute(conn, """
        UPDATE current_approval_step SET decision='APPROVED', decided_by=%s, acted_at=now(),
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
    """, (actor["user_id"], actor["user_id"], rev["id"]))
    if prev is not None:
        execute(conn, "UPDATE file_revision SET status='SUPERSEDED', superseded_at=now() "
                      "WHERE id=%s", (prev["id"],))
    execute(conn, "UPDATE design_file SET current_released_revision_id=%s, updated_by=%s "
                  "WHERE id=%s", (rev["id"], actor["user_id"], rev["design_file_id"]))
    audit.write(conn, action="REVISION_RELEASE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="FILE_REVISION",
                object_id=str(rev["id"]),
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                old_value={"status": "IN_REVIEW"},
                new_value={"status": "RELEASED",
                           "superseded": prev["revision_number"] if prev else None,
                           "signature": "APPROVE",
                           "note": "按 INV-014, 本次发布不改变任何 P/N 的当前技术状态"},
                reason=comments, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def release_revision(conn: psycopg.Connection, revision_id: str, comments: str,
                     actor: dict, password: str | None = None) -> dict:
    """批准人签署"批准"并发布版次。

    三级流程里必须审核已通过; 旧的单级在途申请(升级前提交、只有批准一步)仍可批准,
    但同样需要口令确认。发布后内容冻结(INV-007), 上一发布版次转为 SUPERSEDED。
    """
    from . import approvals, auth, drafts, signers
    drafts.lock_object(conn,'FILE_REVISION',revision_id)
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] != "IN_REVIEW":
        raise ValueError(f"版次状态为 {rev['status']}, 只有 IN_REVIEW 可发布")
    approvals.require_assignee(conn, str(rev["approval_request_id"]), str(actor["user_id"]))
    steps = _current_steps(conn, str(rev["approval_request_id"]))
    legacy = len(steps) == 1
    pending = next((s for s in steps if s["decision"] == "PENDING"), None)
    if pending is None or not pending["is_final"]:
        raise ValueError("审核尚未通过, 不得批准发布" if not legacy else "没有待批准的步骤")
    if not legacy and not signers.is_authorized(conn, str(actor["user_id"]), "APPROVE", rev["file_type_code"]):
        raise ValueError("您不在该类文件的有权批准人清单内(或授权已过期/撤销), 不能签署")
    auth.verify_signature_password(conn, actor, password)

    req = fetch_one(conn, "SELECT submission_round FROM approval_request WHERE id=%s", (rev["approval_request_id"],))
    row = _finalize_release(conn, rev, comments, actor)     # INV-025/026 由数据库触发器在此拦下
    _record_signature(conn, rev, str(rev["approval_request_id"]), req["submission_round"], "APPROVE", actor, comments)
    return row


def simulate_release(conn: psycopg.Connection, revision_id: str, author: dict, reviewer: dict,
                     approver: dict, notice: str) -> dict:
    """仅供模拟数据导入使用: 走完整的三级步骤, 但不校验口令和授权清单。

    模拟身份没有口令(导入后即停用), 其签署记录的 auth_method 标为 SIMULATION,
    在任何界面和报表里都能与真人电子签名区分, 不会被误认为真实签署。
    """
    from . import approvals
    rev = get_revision(conn, revision_id)
    if rev["status"] != "WORKING" or not attachments(conn, revision_id):
        raise ValueError("模拟发布要求 WORKING 版次且带附件")
    req = approvals.open_request(conn, kind='FILE_REVISION', oid=revision_id, request_type='FILE_REVISION_RELEASE',
                                 code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                                 title=f"发布 {rev['file_number']} Rev.{rev['revision_number']}", actor=author)
    for order, name, who, final in ((1, "审核", reviewer, False), (2, "批准", approver, True)):
        execute(conn, """INSERT INTO approval_step (approval_request_id, step_order, step_name, assignee_user_id, is_final)
                         VALUES (%s,%s,%s,%s,%s)""", (req["id"], order, name, who["user_id"], final))
    execute(conn, "UPDATE file_revision SET status='IN_REVIEW', approval_request_id=%s, prepared_by=%s WHERE id=%s",
            (req["id"], author["user_id"], revision_id))
    rnd = req["submission_round"]
    _record_signature(conn, rev, str(req["id"]), rnd, "PREPARE", author, None, "SIMULATION")
    execute(conn, """UPDATE current_approval_step SET decision='APPROVED', decided_by=%s, acted_at=now(), comments=%s
                      WHERE approval_request_id=%s AND step_order=1""", (reviewer["user_id"], notice, req["id"]))
    execute(conn, "UPDATE file_revision SET checked_by=%s WHERE id=%s", (reviewer["user_id"], revision_id))
    _record_signature(conn, rev, str(req["id"]), rnd, "REVIEW", reviewer, notice, "SIMULATION")
    rev = get_revision(conn, revision_id)
    row = _finalize_release(conn, rev, notice, approver)
    _record_signature(conn, rev, str(req["id"]), rnd, "APPROVE", approver, notice, "SIMULATION")
    return req


def cancel_revision(conn: psycopg.Connection, revision_id: str, reason: str,
                    actor: dict) -> None:
    from . import drafts
    drafts.require_editable(conn,'FILE_REVISION',revision_id,actor)
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] not in ("WORKING", "IN_REVIEW"):
        raise ValueError(f"版次状态为 {rev['status']}, 不可取消")
    if rev.get("approval_request_id"):
        execute(conn,"UPDATE approval_request SET status='CANCELLED',closed_at=now() WHERE id=%s AND status='PENDING'",(rev["approval_request_id"],))
    execute(conn, "UPDATE file_revision SET status='CANCELLED', approval_request_id=NULL, updated_by=%s WHERE id=%s",
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
def attachments(conn: psycopg.Connection, revision_id: str, access: dict | None = None) -> list[dict]:
    rows = fetch_all(conn, """
        SELECT id, attachment_role, filename, storage_key, mime_type, size_bytes,
               sha256, integrity_status, integrity_checked_at, uploaded_at
          FROM revision_attachment WHERE file_revision_id = %s
         ORDER BY attachment_role, filename
    """, (revision_id,))
    if access is None:
        return rows
    status = scalar(conn, "SELECT status FROM file_revision WHERE id=%s", (revision_id,))
    return [a for a in rows if attachment_allowed(a["attachment_role"], status, access)]


def upload_attachment(conn: psycopg.Connection, revision_id: str, *, role: str,
                      filename: str, mime_type: str, content: bytes,
                      actor: dict) -> dict:
    """为工作版次上传附件。已发布版次由 trg_revision_attachment_guard 拒绝。"""
    from . import drafts
    drafts.require_editable(conn,'FILE_REVISION',revision_id,actor)
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise LookupError("版次不存在")
    if rev["status"] != "WORKING":
        raise ValueError(f"版次状态为 {rev['status']}, 不得追加或替换附件 (INV-007)。"
                         f"内容变化必须新建版次")

    key = storage.make_key(rev["file_number"], rev["revision_number"], role, filename)
    stored = storage.save(key, content)
    search_text = extract_attachment_text(filename, mime_type, content)

    row = fetch_one(conn, """
        INSERT INTO revision_attachment
            (file_revision_id, attachment_role, filename, storage_key, mime_type,
             size_bytes, sha256, search_text, uploaded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, attachment_role, filename, storage_key, size_bytes, sha256
    """, (revision_id, role, filename, stored.storage_key, mime_type,
          stored.size_bytes, stored.sha256, search_text, actor["user_id"]))

    audit.write(conn, action="ATTACHMENT_UPLOAD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="REVISION_ATTACHMENT",
                object_id=str(row["id"]),
                object_code=f"{rev['file_number']} Rev.{rev['revision_number']}",
                new_value={"role": role, "filename": filename,
                           "size_bytes": stored.size_bytes, "sha256": stored.sha256},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def extract_attachment_text(filename: str, mime_type: str, content: bytes) -> str:
    """提取可检索正文；失败只降级为元数据检索，不阻断附件上传。"""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    text = ""
    try:
        if ext in {"txt", "csv", "tsv", "md", "json", "xml", "html", "htm"}:
            text = content.decode("utf-8", errors="ignore")
        elif ext in {"docx", "xlsx", "pptx"}:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                chunks = []
                for name in zf.namelist():
                    if name.endswith(".xml") and any(p in name for p in
                        ("word/", "xl/sharedStrings", "xl/worksheets", "ppt/slides", "docProps/")):
                        raw = zf.read(name).decode("utf-8", errors="ignore")
                        chunks.append(" ".join(re.findall(r">([^<>]+)<", raw)))
                text = " ".join(chunks)
        elif ext == "pdf":
            try:
                from pypdf import PdfReader
                text = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(content)).pages)
            except Exception:
                text = ""
    except Exception:
        text = ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2_000_000]


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
    from . import drafts
    drafts.require_editable(conn,'FILE_REVISION',str(row['file_revision_id']),actor)
    # 已发布版次的删除由数据库触发器拒绝; 这里先给出可读提示
    if row["rev_status"] != "WORKING":
        raise ValueError(f"版次状态为 {row['rev_status']}, 附件不得删除；退回后方可修改")
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
         LIMIT 200
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
        ON CONFLICT (design_object_id, design_file_id, relation_type) DO UPDATE
           SET is_active=true, deactivated_at=NULL, deactivated_by=NULL,
               applicability_note=EXCLUDED.applicability_note
         WHERE design_definition_link.is_active = false
        RETURNING id, relation_type, is_active
    """, (obj["id"], df["id"], relation_type, note, actor["user_id"]))
    if row is None:
        raise ValueError("该关联已存在")
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


def design_materials(conn, q, file_type_code, revision_status, current_only, page, page_size,
                     attachment_role='', access=None):
    # One row per registered design attachment; software packages are separate objects.
    q=q.strip()
    like='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    hide_native = access is not None and not access["native"]
    published_only = access is not None and not access["unreleased"]
    args=(q,like,like,like,like,file_type_code,file_type_code,revision_status,revision_status,
          attachment_role,attachment_role,current_only,hide_native,published_only)
    source="""FROM revision_attachment a
      JOIN file_revision fr ON fr.id=a.file_revision_id
      JOIN design_file df ON df.id=fr.design_file_id
      JOIN file_type ft ON ft.code=df.file_type_code
      WHERE (%s='' OR a.filename ILIKE %s OR df.file_number ILIKE %s OR df.title_cn ILIKE %s OR df.title_en ILIKE %s)
        AND (%s='' OR df.file_type_code=%s) AND (%s='' OR fr.status=%s)
        AND (%s='' OR a.attachment_role=%s)
        AND (NOT %s OR (df.current_released_revision_id=fr.id AND fr.status='RELEASED' AND df.status='ACTIVE'))
        AND (NOT %s OR a.attachment_role<>'PRIMARY_NATIVE')
        AND (NOT %s OR fr.status IN ('RELEASED','SUPERSEDED'))"""
    total=scalar(conn,'SELECT count(*) '+source,args)
    rows=fetch_all(conn,"""SELECT a.id,a.filename,a.attachment_role,a.size_bytes,a.uploaded_at,
      fr.id AS revision_id,fr.revision_number,fr.status AS revision_status,
      df.file_number,df.title_cn,df.file_type_code,df.status AS file_status,ft.name_cn AS file_type_name,
      COALESCE(df.current_released_revision_id=fr.id AND fr.status='RELEASED',false) AS is_current
      """+source+' ORDER BY a.uploaded_at DESC,a.id LIMIT %s OFFSET %s',args+(page_size,(page-1)*page_size))
    return {'items':rows,'total':total,'page':page,'page_size':page_size}


def unlink_definition(conn: psycopg.Connection, link_id: str, reason: str, actor: dict) -> None:
    """解除设计定义关系(软停用, 保留历史)。Released 对象的主设计定义不得直接解除(INV-022)。"""
    row = fetch_one(conn, """
        SELECT dl.id, dl.relation_type, dl.is_active, d.object_code, d.lifecycle_status, df.file_number
          FROM design_definition_link dl
          JOIN design_object d ON d.id=dl.design_object_id
          JOIN design_file df ON df.id=dl.design_file_id
         WHERE dl.id=%s""", (link_id,))
    if row is None:
        raise LookupError("关联不存在")
    if not row["is_active"]:
        raise ValueError("该关联已解除")
    if row["relation_type"] == "PRIMARY_DEFINITION" and row["lifecycle_status"] == "RELEASED":
        raise ValueError(f"{row['object_code']} 已发布, 其主设计定义不得直接解除; "
                         "请先关联新的主设计定义并走变更流程 (INV-022)")
    execute(conn, """UPDATE design_definition_link SET is_active=false, deactivated_at=now(),
                     deactivated_by=%s WHERE id=%s""", (actor["user_id"], link_id))
    audit.write(conn, action="DEFINITION_UNLINK", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DESIGN_DEFINITION_LINK",
                object_id=link_id, object_code=row["object_code"],
                old_value={"file_number": row["file_number"], "relation_type": row["relation_type"]},
                reason=reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
