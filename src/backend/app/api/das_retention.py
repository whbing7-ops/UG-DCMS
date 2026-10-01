"""记录保存、冻结与销毁端点（基础能力，UG-DAW-004）。

**这里没有"清理到期记录"的批量入口，也没有任何定时任务。** 判据 R-总：销毁是带前置
条件的受控动作，不是定时任务。到期只让记录出现在 `GET /das/retention/due` 里；要销毁
得建批次、登记三项核查、两人批准、再执行，每一步各一个端点。

与 M2、时限引擎同样的理由，端点上没有 `DAS_*_MANAGE` 权限：闸门在服务层按**岗位任命**
判。一处刻意的不对称：冻结可由资料管理／适航管理／独立监督负责人任一发起（冻结是
保护性动作，门槛高到来不及冻就没有意义），解除只有在任适航管理负责人能做。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_retention as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["记录保存与销毁"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAW-004")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class RegisterRequest(BaseModel):
    class_code: str = Field(min_length=1, max_length=32)
    object_type: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=256)
    created_on: dt.date | None = None
    anchor_on: dt.date | None = None


class AnchorRequest(BaseModel):
    anchor_on: dt.date


class LinkRequest(BaseModel):
    link_kind: str = Field(pattern="^(PRODUCT|PROJECT|AUTHORISATION|RESPONSIBILITY|TOOL|DOCUMENT)$")
    link_ref: str = Field(min_length=1, max_length=128)
    required_until: dt.date | None = None
    note: str | None = Field(default=None, max_length=1000)


class FreezeRequest(BaseModel):
    record_id: int | None = None
    scope_kind: str | None = Field(default=None, pattern="^(PRODUCT|PROJECT|CLASS)$")
    scope_ref: str | None = Field(default=None, max_length=128)
    freeze_kind: str = Field(pattern="^(INVESTIGATION|LITIGATION|DISPUTE|CAAC_REQUEST|OPEN_ITEM)$")
    reason: str = Field(min_length=1, max_length=4000)
    release_condition: str = Field(min_length=1, max_length=2000)


class ReleaseRequest(BaseModel):
    release_note: str = Field(min_length=1, max_length=2000)


class HandoverRequest(BaseModel):
    scope_ref: str = Field(min_length=1, max_length=128)
    event_kind: str = Field(pattern="^(TERMINATED|TRANSFERRED)$")
    effective_on: dt.date
    transferred_to: str | None = Field(default=None, max_length=256)
    handover_ref: str = Field(min_length=1, max_length=128)
    evidence_ref: str = Field(min_length=1, max_length=512)


class BatchRequest(BaseModel):
    batch_ref: str = Field(min_length=1, max_length=64)
    record_ids: list[int] = Field(min_length=1)


class CheckRequest(BaseModel):
    check: str = Field(pattern="^(inventory|related_period|freeze)$")
    result: str = Field(min_length=1, max_length=4000)


class RestoreCheckRequest(BaseModel):
    restore_ref: str = Field(min_length=1, max_length=64)
    backup_ref: str = Field(min_length=1, max_length=128)
    retention_recheck_result: str = Field(min_length=1, max_length=4000)
    access_recheck_result: str = Field(min_length=1, max_length=4000)
    destroyed_items_noted: int = Field(default=0, ge=0)
    destroyed_items_note: str | None = Field(default=None, max_length=4000)


# ---------------------------------------------------------------- 读


@router.get("/das/retention/classes")
def classes(conn: Conn, user: CurrentUser, procedure: str | None = Query(None)):
    """UG-DAW-004 第 2 章的 97 类记录。raw_period_text 是期限栏原文，供对照复核。"""
    return svc.classes(conn, procedure)


@router.get("/das/retention/classes/summary")
def class_summary(conn: Conn, user: CurrentUser):
    """按锚点与期限栏原文汇总，便于与 UG-DAW-004 原文对照。"""
    return svc.class_summary(conn)


@router.get("/das/retention/records")
def records(conn: Conn, user: CurrentUser, state: str | None = Query(None)):
    return svc.records(conn, state)


@router.get("/das/retention/due")
def due(conn: Conn, user: CurrentUser):
    """已过保留截止日的**候选**记录——不是待删除队列（判据 R-总）。"""
    return svc.due(conn)


@router.get("/das/retention/unanchored")
def unanchored(conn: Conn, user: CurrentUser):
    """锚点未落地、期限算不出来的记录。既不能销毁，也不该被当成"永久保存"忘掉。"""
    return svc.unanchored(conn)


@router.get("/das/retention/freezes")
def freezes(conn: Conn, user: CurrentUser):
    return svc.freezes(conn)


@router.get("/das/retention/handovers")
def handovers(conn: Conn, user: CurrentUser):
    return svc.handovers(conn)


@router.get("/das/retention/restore-checks")
def restore_checks(conn: Conn, user: CurrentUser):
    return svc.restore_checks(conn)


@router.get("/das/retention/batches")
def batches(conn: Conn, user: CurrentUser):
    """销毁批次的就绪情况。blockers 为空才可执行。"""
    return svc.batches(conn)


@router.get("/das/retention/batches/{batch_id}")
def batch_detail(batch_id: int, conn: Conn, user: CurrentUser):
    return _wrap(svc.batch_detail, conn, batch_id)


# ---------------------------------------------------------------- 写


@router.post("/das/retention/records", status_code=201)
def register(payload: RegisterRequest, conn: Conn, user: CurrentUser):
    return _wrap(svc.register, conn, actor=user, **payload.model_dump())


@router.post("/das/retention/records/{record_id}/anchor")
def set_anchor(record_id: int, payload: AnchorRequest, conn: Conn, user: CurrentUser):
    """回填锚点事件日期（授权终止、工具停用、新版发布）。"""
    return _wrap(svc.set_anchor, conn, record_id=record_id, actor=user, **payload.model_dump())


@router.post("/das/retention/records/{record_id}/links", status_code=201)
def link(record_id: int, payload: LinkRequest, conn: Conn, user: CurrentUser):
    """登记关联对象（判据 R2）。required_until 留空表示该对象的终点还没到。"""
    return _wrap(svc.link, conn, record_id=record_id, actor=user, **payload.model_dump())


@router.post("/das/retention/freezes", status_code=201)
def freeze(payload: FreezeRequest, conn: Conn, user: CurrentUser):
    """发起冻结。门槛刻意放低——冻结是保护性动作。"""
    return _wrap(svc.freeze, conn, actor=user, **payload.model_dump())


@router.post("/das/retention/freezes/{freeze_id}/release")
def release_freeze(freeze_id: int, payload: ReleaseRequest, conn: Conn, user: CurrentUser):
    """解除冻结。只有在任适航管理负责人能做——解除之后记录就可能被销毁。"""
    return _wrap(svc.release_freeze, conn, freeze_id=freeze_id, actor=user,
                 **payload.model_dump())


@router.post("/das/retention/handovers", status_code=201)
def record_handover(payload: HandoverRequest, conn: Conn, user: CurrentUser):
    """登记责任终止或转让的可追溯移交（判据 R3）。"""
    return _wrap(svc.record_handover, conn, actor=user, **payload.model_dump())


@router.post("/das/retention/batches", status_code=201)
def create_batch(payload: BatchRequest, conn: Conn, user: CurrentUser):
    """建立销毁批次。放进批次不等于会被销毁。"""
    return _wrap(svc.create_batch, conn, actor=user, **payload.model_dump())


@router.post("/das/retention/batches/{batch_id}/checks", status_code=201)
def record_check(batch_id: int, payload: CheckRequest, conn: Conn, user: CurrentUser):
    """登记三项核查之一的结果（判据 R4）。每项都要写结果，不是一个布尔值。"""
    return _wrap(svc.record_check, conn, batch_id=batch_id, actor=user, **payload.model_dump())


@router.post("/das/retention/batches/{batch_id}/approve")
def approve_batch(batch_id: int, conn: Conn, user: CurrentUser):
    """批准销毁。按操作人的在任岗位决定占哪个批准位；两位须为两个不同自然人。"""
    return _wrap(svc.approve_batch, conn, batch_id=batch_id, actor=user)


@router.post("/das/retention/batches/{batch_id}/execute")
def execute_batch(batch_id: int, conn: Conn, user: CurrentUser):
    """执行销毁。三项核查、双人批准、冻结、截止日、「长期」类移交全部由数据库逐条判。"""
    return _wrap(svc.execute_batch, conn, batch_id=batch_id, actor=user)


@router.post("/das/retention/restore-checks", status_code=201)
def record_restore_check(payload: RestoreCheckRequest, conn: Conn, user: CurrentUser):
    """登记从备份恢复后的重新核验（判据 R7）。"""
    return _wrap(svc.record_restore_check, conn, actor=user, **payload.model_dump())
