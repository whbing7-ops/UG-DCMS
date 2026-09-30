"""DOA 符合性检查单端点（体系级，UG-DAM-01-附3）。

读取对所有登录用户开放——体系符合到什么程度不是内部机密, 遮起来反而让人猜。
写入需要 das_checklist_manage: 检查单由适航管理负责人归口(UG-DAP-02 步骤7)。

两个不在本模块的东西, 写在这里免得以后找错地方:
  · 监督覆盖的写入属 M2(独立监督), 由审核记录带出, 不从本端点手工录;
  · 项目级符合性检查单属 M3, 是另一个实体(判据 S1), 不共用这些路由。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import das_checklist as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["DOA 符合性检查单"], route_class=TransactionalRoute)


class EvidenceRef(BaseModel):
    kind: str = Field(pattern="^(record|form|surveillance|other)$")
    ref: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class AssessRequest(BaseModel):
    conclusion: str = Field(pattern="^(符合|部分符合|不符合)$")
    statement: str = Field(min_length=1, max_length=4000)
    improvement_plan: str | None = Field(default=None, max_length=4000)
    # 判据 EV3: 自评说明可引运行证据。填写说明 A17 要求必要时说明记录、表单。
    evidence: list[EvidenceRef] = Field(default_factory=list)
    effective_from: dt.date | None = None


class DocRefRequest(BaseModel):
    doc_code: str = Field(min_length=1, max_length=64)
    doc_name: str = Field(min_length=1, max_length=200)
    doc_version: str = Field(min_length=1, max_length=32)     # 判据 EV1: 版本必填
    clause: str = Field(min_length=1, max_length=200)


class FlagRequest(BaseModel):
    reason: str = Field(pattern="^(doc_revised|authorization_revoked|record_unavailable)$")
    source_ref: str = Field(min_length=1, max_length=256)


class ClearFlagRequest(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class DocRevisionRequest(BaseModel):
    doc_code: str = Field(min_length=1, max_length=64)
    old_version: str = Field(min_length=1, max_length=32)
    new_version: str = Field(min_length=1, max_length=32)


# ---------------------------------------------------------------- 读

@router.get("/das/checklist")
def list_items(conn: Conn, user: CurrentUser):
    return svc.items(conn)


@router.get("/das/checklist/{item_id}")
def get_item(item_id: int, conn: Conn, user: CurrentUser):
    row = svc.item_detail(conn, item_id)
    if row is None:
        raise errors.not_found("检查单条目不存在")
    return row


@router.get("/das/checklist/impact/{doc_code}")
def doc_impact(doc_code: str, conn: Conn, user: CurrentUser,
               doc_version: str | None = Query(None)):
    """判据 S2: 变更评估时反查"改这份文件会影响哪些检查单条目"。"""
    return svc.doc_impact(conn, doc_code, doc_version)


@router.get("/das/checklist/export/submission")
def export_submission(conn: Conn, actor: dict = Depends(require(Perm.DAS_CHECKLIST_MANAGE))):
    """判据 S3: 提交局方的口径, 只含前 5 列。

    要 das_checklist_manage 不是因为内容保密, 而是导出件会作为提交材料流出去,
    谁导的、什么时候导的要留痕(返回体里带 exported_by / exported_at)。
    """
    return svc.submission(conn, actor)


@router.get("/das/checklist/stat/coverage")
def coverage_stat(conn: Conn, user: CurrentUser):
    """判据 EV4、EV5: 覆盖率, 分母只含适用项。"""
    return svc.coverage_stat(conn)


@router.get("/das/checklist/stat/renewal")
def renewal_view(conn: Conn, user: CurrentUser):
    """判据 S4: 证件延续审查用的覆盖与未关闭不符合项关联视图。"""
    return svc.renewal_view(conn)


# ---------------------------------------------------------------- 写

@router.post("/das/checklist/{item_id}/assess", status_code=201)
def assess(item_id: int, payload: AssessRequest, conn: Conn,
           actor: dict = Depends(require(Perm.DAS_CHECKLIST_MANAGE))):
    """逐项自评并签署（判据 EV4-3）。改判是新增一行, 不覆盖历史。"""
    try:
        return svc.assess(conn, item_id=item_id, conclusion=payload.conclusion,
                          statement=payload.statement, improvement_plan=payload.improvement_plan,
                          evidence=[e.model_dump() for e in payload.evidence],
                          effective_from=payload.effective_from, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/checklist/{item_id}/doc-refs", status_code=201)
def add_doc_ref(item_id: int, payload: DocRefRequest, conn: Conn,
                actor: dict = Depends(require(Perm.DAS_CHECKLIST_MANAGE))):
    try:
        return svc.add_doc_ref(conn, item_id=item_id, doc_code=payload.doc_code,
                               doc_name=payload.doc_name, doc_version=payload.doc_version,
                               clause=payload.clause, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/checklist/{item_id}/flags", status_code=201)
def raise_flag(item_id: int, payload: FlagRequest, conn: Conn,
               actor: dict = Depends(require(Perm.DAS_CHECKLIST_MANAGE))):
    """标记待复核（判据 EV9）。只影响自评, 不回退监督覆盖记录（EV9-1）。"""
    try:
        return svc.raise_flag(conn, item_id=item_id, reason=payload.reason,
                              source_ref=payload.source_ref, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/checklist/flags/{flag_id}/clear")
def clear_flag(flag_id: int, payload: ClearFlagRequest, conn: Conn,
               actor: dict = Depends(require(Perm.DAS_CHECKLIST_MANAGE))):
    """关闭待复核标记。须写复核结论, 不提供一键清空。"""
    try:
        svc.clear_flag(conn, flag_id=flag_id, note=payload.note, actor=actor)
        return {"ok": True}
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/das/checklist/doc-revision")
def doc_revision(payload: DocRevisionRequest, conn: Conn,
                 actor: dict = Depends(require(Perm.DAS_CHECKLIST_MANAGE))):
    """体系文件改版的反查与传播（判据 EV2 + EV9）。

    旧版本引用置失效日期(不删行), 受影响条目挂待复核标记。
    新引用建不建、自评怎么改, 由人决定, 本端点不代劳。
    """
    try:
        return svc.propagate_doc_revision(conn, doc_code=payload.doc_code,
                                          old_version=payload.old_version,
                                          new_version=payload.new_version, actor=actor)
    except ValueError as e:
        raise errors.bad_request(str(e))
