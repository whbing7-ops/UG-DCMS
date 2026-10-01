"""时限与周期引擎端点（基础能力）。

与 M2 同样的理由，这些端点上**没有 DAS_*_MANAGE 权限**：改一个时限参数不是"管理员
改个设置"（判据 P2-1）。闸门在服务层按岗位任命判——分类与规章修订须在任适航管理
负责人，批准与启用须在任责任经理。

改动"规章规定的那个数"只有一个入口：`POST /das/deadlines/{code}/amendment`，它要求
条款出处、修订文号、规章生效日期和证据留存位置。没有单独的"改上限"端点，因为那条
路一开就等于内部决定能改法规时限。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_deadline as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["时限与周期"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-02")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class ProposeRequest(BaseModel):
    value_num: float = Field(gt=0)
    value_unit: str = Field(pattern="^(HOUR|DAY|MONTH|YEAR)$")
    effective_from: dt.date
    change_request_ref: str = Field(min_length=1, max_length=128)
    classification: str = Field(pattern="^(MAJOR|MINOR)$")


class AckRequest(BaseModel):
    ack_ref: str = Field(min_length=1, max_length=256)


class AmendRequest(BaseModel):
    field_changed: str = Field(pattern="^(CEILING|CALENDAR_BASIS)$")
    new_ceiling_value: float | None = Field(default=None, gt=0)
    new_ceiling_unit: str | None = Field(default=None, pattern="^(HOUR|DAY|MONTH|YEAR)$")
    new_calendar_basis: str | None = Field(default=None, pattern="^(CALENDAR|WORKING)$")
    regulation_ref: str = Field(min_length=1, max_length=256)
    regulation_revision_ref: str = Field(min_length=1, max_length=256)
    regulation_effective_from: dt.date
    evidence_ref: str = Field(min_length=1, max_length=512)


class PeriodicRequest(BaseModel):
    period_key: str = Field(min_length=1, max_length=32)
    started_at: dt.datetime


class EventTaskRequest(BaseModel):
    object_type: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=64)
    started_at: dt.datetime


class CompleteRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- 读


@router.get("/das/deadlines")
def registry(conn: Conn, user: CurrentUser, module: str | None = Query(None)):
    """追溯矩阵：判据 T1-2 的八个字段加当前实际取值。"""
    return svc.registry(conn, module)


@router.get("/das/deadlines/unset")
def unset(conn: Conn, user: CurrentUser):
    """尚无生效取值的控制项（判据 T1：不得默认按数值时长实现）。"""
    return svc.unset(conn)


@router.get("/das/deadlines/pending-activation")
def pending_activation(conn: Conn, user: CurrentUser):
    """已批准但未生效，以及卡在哪一步（判据 P2-1）。"""
    return svc.pending_activation(conn)


@router.get("/das/deadlines/amendments")
def amendments(conn: Conn, user: CurrentUser):
    """规章修订史：哪条时限因为哪次修订变过。"""
    return svc.amendment_history(conn)


@router.get("/das/deadlines/tasks")
def tasks(conn: Conn, user: CurrentUser, state: str | None = Query(None)):
    return svc.tasks(conn, state)


@router.get("/das/deadlines/{code}")
def param_detail(code: str, conn: Conn, user: CurrentUser):
    return _wrap(svc.param_detail, conn, code)


@router.get("/das/deadlines/{code}/due-at")
def due_at(code: str, started_at: dt.datetime, conn: Conn, user: CurrentUser):
    """判据 T1：一处取值。日历口径取自控制项，不由调用方传入。"""
    return _wrap(svc.due_at, conn, code, started_at)


# ---------------------------------------------------------------- 执行值的变更闸门


@router.post("/das/deadlines/{code}/values", status_code=201)
def propose(code: str, payload: ProposeRequest, conn: Conn, user: CurrentUser):
    """提出新执行值并分类。须在任适航管理负责人（判据 P2-1 第 1、2 步）。"""
    return _wrap(svc.propose_value, conn, param_code=code, actor=user, **payload.model_dump())


@router.post("/das/deadlines/values/{value_id}/approve")
def approve(value_id: int, conn: Conn, user: CurrentUser):
    """批准。批准不等于生效——"已批准但未生效"是一个能查到的状态。"""
    return _wrap(svc.approve_value, conn, value_id=value_id, actor=user)


@router.post("/das/deadlines/values/{value_id}/caac-ack")
def caac_ack(value_id: int, payload: AckRequest, conn: Conn, user: CurrentUser):
    """登记局方认可证据。只对重大更改适用——非重大更改不需要，也不应被要求。"""
    return _wrap(svc.record_caac_ack, conn, value_id=value_id, actor=user,
                 **payload.model_dump())


@router.post("/das/deadlines/values/{value_id}/activate")
def activate(value_id: int, conn: Conn, user: CurrentUser):
    """启用新值。重大更改在认可证据到手前由 DCMS-INV-067 拒绝。"""
    return _wrap(svc.activate_value, conn, value_id=value_id, actor=user)


# ---------------------------------------------------------------- 规章修订


@router.post("/das/deadlines/{code}/amendment")
def amend(code: str, payload: AmendRequest, conn: Conn, user: CurrentUser):
    """登记规章修订并据此改动上限或日历口径。

    这是改动"规章规定的那个数"的唯一入口。法规时限确实会变，但改它必须说得出是哪一次
    修订改的、规章什么时候生效、原文存在哪里——否则这就不是修订，是内部决定。
    """
    return _wrap(svc.amend_statutory, conn, param_code=code, actor=user,
                 **payload.model_dump())


# ---------------------------------------------------------------- 任务


@router.post("/das/deadlines/{code}/periodic-tasks", status_code=201)
def generate_periodic(code: str, payload: PeriodicRequest, conn: Conn, user: CurrentUser):
    """按周期生成任务。事件触发类由 DCMS-INV-069 拒绝（判据 T3）。"""
    return _wrap(svc.generate_periodic, conn, param_code=code, actor=user,
                 **payload.model_dump())


@router.post("/das/deadlines/{code}/event-tasks", status_code=201)
def start_event_task(code: str, payload: EventTaskRequest, conn: Conn, user: CurrentUser):
    """由一个真实事件实例起算（判据 T3）。没有事件就没有任务。"""
    return _wrap(svc.start_event_task, conn, param_code=code, actor=user,
                 **payload.model_dump())


@router.post("/das/deadlines/tasks/{task_id}/complete")
def complete_task(task_id: int, payload: CompleteRequest, conn: Conn, user: CurrentUser):
    return _wrap(svc.complete_task, conn, task_id=task_id, actor=user, **payload.model_dump())
