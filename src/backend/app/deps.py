"""FastAPI 依赖: 当前用户、权限校验、请求上下文。"""
from __future__ import annotations

import uuid
from typing import Annotated

import psycopg
from fastapi import Depends, Header, Request

from . import errors
from .rbac import Perm, has
from .services import auth, licensing


def get_conn(request: Request) -> psycopg.Connection:
    """取本次请求的事务连接。事务由 TransactionalRoute 持有并在响应前提交。"""
    return request.state.conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or str(uuid.uuid4())


def current_user(
    conn: Conn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise errors.unauthorized("缺少访问令牌")
    token = authorization.split(" ", 1)[1].strip()
    session = auth.resolve_session(conn, token)
    if session is None:
        raise errors.unauthorized("会话无效或已过期, 请重新登录")
    if not session["is_active"]:
        raise errors.unauthorized("账户已停用")
    licensing.touch(conn, str(session["session_id"]))
    session["client_ip"] = client_ip(request)
    session["request_id"] = request_id(request)
    return session


CurrentUser = Annotated[dict, Depends(current_user)]


def require(*perms: Perm):
    """要求具备全部指定权限。

    刻意不接受"任一权限满足"的语义 —— 权限判断写成 OR 很容易在阅读时被误解为更严格,
    需要 OR 的场景在端点里显式写。
    """
    def _dep(user: CurrentUser) -> dict:
        roles = list(user.get("roles") or [])
        missing = [p for p in perms if not has(roles, p)]
        if missing:
            raise errors.forbidden(
                f"当前角色 {', '.join(roles) or '(无)'} 不具备所需权限: "
                f"{', '.join(str(p) for p in missing)}")
        return user
    return _dep


def require_password_changed(user: CurrentUser) -> dict:
    """首次登录必须先改密, 否则不得执行业务操作。"""
    if user.get("must_change_password"):
        raise errors.forbidden("首次登录须先修改初始口令", rule="SRS-ACC-001")
    return user
