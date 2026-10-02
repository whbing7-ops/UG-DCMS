"""M4 设计更改分类端点（第二批）。

**分类不是一个字段，是三项**：大改／小改、声学／非声学、排放／非排放（UG-DAP-06 第 3 步）。
后两项本单位**未申请批准权**，所以没有"内部给声学结论"的入口，只有
`POST .../caac-result` 登记局方结论——内部给了就是在没有权利的事项上作了批准。

`state` 把两件常被混为一谈的事分开：`UNDETERMINED` 是**技术上**判不了（按重大更改处理、
不得签署，待确认），`BEYOND_AUTHORITY` 是**权限上**不够（结论留空、转局方办理）。
合成一个"拿不准就判大改"的分支，就会在权限不足时自动升级为重大更改——那是用权限问题
冒充技术判断，UG-DAP-06 第 4 步明文禁止。

与基础能力各模块一样，端点上没有 `DAS_*_MANAGE` 权限：闸门在服务层按**岗位任命**判。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_change as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["设计更改分类"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-06")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class RaiseRequest(BaseModel):
    change_no: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    purpose: str = Field(min_length=1)
    content: str = Field(min_length=1)
    products: str = Field(min_length=1)
    drawings: str = Field(min_length=1)
    project_no: str | None = Field(default=None, max_length=32)
    raised_on: dt.date | None = None


class ImpactRequest(BaseModel):
    impact_list: str = Field(min_length=1)
    impact_baseline_ref: str = Field(min_length=1, max_length=256)
    ad_checked: bool
    cert_basis_checked: bool
    impact_on: dt.date | None = None


class AssessRequest(BaseModel):
    criterion_code: str = Field(min_length=1, max_length=32)
    verdict: str = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1)
    evidence_ref: str | None = Field(default=None, max_length=256)


class CumulativeRequest(BaseModel):
    prior_change_no: str = Field(min_length=1, max_length=64)
    assessment: str = Field(min_length=1)


class ClassifyRequest(BaseModel):
    state: str = Field(min_length=1, max_length=24)
    conclusion_reason: str = Field(min_length=1)
    major_minor: str | None = Field(default=None, max_length=8)
    form_no: str | None = Field(default=None, max_length=64)
    caac_referral_ref: str | None = Field(default=None, max_length=128)
    signed_on: dt.date | None = None


class CaacResultRequest(BaseModel):
    aspect: str = Field(min_length=1, max_length=10)
    result: str = Field(min_length=1, max_length=16)
    caac_ref: str = Field(min_length=1, max_length=128)
    caac_on: dt.date
    limitation: str | None = None


class DeviationRequest(BaseModel):
    deviation_no: str = Field(min_length=1, max_length=64)
    requested_by: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    assessment: str = Field(min_length=1)
    affects_design_data: bool
    affects_key_characteristics: bool
    affects_airworthiness: bool
    approved: bool
    change_no: str | None = Field(default=None, max_length=64)
    requested_on: dt.date | None = None


@router.get("/das/changes/criteria")
def criteria(conn: Conn, user: CurrentUser):
    """UG-DAW-010 第 2 章的 9 项判据。

    **没有数值阈值字段**：「重量与平衡」的 kg／%MAC 子句已按待澄清项第 9 条删除——
    体系文件是制度不是技术标准（判据 P5）。加一个阈值进来，它会变成一个没人维护、
    却被当成判定依据的数。
    """
    return svc.criteria(conn)


@router.get("/das/changes")
def changes(conn: Conn, user: CurrentUser, state: str | None = Query(None)):
    """更改台账，含判据覆盖情况（少一条判据就不能出结论）。"""
    return svc.changes(conn, state=state)


@router.get("/das/changes/gaps")
def gaps(conn: Conn, user: CurrentUser):
    """要盯的三张表。

    `awaiting_caac` 转局方或待确认而尚无结论的——它们**不是「已分类」**，结论是空的；
    `cumulative_gap` 判为小改、同项目此前另有更改、却没有任何合并评估记录的，
    这正是「一串小改永远是小改」会发生的地方；
    `deviation_to_change` 影响适航的偏离及其对应的设计更改。
    """
    return svc.gaps(conn)


@router.get("/das/changes/{change_no}")
def change(conn: Conn, user: CurrentUser, change_no: str):
    """单条更改：影响分析、9 项判据的逐条结论、分类结论、累计评估与改动留痕。"""
    return _wrap(svc.change, conn, change_no)


@router.post("/das/changes", status_code=201)
def raise_change(conn: Conn, user: CurrentUser, body: RaiseRequest):
    """提出更改（UG-DAP-06 第 1 步，须在任设计工程师）。"""
    return _wrap(svc.raise_change, conn, actor=user, **body.model_dump())


@router.put("/das/changes/{change_no}/impact")
def record_impact(conn: Conn, user: CurrentUser, change_no: str, body: ImpactRequest):
    """影响分析（UG-DAP-06 第 2 步）。四项要齐——做了一半会让下一步以为分析过了。"""
    return _wrap(svc.record_impact, conn, change_no=change_no, actor=user,
                 **body.model_dump())


@router.post("/das/changes/{change_no}/criteria", status_code=201)
def assess_criterion(conn: Conn, user: CurrentUser, change_no: str, body: AssessRequest):
    """对一项判据给结论。

    **「无影响」也要写理由**（UG-DAW-010 第 3 章），判为显著影响时还要指出证据。
    给过的结论不得改写（DCMS-INV-108）。
    """
    return _wrap(svc.assess_criterion, conn, change_no=change_no, actor=user,
                 **body.model_dump())


@router.post("/das/changes/{change_no}/cumulative", status_code=201)
def record_cumulative(conn: Conn, user: CurrentUser, change_no: str,
                      body: CumulativeRequest):
    """登记与此前某次更改的合并评估（UG-DAW-010 第 1 章：影响须累计考虑）。

    做成关联记录而不是一个「已考虑」的布尔：布尔勾完说不出跟哪几次一起评的。
    """
    return _wrap(svc.record_cumulative, conn, change_no=change_no, actor=user,
                 **body.model_dump())


@router.post("/das/changes/{change_no}/classification", status_code=201)
def classify(conn: Conn, user: CurrentUser, change_no: str, body: ClassifyRequest):
    """出分类结论（UG-DAF-02／表-21-173）。

    9 项判据少一条就不能出结论；任一项显著影响只能是大改；超权限结论留空且须写明转局方
    依据；判不了按大改处理但**不得签署**（按重大更改处理是暂行处置，不是已分类）。
    """
    return _wrap(svc.classify, conn, change_no=change_no, actor=user,
                 **body.model_dump())


@router.post("/das/changes/{change_no}/caac-result", status_code=201)
def record_caac_result(conn: Conn, user: CurrentUser, change_no: str,
                       body: CaacResultRequest):
    """登记局方给出的声学或排放分类结论。

    本单位**未申请这两项的批准权**，所以只有这一个入口，且必须带局方编号与日期。
    """
    return _wrap(svc.record_caac_result, conn, change_no=change_no, actor=user,
                 **body.model_dump())


@router.post("/das/changes/deviations", status_code=201)
def record_deviation(conn: Conn, user: CurrentUser, body: DeviationRequest):
    """制造偏离／让步的评估（UG-DAP-06 第 7 步）。

    **影响适航的不得批准**，且须指向回到设计更改流程的那条更改申请——
    只记一句「不批准」而不转设计更改，这个偏离就没有下文，而产品上的那个偏差还在。
    """
    return _wrap(svc.record_deviation, conn, actor=user, **body.model_dump())
