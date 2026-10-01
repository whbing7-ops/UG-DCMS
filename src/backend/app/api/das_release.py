"""上线前六项控制验证与投用端点（基础能力，判据 N16）。

**这里没有"标记为已验证"的批量入口。** 每一项各记一条，带提交号、批次号和证据位置；
备份恢复那一项还要记抽查份数与三项核对结论（判据 N15）。写入后不得改写、不得删除。

投用的闸门在数据库里（`DCMS-INV-087`），服务层先把"还差哪几项"列出来——报一句
"不满足条件"等于让人去猜。验证绑在批次当前登记的构建上：构建变了，之前的验证一律
不算，`GET /das/release/stale` 列出来。

与 M2、时限引擎、留存一样，端点上没有 `DAS_*_MANAGE` 权限：闸门在服务层按**岗位任命**
判。两道门槛不同高：记录验证结果要在任适航管理负责人或资料管理负责人；
登记构建号与投用只有在任适航管理负责人——往回改构建能让一个已经不成立的绿灯重新生效。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_release as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["上线前验证与投用"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAW-005")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class BuildRequest(BaseModel):
    build_ref: str = Field(min_length=1, max_length=128)


class VerifyRequest(BaseModel):
    item_code: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1)
    commit_ref: str = Field(min_length=1, max_length=64)
    batch_ref: str = Field(min_length=1, max_length=128)
    result: str = Field(min_length=1, max_length=8)
    verified_by: str = Field(min_length=1, max_length=128)
    verifier_is_external: bool = False
    verified_by_user: str | None = None
    evidence_ref: str | None = Field(default=None, max_length=512)
    ncr_id: int | None = None
    # 判据 N15 专用（备份恢复项）
    sample_count: int | None = None
    content_ok: bool | None = None
    version_ok: bool | None = None
    signature_ok: bool | None = None
    sample_note: str | None = None


class CommissionRequest(BaseModel):
    commissioned_on: dt.date | None = None
    approval_ref: str | None = Field(default=None, max_length=256)
    note: str | None = None


@router.get("/das/release/items")
def items(conn: Conn, user: CurrentUser):
    """UG-DAW-005 第 1 章的六项控制，含"冻结"展开后的两个机制。

    `verify_content` 写明怎样才算验过，具体到可以照着做——写一句"已测试"的那种
    验证记录，三个月后没人能判断当时到底测了什么。
    """
    return svc.items(conn)


@router.get("/das/release/batches")
def batches(conn: Conn, user: CurrentUser):
    """批次及就绪情况（设计输入第 8.6 节）。含已失效的验证记录条数。"""
    return svc.batches(conn)


@router.get("/das/release/readiness")
def readiness(conn: Conn, user: CurrentUser, batch: str | None = Query(None)):
    """批次 × 控制项 → 本构建上最近一次验证。

    `state`：NO_BUILD（批次还没登记构建号）／NOT_VERIFIED／FAILED／VERIFIED。
    只有全部 VERIFIED 才能投用。
    """
    return svc.readiness(conn, batch)


@router.get("/das/release/blockers")
def blockers(conn: Conn, user: CurrentUser, batch: str | None = Query(None)):
    """拦住投用的那几项。闸门报错前先能看见。"""
    return svc.blockers(conn, batch)


@router.get("/das/release/stale")
def stale(conn: Conn, user: CurrentUser):
    """验过之后构建又变了的记录。

    这是本模块最要紧的一张表：没有它，"上线前已验证"会一直是真的——只不过指向上一个
    版本的系统，而这种偏差不会自己暴露。这些记录不计入闸门。
    """
    return svc.stale(conn)


@router.get("/das/release/oversight")
def oversight(conn: Conn, user: CurrentUser):
    """交独立监督核对的可见性清单，均不阻断。

    `self_verified` 执行人同时是投用批准人（判据 I6 的同形问题）；
    `external` 声明为外部人员执行的——正当做法，但也是绕过上一张表的唯一路径；
    `item_mismatch` 控制项层级与结果落点错位，平时应为空。
    """
    return svc.oversight(conn)


@router.get("/das/release/batches/{batch_code}/verifications")
def verifications(conn: Conn, user: CurrentUser, batch_code: str):
    """某批次的全部验证记录。`current_build` 为 false 的是已失效的那些。"""
    return svc.verifications(conn, batch_code)


@router.put("/das/release/batches/{batch_code}/build")
def set_build(conn: Conn, user: CurrentUser, batch_code: str, body: BuildRequest):
    """登记本批次当前待验证的构建号（须在任适航管理负责人）。

    往前改只会让闸门更严；**往回改**能让一个已经不成立的绿灯重新生效，所以
    审计记录里一并写下目标构建上已有多少条旧验证。
    """
    return _wrap(svc.set_build, conn, batch_code=batch_code,
                 build_ref=body.build_ref, actor=user)


@router.post("/das/release/batches/{batch_code}/verifications", status_code=201)
def record_verification(conn: Conn, user: CurrentUser, batch_code: str,
                        body: VerifyRequest):
    """登记一项控制的验证结果。

    构建号不由调用方给，从批次读——让调用方自己填，填错了就会得到一条指向别的版本的
    "已验证"。失败必须挂不符合项（判据 N6／N15：验证失败即为不符合项）。
    """
    return _wrap(svc.record_verification, conn, batch_code=batch_code, actor=user,
                 **body.model_dump())


@router.post("/das/release/batches/{batch_code}/commissioning", status_code=201)
def commission(conn: Conn, user: CurrentUser, batch_code: str, body: CommissionRequest):
    """投用本批次（须在任适航管理负责人）。

    六项（按机制展开）在本构建上均有 PASS 才放行，取每项最近一次——后一次 FAIL
    推翻前一次 PASS。
    """
    return _wrap(svc.commission, conn, batch_code=batch_code, actor=user,
                 **body.model_dump())
