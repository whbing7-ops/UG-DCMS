"""线下审批与证据上传端点（判据 B1）。

2026-10-02 的决定：**系统实现不了的走线下审批，证据资料上传系统即可。**

所以登记线下审批与上传证据是**一个请求**（multipart）：`DCMS-INV-115` 要求线下审批记录
必须有证据文件，它是个延迟到事务末尾检查的约束触发器——分成「先建记录、再上传」两个请求
的话第一个必然失败，等于逼调用方把两件事塞进一个事务，而一个 HTTP 请求就是一个事务。
没有证据就没有记录，这和决定本身一致。

三件缺一不可：挂在**已批准的**人工替代流程上（判据 B1）、有**证据文件**并写明纸质原件
在哪（判据 N1）、**已复核**（判据 N-总：补录数据复核前不具有权威性）。缺任一项，
M4 的两道闸门照旧拦住——`GET /das/offline/coverage` 的 `change_path` 里会显示为 NEITHER。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_offline as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["线下审批与证据"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAM-01 判据 B1")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    except FileExistsError as e:
        raise errors.conflict(str(e), rule="INV-007")


class ProcessRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    scope_note: str = Field(min_length=1)
    requirement_ref: str = Field(min_length=1, max_length=256)
    offline_owner: str = Field(min_length=1, max_length=128)
    forms_and_ledger: str = Field(min_length=1)
    reconcile_frequency: str = Field(min_length=1, max_length=128)
    backfill_rule: str = Field(min_length=1)
    exit_condition: str = Field(min_length=1)
    self_assessment_text: str = Field(min_length=1)


class DateRequest(BaseModel):
    on: dt.date | None = None


@router.get("/das/offline/processes")
def processes(conn: Conn, user: CurrentUser):
    """人工替代流程的登记与状态。

    DRAFT 还没批准——**按判据 B1 不得据以开展业务**；IN_FORCE 在用；RETIRED 已停用
    （对应模块上线了）。待澄清项第 10 条要填的就是这张表。
    """
    return svc.processes(conn)


@router.post("/das/offline/processes", status_code=201)
def register_process(conn: Conn, user: CurrentUser, body: ProcessRequest):
    """登记一条人工替代流程（判据 B1 的六项，全部必填）。

    登记完还不能用：判据 B1 要的是**已批准的**流程。
    """
    return _wrap(svc.register_process, conn, actor=user, **body.model_dump())


@router.post("/das/offline/processes/{code}/approval", status_code=201)
def approve_process(conn: Conn, user: CurrentUser, code: str, body: DateRequest):
    """批准（须在任责任经理）。未批准就照着做，那条体系要求既不在系统里也不在受控流程里。"""
    return _wrap(svc.approve_process, conn, code=code, approved_on=body.on, actor=user)


@router.post("/das/offline/processes/{code}/retirement", status_code=201)
def retire_process(conn: Conn, user: CurrentUser, code: str, body: DateRequest):
    """停用（判据 B1 第六项：退出条件）。停用后仍挂上来的审批会进越界清单。"""
    return _wrap(svc.retire_process, conn, code=code, retired_on=body.on, actor=user)


@router.get("/das/offline/approvals")
def register(conn: Conn, user: CurrentUser, object_type: str | None = Query(None)):
    """线下审批台账。`backfill_lag_days` 是补录滞后天数（判据 N1 要求两个时间都留）。"""
    return svc.register(conn, object_type=object_type)


@router.get("/das/offline/approvals/unreviewed")
def unreviewed(conn: Conn, user: CurrentUser):
    """补录了但还没复核的。

    **不是「待办」**：判据 N-总 说补录数据在完成复核并发布之前不具有权威性——
    所以这些结论现在还不能当依据用，M4 的闸门对它们仍然是关着的。
    """
    return svc.unreviewed(conn)


@router.get("/das/offline/self-reviewed")
def self_reviewed(conn: Conn, user: CurrentUser):
    """补录人与复核人是同一人的。**可见性清单，不阻断**，交独立监督核对。"""
    return svc.self_reviewed(conn)


@router.get("/das/offline/coverage")
def coverage(conn: Conn, user: CurrentUser):
    """自评要抄的那一栏：哪几类业务靠线下承接。

    `change_path` / `minor_path` 里 `evidence_path` 为 `NEITHER` 的，是**两边都拿不出
    依据**的那些——走线下不是问题，说不清哪些走了线下才是问题。
    """
    return svc.coverage(conn)


@router.get("/das/offline/approvals/{approval_id}")
def approval(conn: Conn, user: CurrentUser, approval_id: int):
    """单条线下审批：证据文件清单（含摘要与纸质原件存放位置）。"""
    return _wrap(svc.approval, conn, approval_id)


@router.post("/das/offline/approvals", status_code=201)
async def record(conn: Conn, user: CurrentUser,
                 object_type: str = Form(...), object_key: str = Form(...),
                 subject: str = Form(...), approver: str = Form(...),
                 approved_on: dt.date = Form(...), form_ref: str = Form(...),
                 conclusion: str = Form(...), original_location: str = Form(...),
                 file: UploadFile = File(...),
                 manual_process_code: str | None = Form(None),
                 note: str | None = Form(None)):
    """登记一次线下审批**并同时上传证据文件**（multipart）。

    一个请求做完两件事，因为 `DCMS-INV-115` 不允许没有证据的记录存在——
    没有上传的证据就不成立，而只有结论没有扫描件的记录比没有记录更坏：台账上看起来有。

    `original_location` 是纸质原件的存放位置，必填：判据 N1 要求扫描件与原件一并归档，
    只有扫描件而不知道原件在哪，局方要看原件时拿不出来。
    """
    content = await file.read()
    return _wrap(svc.record, conn, object_type=object_type, object_key=object_key,
                 subject=subject, approver=approver, approved_on=approved_on,
                 form_ref=form_ref, conclusion=conclusion,
                 original_location=original_location,
                 filename=file.filename or "evidence.bin",
                 mime_type=file.content_type or "application/octet-stream",
                 content=content, manual_process_code=manual_process_code,
                 note=note, actor=user)


@router.post("/das/offline/approvals/{approval_id}/evidence", status_code=201)
async def add_evidence(conn: Conn, user: CurrentUser, approval_id: int,
                       original_location: str = Form(...),
                       file: UploadFile = File(...)):
    """给已有的线下审批再加一份证据（多页扫描、补充附件）。"""
    content = await file.read()
    return _wrap(svc.add_evidence, conn, approval_id=approval_id,
                 filename=file.filename or "evidence.bin",
                 mime_type=file.content_type or "application/octet-stream",
                 content=content, original_location=original_location, actor=user)


@router.post("/das/offline/approvals/{approval_id}/review", status_code=201)
def review(conn: Conn, user: CurrentUser, approval_id: int, body: DateRequest):
    """复核补录的线下审批（判据 N-总）。

    复核之后这条线下承接才被 M4 的闸门认账。补录人自己复核不阻断，但会进
    `/das/offline/self-reviewed` 交独立监督核对。
    """
    return _wrap(svc.review, conn, approval_id=approval_id, reviewed_on=body.on,
                 actor=user)
