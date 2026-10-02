"""M5 构型管理端点（UG-DAP-05）。

按 2026-10-02 的决定，这一组只在四处设闸门，其余是记录与查询：

| 闸门 | 原文 |
|---|---|
| `DCMS-INV-118` | 第 2 步：不可互换或单向互换的更改**必须更换件号** |
| `DCMS-INV-119` | 第 3 步：基线冻结后**只能通过批准的更改修改**（且不得解冻）|
| 有效性必填 | 第 5 步：**未明确有效性范围的更改不得发布** |
| `DCMS-INV-120` | 第 7 步 b：交付前核查的差异**未关闭的不得放行** |

**不做逐项录入的**：第 7 步 a 的设计内部一致性核查要比对图纸、数模、BOM、规范、工艺、
手册六者之间的参数、版次和引用标准——这不是系统判得了的事，也不该逼人把六方比对录成
表格。走线下（`POST /das/offline/approvals`，object_type=`DAS_CONFIG_AUDIT`），
这里只记结论。

第 6 步的构型纪实是**查询能力**而不是闸门：`GET /das/config/status` 按产品、按日期
查得出「某台产品由哪些件号和版次构成、已实施哪些更改」——原文要的就是这个。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_config as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["构型管理"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-05")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class ItemRequest(BaseModel):
    project_no: str = Field(min_length=1, max_length=32)
    item_kind: str = Field(min_length=1, max_length=16)
    identifier: str = Field(min_length=1, max_length=128)
    name_cn: str = Field(min_length=1, max_length=256)
    inclusion_reason: str = Field(min_length=1)
    form_ref: str | None = Field(default=None, max_length=64)
    note: str | None = None


class BaselineRequest(BaseModel):
    project_no: str = Field(min_length=1, max_length=32)
    baseline_kind: str = Field(min_length=1, max_length=16)
    code: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    serial_no: str | None = Field(default=None, max_length=64)


class BaselineItemRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=32)
    effectivity: str | None = Field(default=None, max_length=128)
    change_no: str | None = Field(default=None, max_length=64)


class InterchangeabilityRequest(BaseModel):
    change_no: str = Field(min_length=1, max_length=64)
    verdict: str = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1)
    identifier: str | None = Field(default=None, max_length=128)
    old_part_number: str | None = Field(default=None, max_length=128)
    new_part_number: str | None = Field(default=None, max_length=128)
    old_revision: str | None = Field(default=None, max_length=32)
    new_revision: str | None = Field(default=None, max_length=32)
    decided_on: dt.date | None = None


class EffectivityRequest(BaseModel):
    change_no: str = Field(min_length=1, max_length=64)
    effective_from_unit: str = Field(min_length=1, max_length=128)
    retrofit_delivered: bool
    transition_coexist: bool
    notified_production: bool
    notified_procurement: bool
    notified_airworthiness: bool
    retrofit_scope: str | None = None
    transition_note: str | None = None
    decided_on: dt.date | None = None


class ClosureRequest(BaseModel):
    closure_note: str = Field(min_length=1)
    closed_on: dt.date | None = None


class AuditRequest(BaseModel):
    project_no: str = Field(min_length=1, max_length=32)
    audit_kind: str = Field(min_length=1, max_length=24)
    scope_note: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    serial_no: str | None = Field(default=None, max_length=64)
    audited_on: dt.date | None = None


class DiscrepancyRequest(BaseModel):
    description: str = Field(min_length=1)
    unapproved: bool = False


class DiscrepancyCloseRequest(BaseModel):
    disposition: str = Field(min_length=1)
    ncr_id: int | None = None
    occurrence_checked: bool = False
    closed_on: dt.date | None = None


class DeliveryRequest(BaseModel):
    project_no: str = Field(min_length=1, max_length=32)
    serial_no: str = Field(min_length=1, max_length=64)
    form_ref: str = Field(min_length=1, max_length=64)
    part_list: str = Field(min_length=1)
    implemented_changes: str = Field(min_length=1)
    residual_discrepancies: str | None = None
    no_residual_declared: bool = False
    baseline_code: str | None = Field(default=None, max_length=64)
    delivered_on: dt.date | None = None


@router.get("/das/config/items")
def items(conn: Conn, user: CurrentUser, project_no: str | None = Query(None)):
    """构型项目清单（UG-DAF-10）。

    `inclusion_reason` 指明是哪一类影响（形状／配合／功能／适航性／可追溯性）——
    空着就说不出为什么这个项目在清单里、那个不在。
    """
    return svc.items(conn, project_no)


@router.get("/das/config/baselines")
def baselines(conn: Conn, user: CurrentUser, project_no: str | None = Query(None)):
    """基线（三类：审定／设计／交付）。`frozen_at` 非空的已冻结，**不得解冻**。"""
    return svc.baselines(conn, project_no)


@router.get("/das/config/status")
def status(conn: Conn, user: CurrentUser, project_no: str | None = Query(None),
           serial_no: str | None = Query(None)):
    """构型纪实（第 6 步）：某台产品由哪些件号和版次构成、已实施哪些更改。

    这是**查询能力**而不是闸门——原文要的就是「做到可按产品、按日期查询」。
    """
    return svc.status(conn, project_no=project_no, serial_no=serial_no)


@router.get("/das/config/interchangeability")
def interchangeability(conn: Conn, user: CurrentUser):
    """互换性结论台账。`part_number_changed` 为真的要单独看，因为它们影响现场。"""
    return svc.interchangeability(conn)


@router.get("/das/config/gaps")
def gaps(conn: Conn, user: CurrentUser):
    """构型管理员要盯的几份清单。

    `effectivity_missing` 已分类却没有有效性记录的——第 5 步：未明确有效性范围的更改
    不得发布。闸门落在各自的发布点更准（「发布」这个动作散在小改发布、对外发布等多处），
    这张表是那份清单。
    """
    return svc.gaps(conn)


@router.post("/das/config/items", status_code=201)
def add_item(conn: Conn, user: CurrentUser, body: ItemRequest):
    """纳入构型控制（第 1 步）。凡影响形状、配合、功能、适航性或可追溯性的均须纳入。"""
    return _wrap(svc.add_item, conn, actor=user, **body.model_dump())


@router.post("/das/config/items/{project_no}/{identifier}/confirmation",
             status_code=201)
def confirm_item(conn: Conn, user: CurrentUser, project_no: str, identifier: str):
    """授权人员确认构型项目清单（第 1 步）。"""
    return _wrap(svc.confirm_item, conn, project_no=project_no,
                 identifier=identifier, actor=user)


@router.post("/das/config/baselines", status_code=201)
def create_baseline(conn: Conn, user: CurrentUser, body: BaselineRequest):
    """建立基线（第 3 步）。建完还没冻结——冻结是单独一步，且冻结后不得解冻。"""
    return _wrap(svc.create_baseline, conn, actor=user, **body.model_dump())


@router.post("/das/config/baselines/{baseline_code}/items", status_code=201)
def add_baseline_item(conn: Conn, user: CurrentUser, baseline_code: str,
                      body: BaselineItemRequest):
    """把一个构型项目连同版次放进基线。

    基线已冻结时，改动必须凭一条**已批准**的更改并记明更改单号（第 3 步）。
    """
    return _wrap(svc.add_baseline_item, conn, baseline_code=baseline_code,
                 actor=user, **body.model_dump())


@router.post("/das/config/baselines/{baseline_code}/freeze", status_code=201)
def freeze_baseline(conn: Conn, user: CurrentUser, baseline_code: str):
    """冻结基线（第 3 步：由授权人员批准后在系统中冻结）。

    **不得解冻**——解冻等于让「冻结」这件事不成立。构型要退回，应当另建一条基线并说明
    它取代了哪一条。
    """
    return _wrap(svc.freeze_baseline, conn, baseline_code=baseline_code, actor=user)


@router.post("/das/config/interchangeability", status_code=201)
def decide_interchangeability(conn: Conn, user: CurrentUser,
                             body: InterchangeabilityRequest):
    """互换性判定（第 2 步）。

    完全互换的更改升版次；**不可互换或单向互换的更改必须更换件号**。
    判错的后果不在系统里：一个不可互换的件沿用旧件号发出去，现场会把它装到装不上、
    或装上去不安全的位置——而装的人手里的件号是对的。
    """
    return _wrap(svc.decide_interchangeability, conn, actor=user, **body.model_dump())


@router.post("/das/config/effectivity", status_code=201)
def decide_effectivity(conn: Conn, user: CurrentUser, body: EffectivityRequest):
    """确定有效性范围（第 5 步）。**未明确有效性范围的更改不得发布。**

    要追溯改装就得说清范围，有过渡期并存就得说清怎么并存——只填一个布尔而不写范围，
    等于说了「要追溯」但没人知道追到哪。
    """
    return _wrap(svc.decide_effectivity, conn, actor=user, **body.model_dump())


@router.post("/das/config/effectivity/{change_no}/closure", status_code=201)
def close_effectivity(conn: Conn, user: CurrentUser, change_no: str,
                      body: ClosureRequest):
    """贯彻销项（第 5 步：跟踪实施至完成并销项）。"""
    return _wrap(svc.close_effectivity, conn, change_no=change_no, actor=user,
                 **body.model_dump())


@router.post("/das/config/audits", status_code=201)
def record_audit(conn: Conn, user: CurrentUser, body: AuditRequest):
    """登记构型核查（第 7 步）。

    设计内部一致性核查的六方比对走线下（`POST /das/offline/approvals`，
    object_type=`DAS_CONFIG_AUDIT`），这里只记结论。
    """
    return _wrap(svc.record_audit, conn, actor=user, **body.model_dump())


@router.post("/das/config/audits/{audit_id}/discrepancies", status_code=201)
def record_discrepancy(conn: Conn, user: CurrentUser, audit_id: int,
                       body: DiscrepancyRequest):
    """逐条记录差异（第 7 步 b）。`unapproved` 为真的另有两件事要做（第 8 步）。"""
    return _wrap(svc.record_discrepancy, conn, audit_id=audit_id, actor=user,
                 **body.model_dump())


@router.post("/das/config/discrepancies/{discrepancy_id}/closure", status_code=201)
def close_discrepancy(conn: Conn, user: CurrentUser, discrepancy_id: int,
                      body: DiscrepancyCloseRequest):
    """关闭差异。

    **未经批准的**构型差异（实物与设计不一致、擅自使用旧版次）须按 UG-DAP-14 记不符合项，
    并按 UG-DAP-12 判断是否属应报告的事件（第 8 步）——两件事都要做过才算处置完。
    """
    return _wrap(svc.close_discrepancy, conn, discrepancy_id=discrepancy_id,
                 actor=user, **body.model_dump())


@router.post("/das/config/deliveries", status_code=201)
def deliver(conn: Conn, user: CurrentUser, body: DeliveryRequest):
    """交付构型记录（第 9 步，UG-DAF-11）。

    该台产品的交付前核查还有未关闭的差异时**不得放行**。遗留差异要么写明、要么显式声明
    「无」——空着发出去，接收方以为这台产品没有遗留差异。
    """
    return _wrap(svc.deliver, conn, actor=user, **body.model_dump())
