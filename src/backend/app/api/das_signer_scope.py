"""签署授权的产品范围端点（判据 I10.PRODUCT_SCOPE）。

**这组端点存在的前提是有一条真会读它的校验路径。** PR #5 的自由文本 `product_scope`
被否决移除，不是因为"不该有范围"，而是因为 `is_authorized()` 从不读它——手册要求强制
执行的一条控制在系统里成了装饰，还会出现在授权名单上让审查者以为系统在管。

落地的强制点目前只有一处：符合性声明的签署（M3，标的就是该项目）。其余签署路径调用方
传不出标的，`GET /das/signer-scope/unenforced` 逐条列出，登记册记为**部分实现**。

权限用 `signer_manage`（与授予撤销授权同一道门）：范围是授权内容的一部分。另外不得给
自己的授权登记范围——理由同"不得给自己授权"（判据 I9）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import das_signer_scope as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["签署授权的产品范围"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-03")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class ScopeRequest(BaseModel):
    approval_type_code: str = Field(min_length=1, max_length=8)
    scope_kind: str = Field(min_length=1, max_length=24)
    source_ref: str = Field(min_length=1, max_length=256)
    part_number_id: str | None = None
    family_id: str | None = None
    aircraft_type: str | None = Field(default=None, max_length=64)
    product_category: str | None = Field(default=None, max_length=64)
    limitation: str | None = None
    no_limitation_declared: bool = False


class BindRequest(BaseModel):
    project_no: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1)


class WaiverRequest(BaseModel):
    paper_ref: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1)


@router.get("/das/signer-scope")
def registry(conn: Conn, user: CurrentUser, state: str | None = Query(None)):
    """每条签署授权的范围登记情况。

    `scope_state`：SYSTEM_ENFORCED／PAPER_ONLY／**UNDECLARED**。
    最后一档既没登记范围也没声明由纸面把关——按手册 3.2 的白名单口径它什么都不该能签，
    而在落地强制点之外它什么都能签，所以这一档必须清零。
    """
    return svc.registry(conn, state=state)


@router.get("/das/signer-scope/undeclared")
def undeclared(conn: Conn, user: CurrentUser):
    """既没登记范围也没声明的在册授权。**这一档必须清零。**"""
    return svc.undeclared(conn)


@router.get("/das/signer-scope/paper-only")
def paper_only(conn: Conn, user: CurrentUser):
    """声明"范围由纸面授权书把关"的，交独立监督核对那份授权书。"""
    return svc.paper_only(conn)


@router.get("/das/signer-scope/unenforced")
def unenforced(conn: Conn, user: CurrentUser):
    """范围校验尚未施加的签署路径及原因。

    判据 I10.PRODUCT_SCOPE 的如实状态就在这里：只有符合性声明一条路在校验，因为只有它
    说得出标的。其余不是"漏了"，是调用方传不出标的——传不出标的时默默放行，正是判据
    I10.DISCIPLINE 已有的那个缺口，明知故犯地再来一次是不行的。
    """
    return svc.unenforced(conn)


@router.get("/das/signer-scope/statement-gap")
def statement_gap(conn: Conn, user: CurrentUser):
    """没有校验范围就签出去的符合性声明。自评要数得出这个数。"""
    return svc.statement_gap(conn)


@router.get("/das/signer-scope/{auth_id}")
def scope_of(conn: Conn, user: CurrentUser, auth_id: str):
    """一条授权的范围条目、项目限定与声明。"""
    return _wrap(svc.scope_of, conn, auth_id)


@router.get("/das/signer-scope/{auth_id}/covers")
def covers(conn: Conn, user: CurrentUser, auth_id: str,
           approval_type_code: str | None = Query(None),
           part_number_id: str | None = Query(None),
           family_id: str | None = Query(None),
           aircraft_type: str | None = Query(None),
           product_category: str | None = Query(None)):
    """范围是否覆盖某标的。**三值**：true／false／null。

    `null` 是"判不了"（没给标的，或该授权没有范围条目），**不等于放行**。
    合成布尔的话判不了只能折成 true 或 false：折成 true 就是 PR #5 那个装饰字段的翻版，
    折成 false 会把说不出标的的正常签署全拦死。
    """
    return _wrap(svc.covers, conn, auth_id=auth_id,
                 approval_type_code=approval_type_code, part_number_id=part_number_id,
                 family_id=family_id, aircraft_type=aircraft_type,
                 product_category=product_category)


@router.post("/das/signer-scope/{auth_id}/entries", status_code=201)
def add_scope(conn: Conn, auth_id: str, body: ScopeRequest,
              actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    """登记一条范围条目（白名单的一行）。

    批准类型引用 `das_project_type`，范围粒度恰好给一种引用（件号／图号族／航空器型号／
    产品类别），限制栏为空须显式声明"无限制"——空着可能是无限制，也可能是忘了抄，
    而这两种在符合性自评里完全不同。
    """
    return _wrap(svc.add_scope, conn, auth_id=auth_id, actor=actor, **body.model_dump())


@router.post("/das/signer-scope/{auth_id}/project-binds", status_code=201)
def bind_project(conn: Conn, auth_id: str, body: BindRequest,
                 actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    """把授权限定在某个项目上（**关联维度，不替代范围**）。

    一个项目可以包含多个产品、多个型号件号和不同限制条件，所以"同属一个项目"不能证明
    "签署范围相符"。典型用途：受托项目的授权不得顺带签自家 STC 的资料。
    """
    return _wrap(svc.bind_project, conn, auth_id=auth_id, actor=actor,
                 **body.model_dump())


@router.post("/das/signer-scope/{auth_id}/waiver", status_code=201)
def declare_paper_only(conn: Conn, auth_id: str, body: WaiverRequest,
                       actor: dict = Depends(require(Perm.SIGNER_MANAGE))):
    """声明该授权的范围由纸面授权书把关。

    **不是默认值，是一条带声明人与日期的陈述**，全部进 paper-only 清单交独立监督核对。
    已有范围条目的授权不得再声明——系统已经在管了，再声明一句不管，两者必有一个是假的。
    """
    return _wrap(svc.declare_paper_only, conn, auth_id=auth_id, actor=actor,
                 **body.model_dump())
