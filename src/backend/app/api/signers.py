"""有权签署人清单端点。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import signers as signer_svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["有权签署人"], route_class=TransactionalRoute)


class GrantRequest(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)
    level: str = Field(pattern="^(REVIEW|APPROVE)$")
    file_type_code: str | None = Field(default=None, max_length=64)      # 空 = 所有文件类型
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    note: str | None = Field(default=None, max_length=500)


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


@router.get("/signers")
def list_signers(conn: Conn, user: CurrentUser, include_revoked: bool = Query(False)):
    """清单本身所有登录用户可查看(谁被授权是公开信息); 维护需要 signer_manage。"""
    return signer_svc.list_authorizations(conn, include_revoked)


@router.post("/signers", status_code=201)
def grant_signer(payload: GrantRequest, conn: Conn, actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        return signer_svc.grant(conn, user_id=payload.user_id, level=payload.level,
                                file_type_code=payload.file_type_code or None,
                                valid_from=payload.valid_from, valid_to=payload.valid_to,
                                note=payload.note, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/signers/{auth_id}/revoke")
def revoke_signer(auth_id: str, payload: RevokeRequest, conn: Conn,
                  actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        signer_svc.revoke(conn, auth_id, payload.reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "授权已撤销"}


@router.get("/signers/eligible-users")
def eligible_users(conn: Conn, actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    """可被授权的账户: 启用且带有可签署角色, 不含操作人自己(不得自授权)。"""
    from ..db import fetch_all
    from ..rbac import ROLE_PERMISSIONS
    roles = [str(r) for r, perms in ROLE_PERMISSIONS.items() if Perm.SIGN in perms]
    return fetch_all(conn, """
        SELECT u.id::text, u.username, u.full_name, u.employee_no FROM app_user u
         WHERE u.is_active AND u.id <> %s
           AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s))
         ORDER BY u.full_name, u.username""", (actor["user_id"], roles))
