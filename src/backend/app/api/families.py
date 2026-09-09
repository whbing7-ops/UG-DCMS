"""设计族、Dash P/N、发号中心端点。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require, require_password_changed
from ..rbac import Perm
from ..services import families as fam_svc, naming, numbering

router = APIRouter(tags=["设计族与发号"], route_class=TransactionalRoute)


# ---------------- 请求模型 ----------------
class NamePreviewRequest(BaseModel):
    core_term_id: str
    qualifier_1_id: str | None = None
    qualifier_2_id: str | None = None


class SimilarSearchRequest(BaseModel):
    primary_class_code: str
    physical_class_id: str
    core_term_id: str
    qualifier_ids: list[str] = Field(default_factory=list)


class FamilyCreateRequest(BaseModel):
    primary_class_code: str
    physical_class_id: str
    object_level_code: str
    core_term_id: str
    qualifier_1_id: str | None = None
    qualifier_2_id: str | None = None
    primary_function_id: str
    family_definition: str = Field(min_length=1, max_length=1000)
    allowed_variation: str = Field(min_length=1, max_length=1000)
    excluded_variation: str = Field(min_length=1, max_length=1000)
    new_family_reason: str = Field(min_length=1, max_length=500)
    classification_note: str | None = Field(default=None, max_length=500)


class DashCreateRequest(BaseModel):
    formal_name_cn: str = Field(min_length=1, max_length=128)
    formal_name_en: str = Field(min_length=1, max_length=128)
    object_level_code: str
    difference_summary: str = Field(min_length=1, max_length=500)
    requested_dash: int | None = Field(default=None, ge=1, le=999)


class SubmitRequest(BaseModel):
    approver_user_id: str


# ---------------- 命名 ----------------
@router.post("/naming/preview")
def name_preview(payload: NamePreviewRequest, conn: Conn, user: CurrentUser):
    """预览系统将生成的基本图号名称 — NAM §9。"""
    try:
        return naming.compose_names(conn, payload.core_term_id,
                                    payload.qualifier_1_id, payload.qualifier_2_id)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.post("/naming/check")
def name_check(conn: Conn, user: CurrentUser,
               text: str = Body(..., embed=True, max_length=256),
               scope: str = Body("DASH", embed=True)):
    """受限词校验 — AC-NAM-01。主要用于 Dash P/N 名称等自由文本字段。"""
    if scope not in ("BASIC_DRAWING", "DASH"):
        raise errors.bad_request("scope 只能是 BASIC_DRAWING 或 DASH")
    return naming.check_restricted(conn, text, scope)


# ---------------- 设计族 ----------------
@router.post("/families/similar-search")
def similar_search(payload: SimilarSearchRequest, conn: Conn, user: CurrentUser):
    """相似设计族检索 — AC-FAM-01。新建族前必须执行。"""
    return fam_svc.similar_search(conn, payload.primary_class_code,
                                  payload.physical_class_id, payload.core_term_id,
                                  payload.qualifier_ids)


@router.get("/families")
def list_families(conn: Conn, user: CurrentUser,
                  primary_class_code: str | None = None,
                  status: str | None = None, q: str | None = None):
    return fam_svc.list_families(conn, primary_class_code, status, q)


@router.get("/families/{family_id}")
def get_family(family_id: str, conn: Conn, user: CurrentUser):
    fam = fam_svc.get_family(conn, family_id)
    if fam is None:
        raise errors.not_found("设计族不存在")
    return fam


@router.post("/families", status_code=201)
def create_family(payload: FamilyCreateRequest, conn: Conn,
                  actor: dict = Depends(require(Perm.DRAFT_WRITE)),
                  _: dict = Depends(require_password_changed)):
    try:
        return fam_svc.create_family(
            conn, primary_class_code=payload.primary_class_code,
            physical_class_id=payload.physical_class_id,
            object_level_code=payload.object_level_code,
            core_term_id=payload.core_term_id,
            qualifier_1_id=payload.qualifier_1_id,
            qualifier_2_id=payload.qualifier_2_id,
            primary_function_id=payload.primary_function_id,
            family_definition=payload.family_definition,
            allowed_variation=payload.allowed_variation,
            excluded_variation=payload.excluded_variation,
            new_family_reason=payload.new_family_reason,
            classification_note=payload.classification_note, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/families/{family_id}/submit")
def submit_family(family_id: str, payload: SubmitRequest, conn: Conn,
                  actor: dict = Depends(require(Perm.SUBMIT))):
    try:
        return fam_svc.submit_family(conn, family_id, payload.approver_user_id, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/families/{family_id}/approve")
def approve_family(family_id: str, conn: Conn,
                   comments: str = Query("同意", max_length=500),
                   actor: dict = Depends(require(Perm.APPROVE))):
    """批准设计族并分配基本图号。申请人不得批准自己的申请(INV-025)。"""
    try:
        return fam_svc.approve_family(conn, family_id, comments, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


# ---------------- Dash P/N ----------------
@router.get("/families/{family_id}/dashes")
def list_dashes(family_id: str, conn: Conn, user: CurrentUser):
    return fam_svc.list_dashes(conn, family_id)


@router.post("/families/{family_id}/dashes", status_code=201)
def create_dash(family_id: str, payload: DashCreateRequest, conn: Conn,
                actor: dict = Depends(require(Perm.DRAFT_WRITE)),
                _: dict = Depends(require_password_changed)):
    try:
        return fam_svc.create_dash(
            conn, family_id, formal_name_cn=payload.formal_name_cn,
            formal_name_en=payload.formal_name_en,
            object_level_code=payload.object_level_code,
            difference_summary=payload.difference_summary,
            actor=actor, requested_dash=payload.requested_dash)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


# ---------------- 发号中心 ----------------
@router.get("/families/{family_id}/numbers")
def family_numbers(family_id: str, conn: Conn, user: CurrentUser):
    """族内 Dash 号占用全貌 — SRS-NUM-005/006。

    同时给出下一个可用号与空缺清单。空缺区分"从未分配"与"已作废"两种,
    后者不可复用(INV-004)。
    """
    return {
        "occupied": numbering.occupied_numbers(conn, "DASH", family_id),
        "next_available": numbering.next_available(
            conn, "DASH", family_id, numbering.DASH_MIN, numbering.DASH_MAX),
        "gaps": numbering.gaps(conn, family_id),
    }


@router.post("/families/{family_id}/numbers/{dash}/cancel")
def cancel_dash_number(family_id: str, dash: int, conn: Conn,
                       reason: str = Query(..., min_length=1, max_length=256),
                       actor: dict = Depends(require(Perm.NUMBER_ALLOCATE))):
    try:
        numbering.cancel_dash(conn, family_id, dash, reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": f"Dash -{numbering.dash_suffix(dash)} 已作废, 该号永不复用"}


@router.post("/families/{family_id}/numbers/{dash}/skip")
def skip_dash_number(family_id: str, dash: int, conn: Conn,
                     reason: str = Query(..., min_length=1, max_length=256),
                     actor: dict = Depends(require(Perm.NUMBER_ALLOCATE))):
    """跳号并记录理由 — SRS-NUM-004。"""
    try:
        numbering.skip_dash(conn, family_id, dash, reason, actor)
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": f"Dash -{numbering.dash_suffix(dash)} 已记为跳号"}


@router.get("/numbers/basic-drawing")
def basic_drawing_numbers(conn: Conn, user: CurrentUser,
                          primary_class_code: str | None = None):
    rows = numbering.occupied_numbers(conn, "BASIC_DRAWING", None)
    if primary_class_code:
        prefix = numbering.CLASS_PREFIX.get(primary_class_code, "")
        rows = [r for r in rows if r["allocated_number"].startswith(prefix)]
    return {"occupied": rows,
            "next_sequence": numbering.next_available(
                conn, "BASIC_DRAWING", None, numbering.BASIC_MIN, numbering.BASIC_MAX)}
