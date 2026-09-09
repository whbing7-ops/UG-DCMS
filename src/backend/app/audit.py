"""审计写入。

两条硬要求:
  SRS-AUD-001 / INV-020  关键动作必须写审计, 且审计不可被业务账户改删(数据库触发器保证)
  AC-SEC-07              口令哈希、令牌等敏感字段不得写入 old/new JSON

敏感字段过滤采用"拒绝清单 + 键名模式"双重判断, 并且是默认生效的 —— 调用方不需要
记得脱敏。让安全依赖调用方自觉, 迟早会漏。
"""
from __future__ import annotations

import json
from typing import Any

import psycopg

from .db import autonomous, execute

# 完全不写入审计 JSON 的键名
SENSITIVE_KEYS = {
    "password", "password_hash", "new_password", "old_password", "current_password",
    "token", "token_hash", "access_token", "refresh_token", "session_token",
    "secret", "api_key", "private_key", "authorization", "cookie",
}
# 键名包含这些片段的一律脱敏
SENSITIVE_PATTERNS = ("password", "passwd", "token", "secret", "credential", "private_key")

REDACTED = "[REDACTED]"


def scrub(value: Any) -> Any:
    """递归剔除敏感字段。保留键名并置为 [REDACTED], 使审计能看出"该字段被改过",
    但看不到内容 —— 直接删键会让审计读不出发生过什么。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if kl in SENSITIVE_KEYS or any(p in kl for p in SENSITIVE_PATTERNS):
                out[k] = REDACTED
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(scrub(value), ensure_ascii=False, default=str)


def write(
    conn: psycopg.Connection,
    *,
    action: str,
    user_id: str | None = None,
    username: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    object_code: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    session_id: str | None = None,
    client_ip: str | None = None,
    request_id: str | None = None,
    result: str = "SUCCESS",
) -> None:
    execute(conn, """
        INSERT INTO audit_log
            (user_id, username, action, object_type, object_id, object_code,
             old_value, new_value, reason, session_id, client_ip, request_id, result)
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)
    """, (user_id, username, action, object_type, object_id, object_code,
          _dump(old_value), _dump(new_value), reason, session_id, client_ip,
          request_id, result))


def write_autonomous(**kwargs) -> None:
    """在独立事务中写审计, 不受调用方事务回滚影响。

    仅用于 DENIED/FAILURE 一类"请求会失败但必须留痕"的事件。SUCCESS 事件仍应与
    业务变更同事务写入 —— 否则业务回滚了、审计却声称做成了, 那是更糟的假记录。
    """
    with autonomous() as conn:
        write(conn, **kwargs)
