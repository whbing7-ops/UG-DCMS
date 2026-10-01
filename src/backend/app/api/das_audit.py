"""独立监督与内部审核端点（M2，UG-DAP-13）。

**这些端点上没有 DAS_*_MANAGE 权限。** 不是漏了 —— 判据 M2-2 要求本模块是独立
权限域、被监督对象无写权限、`admin` 角色亦然。写权限一挂到角色上，持有该角色的人
就能写监督记录，I6／I7 就落空了。所以端点只要求"已登录"，真正的闸门在服务层按
**岗位任命**判：排计划／实施审核／出报告须在任独立监督负责人，批准计划须在任责任
经理，确认审核员资格须在任适航管理负责人。不满足返回 403。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_audit as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["独立监督与内部审核"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-13")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class AuditorRequest(BaseModel):
    user_id: str | None = None
    external_name: str | None = Field(default=None, max_length=128)
    external_org: str | None = Field(default=None, max_length=256)
    method_training_ref: str = Field(min_length=1, max_length=512)
    ccar21_training_ref: str = Field(min_length=1, max_length=512)
    manual_training_ref: str = Field(min_length=1, max_length=512)
    external_evidence_ref: str | None = Field(default=None, max_length=512)
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None


class RevokeAuditorRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


class PlanRequest(BaseModel):
    kind: str = Field(pattern="^(DAS_SUPERVISION|QMS_AUDIT)$")
    period_from: dt.date
    period_to: dt.date
    scope_note: str = Field(min_length=1, max_length=4000)
    document_ref: str | None = Field(default=None, max_length=128)
    supersedes: int | None = None
    change_reason: str | None = Field(default=None, max_length=2000)


class PlanItemRequest(BaseModel):
    seq: int = Field(ge=1)
    scope_kind: str = Field(pattern="^(DEPARTMENT|PROCESS|CHECKLIST|FUNCTION)$")
    scope_ref: str = Field(min_length=1, max_length=512)
    scope_owner: str | None = None
    planned_from: dt.date | None = None
    planned_to: dt.date | None = None


class TriggerRequest(BaseModel):
    trigger_kind: str = Field(pattern="^(SERIOUS_SERVICE_PROBLEM|MAJOR_CHANGE|"
                                      "NEW_CAPABILITY|SITE_RELOCATION|KEY_PERSONNEL_CHANGE)$")
    occurred_on: dt.date
    description: str = Field(min_length=1, max_length=4000)


class ConductRequest(BaseModel):
    kind: str = Field(pattern="^(DAS_SUPERVISION|QMS_AUDIT)$")
    plan_item_id: int | None = None
    trigger_id: int | None = None
    scope_ref: str = Field(min_length=1, max_length=512)
    scope_owner: str | None = None
    criteria_ccar21: bool = False
    criteria_ap2118_d: bool = False
    criteria_checklist: bool = False
    criteria_manual: bool = False
    criteria_other: str | None = Field(default=None, max_length=1000)
    lead_auditor: int
    conducted_from: dt.date
    conducted_to: dt.date
    audit_ref: str | None = Field(default=None, max_length=64)


class AssignAuditorRequest(BaseModel):
    auditor_id: int


class FindingRequest(BaseModel):
    clause_ref: str = Field(min_length=1, max_length=256)
    verdict: str = Field(pattern="^(CONFORM|NONCONFORM|OBSERVATION)$")
    fact: str = Field(min_length=1, max_length=8000)
    evidence: str = Field(min_length=1, max_length=8000)
    confirmed_with_auditee: bool = False
    auditee_rep: str | None = Field(default=None, max_length=128)
    ncr_id: int | None = None


class CoverageRequest(BaseModel):
    checklist_item_ids: list[int] = Field(min_length=1)


class ReportRequest(BaseModel):
    report_ref: str = Field(min_length=1, max_length=128)
    conclusion: str = Field(min_length=1, max_length=8000)
    issued_to_am: str
    issued_to_action_owner: str | None = None


class IsmFunctionAuditRequest(BaseModel):
    report_ref: str = Field(min_length=1, max_length=128)
    period_from: dt.date
    period_to: dt.date
    is_external: bool
    auditor_user_id: str | None = None
    external_name: str | None = Field(default=None, max_length=128)
    external_org: str | None = Field(default=None, max_length=256)
    designation_basis: str | None = Field(default=None, max_length=2000)
    chaired_by_am: bool = False
    auditor_capability_note: str | None = Field(default=None, max_length=2000)
    finding_plan_completeness: str = Field(min_length=1, max_length=4000)
    finding_execution_rate: str = Field(min_length=1, max_length=4000)
    finding_adequacy: str = Field(min_length=1, max_length=4000)
    finding_car_closure: str = Field(min_length=1, max_length=4000)
    finding_auditor_qual: str = Field(min_length=1, max_length=4000)
    finding_independence: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- 读


@router.get("/das/audit/cycle-status")
def cycle_status(conn: Conn, user: CurrentUser):
    """两类活动的周期状态，分别计算（判据 M2-1）。12 个月逾期即为不符合项。"""
    return svc.cycle_status(conn)


@router.get("/das/audit/auditors")
def auditors(conn: Conn, user: CurrentUser):
    return svc.auditors(conn)


@router.get("/das/audit/plans")
def plans(conn: Conn, user: CurrentUser, kind: str | None = Query(None)):
    return svc.plans(conn, kind)


@router.get("/das/audit/triggers-due")
def triggers_due(conn: Conn, user: CurrentUser):
    """触发了专项审核却未启动的（第 7 章：为不符合项）。"""
    return svc.triggers_due(conn)


@router.get("/das/audit/checklist-coverage")
def checklist_coverage(conn: Conn, user: CurrentUser):
    """独立监督对符合性检查单的覆盖率，以及缺的是哪几条。"""
    return svc.checklist_coverage(conn)


@router.get("/das/audit/ism-function")
def ism_function_audits(conn: Conn, user: CurrentUser):
    return svc.ism_function_audits(conn)


@router.get("/das/audit/ism-function/due")
def ism_function_due(conn: Conn, user: CurrentUser):
    return svc.ism_function_audit_due(conn)


@router.get("/das/audit")
def list_audits(conn: Conn, user: CurrentUser):
    return svc.register_list(conn)


@router.get("/das/audit/{audit_id}")
def get_audit(audit_id: int, conn: Conn, user: CurrentUser):
    return _wrap(svc.detail, conn, audit_id)


# ---------------------------------------------------------------- 写


@router.post("/das/audit/auditors", status_code=201)
def qualify_auditor(payload: AuditorRequest, conn: Conn, user: CurrentUser):
    """确认审核员资格。须在任适航管理负责人（步骤 4）。"""
    return _wrap(svc.qualify_auditor, conn, actor=user, **payload.model_dump())


@router.post("/das/audit/auditors/{auditor_id}/revoke")
def revoke_auditor(auditor_id: int, payload: RevokeAuditorRequest, conn: Conn,
                   user: CurrentUser):
    _wrap(svc.revoke_auditor, conn, auditor_id=auditor_id, actor=user,
          **payload.model_dump())
    return {"ok": True}


@router.post("/das/audit/plans", status_code=201)
def create_plan(payload: PlanRequest, conn: Conn, user: CurrentUser):
    """编制计划。须在任独立监督负责人；一份计划只能是一类活动（判据 M2-1）。"""
    return _wrap(svc.create_plan, conn, actor=user, **payload.model_dump())


@router.post("/das/audit/plans/{plan_id}/approve")
def approve_plan(plan_id: int, conn: Conn, user: CurrentUser):
    """批准计划。须在任责任经理（步骤 3）。"""
    return _wrap(svc.approve_plan, conn, plan_id=plan_id, actor=user)


@router.post("/das/audit/plans/{plan_id}/items", status_code=201)
def add_plan_item(plan_id: int, payload: PlanItemRequest, conn: Conn, user: CurrentUser):
    return _wrap(svc.add_plan_item, conn, plan_id=plan_id, actor=user,
                 **payload.model_dump())


@router.post("/das/audit/triggers", status_code=201)
def record_trigger(payload: TriggerRequest, conn: Conn, user: CurrentUser):
    return _wrap(svc.record_trigger, conn, actor=user, **payload.model_dump())


@router.post("/das/audit", status_code=201)
def conduct(payload: ConductRequest, conn: Conn, user: CurrentUser):
    return _wrap(svc.conduct, conn, actor=user, **payload.model_dump())


@router.post("/das/audit/{audit_id}/auditors", status_code=201)
def assign_auditor(audit_id: int, payload: AssignAuditorRequest, conn: Conn,
                   user: CurrentUser):
    return _wrap(svc.assign_auditor, conn, audit_id=audit_id, actor=user,
                 **payload.model_dump())


@router.post("/das/audit/{audit_id}/findings", status_code=201)
def record_finding(audit_id: int, payload: FindingRequest, conn: Conn, user: CurrentUser):
    return _wrap(svc.record_finding, conn, audit_id=audit_id, actor=user,
                 **payload.model_dump())


@router.post("/das/audit/{audit_id}/checklist-coverage", status_code=201)
def cover_checklist(audit_id: int, payload: CoverageRequest, conn: Conn, user: CurrentUser):
    return _wrap(svc.cover_checklist_items, conn, audit_id=audit_id, actor=user,
                 **payload.model_dump())


@router.post("/das/audit/{audit_id}/report", status_code=201)
def issue_report(audit_id: int, payload: ReportRequest, conn: Conn, user: CurrentUser):
    """出具报告。直接报送责任经理，不经被审核部门转交（第 7 章）。"""
    return _wrap(svc.issue_report, conn, audit_id=audit_id, actor=user,
                 **payload.model_dump())


@router.post("/das/audit/ism-function", status_code=201)
def record_ism_function_audit(payload: IsmFunctionAuditRequest, conn: Conn,
                              user: CurrentUser):
    """对独立监督职能自身的审核。须在任责任经理组织（步骤 10）。"""
    return _wrap(svc.record_ism_function_audit, conn, actor=user, **payload.model_dump())
