"""不符合项与纠正措施端点（M7，UG-DAP-14），含工作日日历。

两处端点形状上的刻意安排：

  · 内部关闭与局方关闭是**两个端点**, 不是一个带参数的 close。局方开具的不符合项
    要"局方评估其合理性并验证后方可关闭"(步骤 8), 做成一个 close 并在内部判来源,
    就等于把那支笔交回给内部操作（判据 M7-1）。
  · 关闭验证的签署人取自当前登录人, 不从请求体里读。验证是本人的签署行为,
    让调用方指定"验证人是谁"等于允许代签。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import das_ncr as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["不符合项与纠正措施"], route_class=TransactionalRoute)
_manage = require(Perm.DAS_NCR_MANAGE)


class CalendarYearRequest(BaseModel):
    year: int = Field(ge=2000, le=2199)
    source_ref: str = Field(min_length=1, max_length=256)


class CalendarDayRequest(BaseModel):
    day: dt.date
    kind: str = Field(pattern="^(HOLIDAY|MAKEUP)$")
    name_cn: str | None = Field(default=None, max_length=64)
    source_ref: str = Field(min_length=1, max_length=256)


class NcrRequest(BaseModel):
    source: str = Field(pattern="^(CAAC|INTERNAL_AUDIT|SUPERVISION|INSPECTION|SUPPLIER|OCCURRENCE)$")
    ncr_class: str | None = Field(default=None, pattern="^(CLASS_1|CLASS_2|OBSERVATION)$")
    fact: str = Field(min_length=1, max_length=8000)
    basis_clause: str = Field(min_length=1, max_length=256)
    evidence: str = Field(min_length=1, max_length=8000)
    issued_on: dt.date | None = None
    received_on: dt.date | None = None
    source_ref: str | None = Field(default=None, max_length=256)
    ncr_no: str | None = Field(default=None, max_length=64)
    responsible_user: str | None = None
    responsible_dept: str | None = Field(default=None, max_length=128)
    occurrence_id: int | None = None
    concerns_ism: bool = False


class ContainRequest(BaseModel):
    measures: str = Field(min_length=1, max_length=4000)
    issued_docs_impact: str | None = Field(default=None, max_length=4000)


class AnalyseRequest(BaseModel):
    level: str = Field(pattern="^(PROCEDURE|MANAGEMENT|TRAINING|RESOURCE|SUPPLIER|HUMAN_ERROR_ONLY)$")
    analysis: str = Field(min_length=1, max_length=8000)


class CarRequest(BaseModel):
    correction: str = Field(min_length=1, max_length=8000)
    corrective_action: str = Field(min_length=1, max_length=8000)
    preventive_action: str = Field(min_length=1, max_length=8000)
    correction_owner: str
    corrective_owner: str
    preventive_owner: str
    correction_due: dt.date
    corrective_due: dt.date
    preventive_due: dt.date


class ExtensionRequest(BaseModel):
    requested_to: dt.date
    reason: str = Field(min_length=1, max_length=4000)


class ExtensionAgreementRequest(BaseModel):
    agreed_on: dt.date
    agreed_ref: str = Field(min_length=1, max_length=256)


class CaacReplyRequest(BaseModel):
    form_ref: str = Field(min_length=1, max_length=128)
    correction_completed_on: dt.date
    corrective_completed_on: dt.date
    confirmed_by: str
    submitted_on: dt.date | None = None


class ObservationRequest(BaseModel):
    assessment: str = Field(min_length=1, max_length=8000)
    disposition: str = Field(min_length=1, max_length=8000)
    to_management_review: bool = True


class VerifyRequest(BaseModel):
    evidence: str = Field(min_length=1, max_length=8000)
    effectiveness: str = Field(min_length=1, max_length=8000)
    designated_by: str | None = None
    designation_basis: str | None = Field(default=None, max_length=1000)
    verifier_qualification: str | None = Field(default=None, max_length=1000)
    independence_note: str | None = Field(default=None, max_length=1000)


class CaacClosureRequest(BaseModel):
    closed_ref: str = Field(min_length=1, max_length=256)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


# ---------------------------------------------------------------- 工作日日历


@router.get("/das/work-calendar/gaps")
def calendar_gaps(conn: Conn, user: CurrentUser):
    """哪些年份的节假日安排还没加载。未加载的年份算不出工作日时限。"""
    return svc.calendar_gaps(conn)


@router.get("/das/work-calendar")
def calendar_days(conn: Conn, user: CurrentUser, year: int | None = Query(None)):
    return svc.calendar_days(conn, year)


@router.post("/das/work-calendar/years", status_code=201)
def declare_year(payload: CalendarYearRequest, conn: Conn, actor: dict = Depends(_manage)):
    return _wrap(svc.declare_calendar_year, conn, actor=actor, **payload.model_dump())


@router.post("/das/work-calendar/days", status_code=201)
def add_day(payload: CalendarDayRequest, conn: Conn, actor: dict = Depends(_manage)):
    return _wrap(svc.add_calendar_day, conn, actor=actor, **payload.model_dump())


# ---------------------------------------------------------------- 读


@router.get("/das/ncr")
def list_ncr(conn: Conn, user: CurrentUser):
    return svc.register_list(conn)


@router.get("/das/ncr/deadlines")
def list_deadlines(conn: Conn, user: CurrentUser, only_open: bool = Query(False)):
    return svc.deadlines(conn, only_open)


@router.get("/das/ncr/overdue")
def list_overdue(conn: Conn, user: CurrentUser):
    return svc.overdue(conn)


@router.get("/das/ncr/escalation-risk")
def list_escalation_risk(conn: Conn, user: CurrentUser):
    """二类临近时限而缺 CAR 或缺答复的——第 7 章：将上升为一类问题。"""
    return svc.escalation_risk(conn)


@router.get("/das/ncr/recurrence")
def list_recurrence(conn: Conn, user: CurrentUser):
    return svc.recurrence(conn)


@router.get("/das/ncr/observations-open")
def list_observations_open(conn: Conn, user: CurrentUser):
    return svc.observations_open(conn)


@router.get("/das/ncr/preventive-review")
def list_preventive_review(conn: Conn, user: CurrentUser):
    """预防措施待人工复核的线索。系统不判语义雷同，只给线索。"""
    return svc.preventive_review(conn)


@router.get("/das/ncr/quarterly")
def list_quarterly(conn: Conn, user: CurrentUser):
    return svc.quarterly(conn)


@router.get("/das/ncr/{ncr_id}")
def get_ncr(ncr_id: int, conn: Conn, user: CurrentUser):
    return _wrap(svc.detail, conn, ncr_id)


# ---------------------------------------------------------------- 写


@router.post("/das/ncr", status_code=201)
def register(payload: NcrRequest, conn: Conn, actor: dict = Depends(_manage)):
    return _wrap(svc.register, conn, actor=actor, **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/containment", status_code=201)
def contain(ncr_id: int, payload: ContainRequest, conn: Conn, actor: dict = Depends(_manage)):
    return _wrap(svc.contain, conn, ncr_id=ncr_id, actor=actor, **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/root-causes", status_code=201)
def analyse(ncr_id: int, payload: AnalyseRequest, conn: Conn, actor: dict = Depends(_manage)):
    return _wrap(svc.analyse, conn, ncr_id=ncr_id, actor=actor, **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/car", status_code=201)
def draft_car(ncr_id: int, payload: CarRequest, conn: Conn, actor: dict = Depends(_manage)):
    return _wrap(svc.draft_car, conn, ncr_id=ncr_id, actor=actor, **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/extensions", status_code=201)
def request_extension(ncr_id: int, payload: ExtensionRequest, conn: Conn,
                      actor: dict = Depends(_manage)):
    return _wrap(svc.request_extension, conn, ncr_id=ncr_id, actor=actor,
                 **payload.model_dump())


@router.post("/das/ncr/extensions/{extension_id}/agreement")
def agree_extension(extension_id: int, payload: ExtensionAgreementRequest, conn: Conn,
                    actor: dict = Depends(_manage)):
    return _wrap(svc.record_extension_agreement, conn, extension_id=extension_id,
                 actor=actor, **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/caac-reply", status_code=201)
def submit_caac_reply(ncr_id: int, payload: CaacReplyRequest, conn: Conn,
                      actor: dict = Depends(_manage)):
    return _wrap(svc.submit_caac_reply, conn, ncr_id=ncr_id, actor=actor,
                 **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/observation-response", status_code=201)
def respond_observation(ncr_id: int, payload: ObservationRequest, conn: Conn,
                        actor: dict = Depends(_manage)):
    return _wrap(svc.respond_observation, conn, ncr_id=ncr_id, actor=actor,
                 **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/verification", status_code=201)
def verify_closure(ncr_id: int, payload: VerifyRequest, conn: Conn,
                   actor: dict = Depends(_manage)):
    """内部关闭验证（步骤 9）。验证人即当前登录人，不从请求体读取。"""
    return _wrap(svc.verify_closure, conn, ncr_id=ncr_id, actor=actor, **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/internal-closure")
def close_internally(ncr_id: int, conn: Conn, actor: dict = Depends(_manage)):
    """登记内部关闭。局方开具项不因此变为已关闭（判据 M7-1）。"""
    return _wrap(svc.close_internally, conn, ncr_id=ncr_id, actor=actor)


@router.post("/das/ncr/{ncr_id}/caac-closure")
def record_caac_closure(ncr_id: int, payload: CaacClosureRequest, conn: Conn,
                        actor: dict = Depends(_manage)):
    """登记局方的关闭确认。与内部关闭是两支笔，互相写不到。"""
    return _wrap(svc.record_caac_closure, conn, ncr_id=ncr_id, actor=actor,
                 **payload.model_dump())


@router.post("/das/ncr/{ncr_id}/systemic")
def mark_systemic(ncr_id: int, conn: Conn, actor: dict = Depends(_manage)):
    return _wrap(svc.mark_systemic, conn, ncr_id=ncr_id, actor=actor)
