"""受控字典端点。

AC-SEC-02 要求 ENGINEER 不能修改受控字典, 因此写操作统一要求 DICTIONARY_WRITE 权限,
该权限只授予 DATA_ADMIN。读操作对全部已登录角色开放。

字典条目不提供删除端点 —— DD §1 与 CLS-SYS-001 规定字典只能 ACTIVE/DEPRECATED,
数据库也有禁止删除触发器。这里连路由都不建, 使"没有删除入口"在 API 层面同样成立。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import audit, errors
from ..db import fetch_all, fetch_one
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm

router = APIRouter(prefix="/dictionary", tags=["受控字典"], route_class=TransactionalRoute)

# 可读写的字典表白名单。用白名单而不是拼接用户传入的表名, 避免 SQL 注入面。
DICTIONARIES: dict[str, dict] = {
    "manufacturer": {"table": "manufacturer", "key": "code",
                     "cols": "id, code, name_cn, name_en, cage_code, namespace_id, status"},
    "primary-class":   {"table": "primary_class",   "key": "code",
                        "cols": "code, name_cn, name_en, definition, status, sort_order"},
    "physical-class":  {"table": "physical_class",  "key": "code",
                        "cols": "id, code, primary_class_code, name_cn, name_en, definition, status, sort_order"},
    "object-level":    {"table": "object_level",    "key": "code",
                        "cols": "code, name_cn, name_en, status, sort_order"},
    "function-domain": {"table": "function_domain", "key": "code",
                        "cols": "code, name_cn, name_en, definition, status, sort_order"},
    "function-item":   {"table": "function_item",   "key": "code",
                        "cols": "id, code, domain_code, name_cn, name_en, definition, status, sort_order"},
    "core-term":       {"table": "naming_core_term", "key": "code",
                        "cols": "id, code, name_cn, name_en, primary_class_code, definition, status"},
    "qualifier":       {"table": "naming_qualifier", "key": "code",
                        "cols": "id, code, name_cn, name_en, qualifier_type, definition, status"},
    "restricted-term": {"table": "restricted_term", "key": "id",
                        "cols": "id, restricted_type, control_level, pattern, is_regex, example, message_cn, status"},
    "file-type":       {"table": "file_type",       "key": "code",
                        "cols": "code, name_cn, name_en, category, definition, status"},
    "namespace":       {"table": "namespace",       "key": "code",
                        "cols": "id, code, name_cn, name_en, kind, status"},
    "material":        {"table": "material",        "key": "code",
                        "cols": "id, code, material_family, grade, specification, condition, name_cn, status"},
    "surface-treatment": {"table": "surface_treatment", "key": "code",
                        "cols": "id, code, process_type, specification, type_class, color, name_cn, status"},
    "unit":            {"table": "unit",            "key": "code",
                        "cols": "code, dimension_code, symbol, name_cn, factor_to_base, status"},
    "attribute-definition": {"table": "attribute_definition", "key": "code",
                        "cols": "id, code, name_cn, name_en, data_type, unit_dimension_code, default_unit_code, enum_group_code, is_searchable, is_comparable, definition, status"},
}


def _spec(name: str) -> dict:
    spec = DICTIONARIES.get(name)
    if spec is None:
        raise errors.not_found(
            f"未知字典 '{name}'。可用: {', '.join(sorted(DICTIONARIES))}")
    return spec


@router.get("")
def list_dictionaries(user: CurrentUser):
    return sorted(DICTIONARIES)


@router.get("/{name}")
def read_dictionary(name: str, conn: Conn, user: CurrentUser,
                    active_only: bool = Query(True),
                    q: str | None = Query(None, max_length=64)):
    spec = _spec(name)
    sql = f"SELECT {spec['cols']} FROM {spec['table']} WHERE (NOT %s OR status = 'ACTIVE')"
    params: list = [active_only]
    if name == 'object-level' and active_only:
        sql += " AND code IN ('PART','ASSEMBLY')"
    if q:
        sql += " AND (name_cn ILIKE %s OR code::text ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    sql += f" ORDER BY {spec['key']}"
    return fetch_all(conn, sql, params)


@router.post("/{name}/{key}/deprecate")
def deprecate_entry(name: str, key: str, conn: Conn,
                    reason: str = Query(..., min_length=1, max_length=256),
                    actor: dict = Depends(require(Perm.DICTIONARY_WRITE))):
    """废止字典条目 — AC-DATA-07。

    废止后新对象不可再选用, 历史对象不受影响, 也不做任何自动改写。
    """
    spec = _spec(name)
    before = fetch_one(conn,
                       f"SELECT {spec['cols']} FROM {spec['table']} WHERE {spec['key']}::text = %s",
                       (key,))
    if before is None:
        raise errors.not_found(f"字典 {name} 中不存在条目 {key}")
    if before.get("status") == "DEPRECATED":
        raise errors.bad_request(f"条目 {key} 已处于废止状态")

    after = fetch_one(conn,
        f"UPDATE {spec['table']} SET status = 'DEPRECATED' WHERE {spec['key']}::text = %s "
        f"RETURNING {spec['cols']}", (key,))

    audit.write(conn, action="DICTIONARY_DEPRECATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type=spec["table"].upper(),
                object_id=str(before.get("id") or key), object_code=key,
                old_value=dict(before), new_value=dict(after), reason=reason,
                session_id=str(actor["session_id"]), client_ip=actor.get("client_ip"))
    return after


@router.post("/{name}/{key}/reactivate")
def reactivate_entry(name: str, key: str, conn: Conn,
                     reason: str = Query(..., min_length=1, max_length=256),
                     actor: dict = Depends(require(Perm.DICTIONARY_WRITE))):
    if name == 'object-level' and key not in ('PART','ASSEMBLY'):
        raise errors.bad_request('对象层级仅使用零件、组件，其他历史层级不再启用')
    spec = _spec(name)
    before = fetch_one(conn,
                       f"SELECT {spec['cols']} FROM {spec['table']} WHERE {spec['key']}::text = %s",
                       (key,))
    if before is None:
        raise errors.not_found(f"字典 {name} 中不存在条目 {key}")
    after = fetch_one(conn,
        f"UPDATE {spec['table']} SET status = 'ACTIVE' WHERE {spec['key']}::text = %s "
        f"RETURNING {spec['cols']}", (key,))
    audit.write(conn, action="DICTIONARY_REACTIVATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type=spec["table"].upper(),
                object_id=str(before.get("id") or key), object_code=key,
                old_value=dict(before), new_value=dict(after), reason=reason,
                session_id=str(actor["session_id"]), client_ip=actor.get("client_ip"))
    return after
