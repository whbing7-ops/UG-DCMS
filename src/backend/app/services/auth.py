"""认证 — SRS-ACC-001/002/003/004, AC-SEC-05/06。

会话采用服务端不透明令牌而非自包含 JWT。原因是 SRS-ACC-004 要求管理员能"强制下线"
某账户的全部会话, 而 JWT 在过期前无法真正作废, 只能再维护一份黑名单 —— 那还不如
一开始就把会话状态放在库里。库里只存令牌的 SHA-256, 明文令牌只在签发时出现一次。
"""
from __future__ import annotations

from typing import Any

import psycopg

from .. import audit
from ..db import autonomous, execute, fetch_one, scalar
from ..security import hash_password, hash_token, new_session_token, verify_password
from . import licensing


class LoginResult:
    def __init__(self, ok: bool, *, token: str | None = None, user: dict | None = None,
                 session: dict | None = None, reason: str | None = None,
                 code: str | None = None):
        self.ok = ok
        self.token = token
        self.user = user
        self.session = session
        self.reason = reason
        self.code = code


def _load_user_by_name(conn: psycopg.Connection, username: str) -> dict | None:
    return fetch_one(conn, """
        SELECT u.*, COALESCE(
                 (SELECT array_agg(role_code ORDER BY role_code)
                    FROM user_role WHERE user_id = u.id), '{}') AS roles
          FROM app_user u
         WHERE lower(u.username) = lower(%s)
    """, (username,))


def load_user(conn: psycopg.Connection, user_id: str) -> dict | None:
    return fetch_one(conn, """
        SELECT u.*, COALESCE(
                 (SELECT array_agg(role_code ORDER BY role_code)
                    FROM user_role WHERE user_id = u.id), '{}') AS roles
          FROM app_user u
         WHERE u.id = %s
    """, (user_id,))


def login(conn: psycopg.Connection, username: str, password: str,
          client_ip: str | None = None, user_agent: str | None = None) -> LoginResult:
    user = _load_user_by_name(conn, username)

    # 用户不存在与口令错误返回同一提示, 避免账户枚举
    generic = "用户名或口令不正确"

    if user is None:
        audit.write_autonomous(action="LOGIN", username=username, result="DENIED",
                               reason="用户不存在", client_ip=client_ip)
        return LoginResult(False, reason=generic, code="INVALID_CREDENTIALS")

    if not user["is_active"]:
        audit.write_autonomous(action="LOGIN", user_id=str(user["id"]),
                               username=user["username"], result="DENIED",
                               reason="账户已停用", client_ip=client_ip)
        return LoginResult(False, reason="账户已停用, 请联系系统管理员",
                           code="ACCOUNT_DISABLED")

    locked_until = user["locked_until"]
    if locked_until is not None:
        still_locked = scalar(conn, "SELECT %s > now()", (locked_until,))
        if still_locked:
            audit.write_autonomous(action="LOGIN", user_id=str(user["id"]),
                                   username=user["username"], result="DENIED",
                                   reason="账户处于锁定期", client_ip=client_ip)
            return LoginResult(False, reason="账户因连续登录失败已被临时锁定, 请稍后再试",
                               code="ACCOUNT_LOCKED")

    if not verify_password(password, user["password_hash"]):
        _register_failure(conn, user, client_ip)
        return LoginResult(False, reason=generic, code="INVALID_CREDENTIALS")

    token, token_hash = new_session_token()
    ok, session, deny = licensing.acquire_slot(
        conn, str(user["id"]), token_hash, client_ip, user_agent)

    if not ok:
        # 并发许可耗尽不计入登录失败次数 —— 口令是对的, 锁账户没有道理
        audit.write_autonomous(action="LOGIN", user_id=str(user["id"]),
                               username=user["username"], result="DENIED",
                               reason=f"并发许可耗尽: {deny}", client_ip=client_ip)
        return LoginResult(False, reason=deny, code="LICENSE_EXHAUSTED")

    execute(conn, """
        UPDATE app_user SET failed_login_count = 0, locked_until = NULL, last_login_at = now()
         WHERE id = %s
    """, (user["id"],))

    audit.write(conn, action="LOGIN", user_id=str(user["id"]), username=user["username"],
                object_type="USER_SESSION", object_id=str(session["id"]),
                session_id=str(session["id"]), client_ip=client_ip, result="SUCCESS")

    user = load_user(conn, str(user["id"]))
    return LoginResult(True, token=token, user=user, session=session)


def _register_failure(conn: psycopg.Connection, user: dict, client_ip: str | None) -> None:
    """记录一次登录失败并在达阈值时锁定账户。

    必须走自治事务: 登录端点最终会抛 401, 请求事务随之回滚。若计数写在请求事务里,
    回滚会把它一并撤销, 结果是无论错多少次都锁不上账户 —— 暴力破解防护形同虚设。
    """
    max_fail = int(scalar(conn, "SELECT value FROM system_setting WHERE key='login_max_failures'") or 5)
    lock_min = int(scalar(conn, "SELECT value FROM system_setting WHERE key='login_lock_minutes'") or 15)
    with autonomous() as own:
        row = fetch_one(own, """
            UPDATE app_user
               SET failed_login_count = failed_login_count + 1,
                   locked_until = CASE WHEN failed_login_count + 1 >= %s
                                       THEN now() + make_interval(mins => %s) END
             WHERE id = %s
            RETURNING failed_login_count, locked_until
        """, (max_fail, lock_min, user["id"]))
        audit.write(own, action="LOGIN", user_id=str(user["id"]), username=user["username"],
                    result="DENIED", client_ip=client_ip,
                    reason=f"口令错误 (累计 {row['failed_login_count']} 次)"
                           + ("; 已触发锁定" if row["locked_until"] else ""))


def resolve_session(conn: psycopg.Connection, token: str) -> dict | None:
    """按令牌取出有效会话及其用户。过期或已撤销一律视为无效。"""
    return fetch_one(conn, """
        SELECT s.id AS session_id, s.user_id, s.issued_at, s.expires_at,
               u.username, u.full_name, u.is_active, u.must_change_password,
               COALESCE((SELECT array_agg(role_code ORDER BY role_code)
                           FROM user_role WHERE user_id = u.id), '{}') AS roles
          FROM user_session s
          JOIN app_user u ON u.id = s.user_id
         WHERE s.token_hash = %s
           AND s.revoked_at IS NULL
           AND s.expires_at > now()
    """, (hash_token(token),))


def logout(conn: psycopg.Connection, session_id: str, user: dict,
           client_ip: str | None = None) -> None:
    licensing.revoke_session(conn, session_id, str(user["user_id"]), "USER_LOGOUT")
    audit.write(conn, action="LOGOUT", user_id=str(user["user_id"]),
                username=user["username"], object_type="USER_SESSION",
                object_id=session_id, session_id=session_id, client_ip=client_ip)


def change_own_password(conn: psycopg.Connection, user_id: str, username: str,
                        old_password: str, new_password: str,
                        session_id: str | None = None,
                        client_ip: str | None = None) -> tuple[bool, str | None]:
    row = fetch_one(conn, "SELECT password_hash FROM app_user WHERE id = %s", (user_id,))
    if row is None or not verify_password(old_password, row["password_hash"]):
        audit.write_autonomous(action="PASSWORD_CHANGE", user_id=user_id, username=username,
                               result="DENIED", reason="原口令不正确", session_id=session_id,
                               client_ip=client_ip)
        return False, "原口令不正确"

    if old_password == new_password:
        return False, "新口令不得与原口令相同"

    execute(conn, """
        UPDATE app_user SET password_hash = %s, must_change_password = false, updated_by = %s
         WHERE id = %s
    """, (hash_password(new_password), user_id, user_id))

    # 改密后撤销其它会话, 只保留当前会话 —— 口令泄露后改密才有实际效果
    revoked = execute(conn, """
        UPDATE user_session SET revoked_at = now(), revoked_by = %s,
               revoke_reason = 'PASSWORD_CHANGED'
         WHERE user_id = %s AND revoked_at IS NULL AND id <> %s
    """, (user_id, user_id, session_id))

    # new_value 里绝不放口令; audit.scrub 也会兜底
    audit.write(conn, action="PASSWORD_CHANGE", user_id=user_id, username=username,
                object_type="APP_USER", object_id=user_id, object_code=username,
                new_value={"must_change_password": False,
                           "other_sessions_revoked": revoked},
                session_id=session_id, client_ip=client_ip)
    return True, None
