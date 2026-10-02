"""M8 证后与对外发布端点（UG-DAP-11，17 步）。

挂在**设计批准**上而不是项目上（设计输入第 8.3 节：项目有结束时间，证件长期有效；
一个产品可能有多个 STC）。

按 2026-10-02 的决定，闸门只在第 7 章那五条「不得」上：

| 闸门 | 原文 |
|---|---|
| `DCMS-INV-122` | 已发布的资料**只能由原批准人或其上级撤销**；**止用通知未取得全部接收确认前不得认为撤销已完成** |
| `DCMS-INV-123` | **撤销期间，相关资料不得继续用于改装、生产或放行** |
| `DCMS-INV-124` | 偏离已批准设计类的询问**不得以答复代替更改**；**对外答复不得超出已批准的设计数据范围** |
| `ck_dpd_statement` | 局方批准的资料标明实际批准依据，**不得加注虚假的本单位批准声明** |

第 16 步的「生效或终止后 30 天内通知局方」原文标了「（法规）」，所以它在时限引擎里
（`L11.STC_TRANSFER_NOTICE`），受规章修订闸门保护，不是这里的一个字段。

其余各步做成记录，实质过程走线下并挂证据（`POST /das/offline/approvals`）。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_holder as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["证后与对外发布"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-11")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class PublishRequest(BaseModel):
    certificate_no: str = Field(min_length=1, max_length=64)
    doc_kind: str = Field(min_length=1, max_length=32)
    doc_no: str = Field(min_length=1, max_length=64)
    revision: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=256)
    approval_basis: str = Field(min_length=1, max_length=16)
    caac_approval_ref: str | None = Field(default=None, max_length=128)
    statement_text: str | None = None
    approved_on: dt.date | None = None
    released_on: dt.date | None = None


class DistributeRequest(BaseModel):
    recipient: str = Field(min_length=1, max_length=256)
    recipient_kind: str = Field(min_length=1, max_length=16)
    sent_on: dt.date | None = None


class AckRequest(BaseModel):
    ack_ref: str = Field(min_length=1, max_length=128)
    acknowledged_on: dt.date | None = None


class RevocationStartRequest(BaseModel):
    doc_no: str = Field(min_length=1, max_length=64)
    revision: str = Field(min_length=1, max_length=32)
    trigger_kind: str = Field(min_length=1, max_length=32)
    trigger_note: str = Field(min_length=1)
    initiated_on: dt.date | None = None


class StopUseRequest(BaseModel):
    scope: str = Field(min_length=1)
    interim_measures: str = Field(min_length=1)
    sent_on: dt.date | None = None


class ImpactRequest(BaseModel):
    affected_scope: str = Field(min_length=1)
    retrofit_done_note: str = Field(min_length=1)
    unsafe_condition: bool
    occurrence_reported_ref: str | None = Field(default=None, max_length=128)
    assessed_on: dt.date | None = None


class RevocationDecideRequest(BaseModel):
    revoked_approval_ref: str = Field(min_length=1, max_length=128)
    revocation_reason: str = Field(min_length=1)
    corrected_doc_no: str | None = Field(default=None, max_length=64)
    corrected_revision: str | None = Field(default=None, max_length=32)
    decided_on: dt.date | None = None


class RevocationCloseRequest(BaseModel):
    caac_report_ref: str = Field(min_length=1, max_length=128)
    similar_review_note: str = Field(min_length=1)
    caac_reported_on: dt.date | None = None
    closed_on: dt.date | None = None


class InquiryRequest(BaseModel):
    inquiry_no: str = Field(min_length=1, max_length=64)
    received_from: str = Field(min_length=1, max_length=256)
    question: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=16)
    received_on: dt.date | None = None


class AnswerRequest(BaseModel):
    answer: str = Field(min_length=1)
    within_approved_data: bool
    based_on_doc_no: str | None = Field(default=None, max_length=64)
    based_on_revision: str | None = Field(default=None, max_length=32)
    change_no: str | None = Field(default=None, max_length=64)
    occurrence_ref: str | None = Field(default=None, max_length=128)
    designation_ref: str | None = Field(default=None, max_length=256)
    answered_on: dt.date | None = None


class SimilarityRequest(BaseModel):
    source_kind: str = Field(min_length=1, max_length=24)
    source_ref: str = Field(min_length=1, max_length=128)
    source_summary: str = Field(min_length=1)
    comparison_note: str = Field(min_length=1)
    verdict: str = Field(min_length=1, max_length=12)
    rationale: str = Field(min_length=1)
    airworthiness_impact: str | None = None
    occurrence_ref: str | None = Field(default=None, max_length=128)
    action_taken: str | None = None
    reviewed_on: dt.date | None = None


class TransferRequest(BaseModel):
    certificate_no: str = Field(min_length=1, max_length=64)
    transfer_kind: str = Field(min_length=1, max_length=16)
    counterparty: str = Field(min_length=1, max_length=256)
    scope_note: str = Field(min_length=1)
    condition_check_note: str = Field(min_length=1)
    continued_airworthiness_handover: str = Field(min_length=1)
    agreement_ref: str = Field(min_length=1, max_length=128)
    effective_on: dt.date
    counterparty_address: str | None = None


class TransferNoticeRequest(BaseModel):
    notice_ref: str = Field(min_length=1, max_length=128)
    ack_ref: str | None = Field(default=None, max_length=128)
    notified_on: dt.date | None = None


class LicenceRequest(BaseModel):
    certificate_no: str = Field(min_length=1, max_length=64)
    licensee: str = Field(min_length=1, max_length=256)
    products_and_scope: str = Field(min_length=1)
    validity_note: str = Field(min_length=1)
    controlled_data_note: str = Field(min_length=1)
    support_note: str = Field(min_length=1)
    config_feedback_note: str = Field(min_length=1)
    termination_note: str = Field(min_length=1)
    agreement_ref: str = Field(min_length=1, max_length=128)
    acceptability_note: str = Field(min_length=1)
    signed_on: dt.date | None = None
    delivered_on: dt.date | None = None


@router.get("/das/holder/docs")
def docs(conn: Conn, user: CurrentUser):
    """发布资料及其分发确认情况（第 7 章：发布对象名单和接收确认必须可追溯）。"""
    return svc.docs(conn)


@router.get("/das/holder/gaps")
def gaps(conn: Conn, user: CurrentUser):
    """要盯的几份清单。

    `revocation_pending_ack` 止用通知还有接收方未确认的——盯的是「那边还在用、我们这边
    以为已撤销」的那段窗口；`transfer_notice_due` 权益转让通知局方逾期或未通知的
    （法规 30 天）。
    """
    return svc.gaps(conn)


@router.get("/das/holder/inquiries")
def inquiries(conn: Conn, user: CurrentUser):
    """技术支持台账，按三类分（第 13 步）。"""
    return svc.inquiries(conn)


@router.get("/das/holder/similarity")
def similarity(conn: Conn, user: CurrentUser):
    """相似性评估台账。判「不相关」的也在里面——原文：无论是否相关均须记录。"""
    return svc.similarity(conn)


@router.post("/das/holder/docs", status_code=201)
def publish(conn: Conn, user: CurrentUser, body: PublishRequest):
    """登记一份对外发布的资料（第 2～4 步）。

    局方批准的资料要标明实际批准依据，且**不得加注本单位批准声明**——加了就是把局方的
    批准说成了本单位在权利范围内的批准，而那两者的责任主体不同。
    """
    return _wrap(svc.publish, conn, actor=user, **body.model_dump())


@router.post("/das/holder/docs/{doc_no}/{revision}/distribution", status_code=201)
def distribute(conn: Conn, user: CurrentUser, doc_no: str, revision: str,
               body: DistributeRequest):
    """登记一个分发对象（第 4 步）。撤销期间的资料不得再分发。"""
    return _wrap(svc.distribute, conn, doc_no=doc_no, revision=revision, actor=user,
                 **body.model_dump())


@router.post("/das/holder/distribution/{distribution_id}/ack", status_code=201)
def acknowledge(conn: Conn, user: CurrentUser, distribution_id: int, body: AckRequest):
    """登记接收确认（第 7 章：接收确认必须可追溯）。"""
    return _wrap(svc.acknowledge, conn, distribution_id=distribution_id, actor=user,
                 **body.model_dump())


@router.post("/das/holder/revocations", status_code=201)
def start_revocation(conn: Conn, user: CurrentUser, body: RevocationStartRequest):
    """启动撤销与召回（第 7 步）。

    四种触发情形任一出现，**立即**启动。启动之后这份资料就进入「撤销期间」，
    不得继续用于改装、生产或放行。止用通知的收件人自动取自分发清单
    （第 8 步：向**所有已接收该资料的对象**发出）。
    """
    return _wrap(svc.start_revocation, conn, actor=user, **body.model_dump())


@router.post("/das/holder/revocations/{revocation_id}/stop-use", status_code=201)
def send_stop_use(conn: Conn, user: CurrentUser, revocation_id: int,
                  body: StopUseRequest):
    """发出立即止用通知（第 8 步）。**在完成评估前**先发——它是第一步，不是最后一步。"""
    return _wrap(svc.send_stop_use, conn, revocation_id=revocation_id, actor=user,
                 **body.model_dump())


@router.post("/das/holder/stop-use/{ack_id}/ack", status_code=201)
def ack_stop_use(conn: Conn, user: CurrentUser, ack_id: int, body: AckRequest):
    """登记止用通知的接收确认。未确认的用 `/chase` 跟催（第 8 步）。"""
    return _wrap(svc.ack_stop_use, conn, ack_id=ack_id, actor=user,
                 ack_ref=body.ack_ref, acknowledged_on=body.acknowledged_on)


@router.post("/das/holder/stop-use/{ack_id}/chase", status_code=201)
def chase_stop_use(conn: Conn, user: CurrentUser, ack_id: int):
    """跟催未确认的止用通知（第 8 步：未确认的逐一跟催）。"""
    return _wrap(svc.chase_stop_use, conn, ack_id=ack_id, actor=user)


@router.post("/das/holder/revocations/{revocation_id}/impact", status_code=201)
def assess_impact(conn: Conn, user: CurrentUser, revocation_id: int,
                  body: ImpactRequest):
    """影响评估（第 9 步）。

    判为构成不安全状态时要给出事件报告编号——构成 CCAR-21.5 报告情形的按 UG-DAP-12
    报局方，判了不安全却不报，这条链就断在这里。
    """
    return _wrap(svc.assess_revocation_impact, conn, revocation_id=revocation_id,
                 actor=user, **body.model_dump())


@router.post("/das/holder/revocations/{revocation_id}/decision", status_code=201)
def decide_revocation(conn: Conn, user: CurrentUser, revocation_id: int,
                      body: RevocationDecideRequest):
    """签署撤销决定（第 10 步）。

    **只能由原批准人或其上级**。「其上级」在系统里判为在任责任经理或在任适航管理
    负责人——系统里没有组织层级表，硬造一个会变成又一个与事实不符的常量（判据 A7）。
    """
    return _wrap(svc.decide_revocation, conn, revocation_id=revocation_id, actor=user,
                 **body.model_dump())


@router.post("/das/holder/revocations/{revocation_id}/closure", status_code=201)
def close_revocation(conn: Conn, user: CurrentUser, revocation_id: int,
                     body: RevocationCloseRequest):
    """报局方与闭环（第 11 步）。

    止用通知未取得**全部**接收确认前不得结案——没确认就结案，等于那边还在用这份资料，
    而我们这边记着已经撤销了。还要举一反三检查同类批准：一次撤销只改这一份资料，
    同类的那几份还在外面。
    """
    return _wrap(svc.close_revocation, conn, revocation_id=revocation_id, actor=user,
                 **body.model_dump())


@router.post("/das/holder/inquiries", status_code=201)
def record_inquiry(conn: Conn, user: CurrentUser, body: InquiryRequest):
    """登记技术询问（第 13 步）。分类决定了后续走哪条路，所以它不是备注。"""
    return _wrap(svc.record_inquiry, conn, actor=user, **body.model_dump())


@router.post("/das/holder/inquiries/{inquiry_no}/answer", status_code=201)
def answer_inquiry(conn: Conn, user: CurrentUser, inquiry_no: str, body: AnswerRequest):
    """答复技术询问（第 13 步）。

    三类各走各的路：a) 指明依据哪份已发布资料；b) **不得以答复代替更改**，须指向一条
    已有分类结论的设计更改；c) 须指向事件报告。另：核对结论为「超出已批准的设计数据
    范围」时不得发出。
    """
    return _wrap(svc.answer_inquiry, conn, inquiry_no=inquiry_no, actor=user,
                 **body.model_dump())


@router.post("/das/holder/similarity", status_code=201)
def record_similarity(conn: Conn, user: CurrentUser, body: SimilarityRequest):
    """在役问题的相似性评估（第 15 步）。

    判「相关／可能相关」的要评估对适航性的影响；判「不相关」也必须记——原文：
    评估结论无论是否相关均须记录。不记的话，过后说不清这条通报到底看过没有。
    """
    return _wrap(svc.record_similarity, conn, actor=user, **body.model_dump())


@router.post("/das/holder/transfers", status_code=201)
def record_transfer(conn: Conn, user: CurrentUser, body: TransferRequest):
    """STC 权益转让或终止（第 16 步，须在任责任经理）。

    返回里带 `caac_notice_due_on`：**生效或终止后 30 天内**书面通知局方（法规时限，
    上限值在 `L11.STC_TRANSFER_NOTICE`，受规章修订闸门保护）。
    **不得以内部协议免除法定持有人责任**——所以这里记的是通知与接收证据，不是免责。
    """
    return _wrap(svc.record_transfer, conn, actor=user, **body.model_dump())


@router.post("/das/holder/transfers/{transfer_id}/notice", status_code=201)
def notify_transfer(conn: Conn, user: CurrentUser, transfer_id: int,
                    body: TransferNoticeRequest):
    """书面通知局方（第 16 步）。返回里带是否逾期；逾期的进 gaps 清单。"""
    return _wrap(svc.notify_transfer, conn, transfer_id=transfer_id, actor=user,
                 **body.model_dump())


@router.post("/das/holder/licences", status_code=201)
def record_licence(conn: Conn, user: CurrentUser, body: LicenceRequest):
    """STC 书面许可（第 17 步，CCAR-21.119，须在任责任经理）。

    原文把要载明的各项逐条列了出来，所以七项全部必填——少一项，这份许可在局方面前
    就说不清许到哪为止。
    """
    return _wrap(svc.record_licence, conn, actor=user, **body.model_dump())
