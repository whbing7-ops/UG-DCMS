"""用户与角色管理 — SRS-ACC-001/005。

账户总数不设上限(INV-024 限制的是并发活动账户, 不是账户数), 因此这里没有任何
创建账户时的数量校验。这个区别很容易搞混, 一旦在建账户时加了数量限制, INV-024 的
本意就被曲解了。
"""
from __future__ import annotations

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one
from ..rbac import Role
from ..security import check_password_policy, hash_password
from . import licensing

VALID_ROLES = {r.value for r in Role}


def list_users(conn: psycopg.Connection, include_inactive: bool = True) -> list[dict]:
    return fetch_all(conn, """
        SELECT u.id, u.username, u.full_name, u.email, u.employee_no,
               u.is_active, u.must_change_password, u.last_login_at, u.created_at,
               COALESCE((SELECT array_agg(role_code ORDER BY role_code)
                           FROM user_role WHERE user_id = u.id), '{}') AS roles,
               EXISTS (SELECT 1 FROM user_session s
                        WHERE s.user_id = u.id AND s.revoked_at IS NULL
                          AND s.expires_at > now()) AS is_online
          FROM app_user u
         WHERE (%s OR u.is_active)
         ORDER BY u.username
    """, (include_inactive,))


def get_user(conn: psycopg.Connection, user_id: str) -> dict | None:
    return fetch_one(conn, """
        SELECT u.id, u.username, u.full_name, u.email, u.employee_no,
               u.is_active, u.must_change_password, u.last_login_at, u.created_at,
               COALESCE((SELECT array_agg(role_code ORDER BY role_code)
                           FROM user_role WHERE user_id = u.id), '{}') AS roles
          FROM app_user u WHERE u.id = %s
    """, (user_id,))


def validate_roles(roles: list[str]) -> list[str]:
    bad = [r for r in roles if r not in VALID_ROLES]
    return bad


def create_user(conn: psycopg.Connection, *, username: str, full_name: str,
                email: str | None, employee_no: str | None, password: str,
                roles: list[str], actor: dict) -> dict:
    email = (email.strip() or None) if email else None
    problems = check_password_policy(password, username)
    if problems:
        raise ValueError("口令不满足策略: " + "; ".join(problems))
    bad = validate_roles(roles)
    if bad:
        raise ValueError(f"无效角色: {', '.join(bad)}")

    row = fetch_one(conn, """
        INSERT INTO app_user (username, full_name, email, employee_no,
                              password_hash, must_change_password, created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,true,%s,%s)
        RETURNING id, username, full_name, email, employee_no, is_active,
                  must_change_password, created_at
    """, (username, full_name, email, employee_no, hash_password(password),
          actor["user_id"], actor["user_id"]))

    for r in roles:
        execute(conn, """
            INSERT INTO user_role (user_id, role_code, granted_by) VALUES (%s,%s,%s)
        """, (row["id"], r, actor["user_id"]))

    audit.write(conn, action="USER_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="APP_USER",
                object_id=str(row["id"]), object_code=username,
                new_value={"username": username, "full_name": full_name,
                           "email": email, "roles": roles,
                           "must_change_password": True},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    row["roles"] = roles
    return row


def update_user(conn: psycopg.Connection, user_id: str, *, full_name: str | None,
                email: str | None, is_active: bool | None, roles: list[str] | None,
                reason: str | None, actor: dict, email_supplied: bool = False) -> dict:
    replace_email = email_supplied or email is not None
    email = (email.strip() or None) if email else None
    before = get_user(conn, user_id)
    if before is None:
        raise LookupError("用户不存在")

    if roles is not None:
        bad = validate_roles(roles)
        if bad:
            raise ValueError(f"无效角色: {', '.join(bad)}")

    # 不允许把最后一个系统管理员降权或停用, 否则系统会锁死无人可管
    losing_admin = (
        "SYSTEM_ADMIN" in (before["roles"] or [])
        and ((roles is not None and "SYSTEM_ADMIN" not in roles) or is_active is False)
    )
    if losing_admin:
        remaining = fetch_one(conn, """
            SELECT count(*) AS n FROM user_role ur
              JOIN app_user u ON u.id = ur.user_id
             WHERE ur.role_code = 'SYSTEM_ADMIN' AND u.is_active AND u.id <> %s
        """, (user_id,))
        if remaining["n"] == 0:
            raise ValueError("这是最后一个启用状态的系统管理员, 不得停用或移除其管理员角色")

    execute(conn, """
        UPDATE app_user
           SET full_name = COALESCE(%s, full_name),
               email     = CASE WHEN %s THEN %s ELSE email END,
               is_active = COALESCE(%s, is_active),
               updated_by = %s
         WHERE id = %s
    """, (full_name, replace_email, email, is_active, actor["user_id"], user_id))

    if roles is not None:
        execute(conn, "DELETE FROM user_role WHERE user_id = %s", (user_id,))
        for r in roles:
            execute(conn, """
                INSERT INTO user_role (user_id, role_code, granted_by) VALUES (%s,%s,%s)
            """, (user_id, r, actor["user_id"]))

    # 停用账户必须同时踢掉其在线会话, 否则"停用"只是个标记, 已登录的人照常在用
    if is_active is False:
        licensing.revoke_user_sessions(conn, user_id, str(actor["user_id"]),
                                       "ACCOUNT_DISABLED")

    after = get_user(conn, user_id)
    audit.write(conn, action="USER_UPDATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="APP_USER",
                object_id=user_id, object_code=before["username"],
                old_value=_diffable(before), new_value=_diffable(after),
                reason=reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return after


def reset_password(conn: psycopg.Connection, user_id: str, new_password: str,
                   reason: str, actor: dict) -> None:
    target = get_user(conn, user_id)
    if target is None:
        raise LookupError("用户不存在")
    problems = check_password_policy(new_password, target["username"])
    if problems:
        raise ValueError("口令不满足策略: " + "; ".join(problems))

    execute(conn, """
        UPDATE app_user SET password_hash = %s, must_change_password = true,
               failed_login_count = 0, locked_until = NULL, updated_by = %s
         WHERE id = %s
    """, (hash_password(new_password), actor["user_id"], user_id))

    # 管理员重置口令后, 该账户全部旧会话立即失效
    revoked = licensing.revoke_user_sessions(conn, user_id, str(actor["user_id"]),
                                             "PASSWORD_RESET")
    audit.write(conn, action="PASSWORD_RESET", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="APP_USER",
                object_id=user_id, object_code=target["username"],
                new_value={"must_change_password": True, "sessions_revoked": revoked},
                reason=reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))


def unlock_user(conn: psycopg.Connection, user_id: str, actor: dict) -> None:
    execute(conn, """
        UPDATE app_user SET failed_login_count = 0, locked_until = NULL, updated_by = %s
         WHERE id = %s
    """, (actor["user_id"], user_id))
    audit.write(conn, action="USER_UNLOCK", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="APP_USER", object_id=user_id,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


def _diffable(u: dict) -> dict:
    return {k: u.get(k) for k in ("username", "full_name", "email", "is_active", "roles")}
