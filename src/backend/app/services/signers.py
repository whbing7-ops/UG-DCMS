"""有权签署人清单。

三级签署里的"审核"和"批准"不是谁有审批权限谁就能做, 而是必须在这张清单里被明确授权:
哪个人、哪一级、哪类文件、什么时候到什么时候。清单是设计保证里"授权签署人"的系统体现。

两条规则:
  · 授权记录只能撤销, 不能改写或删除(数据库触发器), 历史授权永远可查。
  · 不得给自己授权(数据库约束)。授权由另一名构型管理员做出。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar
from ..rbac import ROLE_PERMISSIONS, Perm

LEVELS = {"REVIEW": "审核", "APPROVE": "批准"}


def _signing_roles() -> list[str]:
    return [str(r) for r, perms in ROLE_PERMISSIONS.items() if Perm.SIGN in perms]


def list_authorizations(conn: psycopg.Connection, include_revoked: bool = False) -> list[dict]:
    return fetch_all(conn, """
        SELECT sa.id, sa.level, sa.file_type_code, ft.name_cn AS file_type_name, sa.valid_from, sa.valid_to,
               sa.note, sa.granted_at, sa.revoked_at, sa.revoke_reason,
               u.id AS user_id, u.username, u.full_name, u.is_active,
               g.full_name AS granted_by_name, r.full_name AS revoked_by_name,
               (sa.revoked_at IS NULL AND sa.valid_from <= current_date
                 AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)) AS in_force
          FROM signer_authorization sa
          JOIN app_user u ON u.id = sa.user_id
          JOIN app_user g ON g.id = sa.granted_by
          LEFT JOIN app_user r ON r.id = sa.revoked_by
          LEFT JOIN file_type ft ON ft.code = sa.file_type_code
         WHERE (%s OR sa.revoked_at IS NULL)
         ORDER BY (sa.revoked_at IS NOT NULL), u.full_name, sa.level, sa.granted_at DESC
    """, (include_revoked,))


def grant(conn: psycopg.Connection, *, user_id: str, level: str, file_type_code: str | None,
          valid_from: dt.date | None, valid_to: dt.date | None, note: str | None, actor: dict) -> dict:
    if level not in LEVELS:
        raise ValueError("签署级别须为 审核 或 批准")
    if str(user_id) == str(actor["user_id"]):
        raise ValueError("不得给自己授权, 请由另一名构型管理员操作")
    user = fetch_one(conn, """
        SELECT u.id, u.username, u.full_name FROM app_user u
         WHERE u.id = %s AND u.is_active
           AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s))
    """, (user_id, _signing_roles()))
    if user is None:
        raise ValueError("该账户已停用, 或没有可签署的角色(设计工程师/审批员/构型管理员)")
    if file_type_code and scalar(conn, "SELECT count(*) FROM file_type WHERE code=%s", (file_type_code,)) == 0:
        raise ValueError("文件类型不存在")
    start = valid_from or dt.date.today()
    if valid_to is not None and valid_to < start:
        raise ValueError("有效期截止日不得早于起始日")
    dup = scalar(conn, """
        SELECT count(*) FROM signer_authorization
         WHERE user_id=%s AND level=%s AND file_type_code IS NOT DISTINCT FROM %s AND revoked_at IS NULL
           AND (valid_to IS NULL OR valid_to >= current_date)""", (user_id, level, file_type_code))
    if dup:
        raise ValueError("该账户已有同级别、同文件类型的有效授权")
    row = fetch_one(conn, """
        INSERT INTO signer_authorization (user_id, level, file_type_code, valid_from, valid_to, note, granted_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, level, file_type_code, valid_from, valid_to
    """, (user_id, level, file_type_code, start, valid_to, note, actor["user_id"]))
    audit.write(conn, action="SIGNER_GRANT", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="SIGNER_AUTHORIZATION", object_id=str(row["id"]), object_code=user["username"],
                new_value={"level": level, "file_type_code": file_type_code,
                           "valid_from": str(start), "valid_to": str(valid_to) if valid_to else None},
                reason=note, session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def revoke(conn: psycopg.Connection, auth_id: str, reason: str, actor: dict) -> None:
    row = fetch_one(conn, """
        SELECT sa.id, sa.level, sa.revoked_at, u.username FROM signer_authorization sa
          JOIN app_user u ON u.id = sa.user_id WHERE sa.id = %s""", (auth_id,))
    if row is None:
        raise LookupError("授权记录不存在")
    if row["revoked_at"] is not None:
        raise ValueError("该授权已撤销")
    execute(conn, """UPDATE signer_authorization SET revoked_at = now(), revoked_by = %s, revoke_reason = %s
                      WHERE id = %s""", (actor["user_id"], reason, auth_id))
    audit.write(conn, action="SIGNER_REVOKE", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="SIGNER_AUTHORIZATION", object_id=auth_id, object_code=row["username"],
                new_value={"level": row["level"]}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


def is_authorized(conn: psycopg.Connection, user_id: str, level: str, file_type_code: str | None) -> bool:
    """此刻该用户能否以 level 级别签署这类文件(有授权、在有效期内、账户启用且有可签署角色)。"""
    return bool(scalar(conn, """
        SELECT EXISTS (
          SELECT 1 FROM signer_authorization sa JOIN app_user u ON u.id = sa.user_id
           WHERE sa.user_id = %s AND sa.level = %s AND sa.revoked_at IS NULL AND u.is_active
             AND sa.valid_from <= current_date AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)
             AND (sa.file_type_code IS NULL OR sa.file_type_code = %s)
             AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s)))
    """, (user_id, level, file_type_code, _signing_roles())))


def candidates(conn: psycopg.Connection, level: str, file_type_code: str | None,
               exclude_user_ids: list[str]) -> list[dict]:
    """可被选为该级签署人的账户: 已授权且在有效期内, 排除已占用其它级别的人。"""
    return fetch_all(conn, """
        SELECT DISTINCT u.id::text, u.username, u.full_name, u.employee_no
          FROM signer_authorization sa JOIN app_user u ON u.id = sa.user_id
         WHERE sa.level = %s AND sa.revoked_at IS NULL AND u.is_active
           AND sa.valid_from <= current_date AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)
           AND (sa.file_type_code IS NULL OR sa.file_type_code = %s)
           AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s))
           AND NOT (u.id = ANY(%s::uuid[]))
         ORDER BY u.full_name, u.username
    """, (level, file_type_code, _signing_roles(), exclude_user_ids))
