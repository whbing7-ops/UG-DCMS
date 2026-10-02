"""M3 项目端点：三条审定路径（第二批，判据 M3-1）。

**端点上没有按步骤编号分出来的接口**，只有一个"登记某步骤完成"。步骤适用于哪类项目、
前置步骤是什么、实质证据在哪张表，全部从 `das_project_step` 读——判据 M3-1 要的就是
按类型参数化。给每个步骤开一个端点等于把分支写回代码里，第三类迟早会沿着 STC 的分支
走到"签符合性声明"，而那一签就是对委托方的产品作了设计批准。

与基础能力各模块一样，端点上没有 `DAS_*_MANAGE` 权限：闸门在服务层按**岗位任命**判。
三道门槛来自三份不同的文件：立项与编号归资料管理负责人（UG-DAW-002 第 5 章），
批准立项归责任经理（UG-DAP-09 第 1 步），PMA 质量系统确认与取证归适航管理负责人。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_project as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["项目与审定路径"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-09")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class CreateRequest(BaseModel):
    project_no: str = Field(min_length=1, max_length=32)
    type_code: str = Field(min_length=1, max_length=8)
    name_cn: str = Field(min_length=1, max_length=256)
    aircraft_type: str | None = Field(default=None, max_length=64)
    product_desc: str | None = None
    caac_project_no: str | None = Field(default=None, max_length=64)
    certification_basis: str | None = Field(default=None, max_length=256)
    initiated_on: dt.date | None = None


class ApproveRequest(BaseModel):
    approval_ref: str = Field(min_length=1, max_length=128)
    approved_on: dt.date | None = None


class StepRequest(BaseModel):
    step_code: str = Field(min_length=1, max_length=32)
    record_ref: str = Field(min_length=1, max_length=256)
    completed_on: dt.date | None = None
    note: str | None = None


class PmaQualityRequest(BaseModel):
    manual_ref: str = Field(min_length=1, max_length=128)
    established: bool
    covers_project: bool
    materials_submitted: bool
    interface_ref: str = Field(min_length=1, max_length=256)
    confirmed_on: dt.date | None = None
    note: str | None = None


class StatementRequest(BaseModel):
    statement_no: str = Field(min_length=1, max_length=64)
    completion_confirm_ref: str = Field(min_length=1, max_length=128)
    verification_docs_ref: str = Field(min_length=1)
    signed_under_authority_ref: str | None = Field(default=None, max_length=256)
    signed_on: dt.date | None = None
    note: str | None = None


class ApprovalRequest(BaseModel):
    certificate_no: str = Field(min_length=1, max_length=64)
    issued_on: dt.date
    product_scope_ref: str = Field(min_length=1)
    renew_due_on: dt.date | None = None
    note: str | None = None


class HandoverRequest(BaseModel):
    item_code: str = Field(min_length=1, max_length=16)
    target_ref: str = Field(min_length=1, max_length=256)
    received_by: str
    handed_on: dt.date | None = None
    note: str | None = None


class CloseRequest(BaseModel):
    closed_on: dt.date | None = None


@router.get("/das/projects/types")
def types(conn: Conn, user: CurrentUser):
    """三条审定路径（设计输入第 8.2 节）。

    `yields_design_approval` 为 false 的第三类不出符合性声明、不产生设计批准——
    UG-DAP-07 第 12 步 d)：本单位不对委托方的产品作设计批准。
    """
    return svc.types(conn)


@router.get("/das/projects/steps")
def steps(conn: Conn, user: CurrentUser, type_code: str | None = Query(None)):
    """33 个步骤（UG-DAP-04 的 11 ＋ UG-DAP-07 的 13 ＋ UG-DAP-09 的 9）。

    给定 `type_code` 时只返回适用于该类型的。`is_inferred` 为真的那几条，
    适用性是我们判的而不是原文明定的，理由在 `note` 里——自评要分得清这两者。
    """
    return svc.steps(conn, type_code)


@router.get("/das/projects/step-coverage")
def step_coverage(conn: Conn, user: CurrentUser):
    """判据 M3-1 的证据。

    看 `exclusive` 而不是 `counts`：STC 与 PMA 都是 30 步，条数一样，只有按**集合**
    比才看得出 STC 独有第 4 步（要点核对）、PMA 独有第 3 步（质量系统确认）。
    `unassigned` 平时只应有 UG-DAP-09 第 2 步（首次申请 DOA 本身，归 M10）。
    """
    return svc.step_coverage(conn)


@router.get("/das/projects")
def projects(conn: Conn, user: CurrentUser, type_code: str | None = Query(None),
             open_only: bool = Query(False)):
    """项目台账。本单位项目编号与局方受理编号并列（UG-DAW-002 第 5 章）。"""
    return svc.projects(conn, type_code=type_code, open_only=open_only)


@router.get("/das/projects/oversight")
def oversight(conn: Conn, user: CurrentUser):
    """交独立监督核对的清单，均不阻断。

    `delegated` 由"授权人员"而非在任责任经理签署的符合性声明（UG-DAP-07 第 11 步明文
    允许，但系统判不了该授权是否覆盖签署事项——待澄清项第 1 条）；
    `handover_pending` 已取证而转段未齐；`renewal` PMA 项目单的 2 年延续。
    """
    return svc.oversight(conn)


@router.get("/das/projects/{project_no}")
def project(conn: Conn, user: CurrentUser, project_no: str):
    """单个项目：按其类型适用的步骤进展、声明、设计批准、转段与 PMA 质量确认。"""
    return _wrap(svc.project, conn, project_no)


@router.post("/das/projects", status_code=201)
def create(conn: Conn, user: CurrentUser, body: CreateRequest):
    """立项并登记项目编号（须在任资料管理负责人，UG-DAW-002 第 5 章）。

    编号前缀由类型决定（UG-STC／UG-PMA／UG-SUP），格式为前缀＋两位序号＋版本字母＋
    两位年份。前缀对不上的项目在台账里会被归错类，故由 DCMS-INV-091 当场拒绝。
    """
    return _wrap(svc.create, conn, actor=user, **body.model_dump())


@router.post("/das/projects/{project_no}/approval", status_code=201)
def approve(conn: Conn, user: CurrentUser, project_no: str, body: ApproveRequest):
    """批准立项（须在任责任经理，UG-DAP-09 第 1 步）。未批准不得登记后续步骤。"""
    return _wrap(svc.approve, conn, project_no=project_no, actor=user,
                 **body.model_dump())


@router.post("/das/projects/{project_no}/steps", status_code=201)
def complete_step(conn: Conn, user: CurrentUser, project_no: str, body: StepRequest):
    """登记一个步骤完成。

    适用性、前置步骤、实质证据表都从配置读（判据 M3-1）。PMA 的"申请"要在
    "生产质量系统确认"之后，而且确认记录本身必须存在——拦的是"没确认"，不是"没登记"。
    """
    return _wrap(svc.complete_step, conn, project_no=project_no, actor=user,
                 **body.model_dump())


@router.post("/das/projects/{project_no}/pma-quality", status_code=201)
def confirm_pma_quality(conn: Conn, user: CurrentUser, project_no: str,
                        body: PmaQualityRequest):
    """PMA 生产质量系统确认（仅 PMA，须在任适航管理负责人）。

    三项有一项为否就不算确认具备——把"不具备"写成"已确认"的下一步就是提交 PMA 申请。
    该质量系统按 CCAR-21.307 由生产单位另行建立，不在本设计保证系统范围内。
    """
    return _wrap(svc.confirm_pma_quality, conn, project_no=project_no, actor=user,
                 **body.model_dump())


@router.post("/das/projects/{project_no}/statements", status_code=201)
def sign_statement(conn: Conn, user: CurrentUser, project_no: str,
                   body: StatementRequest):
    """签署符合性声明（表-21-160／UG-DAF-03）。

    第三类项目签不了。签署人不是在任责任经理时须写明纸面授权依据，系统**不冒充校验**
    该授权覆盖了符合性声明这件事（判据 I10.FILE_TYPE 语义不符，待澄清项第 1 条）。
    """
    return _wrap(svc.sign_statement, conn, project_no=project_no, actor=user,
                 **body.model_dump())


@router.post("/das/projects/{project_no}/design-approval", status_code=201)
def record_approval(conn: Conn, user: CurrentUser, project_no: str,
                    body: ApprovalRequest):
    """登记设计批准（须在任适航管理负责人）。

    类别由项目类型决定，不由调用方给。证后活动（M8）挂在这里而不是项目上：
    项目有结束时间，证件长期有效，PMA 项目单还要每 2 年续。
    """
    return _wrap(svc.record_approval, conn, project_no=project_no, actor=user,
                 **body.model_dump())


@router.post("/das/projects/{project_no}/handovers", status_code=201)
def record_handover(conn: Conn, user: CurrentUser, project_no: str,
                    body: HandoverRequest):
    """登记转段移交的一项：持续适航／生产协调／归档（UG-DAP-09 第 9 步）。"""
    return _wrap(svc.record_handover, conn, project_no=project_no, actor=user,
                 **body.model_dump())


@router.post("/das/projects/{project_no}/closure", status_code=201)
def close(conn: Conn, user: CurrentUser, project_no: str, body: CloseRequest):
    """结项。转段三项齐备才放行，并报出还差哪几项。"""
    return _wrap(svc.close, conn, project_no=project_no, actor=user, **body.model_dump())
