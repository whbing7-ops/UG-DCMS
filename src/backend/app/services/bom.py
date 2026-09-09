"""BOM 与 Where-Used — SRS-BOM-001~005 / AC-BOM-01 / AC-WU-01 / INV-008/009/010/015。

三点值得说明:

**自引用与循环由数据库触发器拦截, 应用层不重复实现。** trg_bom_line_guard 用递归 CTE
上溯判定, 深度上限 500。应用层若再写一份判定逻辑, 两份规则会漂移, 而漂移的后果是
应用以为拦住了、实际没有。这里只负责把触发器抛出的 DCMS-INV-008/009 翻译成可读提示。

**数量、项号、位号只属于 BOM 关系, 不属于 Part Master**(INV-010)。同一个零件在不同
父项下用量不同, 把用量写进零件主记录必然自相矛盾。

**正式基线锁定的是不可变快照, 不是工作 BOM**(INV-015)。工作 BOM 随时可改, 快照一经
生成即冻结; 二者混用会让"已发布构型"随后续编辑而变化。
"""
from __future__ import annotations

import hashlib
import json

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

MAX_EXPAND_DEPTH = 60


# ---------------------------------------------------------------------
# 工作 BOM
# ---------------------------------------------------------------------
def get_or_create_working(conn: psycopg.Connection, parent_object_id: str,
                          actor: dict) -> dict:
    row = fetch_one(conn, """
        SELECT * FROM bom_header
         WHERE parent_design_object_id = %s AND status = 'WORKING'
    """, (parent_object_id,))
    if row is not None:
        return row
    seq = scalar(conn, """
        SELECT COALESCE(max(working_sequence), 0) + 1 FROM bom_header
         WHERE parent_design_object_id = %s
    """, (parent_object_id,)) or 1
    return fetch_one(conn, """
        INSERT INTO bom_header (parent_design_object_id, working_sequence, created_by, updated_by)
        VALUES (%s, %s, %s, %s) RETURNING *
    """, (parent_object_id, seq, actor["user_id"], actor["user_id"]))


def list_lines(conn: psycopg.Connection, header_id: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT bl.id, bl.item_number, bl.child_design_object_id, bl.quantity,
               bl.unit_code, bl.reference_designator, bl.effectivity, bl.notes,
               bl.sort_order, ar.rule_code AS applicability_rule_code,
               ar.expression AS applicability_expression,
               d.object_code AS child_object_code, d.display_name AS child_name,
               d.object_type AS child_object_type, d.lifecycle_status AS child_status
          FROM bom_line bl
          JOIN design_object d ON d.id = bl.child_design_object_id
          LEFT JOIN bom_line_applicability bla ON bla.bom_line_id = bl.id
          LEFT JOIN applicability_rule ar ON ar.id = bla.applicability_rule_id
         WHERE bl.bom_header_id = %s
         ORDER BY bl.sort_order, bl.item_number
    """, (header_id,))


def add_line(conn: psycopg.Connection, parent_object_id: str, *, item_number: str,
             child_object_code: str, quantity: float, unit_code: str | None,
             reference_designator: str | None, effectivity: str | None,
             notes: str | None, actor: dict) -> dict:
    header = get_or_create_working(conn, parent_object_id, actor)
    child = fetch_one(conn, """
        SELECT id, object_code, display_name, lifecycle_status
          FROM design_object WHERE object_code = %s
    """, (child_object_code,))
    if child is None:
        raise LookupError(f"子项对象不存在: {child_object_code}")

    seq = scalar(conn, "SELECT COALESCE(max(sort_order),0)+10 FROM bom_line "
                       "WHERE bom_header_id = %s", (header["id"],)) or 10

    # 自引用与循环由 trg_bom_line_guard 在此拦截
    row = fetch_one(conn, """
        INSERT INTO bom_line (bom_header_id, item_number, child_design_object_id, quantity,
                              unit_code, reference_designator, effectivity, notes,
                              sort_order, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, item_number, quantity, unit_code, sort_order
    """, (header["id"], item_number, child["id"], quantity, unit_code,
          reference_designator, effectivity, notes, seq, actor["user_id"]))

    audit.write(conn, action="BOM_LINE_ADD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="BOM_HEADER",
                object_id=str(header["id"]),
                new_value={"item_number": item_number, "child": child_object_code,
                           "quantity": float(quantity), "unit_code": unit_code},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    row["child_object_code"] = child["object_code"]
    row["warnings"] = _line_warnings(conn, header["id"], child)
    return row


def _line_warnings(conn: psycopg.Connection, header_id: str, child: dict) -> list[str]:
    """SRS-BOM-005: 同一子项在同一 BOM 中重复出现是允许的, 但默认给出警告。

    允许是因为确有合理场景(同一紧固件出现在不同项号、不同位号下); 警告是因为
    多数情况下它是录入错误。
    """
    warns: list[str] = []
    n = scalar(conn, "SELECT count(*) FROM bom_line WHERE bom_header_id=%s "
                     "AND child_design_object_id=%s", (header_id, child["id"]))
    if n and n > 1:
        warns.append(f"子项 {child['object_code']} 在本 BOM 中出现 {n} 次 (DQ-BOM-001)")
    if child["lifecycle_status"] == "DRAFT":
        warns.append(f"子项 {child['object_code']} 仍为草稿状态 (DQ-BOM-002)")
    if child["lifecycle_status"] == "OBSOLETE":
        warns.append(f"子项 {child['object_code']} 已废止")
    return warns


def update_line(conn: psycopg.Connection, line_id: str, *, quantity=None,
                item_number=None, unit_code=None, reference_designator=None,
                effectivity=None, notes=None, actor: dict) -> dict:
    before = fetch_one(conn, "SELECT * FROM bom_line WHERE id = %s", (line_id,))
    if before is None:
        raise LookupError("BOM 行不存在")
    row = fetch_one(conn, """
        UPDATE bom_line
           SET quantity = COALESCE(%s, quantity),
               item_number = COALESCE(%s, item_number),
               unit_code = COALESCE(%s, unit_code),
               reference_designator = COALESCE(%s, reference_designator),
               effectivity = COALESCE(%s, effectivity),
               notes = COALESCE(%s, notes)
         WHERE id = %s
        RETURNING id, item_number, quantity, unit_code, reference_designator
    """, (quantity, item_number, unit_code, reference_designator, effectivity,
          notes, line_id))
    audit.write(conn, action="BOM_LINE_UPDATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="BOM_HEADER",
                object_id=str(before["bom_header_id"]),
                old_value={"item_number": before["item_number"],
                           "quantity": float(before["quantity"])},
                new_value={"item_number": row["item_number"],
                           "quantity": float(row["quantity"])},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def delete_line(conn: psycopg.Connection, line_id: str, actor: dict, reason: str | None = None) -> None:
    before = fetch_one(conn, """
        SELECT bl.*, d.object_code FROM bom_line bl
          JOIN design_object d ON d.id = bl.child_design_object_id WHERE bl.id = %s
    """, (line_id,))
    if before is None:
        raise LookupError("BOM 行不存在")
    execute(conn, "DELETE FROM bom_line WHERE id = %s", (line_id,))
    audit.write(conn, action="BOM_LINE_DELETE", user_id=str(actor["user_id"]),
                reason=reason,
                username=actor["username"], object_type="BOM_HEADER",
                object_id=str(before["bom_header_id"]),
                old_value={"item_number": before["item_number"],
                           "child": before["object_code"],
                           "quantity": float(before["quantity"])},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


# ---------------------------------------------------------------------
# 多层展开 — SRS-BOM-003
# ---------------------------------------------------------------------
def expand(conn: psycopg.Connection, parent_object_id: str,
           max_depth: int = 10) -> list[dict]:
    """多层展开工作 BOM。累计用量沿路径相乘。

    深度上限双重设防: 参数上限 + 递归内 MAX_EXPAND_DEPTH。即便库中存在历史遗留的
    循环数据(触发器只拦新写入), 展开也不会变成无限递归把连接拖死。
    """
    return fetch_all(conn, """
        WITH RECURSIVE tree AS (
            SELECT bl.id AS line_id, 1 AS level,
                   bl.child_design_object_id AS obj,
                   bl.item_number, bl.quantity, bl.quantity::numeric AS extended_quantity,
                   bl.unit_code, bl.reference_designator,
                   d.object_code::text AS path,
                   bl.sort_order::text AS sort_path
              FROM bom_header bh
              JOIN bom_line bl ON bl.bom_header_id = bh.id
              JOIN design_object d ON d.id = bl.child_design_object_id
             WHERE bh.parent_design_object_id = %s AND bh.status = 'WORKING'
            UNION ALL
            SELECT bl.id, t.level + 1,
                   bl.child_design_object_id,
                   bl.item_number, bl.quantity, (t.extended_quantity * bl.quantity)::numeric,
                   bl.unit_code, bl.reference_designator,
                   t.path || ' / ' || d.object_code,
                   t.sort_path || '.' || lpad(bl.sort_order::text, 6, '0')
              FROM tree t
              JOIN bom_header bh ON bh.parent_design_object_id = t.obj AND bh.status = 'WORKING'
              JOIN bom_line bl ON bl.bom_header_id = bh.id
              JOIN design_object d ON d.id = bl.child_design_object_id
             WHERE t.level < LEAST(%s::int, %s::int)
        )
        SELECT t.level, t.item_number, t.quantity, t.extended_quantity, t.unit_code,
               t.reference_designator, t.path,
               d.object_code AS child_object_code, d.display_name AS child_name,
               d.object_type AS child_object_type, d.lifecycle_status AS child_status
          FROM tree t JOIN design_object d ON d.id = t.obj
         ORDER BY t.sort_path
    """, (parent_object_id, max_depth, MAX_EXPAND_DEPTH))


def summarize(conn: psycopg.Connection, parent_object_id: str,
              max_depth: int = 10) -> list[dict]:
    """汇总用量: 同一子项在多层多处出现时合并累计用量。"""
    rows = expand(conn, parent_object_id, max_depth)
    agg: dict[str, dict] = {}
    for r in rows:
        k = r["child_object_code"]
        e = agg.setdefault(k, {"child_object_code": k, "child_name": r["child_name"],
                               "child_object_type": r["child_object_type"],
                               "child_status": r["child_status"],
                               "total_quantity": 0, "occurrences": 0,
                               "unit_code": r["unit_code"]})
        e["total_quantity"] += float(r["extended_quantity"])
        e["occurrences"] += 1
    return sorted(agg.values(), key=lambda x: x["child_object_code"])


# ---------------------------------------------------------------------
# Where-Used — SRS-BOM-004 / AC-WU-01
# ---------------------------------------------------------------------
def where_used(conn: psycopg.Connection, object_id: str, max_depth: int = 10,
               include_snapshots: bool = True) -> dict:
    """反查某对象被哪些父项使用。

    工作 BOM 与已冻结快照分开返回。二者性质不同: 工作 BOM 反映"当前设计意图",
    快照反映"某个已发布构型实际锁定了什么"。合并成一张表会让使用者分不清某处引用
    究竟能不能改。
    """
    working = fetch_all(conn, """
        WITH RECURSIVE up AS (
            SELECT bh.parent_design_object_id AS obj, 1 AS level,
                   bl.item_number, bl.quantity::numeric AS quantity,
                   d.object_code::text AS path
              FROM bom_line bl
              JOIN bom_header bh ON bh.id = bl.bom_header_id AND bh.status = 'WORKING'
              JOIN design_object d ON d.id = bh.parent_design_object_id
             WHERE bl.child_design_object_id = %s
            UNION ALL
            SELECT bh.parent_design_object_id, u.level + 1,
                   bl.item_number, bl.quantity::numeric,
                   d.object_code || ' / ' || u.path
              FROM up u
              JOIN bom_line bl ON bl.child_design_object_id = u.obj
              JOIN bom_header bh ON bh.id = bl.bom_header_id AND bh.status = 'WORKING'
              JOIN design_object d ON d.id = bh.parent_design_object_id
             WHERE u.level < LEAST(%s::int, %s::int)
        )
        SELECT DISTINCT u.level, u.item_number, u.quantity, u.path,
               d.object_code AS parent_object_code, d.display_name AS parent_name,
               d.lifecycle_status AS parent_status
          FROM up u JOIN design_object d ON d.id = u.obj
         ORDER BY u.level, d.object_code
    """, (object_id, max_depth, MAX_EXPAND_DEPTH))

    snapshots = []
    if include_snapshots:
        snapshots = fetch_all(conn, """
            SELECT s.snapshot_number, s.created_at,
                   p.object_code AS parent_object_code, p.display_name AS parent_name,
                   sl.item_number, sl.quantity,
                   (SELECT db.baseline_code FROM baseline_item bi
                      JOIN design_baseline db ON db.id = bi.design_baseline_id
                     WHERE bi.bom_snapshot_id = s.id LIMIT 1) AS baseline_code
              FROM bom_snapshot_line sl
              JOIN bom_snapshot s ON s.id = sl.bom_snapshot_id
              JOIN design_object p ON p.id = s.parent_design_object_id
             WHERE sl.child_design_object_id = %s
             ORDER BY s.created_at DESC
        """, (object_id,))

    return {"working": working, "snapshots": snapshots,
            "used_in_released_baseline": any(s["baseline_code"] for s in snapshots)}


# ---------------------------------------------------------------------
# 校验与快照 — SRS-BOM-002 / INV-015
# ---------------------------------------------------------------------
def validate(conn: psycopg.Connection, parent_object_id: str) -> dict:
    """BOM 校验。返回 errors / warnings 两档, ERROR 级阻止生成快照。"""
    header = fetch_one(conn, """
        SELECT id FROM bom_header
         WHERE parent_design_object_id = %s AND status = 'WORKING'
    """, (parent_object_id,))
    if header is None:
        return {"errors": ["该对象没有工作 BOM"], "warnings": [],
                "line_count": 0, "passed": False}

    errors: list[str] = []
    warnings: list[str] = []

    lines = list_lines(conn, str(header["id"]))
    if not lines:
        errors.append("BOM 为空, 不得生成快照")

    dupes = fetch_all(conn, """
        SELECT d.object_code, count(*) AS n FROM bom_line bl
          JOIN design_object d ON d.id = bl.child_design_object_id
         WHERE bl.bom_header_id = %s
         GROUP BY d.object_code HAVING count(*) > 1
    """, (header["id"],))
    for r in dupes:
        warnings.append(f"子项 {r['object_code']} 重复出现 {r['n']} 次 (DQ-BOM-001)")

    drafts = [l for l in lines if l["child_status"] == "DRAFT"]
    for l in drafts:
        warnings.append(f"子项 {l['child_object_code']} 仍为草稿状态 (DQ-BOM-002)")

    obsolete = [l for l in lines if l["child_status"] == "OBSOLETE"]
    for l in obsolete:
        errors.append(f"子项 {l['child_object_code']} 已废止, 不得进入正式构型")

    # 循环由触发器在写入时拦截; 这里对历史数据再查一次, 属兜底
    if len(expand(conn, parent_object_id, MAX_EXPAND_DEPTH)) >= 100000:
        errors.append("BOM 展开规模异常, 疑似存在循环 (DQ-BOM-003)")

    execute(conn, """
        UPDATE bom_header SET last_validated_at = now(), last_validation_result = %s::jsonb
         WHERE id = %s
    """, (json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False),
          header["id"]))

    return {"errors": errors, "warnings": warnings, "line_count": len(lines),
            "passed": not errors}


def create_snapshot(conn: psycopg.Connection, parent_object_id: str,
                    actor: dict) -> dict:
    """冻结当前工作 BOM 为不可变快照 — INV-015。

    快照内容含 hash, 用于事后核对"这份基线锁定的 BOM 是否被动过"。
    子项编码与名称冗余固化在快照行里, 保证多年后即使字典变更, 历史快照仍可读。
    """
    result = validate(conn, parent_object_id)
    if not result["passed"]:
        raise ValueError("BOM 校验未通过, 不得生成快照: " + "; ".join(result["errors"]))

    header = fetch_one(conn, """
        SELECT id FROM bom_header
         WHERE parent_design_object_id = %s AND status = 'WORKING'
    """, (parent_object_id,))
    lines = list_lines(conn, str(header["id"]))

    payload = json.dumps([
        {"item": l["item_number"], "child": l["child_object_code"],
         "qty": str(l["quantity"]), "unit": l["unit_code"],
         "designator": l["reference_designator"], "effectivity": l["effectivity"],
         "applicability_rule_code": l.get("applicability_rule_code"),
         "applicability_expression": l.get("applicability_expression")}
        for l in lines], ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()

    seq = scalar(conn, """
        SELECT COALESCE(max(substring(snapshot_number from 6)::int), 0) + 1
          FROM bom_snapshot WHERE snapshot_number LIKE 'SNAP-%'
    """) or 1
    snap = fetch_one(conn, """
        INSERT INTO bom_snapshot (parent_design_object_id, snapshot_number,
                                  source_bom_header_id, hash_sha256, line_count, created_by)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, snapshot_number, hash_sha256, line_count, created_at
    """, (parent_object_id, f"SNAP-{seq:06d}", header["id"], digest, len(lines),
          actor["user_id"]))

    for l in lines:
        execute(conn, """
            INSERT INTO bom_snapshot_line
                (bom_snapshot_id, item_number, child_design_object_id, child_object_code,
                 child_display_name, quantity, unit_code, reference_designator,
                 effectivity, notes, sort_order, applicability_rule_code, applicability_expression)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        """, (snap["id"], l["item_number"], l["child_design_object_id"],
              l["child_object_code"], l["child_name"], l["quantity"], l["unit_code"],
              l["reference_designator"], l["effectivity"], l["notes"], l["sort_order"],
              l.get("applicability_rule_code"),
              json.dumps(l.get("applicability_expression"), ensure_ascii=False) if l.get("applicability_expression") else None))

    audit.write(conn, action="BOM_SNAPSHOT_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="BOM_SNAPSHOT",
                object_id=str(snap["id"]), object_code=snap["snapshot_number"],
                new_value={"line_count": len(lines), "hash_sha256": digest,
                           "warnings": result["warnings"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    snap["warnings"] = result["warnings"]
    return snap


def compare_snapshots(conn: psycopg.Connection, a_number: str, b_number: str) -> dict:
    """比较两份快照 — 支撑构型差异分析。"""
    def _lines(num: str) -> dict[str, dict]:
        rows = fetch_all(conn, """
            SELECT sl.item_number, sl.child_object_code, sl.quantity, sl.unit_code,
                   sl.reference_designator
              FROM bom_snapshot_line sl JOIN bom_snapshot s ON s.id = sl.bom_snapshot_id
             WHERE s.snapshot_number = %s
        """, (num,))
        return {r["child_object_code"]: r for r in rows}

    a, b = _lines(a_number), _lines(b_number)
    added = [b[k] for k in b.keys() - a.keys()]
    removed = [a[k] for k in a.keys() - b.keys()]
    changed = [{"child_object_code": k, "from": a[k], "to": b[k]}
               for k in a.keys() & b.keys()
               if (a[k]["quantity"], a[k]["item_number"]) !=
                  (b[k]["quantity"], b[k]["item_number"])]
    return {"from": a_number, "to": b_number, "added": added,
            "removed": removed, "changed": changed,
            "identical": not (added or removed or changed)}
