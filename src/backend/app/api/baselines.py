"""设计基线端点。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import baselines as bl_svc

router = APIRouter(tags=["设计基线"], route_class=TransactionalRoute)


class BaselineCreateRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    copy_from_current: bool = False
    baseline_type: str = Field(default="DESIGN", pattern="^(FUNCTIONAL|ALLOCATED|DESIGN|PRODUCT|AS_BUILT)$")
    scope_note: str = Field(min_length=1, max_length=1000)
    change_reference: str | None = Field(default=None, max_length=128)
    project_code: str = Field(min_length=1, max_length=64)


class ItemAddRequest(BaseModel):
    item_type: str = Field(pattern="^(FILE_REVISION|BOM_SNAPSHOT|"
                                   "EXTERNAL_TECHNICAL_STATE|SOFTWARE_VERSION)$")
    target: str = Field(min_length=1, max_length=128,
                        description="可读标识, 如 'UG-A10001M001 Rev.00' 或 'SNAP-000001'")
    item_role: str | None = None
    notes: str | None = Field(default=None, max_length=500)


class SubmitRequest(BaseModel):
    approver_user_id: str


@router.get("/parts/{full_pn}/baselines")
def list_baselines(full_pn: str, conn: Conn, user: CurrentUser):
    try:
        return bl_svc.list_baselines(conn, full_pn)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.get("/parts/{full_pn}/configuration")
def current_configuration(full_pn: str, conn: Conn, user: CurrentUser):
    """该 P/N 当前锁定的完整技术状态 — AC-BL-01。"""
    try:
        return bl_svc.current_configuration(conn, full_pn)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.post("/parts/{full_pn}/baselines", status_code=201)
def create_baseline(full_pn: str, payload: BaselineCreateRequest, conn: Conn,
                    actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        return bl_svc.create_baseline(conn, full_pn, payload.reason, actor,
                                      payload.copy_from_current, payload.baseline_type,
                                      payload.scope_note, payload.change_reference,
                                      payload.project_code)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


# 注意: 本端点必须定义在 /baselines/{baseline_id} 之前。
# FastAPI 按声明顺序匹配, 若动态路由在先, "compare" 会被当成 baseline_id,
# 一路传到数据库才因 uuid 解析失败报 500。
@router.get("/baselines/compare")
def compare(conn: Conn, user: CurrentUser,
            a: str = Query(..., description="基线 ID"),
            b: str = Query(..., description="基线 ID")):
    """比较两条基线 — AC-BL-02。"""
    try:
        return bl_svc.compare(conn, a, b)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.get("/baselines/{baseline_id}")
def get_baseline(baseline_id: str, conn: Conn, user: CurrentUser):
    bl = bl_svc.get_baseline(conn, baseline_id)
    if bl is None:
        raise errors.not_found("基线不存在")
    return bl


@router.post("/baselines/{baseline_id}/items", status_code=201)
def add_item(baseline_id: str, payload: ItemAddRequest, conn: Conn,
             actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        return bl_svc.add_item(conn, baseline_id, item_type=payload.item_type,
                               target=payload.target, item_role=payload.item_role,
                               notes=payload.notes, actor=actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.delete("/baselines/items/{item_id}")
def remove_item(item_id: str, conn: Conn,
                actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        bl_svc.remove_item(conn, item_id, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "基线明细已移除"}


@router.get("/baselines/{baseline_id}/validate")
def validate(baseline_id: str, conn: Conn, user: CurrentUser):
    """发布前置校验。与数据库触发器同规则, 提前告知哪里不合格。"""
    try:
        return bl_svc.validate(conn, baseline_id)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.post("/baselines/{baseline_id}/submit")
def submit(baseline_id: str, payload: SubmitRequest, conn: Conn,
           actor: dict = Depends(require(Perm.SUBMIT))):
    try:
        return bl_svc.submit(conn, baseline_id, payload.approver_user_id, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/baselines/{baseline_id}/release")
def release(baseline_id: str, conn: Conn,
            comments: str = Query("同意发布", max_length=500),
            make_current: bool = Query(True),
            actor: dict = Depends(require(Perm.BASELINE_RELEASE))):
    """批准并发布基线。发布后内容冻结 (INV-011)。"""
    try:
        return bl_svc.release(conn, baseline_id, comments, actor, make_current)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/baselines/{baseline_id}/cancel")
def cancel(baseline_id: str, conn: Conn,
           reason: str = Query(..., min_length=1, max_length=256),
           actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        bl_svc.cancel(conn, baseline_id, reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "基线已取消"}
