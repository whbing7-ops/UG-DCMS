"""培训与考核端点（M1）。

课程矩阵与合格标准是第三级作业文件的参数（判据 P1），由归口负责人维护；
培训记录一经录入不可改写，只有有效性评估结论允许事后补填（UG-DAW-006 第 4 章）。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import das_training as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["培训与考核"], route_class=TransactionalRoute)


class RecordRequest(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)
    course_code: str = Field(min_length=1, max_length=32)
    kind: str = Field(pattern="^(INITIAL|RECURRENT|SPECIAL|MENTORING)$")
    trained_on: dt.date | None = None
    hours: float | None = Field(default=None, ge=0, le=999)
    result: str = Field(pattern="^(PASS|FAIL)$")
    is_retake: bool = False
    certificate_ref: str | None = Field(default=None, max_length=128)


class EvaluateRequest(BaseModel):
    effectiveness: str = Field(min_length=1, max_length=2000)


@router.get("/das/training/courses")
def courses(conn: Conn, user: CurrentUser):
    return svc.courses(conn)


@router.get("/das/training/requirements")
def requirements(conn: Conn, user: CurrentUser, position_code: str | None = Query(None)):
    return svc.requirements(conn, position_code)


@router.get("/das/training/status")
def status(conn: Conn, user: CurrentUser, user_id: str | None = Query(None)):
    """在任人员的必修课达标情况。MISSING／FAILED／EXPIRED 任一存在即不满足授权前提。"""
    return svc.status(conn, user_id)


@router.get("/das/training/suspend-due")
def suspend_due(conn: Conn, user: CurrentUser):
    """复训不合格或已过期、却仍持有有效签署授权的人（UG-DAW-006 第 3 章）。

    只列出不自动撤销：撤销会触发已签文件复核（判据 A4-2），该决定须有人做并留痕。
    """
    return svc.suspend_due(conn)


@router.post("/das/training/records", status_code=201)
def add_record(payload: RecordRequest, conn: Conn,
               actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        return svc.record(conn, user_id=payload.user_id, course_code=payload.course_code,
                          kind=payload.kind, trained_on=payload.trained_on, hours=payload.hours,
                          result=payload.result, is_retake=payload.is_retake,
                          certificate_ref=payload.certificate_ref, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/training/records/{record_id}/evaluate")
def evaluate(record_id: int, payload: EvaluateRequest, conn: Conn,
             actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    try:
        svc.evaluate(conn, record_id=record_id, effectiveness=payload.effectiveness, actor=actor)
        return {"ok": True}
    except ValueError as e:
        raise errors.bad_request(str(e))
