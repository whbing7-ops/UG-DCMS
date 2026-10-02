"""M5 设计小改批准端点（第二批，UG-DAP-08／表-21-174）。

第 1 步的三个条件各有自己的端点并要写明怎么核的——**一个「条件已核对」的勾说不出哪一条
是怎么核的**，而局方要问的正是这个。三条全部核过且满足才能发布。

批准声明是**受控配置**（`GET /das/minor-approvals/statement`），发布时逐字比对。
须附声明的是服务通告、飞行手册补充、持续适航文件；符合性文件、审定计划、生产用设计数据
**不使用**此声明——给它们附上「按权利范围进行批准」的声明，是把不代表设计批准的资料说成
了设计批准。

声明原文带占位符【设计机构许可证编号】，而本单位的 DOA 尚在申请中，所以须附声明的资料
暂时发不出去。`GET /das/minor-approvals/blocker` 把这件事摆明：**这是如实状态而不是
缺陷**，硬塞一个编号进去，那份资料会带着一个假的许可证编号发出去。

`GET /das/minor-approvals/invalidated` 是这里最要紧的一张表：分类结论会变（局方有不同
意见时以局方为准），一变，建立在它上面的小改批准就失去了前提——而资料已经发出去了。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_minor_approval as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["设计小改批准"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-08")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class ApproveRequest(BaseModel):
    change_no: str = Field(min_length=1, max_length=64)
    form_no: str = Field(min_length=1, max_length=64)
    approved_scope: str = Field(min_length=1)
    cve_check_ref: str = Field(min_length=1, max_length=128)
    change_doc_ref: str = Field(min_length=1)
    limitations: str | None = None
    no_limitation_declared: bool = False
    authority_ref: str | None = Field(default=None, max_length=256)
    cve_user_id: str | None = None
    approved_on: dt.date | None = None


class ConditionRequest(BaseModel):
    condition_code: str = Field(min_length=1, max_length=32)
    satisfied: bool
    evidence: str = Field(min_length=1)


class ReleaseRequest(BaseModel):
    doc_kind: str = Field(min_length=1, max_length=32)
    doc_ref: str = Field(min_length=1, max_length=128)
    distributed_to: str = Field(min_length=1)
    archived_ref: str = Field(min_length=1, max_length=128)
    statement_text: str | None = None
    caac_notified: bool = False
    released_on: dt.date | None = None


@router.get("/das/minor-approvals/statement")
def statement(conn: Conn, user: CurrentUser):
    """局方规定的批准声明原文（受控配置）、它的改动史，以及资料种类的两分。

    声明文本不是代码里的字符串常量：写进代码，改一个字要发版本，而且没人能在系统里看到
    现行文本是什么。改动留旧版，因为**已发出去的资料印的是旧版那段话**。
    """
    return svc.statement(conn)


@router.get("/das/minor-approvals/blocker")
def blocker(conn: Conn, user: CurrentUser):
    """许可证编号这道门。**如实状态，不是缺陷。**"""
    return svc.blocker(conn)


@router.get("/das/minor-approvals")
def register(conn: Conn, user: CurrentUser):
    """小改批准台账（UG-DAF-04，编号后缀 PZ）。"""
    return svc.register(conn)


@router.get("/das/minor-approvals/invalidated")
def invalidated(conn: Conn, user: CurrentUser):
    """底下那条分类已不是「已签署的小改」的小改批准。

    插入时校验过一次是不够的：分类结论会变，而资料已经发出去了——这不是历史问题，
    是现在外面有一份带着不成立批准的资料。交 M7 按 UG-DAP-14 处理。
    """
    return svc.invalidated(conn)


@router.get("/das/minor-approvals/condition-gaps")
def condition_gaps(conn: Conn, user: CurrentUser):
    """条件没核全、或核了不满足的批准。没发布之前不发生对外效果，但要看得见。"""
    return svc.condition_gaps(conn)


@router.get("/das/minor-approvals/{form_no}")
def approval(conn: Conn, user: CurrentUser, form_no: str):
    """单条小改批准：三个核对条件及其依据、发布与归档记录。"""
    return _wrap(svc.approval, conn, form_no)


@router.post("/das/minor-approvals", status_code=201)
def approve(conn: Conn, user: CurrentUser, body: ApproveRequest):
    """批准（UG-DAP-08 第 4 步，UG-DAF-04，编号后缀 PZ）。

    必须指向一条**已签署且为小改**的分类结论；载明范围，限制条件为空须显式声明「无限制」；
    批准人不是在任适航管理负责人时须写明纸面授权依据（小改批准是 5 类适航签署事项之一，
    系统判不了某份授权是否覆盖这件事）。
    """
    return _wrap(svc.approve, conn, actor=user, **body.model_dump())


@router.post("/das/minor-approvals/{form_no}/conditions", status_code=201)
def check_condition(conn: Conn, user: CurrentUser, form_no: str, body: ConditionRequest):
    """登记第 1 步的一个核对条件，并写明怎么核的。

    三条：a) 已有签署的分类表且为小改；b) 在许可项目单的权利范围内；c) 批准人在授权范围内。
    **任一项不满足，不得批准**（发布时校验）。
    """
    return _wrap(svc.check_condition, conn, form_no=form_no, actor=user,
                 **body.model_dump())


@router.post("/das/minor-approvals/{form_no}/releases", status_code=201)
def release(conn: Conn, user: CurrentUser, form_no: str, body: ReleaseRequest):
    """发布与归档（UG-DAP-08 第 5～6 步，须在任资料管理负责人）。

    三个条件全部核过且满足才放行；须附声明的种类必须附且与受控原文逐字相符，
    不使用声明的种类不得附。
    """
    return _wrap(svc.release, conn, form_no=form_no, actor=user, **body.model_dump())
