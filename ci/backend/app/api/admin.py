"""系统管理端点 — 用户、会话、并发许可。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import audit, errors
from ..db import fetch_all
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import licensing, users as user_svc
from .schemas import (LicenseStatus, PasswordResetRequest, UserCreateRequest,
                      UserUpdateRequest)

router = APIRouter(prefix="/admin", tags=["系统管理"], route_class=TransactionalRoute)


# ---------------- 用户 ----------------
@router.get("/users")
def list_users(conn: Conn, actor: dict = Depends(require(Perm.USER_MANAGE)),
               include_inactive: bool = True):
    return user_svc.list_users(conn, include_inactive)


@router.post("/users", status_code=201)
def create_user(payload: UserCreateRequest, conn: Conn,
                actor: dict = Depends(require(Perm.USER_MANAGE))):
    try:
        return user_svc.create_user(
            conn, username=payload.username, full_name=payload.full_name,
            email=payload.email, employee_no=payload.employee_no,
            password=payload.password, roles=payload.roles, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdateRequest, conn: Conn,
                actor: dict = Depends(require(Perm.USER_MANAGE))):
    try:
        return user_svc.update_user(
            conn, user_id, full_name=payload.full_name, email=payload.email,
            is_active=payload.is_active, roles=payload.roles,
            reason=payload.reason, actor=actor, email_supplied="email" in payload.model_fields_set)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/users/{user_id}/password-reset")
def reset_password(user_id: str, payload: PasswordResetRequest, conn: Conn,
                   actor: dict = Depends(require(Perm.USER_MANAGE))):
    try:
        user_svc.reset_password(conn, user_id, payload.new_password, payload.reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "口令已重置, 该账户全部会话已失效, 下次登录须改密"}


@router.post("/users/{user_id}/unlock")
def unlock_user(user_id: str, conn: Conn,
                actor: dict = Depends(require(Perm.USER_MANAGE))):
    user_svc.unlock_user(conn, user_id, actor)
    return {"message": "账户已解锁"}


# ---------------- 会话与并发许可 ----------------
@router.get("/license", response_model=LicenseStatus)
def license_status(conn: Conn, actor: dict = Depends(require(Perm.SESSION_MANAGE))):
    licensing.expire_stale_sessions(conn)
    limit = licensing.max_concurrent_accounts(conn)
    accounts = licensing.active_accounts(conn)
    return LicenseStatus(limit=limit, active_accounts=len(accounts),
                         available=max(0, limit - len(accounts)),
                         accounts=[dict(a) for a in accounts])


@router.get("/sessions")
def list_sessions(conn: Conn, actor: dict = Depends(require(Perm.SESSION_MANAGE))):
    return fetch_all(conn, """
        SELECT s.id, s.user_id, u.username, u.full_name,
               s.issued_at, s.last_seen_at, s.expires_at, s.client_ip
          FROM user_session s JOIN app_user u ON u.id = s.user_id
         WHERE s.revoked_at IS NULL AND s.expires_at > now()
         ORDER BY s.last_seen_at DESC
    """)


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, conn: Conn,
                   reason: str = Query("ADMIN_REVOKE", max_length=128),
                   actor: dict = Depends(require(Perm.SESSION_MANAGE))):
    n = licensing.revoke_session(conn, session_id, str(actor["user_id"]), reason)
    if n == 0:
        raise errors.not_found("会话不存在或已失效")
    audit.write(conn, action="SESSION_REVOKE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="USER_SESSION",
                object_id=session_id, reason=reason,
                session_id=str(actor["session_id"]), client_ip=actor.get("client_ip"))
    return {"message": "会话已撤销", "revoked": n}


@router.delete("/users/{user_id}/sessions")
def revoke_user_sessions(user_id: str, conn: Conn,
                         reason: str = Query("ADMIN_FORCE_LOGOUT", max_length=128),
                         actor: dict = Depends(require(Perm.SESSION_MANAGE))):
    """强制某账户全部下线 — SRS-ACC-004 / AC-SEC-05。"""
    n = licensing.revoke_user_sessions(conn, user_id, str(actor["user_id"]), reason)
    audit.write(conn, action="SESSION_REVOKE_ALL", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="APP_USER", object_id=user_id,
                new_value={"revoked_sessions": n}, reason=reason,
                session_id=str(actor["session_id"]), client_ip=actor.get("client_ip"))
    return {"message": "该账户全部会话已撤销", "revoked": n}


# ---------------- 审计 ----------------
@router.get("/audit")
def list_audit(conn: Conn, actor: dict = Depends(require(Perm.READ_AUDIT)),
               action: str | None = None, object_type: str | None = None,
               object_id: str | None = None, limit: int = Query(100, le=1000)):
    return fetch_all(conn, """
        SELECT id, occurred_at, username, action, object_type, object_id, object_code,
               old_value, new_value, reason, client_ip, result
          FROM audit_log
         WHERE (%s::text IS NULL OR action = %s)
           AND (%s::text IS NULL OR object_type = %s)
           AND (%s::text IS NULL OR object_id = %s)
         ORDER BY occurred_at DESC, id DESC
         LIMIT %s
    """, (action, action, object_type, object_type, object_id, object_id, limit))
