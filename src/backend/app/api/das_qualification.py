"""资格评估与登记册月度核对端点（M1）。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import das_qualification as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["资格评估与登记核对"], route_class=TransactionalRoute)


class AssessRequest(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)
    sign_types: list[str] = Field(default_factory=list)
    education_experience: str = Field(min_length=1, max_length=4000)
    training_evidence: str = Field(min_length=1, max_length=4000)
    prior_work: str | None = Field(default=None, max_length=4000)
    indep_no_self_check: bool = False
    indep_no_ism_conflict: bool = False
    conclusion: str = Field(pattern="^(AGREE|REJECT)$")
    intended_positions: str | None = Field(default=None, max_length=500)
    intended_scope: str | None = Field(default=None, max_length=1000)
    scope_conditions: str | None = Field(default=None, max_length=2000)
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    backup_user_id: str | None = Field(default=None, min_length=36, max_length=36)
    form_ref: str | None = Field(default=None, max_length=64)
    # 三签中的审核与批准（编制人即当前操作者）
    reviewed_by: str = Field(min_length=36, max_length=36)
    approved_by: str = Field(min_length=36, max_length=36)


class ReconcileRequest(BaseModel):
    period_month: dt.date | None = None
    roster_ref: str = Field(min_length=1, max_length=128)
    differences: int = Field(ge=0, default=0)
    difference_note: str | None = Field(default=None, max_length=4000)
    corrections: str | None = Field(default=None, max_length=4000)


@router.get("/das/qualifications")
def current(conn: Conn, user: CurrentUser, user_id: str | None = Query(None)):
    return svc.current(conn, user_id)


@router.get("/das/qualifications/history/{user_id}")
def history(user_id: str, conn: Conn, user: CurrentUser):
    return svc.history(conn, user_id)


@router.post("/das/qualifications", status_code=201)
def assess(payload: AssessRequest, conn: Conn,
           actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        return svc.assess(conn, actor=actor, **payload.model_dump())
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.get("/das/register/snapshot")
def snapshot(conn: Conn, actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    """系统这一侧的登记册快照，供人与《授权人员名单》比对。

    系统读不到那份受控 Word 文档，所以不判"是否一致"——那是人的工作。
    """
    return svc.system_snapshot(conn)


@router.get("/das/register/reconciliation")
def due(conn: Conn, user: CurrentUser):
    """哪些月份做了核对、哪些漏了（UG-DAW-005 第 4 章要求每月一次）。"""
    return svc.reconciliation_due(conn)


@router.post("/das/register/reconciliation", status_code=201)
def reconcile(payload: ReconcileRequest, conn: Conn,
              actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        return svc.reconcile(conn, actor=actor, **payload.model_dump())
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/register/reconciliation/{recon_id}/review")
def review(recon_id: int, conn: Conn,
           actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    """判据 N12：登记操作由第二人复核。复核人不得是核对人本人。"""
    try:
        svc.review_reconciliation(conn, recon_id=recon_id, actor=actor)
        return {"ok": True}
    except ValueError as e:
        raise errors.bad_request(str(e))
