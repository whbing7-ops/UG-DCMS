"""设计族与 Dash P/N — SRS-FAM-001~003 / SRS-PN-001~003 / AC-FAM-01 / AT-001。

设计族建立流程(对应 UI §4/§5):
  1. 选一级类别 → 二级分类 → 核心实体词 → 0~2 个稳定限定词, 系统生成名称
  2. **相似设计族检索** — 必须执行且留痕(AC-FAM-01)
  3. 作出复用既有族 / 新建族的决定, 新建须写理由
  4. 提交审批 → 批准后分配基本图号并生效

第 2、3 步不是走过场。设计族一旦发号即长期存在(基本图号永不复用), 建错族的代价远高于
多花五分钟检索。因此"未检索"在数据库层就被 ck_family_similar_check 拒绝, 应用层不提供
任何绕过入口。
"""
from __future__ import annotations

import json

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar
from . import naming, numbering


# ---------------------------------------------------------------------
# 相似设计族检索 — AC-FAM-01
# ---------------------------------------------------------------------
def similar_search(conn: psycopg.Connection, primary_class_code: str,
                   physical_class_id: str, core_term_id: str,
                   qualifier_ids: list[str] | None = None) -> list[dict]:
    """按"同核心词 / 同二级分类 / 同主功能"三个维度找候选复用族。

    排序刻意把核心词相同排在最前 —— 核心词相同意味着是同一类工程实体, 这是最强的
    复用信号; 二级分类相同只说明形态接近, 未必是同一种东西。
    """
    qualifier_ids = qualifier_ids or []
    return fetch_all(conn, """
        SELECT f.id, f.basic_drawing_number, f.family_name_cn, f.family_name_en,
               f.status, f.family_definition, f.allowed_variation, f.excluded_variation,
               pc.code AS physical_class_code, pc.name_cn AS physical_class_name,
               ct.code AS core_term_code, ct.name_cn AS core_term_name,
               (SELECT count(*) FROM part_number pn
                 WHERE pn.basic_drawing_family_id = f.id) AS dash_count,
               (ct.id = %s)                AS same_core_term,
               (f.physical_class_id = %s)  AS same_physical_class,
               CASE WHEN ct.id = %s THEN 100 ELSE 0 END
             + CASE WHEN f.physical_class_id = %s THEN 50 ELSE 0 END
             + CASE WHEN f.qualifier_1_id = ANY(%s) THEN 20 ELSE 0 END
             + CASE WHEN f.qualifier_2_id = ANY(%s) THEN 10 ELSE 0 END AS match_score
          FROM basic_drawing_family f
          JOIN physical_class pc ON pc.id = f.physical_class_id
          JOIN naming_core_term ct ON ct.id = f.core_term_id
         WHERE f.primary_class_code = %s
           AND f.status IN ('PENDING', 'ACTIVE')
           AND (ct.id = %s OR f.physical_class_id = %s)
         ORDER BY match_score DESC, f.basic_drawing_number
         LIMIT 50
    """, (core_term_id, physical_class_id, core_term_id, physical_class_id,
          qualifier_ids, qualifier_ids, primary_class_code,
          core_term_id, physical_class_id))


# ---------------------------------------------------------------------
# 设计族
# ---------------------------------------------------------------------
def create_family(conn: psycopg.Connection, *, primary_class_code: str,
                  physical_class_id: str, object_level_code: str,
                  core_term_id: str, qualifier_1_id: str | None,
                  qualifier_2_id: str | None, primary_function_id: str,
                  family_definition: str, allowed_variation: str,
                  excluded_variation: str, new_family_reason: str,
                  classification_note: str | None, actor: dict) -> dict:
    """建立 PENDING 状态的设计族。

    此时**不分配基本图号** —— 号码在批准后才发, 否则一个被否决的族会永久占掉一个
    基本图号(INV-004 使其无法回收)。
    """
    similar = similar_search(conn, primary_class_code, physical_class_id,
                             core_term_id, [q for q in (qualifier_1_id, qualifier_2_id) if q])

    row = fetch_one(conn, """
        INSERT INTO basic_drawing_family
            (basic_drawing_number, primary_class_code, physical_class_id, object_level_code,
             family_name_cn, family_name_en, core_term_id, qualifier_1_id, qualifier_2_id,
             primary_function_id, family_definition, allowed_variation, excluded_variation,
             classification_note, status, similar_check_at, similar_check_result,
             reuse_decision, new_family_reason, created_by, updated_by)
        VALUES (%s, %s, %s, %s, '(待生成)', '(pending)', %s, %s, %s, %s, %s, %s, %s, %s,
                'PENDING', now(), %s::jsonb, 'NEW_FAMILY', %s, %s, %s)
        RETURNING id, basic_drawing_number, family_name_cn, family_name_en, status
    """, (
        # PENDING 阶段用临时占位号, 批准时替换为正式基本图号
        f"PENDING-{scalar(conn, 'SELECT gen_random_uuid()')}"[:32],
        primary_class_code, physical_class_id, object_level_code,
        core_term_id, qualifier_1_id, qualifier_2_id, primary_function_id,
        family_definition, allowed_variation, excluded_variation, classification_note,
        json.dumps({"searched_at": "on_create", "candidates": [
            {"basic_drawing_number": c["basic_drawing_number"],
             "family_name_cn": c["family_name_cn"],
             "match_score": c["match_score"]} for c in similar[:20]]},
            ensure_ascii=False, default=str),
        new_family_reason, actor["user_id"], actor["user_id"]))

    audit.write(conn, action="FAMILY_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="BASIC_DRAWING_FAMILY",
                object_id=str(row["id"]), object_code=row["family_name_cn"],
                new_value={"family_name_cn": row["family_name_cn"],
                           "primary_class_code": primary_class_code,
                           "similar_candidates": len(similar),
                           "reuse_decision": "NEW_FAMILY",
                           "new_family_reason": new_family_reason},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    row["similar_candidates"] = similar
    return row


def submit_family(conn: psycopg.Connection, family_id: str, approver_user_id: str,
                  actor: dict) -> dict:
    """提交设计族审批, 建立审批申请与最终批准步骤。

    走 approval_request / approval_step 而非直接置为 ACTIVE, 是为了让 INV-025
    (申请人不得作为最终批准人)的数据库触发器真正参与进来 —— 权限分离若只写在
    应用层, 绕过应用直连数据库就失效了。
    """
    fam = get_family(conn, family_id)
    if fam is None:
        raise LookupError("设计族不存在")
    if fam["status"] != "PENDING":
        raise ValueError(f"设计族状态为 {fam['status']}, 只有 PENDING 可提交审批")
    if fam.get("approval_request_id"):
        raise ValueError("该设计族已有审批申请，不得重复提交")
    from . import approvals
    approver = approvals.require_approver(conn, approver_user_id, actor["user_id"])

    request_number = approvals.next_request_number(conn)
    req = fetch_one(conn, """
        INSERT INTO approval_request
            (request_number, request_type, object_type, object_id, object_code,
             title, requester_id)
        VALUES (%s, 'BASIC_DRAWING_NUMBER', 'BASIC_DRAWING_FAMILY', %s, %s, %s, %s)
        RETURNING id, request_number, status
    """, (request_number, family_id, fam["family_name_cn"],
          f"新建设计族: {fam['family_name_cn']}", actor["user_id"]))

    execute(conn, """
        INSERT INTO approval_step
            (approval_request_id, step_order, step_name, required_role_code,
             assignee_user_id, is_final)
        VALUES (%s, 1, '设计族批准', 'APPROVER', %s, true)
    """, (req["id"], approver["id"]))

    execute(conn, "UPDATE basic_drawing_family SET approval_request_id = %s WHERE id = %s",
            (req["id"], family_id))

    audit.write(conn, action="FAMILY_SUBMIT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="BASIC_DRAWING_FAMILY",
                object_id=family_id, object_code=fam["family_name_cn"],
                new_value={"approval_request": req["request_number"],
                           "approver": approver["username"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return req


def approve_family(conn: psycopg.Connection, family_id: str, comments: str,
                   actor: dict) -> dict:
    """批准设计族: 记录审批决定 → 分配基本图号 → 置为 ACTIVE。

    审批决定先写, 让 trg_approval_separation 有机会在此拦下"自己批自己"(INV-025);
    通过之后才发号, 保证被否决的族不占号。
    """
    fam = get_family(conn, family_id)
    if fam is None:
        raise LookupError("设计族不存在")
    if fam["status"] != "PENDING":
        raise ValueError(f"设计族状态为 {fam['status']}, 不可批准")
    if fam["approval_request_id"] is None:
        raise ValueError("该设计族尚未提交审批")
    from . import approvals
    approvals.require_assignee(conn, str(fam["approval_request_id"]), str(actor["user_id"]))

    # INV-025 由数据库触发器强制; 这里的写入若违反会被直接拒绝
    execute(conn, """
        UPDATE approval_step
           SET decision = 'APPROVED', decided_by = %s, acted_at = now(), comments = %s
         WHERE approval_request_id = %s AND is_final
    """, (actor["user_id"], comments, fam["approval_request_id"]))
    execute(conn, """
        UPDATE approval_request SET status = 'APPROVED', closed_at = now() WHERE id = %s
    """, (fam["approval_request_id"],))

    number, seq = numbering.allocate_basic_drawing(
        conn, fam["primary_class_code"], family_id, actor)

    row = fetch_one(conn, """
        UPDATE basic_drawing_family
           SET basic_drawing_number = %s, status = 'ACTIVE',
               approved_by = %s, approved_at = now(), updated_by = %s
         WHERE id = %s
        RETURNING id, basic_drawing_number, family_name_cn, family_name_en, status
    """, (number, actor["user_id"], actor["user_id"], family_id))

    audit.write(conn, action="FAMILY_APPROVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="BASIC_DRAWING_FAMILY",
                object_id=family_id, object_code=number,
                old_value={"status": "PENDING"},
                new_value={"status": "ACTIVE", "basic_drawing_number": number},
                reason=comments, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def get_family(conn: psycopg.Connection, family_id: str) -> dict | None:
    return fetch_one(conn, """
        SELECT f.*, pc.code AS physical_class_code, ct.code AS core_term_code,
               fi.code AS primary_function_code,
               aps.assignee_user_id AS approval_assignee_user_id
          FROM basic_drawing_family f
          JOIN physical_class pc ON pc.id = f.physical_class_id
          JOIN naming_core_term ct ON ct.id = f.core_term_id
          LEFT JOIN function_item fi ON fi.id = f.primary_function_id
          LEFT JOIN approval_step aps ON aps.approval_request_id=f.approval_request_id
            AND aps.decision='PENDING'
         WHERE f.id = %s
    """, (family_id,))


def list_families(conn: psycopg.Connection, primary_class_code: str | None = None,
                  status: str | None = None, q: str | None = None) -> list[dict]:
    return fetch_all(conn, """
        SELECT f.id, f.basic_drawing_number, f.family_name_cn, f.family_name_en,
               f.primary_class_code, f.status, pc.code AS physical_class_code,
               ct.code AS core_term_code,
               (SELECT count(*) FROM part_number pn
                 WHERE pn.basic_drawing_family_id = f.id) AS dash_count
          FROM basic_drawing_family f
          JOIN physical_class pc ON pc.id = f.physical_class_id
          JOIN naming_core_term ct ON ct.id = f.core_term_id
         WHERE (%s::text IS NULL OR f.primary_class_code = %s)
           AND (%s::text IS NULL OR f.status = %s)
           AND (%s::text IS NULL OR f.family_name_cn ILIKE %s
                OR f.basic_drawing_number ILIKE %s)
         ORDER BY f.basic_drawing_number
    """, (primary_class_code, primary_class_code, status, status,
          q, f"%{q}%" if q else None, f"%{q}%" if q else None))


def page_families(conn: psycopg.Connection, primary_class_code: str | None,
                  status: str | None, q: str | None, page: int, page_size: int) -> dict:
    like = f"%{q}%" if q else None
    args = (primary_class_code, primary_class_code, status, status, q, like, like)
    where = """WHERE (%s::text IS NULL OR f.primary_class_code=%s)
      AND (%s::text IS NULL OR f.status=%s)
      AND (%s::text IS NULL OR f.family_name_cn ILIKE %s OR f.basic_drawing_number ILIKE %s)"""
    total = scalar(conn, f"SELECT count(*) FROM basic_drawing_family f {where}", args) or 0
    items = fetch_all(conn, f"""SELECT f.id,f.basic_drawing_number,f.family_name_cn,f.family_name_en,
      f.primary_class_code,f.status,pc.code AS physical_class_code,ct.code AS core_term_code,
      (SELECT count(*) FROM part_number pn WHERE pn.basic_drawing_family_id=f.id) AS dash_count
      FROM basic_drawing_family f JOIN physical_class pc ON pc.id=f.physical_class_id
      JOIN naming_core_term ct ON ct.id=f.core_term_id {where}
      ORDER BY f.basic_drawing_number LIMIT %s OFFSET %s""",
      args + (page_size, (page - 1) * page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------
# Dash P/N — SRS-PN-001~003 / AC-DASH-01
# ---------------------------------------------------------------------
def create_dash(conn: psycopg.Connection, family_id: str, *, formal_name_cn: str,
                formal_name_en: str, object_level_code: str,
                difference_summary: str, actor: dict,
                requested_dash: int | None = None) -> dict:
    """在设计族下建立一个 Dash P/N。

    Dash 名称是自由文本, 因此这里做受限词校验(基本图号名称不需要, 它是生成的)。
    """
    fam = get_family(conn, family_id)
    if fam is None:
        raise LookupError("设计族不存在")
    if fam["status"] != "ACTIVE":
        raise ValueError(f"设计族状态为 {fam['status']}, 只有 ACTIVE 可新增 Dash")

    check = naming.check_restricted(conn, formal_name_cn, scope="DASH")
    if not check["passed"]:
        reasons = "; ".join(f"{h['restricted_type']}: {h['message']}"
                            for h in check["blocked"])
        raise ValueError(f"Dash 名称命中禁止级受限词 — {reasons}")

    dash = numbering.reserve_dash(conn, family_id, actor, requested_dash)
    full_pn = f"{fam['basic_drawing_number']}-{numbering.dash_suffix(dash)}"

    obj = fetch_one(conn, """
        INSERT INTO design_object (object_type, object_code, display_name, created_by, updated_by)
        VALUES ('INTERNAL_PART', %s, %s, %s, %s) RETURNING id
    """, (full_pn, formal_name_cn, actor["user_id"], actor["user_id"]))

    pn = fetch_one(conn, """
        INSERT INTO part_number
            (design_object_id, basic_drawing_family_id, dash_number, full_part_number,
             formal_name_cn, formal_name_en, object_level_code, difference_summary,
             created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, full_part_number, dash_number, formal_name_cn, lifecycle_status
    """, (obj["id"], family_id, dash, full_pn, formal_name_cn, formal_name_en,
          object_level_code, difference_summary, actor["user_id"], actor["user_id"]))

    # 对象已建立, 把预留号确认为正式分配并写入归属
    numbering.confirm_dash(conn, family_id, dash, str(pn["id"]), actor)

    audit.write(conn, action="PART_NUMBER_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="PART_NUMBER",
                object_id=str(pn["id"]), object_code=full_pn,
                new_value={"full_part_number": full_pn, "formal_name_cn": formal_name_cn,
                           "difference_summary": difference_summary},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    pn["restricted_warnings"] = check["warnings"] + check["needs_approval"]
    return pn


def list_dashes(conn: psycopg.Connection, family_id: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT pn.id, pn.dash_number, pn.full_part_number, pn.formal_name_cn,
               pn.formal_name_en, pn.lifecycle_status, pn.difference_summary,
               pn.current_baseline_id
          FROM part_number pn WHERE pn.basic_drawing_family_id = %s
         ORDER BY pn.dash_number
    """, (family_id,))
