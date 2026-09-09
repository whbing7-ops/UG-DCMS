"""口令与会话令牌。

口令哈希用 bcrypt。初始管理员由迁移脚本以 pgcrypto 的 crypt(..., gen_salt('bf',12))
生成, 产出 $2a$ 格式; bcrypt 库可直接校验, 两侧兼容。
"""
from __future__ import annotations

import hashlib
import secrets

import bcrypt

from .config import get_settings

# bcrypt 输入上限 72 字节, 超长口令会被静默截断 —— 先做长度校验而不是让它悄悄截断
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 10


def hash_password(plain: str) -> str:
    _check_length(plain)
    rounds = get_settings().bcrypt_rounds
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except (ValueError, TypeError):
        return False


def _check_length(plain: str) -> None:
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"口令长度不得少于 {MIN_PASSWORD_LENGTH} 个字符")
    if len(plain.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError(f"口令不得超过 {MAX_PASSWORD_BYTES} 字节")


def check_password_policy(plain: str, username: str) -> list[str]:
    """返回不满足项的说明; 空列表表示通过。"""
    problems: list[str] = []
    if len(plain) < MIN_PASSWORD_LENGTH:
        problems.append(f"长度不得少于 {MIN_PASSWORD_LENGTH} 个字符")
    if len(plain.encode()) > MAX_PASSWORD_BYTES:
        problems.append(f"长度不得超过 {MAX_PASSWORD_BYTES} 字节")
    kinds = sum([
        any(c.islower() for c in plain),
        any(c.isupper() for c in plain),
        any(c.isdigit() for c in plain),
        any(not c.isalnum() for c in plain),
    ])
    if kinds < 3:
        problems.append("须包含大写字母、小写字母、数字、符号中的至少三类")
    if username and username.lower() in plain.lower():
        problems.append("不得包含用户名")
    return problems


def new_session_token() -> tuple[str, str]:
    """返回 (明文令牌, 令牌哈希)。明文只在签发时出现一次, 库里只存哈希。"""
    token = secrets.token_urlsafe(get_settings().session_token_bytes)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
