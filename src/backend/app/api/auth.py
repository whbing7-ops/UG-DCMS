"""认证端点。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import errors
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, client_ip
from ..rbac import permissions_for
from ..security import check_password_policy
from ..services import auth as auth_svc
from .schemas import LoginRequest, LoginResponse, PasswordChangeRequest

router = APIRouter(prefix="/auth", tags=["认证"], route_class=TransactionalRoute)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, conn: Conn, request: Request):
    result = auth_svc.login(conn, payload.username, payload.password,
                            client_ip=client_ip(request),
                            user_agent=request.headers.get("user-agent"))
    if not result.ok:
        if result.code == "LICENSE_EXHAUSTED":
            raise errors.license_exhausted(result.reason)
        if result.code in ("ACCOUNT_LOCKED", "ACCOUNT_DISABLED"):
            raise errors.forbidden(result.reason)
        raise errors.unauthorized(result.reason)

    u = result.user
    return LoginResponse(
        access_token=result.token,
        expires_at=result.session["expires_at"],
        user={"id": u["id"], "username": u["username"], "full_name": u["full_name"],
              "roles": list(u["roles"] or []),
              "must_change_password": u["must_change_password"]},
        permissions=sorted(str(p) for p in permissions_for(list(u["roles"] or []))),
    )


@router.post("/logout")
def logout(conn: Conn, user: CurrentUser):
    auth_svc.logout(conn, str(user["session_id"]), user, client_ip=user.get("client_ip"))
    return {"message": "已退出登录"}


@router.get("/me")
def me(user: CurrentUser):
    return {
        "id": user["user_id"], "username": user["username"],
        "full_name": user["full_name"], "roles": list(user["roles"] or []),
        "must_change_password": user["must_change_password"],
        "session_id": user["session_id"], "expires_at": user["expires_at"],
        "permissions": sorted(str(p) for p in permissions_for(list(user["roles"] or []))),
    }


@router.post("/password")
def change_password(payload: PasswordChangeRequest, conn: Conn, user: CurrentUser):
    problems = check_password_policy(payload.new_password, user["username"])
    if problems:
        raise errors.bad_request("新口令不满足策略: " + "; ".join(problems))
    ok, reason = auth_svc.change_own_password(
        conn, str(user["user_id"]), user["username"],
        payload.old_password, payload.new_password,
        session_id=str(user["session_id"]), client_ip=user.get("client_ip"))
    if not ok:
        raise errors.bad_request(reason)
    return {"message": "口令已修改, 其它会话已失效"}
