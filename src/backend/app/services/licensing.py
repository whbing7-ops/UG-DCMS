"""并发活动账户许可 — INV-024 / SRS-ACC-002/003 / AC-SEC-06。

规则原文: "账户数量不限, 但不同活动账户同时最多 10 个", 且同一账户多 Session 只按
1 个活动账户计数。因此计数口径是 COUNT(DISTINCT user_id), 不是会话数。

并发正确性: ERD §8 要求"第 11 个活动账户登录判断应以数据库/Redis 原子方式计算"。
这里用 PostgreSQL 事务级 advisory lock 串行化登录判定, 不额外引入 Redis ——
少一个组件就少一处需要单独备份和监控的状态。锁常量 811001 在迁移 0001 的表注释
里登记过, 避免与其它 advisory lock 撞号。

关键点: "检查 + 占位"必须在同一事务、同一把锁内完成。若先查后插, 两个请求可以同时
读到 9 然后各自插入, 得到 11 个活动账户。tests/test_licensing.py 有并发用例守住这点。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from ..db import execute, fetch_all, fetch_one, scalar

ADVISORY_LOCK_KEY = 811001


def _setting_int(conn: psycopg.Connection, key: str, default: int) -> int:
    v = scalar(conn, "SELECT value FROM system_setting WHERE key = %s", (key,))
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def max_concurrent_accounts(conn: psycopg.Connection) -> int:
    return _setting_int(conn, "max_concurrent_accounts", 10)


def expire_stale_sessions(conn: psycopg.Connection) -> int:
    """回收已过期但未标记的会话。

    过期会话若不回收会永久占用许可名额 —— 用户直接关掉浏览器, 名额就再也放不出来。
    """
    return execute(conn, """
        UPDATE user_session
           SET revoked_at = now(), revoke_reason = 'EXPIRED'
         WHERE revoked_at IS NULL AND expires_at <= now()
    """)


def active_account_count(conn: psycopg.Connection) -> int:
    return scalar(conn, """
        SELECT count(DISTINCT user_id) FROM user_session
         WHERE revoked_at IS NULL AND expires_at > now()
    """) or 0


def active_accounts(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """
        SELECT u.id, u.username, u.full_name,
               count(s.id)         AS session_count,
               min(s.issued_at)    AS first_login_at,
               max(s.last_seen_at) AS last_seen_at
          FROM user_session s
          JOIN app_user u ON u.id = s.user_id
         WHERE s.revoked_at IS NULL AND s.expires_at > now()
         GROUP BY u.id, u.username, u.full_name
         ORDER BY max(s.last_seen_at) DESC
    """)


def acquire_slot(
    conn: psycopg.Connection,
    user_id: str,
    token_hash: str,
    client_ip: str | None,
    user_agent: str | None,
) -> tuple[bool, dict | None, str | None]:
    """在许可范围内建立会话, 返回 (是否成功, 会话行, 拒绝说明)。

    已在线账户新开会话不占用新名额(SRS-ACC-002), AC-SEC-06 中"已在线账户刷新不受
    影响"由 already_active 分支保证。
    """
    # 事务级 advisory lock: 事务结束自动释放, 异常也不会泄漏锁
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

    expire_stale_sessions(conn)

    already_active = scalar(conn, """
        SELECT EXISTS (SELECT 1 FROM user_session
                        WHERE user_id = %s AND revoked_at IS NULL AND expires_at > now())
    """, (user_id,))

    limit = max_concurrent_accounts(conn)
    if not already_active:
        current = active_account_count(conn)
        if current >= limit:
            return False, None, (
                f"当前已有 {current} 个账户在线, 达到并发上限 {limit} 个。"
                f"请等待其他用户退出, 或联系系统管理员释放会话")

    idle_minutes = _setting_int(conn, "session_idle_minutes", 120)
    absolute_hours = _setting_int(conn, "session_absolute_hours", 12)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=min(idle_minutes, absolute_hours * 60))

    row = fetch_one(conn, """
        INSERT INTO user_session (user_id, token_hash, expires_at, client_ip, user_agent)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, user_id, issued_at, expires_at
    """, (user_id, token_hash, expires_at, client_ip, user_agent))
    return True, row, None


def touch(conn: psycopg.Connection, session_id: str) -> None:
    """刷新活跃时间并顺延空闲超时, 但不得越过绝对有效期。"""
    idle_minutes = _setting_int(conn, "session_idle_minutes", 120)
    absolute_hours = _setting_int(conn, "session_absolute_hours", 12)
    execute(conn, """
        UPDATE user_session
           SET last_seen_at = now(),
               expires_at = LEAST(now() + make_interval(mins => %s),
                                  issued_at + make_interval(hours => %s))
         WHERE id = %s AND revoked_at IS NULL
    """, (idle_minutes, absolute_hours, session_id))


def revoke_session(conn: psycopg.Connection, session_id: str,
                   revoked_by: str | None, reason: str) -> int:
    return execute(conn, """
        UPDATE user_session SET revoked_at = now(), revoked_by = %s, revoke_reason = %s
         WHERE id = %s AND revoked_at IS NULL
    """, (revoked_by, reason, session_id))


def revoke_user_sessions(conn: psycopg.Connection, user_id: str,
                         revoked_by: str | None, reason: str) -> int:
    """撤销某账户全部会话 — SRS-ACC-004 / AC-SEC-05。"""
    return execute(conn, """
        UPDATE user_session SET revoked_at = now(), revoked_by = %s, revoke_reason = %s
         WHERE user_id = %s AND revoked_at IS NULL
    """, (revoked_by, reason, user_id))
