"""事件报告端点（M6，UG-DAP-12）。

读取对所有登录用户开放，但**台账视图不带报告人**——保密件与匿名件的报告人身份
由服务层剥掉（步骤 9：报告人信息保密）。写入要 DAS_OCCURRENCE_MANAGE。

有一处与别的模块不同: 时限相关的查询(/deadlines、/clock-pending)不设写权限门槛,
也不分页。超 48 小时是要被看见的, 不是要被收起来的。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import das_occurrences as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["事件报告"], route_class=TransactionalRoute)
_manage = require(Perm.DAS_OCCURRENCE_MANAGE)


class RegisterRequest(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    received_at: dt.datetime
    received_via: str = Field(default="WRITTEN", pattern="^(PHONE|WRITTEN|EMAIL|OTHER)$")
    disclosure: str = Field(default="NAMED", pattern="^(NAMED|CONFIDENTIAL|ANONYMOUS)$")
    reporter_user_id: str | None = None
    reporter_name_text: str | None = Field(default=None, max_length=128)
    report_no: str | None = Field(default=None, max_length=64)
    # 报告内容六项: 全部可选。判据 M6-2——信息不全的先报已知部分,
    # 在这里做成必填会直接造成超 48 小时。
    occurrence_at: dt.datetime | None = None
    occurrence_place: str | None = Field(default=None, max_length=256)
    aircraft_serial: str | None = Field(default=None, max_length=128)
    product_model: str | None = Field(default=None, max_length=128)
    part_marking: str | None = Field(default=None, max_length=256)
    failure_nature: str | None = Field(default=None, max_length=1000)
    preliminary_cause: str | None = Field(default=None, max_length=4000)
    backfilled: bool = False


class AssessRequest(BaseModel):
    category_seq: int = Field(ge=1, le=99)
    verdict: str = Field(pattern="^(YES|NO|UNSURE)$")
    note: str | None = Field(default=None, max_length=2000)


class ConcludeRequest(BaseModel):
    conclusion: str = Field(pattern="^(REPORTABLE|EXEMPT|OUT_OF_SCOPE|UNDETERMINED)$")
    containment_needed: bool = False
    containment_note: str | None = Field(default=None, max_length=2000)


class ExemptRequest(BaseModel):
    ground: str = Field(pattern="^(IMPROPER_MAINTENANCE|ABNORMAL_USE|ALREADY_REPORTED)$")
    rationale: str = Field(min_length=1, max_length=4000)
    evidence: str = Field(min_length=1, max_length=4000)


class ConfirmRequest(BaseModel):
    confirmed_at: dt.datetime
    reason: str | None = Field(default=None, max_length=2000)


class SubmitRequest(BaseModel):
    channel: str = Field(min_length=1, max_length=64)
    channel_note: str | None = Field(default=None, max_length=1000)
    submitted_at: dt.datetime | None = None
    reference_no: str | None = Field(default=None, max_length=128)
    receipt_ref: str | None = Field(default=None, max_length=128)
    is_supplement: bool = False


class InvestigateRequest(BaseModel):
    findings: str = Field(min_length=1, max_length=8000)
    root_cause: str = Field(min_length=1, max_length=4000)
    report_ref: str | None = Field(default=None, max_length=128)


class FeedbackRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- 读


@router.get("/das/occurrence-categories")
def list_categories(conn: Conn, user: CurrentUser):
    """CCAR-21.5（二）13 种应报告情形。初判须逐项核对。"""
    return svc.categories(conn)


@router.get("/das/occurrences")
def list_occurrences(conn: Conn, user: CurrentUser):
    return svc.register_list(conn)


@router.get("/das/occurrences/deadlines")
def list_deadlines(conn: Conn, user: CurrentUser, state: str | None = Query(None)):
    """48 日历小时时限。state 可取 NO_CLOCK/RUNNING/OVERDUE/LATE/ON_TIME。"""
    return svc.deadlines(conn, state)


@router.get("/das/occurrences/clock-pending")
def list_clock_pending(conn: Conn, user: CurrentUser):
    """属 21.5 范围却还没有确认存在时间的——计时没开始, 是待办不是合规。"""
    return svc.clock_pending(conn)


@router.get("/das/occurrences/registration-deviations")
def list_registration_deviations(conn: Conn, user: CurrentUser):
    """登记晚于接收超过 4 小时的（UG-DAP-12 步骤 2，含非工作日）。"""
    return svc.registration_deviations(conn)


@router.get("/das/occurrences/content-gaps")
def list_content_gaps(conn: Conn, user: CurrentUser):
    """报告内容缺项。只标记不阻止报送（判据 M6-2）。"""
    return svc.content_gaps(conn)


@router.get("/das/occurrences/feedback-due")
def list_feedback_due(conn: Conn, user: CurrentUser):
    return svc.feedback_due(conn)


@router.get("/das/occurrences/{occurrence_id}")
def get_occurrence(occurrence_id: int, conn: Conn, user: CurrentUser):
    try:
        return svc.detail(conn, occurrence_id)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.get("/das/occurrences/{occurrence_id}/triage")
def get_triage(occurrence_id: int, conn: Conn, user: CurrentUser):
    try:
        return svc.triage_progress(conn, occurrence_id)
    except LookupError as e:
        raise errors.not_found(str(e))


# ---------------------------------------------------------------- 写


@router.post("/das/occurrences", status_code=201)
def register(payload: RegisterRequest, conn: Conn, actor: dict = Depends(_manage)):
    try:
        return svc.register(conn, actor=actor, **payload.model_dump())
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/occurrences/{occurrence_id}/triage-items", status_code=201)
def assess(occurrence_id: int, payload: AssessRequest, conn: Conn,
           actor: dict = Depends(_manage)):
    try:
        return svc.assess_category(conn, occurrence_id=occurrence_id, actor=actor,
                                   **payload.model_dump())
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/occurrences/{occurrence_id}/conclude")
def conclude(occurrence_id: int, payload: ConcludeRequest, conn: Conn,
             actor: dict = Depends(_manage)):
    try:
        return svc.conclude(conn, occurrence_id=occurrence_id, actor=actor,
                            **payload.model_dump())
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/occurrences/{occurrence_id}/exemption", status_code=201)
def exempt(occurrence_id: int, payload: ExemptRequest, conn: Conn,
           actor: dict = Depends(_manage)):
    try:
        return svc.exempt(conn, occurrence_id=occurrence_id, actor=actor,
                          **payload.model_dump())
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/occurrences/{occurrence_id}/confirmed-at")
def set_confirmed(occurrence_id: int, payload: ConfirmRequest, conn: Conn,
                  actor: dict = Depends(_manage)):
    try:
        return svc.set_confirmed(conn, occurrence_id=occurrence_id, actor=actor,
                                 **payload.model_dump())
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/occurrences/{occurrence_id}/submissions", status_code=201)
def submit(occurrence_id: int, payload: SubmitRequest, conn: Conn,
           actor: dict = Depends(_manage)):
    try:
        return svc.submit(conn, occurrence_id=occurrence_id, actor=actor,
                          **payload.model_dump())
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/occurrences/{occurrence_id}/investigation", status_code=201)
def investigate(occurrence_id: int, payload: InvestigateRequest, conn: Conn,
                actor: dict = Depends(_manage)):
    try:
        return svc.investigate(conn, occurrence_id=occurrence_id, actor=actor,
                               **payload.model_dump())
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/occurrences/{occurrence_id}/feedback", status_code=201)
def feedback(occurrence_id: int, payload: FeedbackRequest, conn: Conn,
             actor: dict = Depends(_manage)):
    try:
        return svc.give_feedback(conn, occurrence_id=occurrence_id, actor=actor,
                                 **payload.model_dump())
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
