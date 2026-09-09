"""结构化 Applicability 与构型求值。

表达式格式（JSON）:
  {"all": [{"field":"model","op":"eq","value":"A320-214"}, ...]}
  {"any": [...]}
  {"not": {...}}
单条件也可直接使用 {"field":"msn","op":"between","value":[1,50]}。

支持 op: eq, ne, in, not_in, between, gte, lte, gt, lt, contains, exists。
未绑定规则的 BOM 行定义为 ALL。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

MAX_DEPTH = 60


def _norm(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    return v


from ..applicability_eval import evaluate, validate_expression

def create_context(conn: psycopg.Connection, *, code: str, name: str,
                   attributes: dict, description: str | None, actor: dict) -> dict:
    if not isinstance(attributes, dict): raise ValueError("attributes 必须是对象")
    row = fetch_one(conn, """
        INSERT INTO configuration_context(context_code,name_cn,description,attributes,created_by,updated_by)
        VALUES (%s,%s,%s,%s::jsonb,%s,%s)
        RETURNING id,context_code,name_cn,description,attributes,status,created_at
    """, (code, name, description, json.dumps(attributes, ensure_ascii=False), actor["user_id"], actor["user_id"]))
    audit.write(conn, action="CONFIG_CONTEXT_CREATE", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="CONFIGURATION_CONTEXT", object_id=str(row["id"]), new_value={"context_code": code, "attributes": attributes},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def list_contexts(conn):
    return fetch_all(conn, "SELECT id,context_code,name_cn,description,attributes,status,created_at FROM configuration_context ORDER BY context_code")


def create_rule(conn: psycopg.Connection, *, code: str, name: str,
                expression: dict, description: str | None, actor: dict) -> dict:
    validate_expression(expression)
    row = fetch_one(conn, """
        INSERT INTO applicability_rule(rule_code,name_cn,expression,description,created_by,updated_by)
        VALUES (%s,%s,%s::jsonb,%s,%s,%s)
        RETURNING id,rule_code,name_cn,expression,description,status,created_at
    """, (code, name, json.dumps(expression, ensure_ascii=False), description, actor["user_id"], actor["user_id"]))
    audit.write(conn, action="APPLICABILITY_RULE_CREATE", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="APPLICABILITY_RULE", object_id=str(row["id"]), new_value={"rule_code": code, "expression": expression},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def list_rules(conn):
    return fetch_all(conn, "SELECT id,rule_code,name_cn,expression,description,status,created_at FROM applicability_rule ORDER BY rule_code")


def assign_rule(conn: psycopg.Connection, line_id: str, rule_code: str | None, actor: dict) -> dict:
    line = fetch_one(conn, "SELECT id,bom_header_id FROM bom_line WHERE id=%s", (line_id,))
    if not line: raise LookupError("BOM 行不存在")
    if not rule_code:
        execute(conn, "DELETE FROM bom_line_applicability WHERE bom_line_id=%s", (line_id,))
        audit.write(conn, action="BOM_APPLICABILITY_ASSIGN", user_id=str(actor["user_id"]), username=actor["username"],
                    object_type="BOM_HEADER", object_id=str(line["bom_header_id"]), new_value={"line_id":line_id,"rule_code":None,"meaning":"ALL"},
                    session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
        return {"bom_line_id": line_id, "rule_code": None, "meaning": "ALL"}
    rule = fetch_one(conn, "SELECT id,rule_code,expression FROM applicability_rule WHERE rule_code=%s AND status='ACTIVE'", (rule_code,))
    if not rule: raise LookupError(f"Applicability 规则不存在或不可用: {rule_code}")
    fetch_one(conn, """
        INSERT INTO bom_line_applicability(bom_line_id,applicability_rule_id,created_by)
        VALUES (%s,%s,%s)
        ON CONFLICT(bom_line_id) DO UPDATE SET applicability_rule_id=excluded.applicability_rule_id, created_by=excluded.created_by, created_at=now()
        RETURNING bom_line_id
    """, (line_id, rule["id"], actor["user_id"]))
    audit.write(conn, action="BOM_APPLICABILITY_ASSIGN", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="BOM_HEADER", object_id=str(line["bom_header_id"]), new_value={"line_id": line_id, "rule_code": rule_code},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return {"bom_line_id": line_id, "rule_code": rule_code, "expression": rule["expression"]}


def _children(conn, parent_id: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT bl.id AS line_id, bl.item_number, bl.child_design_object_id, bl.quantity,
               bl.unit_code, bl.reference_designator, bl.sort_order,
               d.object_code AS child_object_code, d.display_name AS child_name,
               d.object_type AS child_object_type, d.lifecycle_status AS child_status,
               ar.rule_code, ar.expression AS applicability_expression
          FROM bom_header bh
          JOIN bom_line bl ON bl.bom_header_id=bh.id
          JOIN design_object d ON d.id=bl.child_design_object_id
          LEFT JOIN bom_line_applicability bla ON bla.bom_line_id=bl.id
          LEFT JOIN applicability_rule ar ON ar.id=bla.applicability_rule_id
         WHERE bh.parent_design_object_id=%s AND bh.status='WORKING'
         ORDER BY bl.sort_order, bl.item_number
    """, (parent_id,))


def resolve(conn: psycopg.Connection, parent_object_id: str, context: dict, max_depth: int = 10) -> dict:
    """按上下文展开 Master BOM。父行不适用时，其整个子树不进入结果。"""
    max_depth = min(max(1, max_depth), MAX_DEPTH)
    rows: list[dict] = []
    rejected: list[dict] = []

    def walk(parent_id: str, level: int, multiplier: Decimal, path: list[str], sort_path: list[str], ancestry: set[str]):
        if level > max_depth: return
        if parent_id in ancestry:
            raise ValueError("检测到历史 BOM 循环，无法解析构型")
        next_ancestry = set(ancestry); next_ancestry.add(parent_id)
        for line in _children(conn, parent_id):
            expr = line.get("applicability_expression")
            ok = evaluate(expr, context)
            if not ok:
                rejected.append({"level": level, "item_number": line["item_number"], "child_object_code": line["child_object_code"], "rule_code": line.get("rule_code")})
                continue
            qty = Decimal(str(line["quantity"]))
            ext = multiplier * qty
            p = path + [line["child_object_code"]]
            sp = sort_path + [str(line["sort_order"]).zfill(6)]
            rows.append({
                "line_id": str(line["line_id"]), "level": level, "item_number": line["item_number"],
                "child_design_object_id": str(line["child_design_object_id"]), "child_object_code": line["child_object_code"],
                "child_name": line["child_name"], "child_object_type": line["child_object_type"], "child_status": line["child_status"],
                "quantity": _norm(qty), "extended_quantity": _norm(ext), "unit_code": line["unit_code"],
                "reference_designator": line["reference_designator"], "rule_code": line.get("rule_code"),
                "applicability_expression": expr, "path": " / ".join(p), "sort_path": ".".join(sp)
            })
            walk(str(line["child_design_object_id"]), level+1, ext, p, sp, next_ancestry)

    walk(parent_object_id, 1, Decimal("1"), [], [], set())
    rows.sort(key=lambda x: x["sort_path"])
    return {"context": context, "lines": rows, "excluded": rejected, "line_count": len(rows)}


def _same_item_overlap(lines: list[dict]) -> list[dict]:
    """同一父 BOM 同一 item 的适用性在当前上下文同时命中即冲突。"""
    groups: dict[tuple, list[dict]] = {}
    for x in lines:
        key = (x["level"], x["item_number"], x["path"].rsplit(" / ", 1)[0] if " / " in x["path"] else "ROOT")
        groups.setdefault(key, []).append(x)
    return [{"level": k[0], "item_number": k[1], "parent_path": k[2], "matches": [x["child_object_code"] for x in v]}
            for k,v in groups.items() if len(v)>1]


def resolve_with_validation(conn, parent_object_id: str, context: dict, max_depth: int=10) -> dict:
    r = resolve(conn, parent_object_id, context, max_depth)
    r["overlaps"] = _same_item_overlap(r["lines"])
    r["passed"] = not r["overlaps"]
    return r


def create_resolved_snapshot(conn: psycopg.Connection, parent_object_id: str, *, context: dict,
                             context_id: str | None, actor: dict, max_depth: int=10) -> dict:
    resolved = resolve_with_validation(conn, parent_object_id, context, max_depth)
    if not resolved["passed"]:
        raise ValueError("当前构型存在同一 ITEM 多条适用记录同时生效，请先消除 Applicability Overlap")
    source = fetch_one(conn, "SELECT id,snapshot_number FROM bom_snapshot WHERE parent_design_object_id=%s ORDER BY created_at DESC LIMIT 1", (parent_object_id,))
    seq = scalar(conn, "SELECT COALESCE(max((regexp_match(resolved_snapshot_number,'([0-9]+)$'))[1]::int),0)+1 FROM resolved_bom_snapshot") or 1
    number = f"RSNAP-{int(seq):06d}"
    canonical = json.dumps({"parent": parent_object_id, "context": context, "lines": resolved["lines"]}, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    snap = fetch_one(conn, """
        INSERT INTO resolved_bom_snapshot(resolved_snapshot_number,parent_design_object_id,source_bom_snapshot_id,
          configuration_context_id,context_attributes,hash_sha256,line_count,created_by)
        VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
        RETURNING id,resolved_snapshot_number,hash_sha256,line_count,created_at
    """, (number, parent_object_id, source["id"] if source else None, context_id,
          json.dumps(context, ensure_ascii=False), digest, len(resolved["lines"]), actor["user_id"]))
    for x in resolved["lines"]:
        execute(conn, """
            INSERT INTO resolved_bom_snapshot_line(resolved_bom_snapshot_id,level,item_number,child_design_object_id,
              child_object_code,child_display_name,quantity,extended_quantity,unit_code,reference_designator,
              applicability_rule_code,applicability_expression,path,sort_path)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        """, (snap["id"], x["level"], x["item_number"], x["child_design_object_id"], x["child_object_code"], x["child_name"],
              x["quantity"], x["extended_quantity"], x["unit_code"], x["reference_designator"], x["rule_code"],
              json.dumps(x["applicability_expression"], ensure_ascii=False) if x["applicability_expression"] else None,
              x["path"], x["sort_path"]))
    audit.write(conn, action="RESOLVED_BOM_SNAPSHOT_CREATE", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="DESIGN_OBJECT", object_id=parent_object_id,
                new_value={"resolved_snapshot_number": number, "context": context, "line_count": len(resolved["lines"])},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return {**snap, "context": context, "source_bom_snapshot_number": source["snapshot_number"] if source else None}
