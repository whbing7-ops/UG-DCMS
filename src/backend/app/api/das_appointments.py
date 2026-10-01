"""岗位任命端点（M1）。

读取对所有登录用户开放：谁在什么岗位上不是内部机密，遮起来反而让人猜。
写入用 das_checklist_manage 之外的另一个权限——任命与检查单是两件事。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import das_appointments as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["岗位任命"], route_class=TransactionalRoute)


class AppointRequest(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)
    position_code: str = Field(min_length=1, max_length=32)
    kind: str = Field(pattern="^(FORMAL|TEMPORARY)$")
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    appointment_ref: str | None = Field(default=None, max_length=128)
    formal_process_ref: str | None = Field(default=None, max_length=128)


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


@router.get("/das/positions")
def list_positions(conn: Conn, user: CurrentUser):
    return svc.positions(conn)


@router.get("/das/appointments")
def list_appointments(conn: Conn, user: CurrentUser, user_id: str | None = Query(None)):
    return svc.in_force(conn, user_id)


@router.get("/das/appointments/history/{user_id}")
def appointment_history(user_id: str, conn: Conn, user: CurrentUser):
    return svc.history(conn, user_id)


@router.get("/das/appointments/temp-pending")
def temp_pending(conn: Conn, user: CurrentUser):
    """判据 O3-2：只发了临时授权、正式流程还没结果的。"""
    return svc.temp_pending(conn)


@router.get("/das/positions/vacant")
def vacant(conn: Conn, user: CurrentUser):
    """无人在任的岗位。只列有没有人，不判够不够人（判据 A7）。"""
    return svc.vacant(conn)


@router.post("/das/appointments", status_code=201)
def appoint(payload: AppointRequest, conn: Conn,
            actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        return svc.appoint(conn, user_id=payload.user_id, position_code=payload.position_code,
                           kind=payload.kind, valid_from=payload.valid_from,
                           valid_to=payload.valid_to, appointment_ref=payload.appointment_ref,
                           formal_process_ref=payload.formal_process_ref, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/appointments/{appointment_id}/revoke")
def revoke(appointment_id: int, payload: RevokeRequest, conn: Conn,
           actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        svc.revoke(conn, appointment_id, payload.reason, actor)
        return {"ok": True}
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
