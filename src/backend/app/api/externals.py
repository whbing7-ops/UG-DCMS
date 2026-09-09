"""外部件、软件对象、审批中心端点。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import approvals as ap_svc, externals as ext_svc

router = APIRouter(tags=["外部件与软件"], route_class=TransactionalRoute)


class ExternalCreateRequest(BaseModel):
    namespace_code: str = Field(min_length=1, max_length=32)
    external_part_number: str = Field(min_length=1, max_length=64)
    name_cn: str = Field(min_length=1, max_length=128)
    name_en: str | None = Field(default=None, max_length=128)
    manufacturer_code: str | None = Field(default=None, max_length=32)
    external_class_code: str = Field(min_length=3, max_length=3)
    project_code: str | None = Field(default=None, min_length=1, max_length=64)
    project_applicability: str | None = Field(default=None, max_length=500)
    project_evaluation_basis: str | None = Field(default=None, max_length=1000)


class TechnicalStateRequest(BaseModel):
    supplier_revision: str = Field(min_length=1, max_length=32)
    supplier_document: str | None = Field(default=None, max_length=128)
    supplier_document_date: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=500)


class ProjectControlRequest(BaseModel):
    project_code: str = Field(min_length=1, max_length=64)
    applicability: str = Field(min_length=1, max_length=500)
    evaluation_basis: str | None = Field(default=None, max_length=1000)


class SubmitApprovalRequest(BaseModel):
    approver_user_id: str


class SoftwareCreateRequest(BaseModel):
    software_number: str = Field(min_length=1, max_length=64)
    name_cn: str = Field(min_length=1, max_length=128)
    name_en: str | None = Field(default=None, max_length=128)
    software_type: str = Field(pattern="^(SOFTWARE|FIRMWARE|CONFIG_DATA|LOADABLE)$")


class VersionRequest(BaseModel):
    version: str = Field(min_length=1, max_length=32)
    build: str = Field(default="", max_length=32)
    hash_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    notes: str | None = Field(default=None, max_length=500)


# ---------------- 外部件 ----------------
@router.get("/external-part-classes")
def external_part_classes(conn: Conn, user: CurrentUser):
    from ..db import fetch_all
    return fetch_all(conn, """
        SELECT code, name_cn, definition FROM external_part_class
         WHERE status='ACTIVE' ORDER BY sort_order, code
    """)


@router.get("/external-parts")
def list_external(conn: Conn, user: CurrentUser,
                  namespace_code: str | None = None, q: str | None = None):
    return ext_svc.list_external(conn, namespace_code, q)


@router.post("/external-parts", status_code=201)
def create_external(payload: ExternalCreateRequest, conn: Conn,
                    actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    """登记外部件。同一件号在不同来源下是不同对象（INV-016）。"""
    try:
        return ext_svc.create_external(
            conn, namespace_code=payload.namespace_code,
            external_part_number=payload.external_part_number,
            name_cn=payload.name_cn, name_en=payload.name_en,
            manufacturer_code=payload.manufacturer_code,
            external_class_code=payload.external_class_code,
            project_code=payload.project_code,
            project_applicability=payload.project_applicability,
            project_evaluation_basis=payload.project_evaluation_basis, actor=actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.get("/external-parts/{object_code}")
def get_external(object_code: str, conn: Conn, user: CurrentUser):
    ep = ext_svc.get_external(conn, object_code)
    if ep is None:
        raise errors.not_found(f"外部件不存在: {object_code}")
    return ep


@router.post("/external-parts/{object_code}/states", status_code=201)
def add_state(object_code: str, payload: TechnicalStateRequest, conn: Conn,
              actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    """登记供应商技术状态。默认 DRAFT——收到不等于接受。"""
    try:
        return ext_svc.add_technical_state(
            conn, object_code, supplier_revision=payload.supplier_revision,
            supplier_document=payload.supplier_document,
            supplier_document_date=payload.supplier_document_date,
            notes=payload.notes, actor=actor)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.post("/external-states/{state_id}/accept")
def accept_state(state_id: str, conn: Conn,
                 comments: str = Query("", max_length=500),
                 actor: dict = Depends(require(Perm.APPROVE))):
    """接受技术状态——基线只能引用已接受的状态（INV-017）。"""
    try:
        return ext_svc.accept_technical_state(conn, state_id, comments, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/external-states/{state_id}/submit")
def submit_state(state_id: str, payload: SubmitApprovalRequest, conn: Conn,
                 actor: dict = Depends(require(Perm.SUBMIT))):
    try:
        return ext_svc.submit_technical_state(conn,state_id,payload.approver_user_id,actor)
    except LookupError as e: raise errors.not_found(str(e))
    except ValueError as e: raise errors.bad_request(str(e))


@router.post("/external-states/{state_id}/reject")
def reject_state(state_id: str, conn: Conn,
                 reason: str = Query(..., min_length=1, max_length=500),
                 actor: dict = Depends(require(Perm.APPROVE))):
    try:
        ext_svc.reject_technical_state(conn, state_id, reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "技术状态已拒绝"}


@router.post("/external-parts/{object_code}/project-controls",status_code=201)
def add_project_control(object_code:str,payload:ProjectControlRequest,conn:Conn,
  actor:dict=Depends(require(Perm.DRAFT_WRITE))):
    try:return ext_svc.add_project_control(conn,object_code,payload.project_code,
      payload.applicability,payload.evaluation_basis,actor)
    except LookupError as e:raise errors.not_found(str(e))


@router.post("/external-project-controls/{control_id}/submit")
def submit_project_control(control_id:str,payload:SubmitApprovalRequest,conn:Conn,
  actor:dict=Depends(require(Perm.SUBMIT))):
    try:return ext_svc.submit_project_control(conn,control_id,payload.approver_user_id,actor)
    except LookupError as e:raise errors.not_found(str(e))
    except ValueError as e:raise errors.bad_request(str(e))


@router.post("/external-project-controls/{control_id}/approve")
def approve_project_control(control_id:str,conn:Conn,comments:str=Query("同意项目准入",max_length=500),
  actor:dict=Depends(require(Perm.APPROVE))):
    try:return ext_svc.approve_project_control(conn,control_id,comments,actor)
    except LookupError as e:raise errors.not_found(str(e))
    except ValueError as e:raise errors.bad_request(str(e))


# ---------------- 软件对象 ----------------
@router.get("/software")
def list_software(conn: Conn, user: CurrentUser, q: str | None = None):
    return ext_svc.list_software(conn, q)


@router.post("/software", status_code=201)
def create_software(payload: SoftwareCreateRequest, conn: Conn,
                    actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    return ext_svc.create_software(
        conn, software_number=payload.software_number, name_cn=payload.name_cn,
        name_en=payload.name_en, software_type=payload.software_type, actor=actor)


@router.get("/software/{software_number}")
def get_software(software_number: str, conn: Conn, user: CurrentUser):
    so = ext_svc.get_software(conn, software_number)
    if so is None:
        raise errors.not_found(f"软件对象不存在: {software_number}")
    return so


@router.post("/software/{software_number}/versions", status_code=201)
def add_version(software_number: str, payload: VersionRequest, conn: Conn,
                actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        return ext_svc.add_version(
            conn, software_number, version=payload.version, build=payload.build,
            hash_sha256=payload.hash_sha256, notes=payload.notes, actor=actor)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.post("/software-versions/{version_id}/release")
def release_version(version_id: str, conn: Conn,
                    comments: str = Query("同意发布", max_length=500),
                    actor: dict = Depends(require(Perm.APPROVE))):
    try:
        return ext_svc.release_version(conn, version_id, comments, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/software-versions/{version_id}/submit")
def submit_version(version_id: str, payload: SubmitApprovalRequest, conn: Conn,
                   actor: dict = Depends(require(Perm.SUBMIT))):
    try:return ext_svc.submit_version(conn,version_id,payload.approver_user_id,actor)
    except LookupError as e:raise errors.not_found(str(e))
    except ValueError as e:raise errors.bad_request(str(e))


# ---------------- 审批中心 ----------------
@router.get("/approvals/summary")
def approval_summary(conn: Conn, user: CurrentUser):
    return ap_svc.summary(conn, user)


@router.get("/approvals/candidates")
def approval_candidates(conn: Conn, user: CurrentUser,
                        required_role: str = Query("APPROVER", max_length=32)):
    return ap_svc.available_approvers(conn, str(user["user_id"]), required_role)


@router.get("/approvals/inbox")
def approval_inbox(conn: Conn, user: CurrentUser):
    """待我处理的审批。已排除自己发起的——INV-025 不允许自批。"""
    return ap_svc.inbox(conn, user)


@router.get("/approvals/mine")
def my_requests(conn: Conn, user: CurrentUser, include_closed: bool = False):
    return ap_svc.my_requests(conn, user, include_closed)


@router.get("/approvals/pending")
def all_pending(conn: Conn, actor: dict = Depends(require(Perm.READ_AUDIT))):
    return ap_svc.all_pending(conn)


@router.get("/approvals/{request_id}")
def get_request(request_id: str, conn: Conn, user: CurrentUser):
    req = ap_svc.get_request(conn, request_id)
    if req is None:
        raise errors.not_found("审批申请不存在")
    return req


@router.post("/approvals/{request_id}/reject")
def reject(request_id: str, conn: Conn,
           reason: str = Query(..., min_length=1, max_length=500),
           actor: dict = Depends(require(Perm.APPROVE))):
    """拒绝。申请就此结束，申请人需另起新申请。"""
    try:
        return ap_svc.reject(conn, request_id, reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/approvals/{request_id}/return")
def send_back(request_id: str, conn: Conn,
              reason: str = Query(..., min_length=1, max_length=500),
              actor: dict = Depends(require(Perm.APPROVE))):
    """退回补充。与拒绝的区别：方向没问题但材料不足，改完再提交即可。"""
    try:
        return ap_svc.send_back(conn, request_id, reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/approvals/{request_id}/withdraw")
def withdraw(request_id: str, conn: Conn, user: CurrentUser,
             reason: str = Query(..., min_length=1, max_length=500)):
    """撤回自己发起且尚无人决定的申请。"""
    try:
        return ap_svc.withdraw(conn, request_id, reason, user)
    except LookupError as e:
        raise errors.not_found(str(e))
    except PermissionError as e:
        raise errors.forbidden(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
