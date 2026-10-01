"""独立性规则登记册端点（基础能力，判据 I1～I10／I-总／E2）。

这一组端点回答符合性自评里最难回答的三个问题：**判据 I3 在哪里实现、有没有反例
用例、这一版跑过没有。** 三问分三栏（judgement 由 verdict 推导），少哪一栏就说少
哪一栏。`GET /das/independence/summary` 刻意**不返回"已实现几条"**——判据 I10
禁止笼统计数，一个总数会把八种不同状态压成一个数字。

两类写入门槛不同，这是刻意的：状态登记（PATCH）是判断，须在任适航管理负责人；
执行证据（POST executions）是事实，由 CI 写，**不设任命门槛**——设了就没人能写，
第三栏永远空着。后者的防护是提交号＋批次号必填且不得改写（DCMS-INV-084）。
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .. import errors
from ..deps import Conn, CurrentUser
from ..services import das_independence as svc
from ..txroute import TransactionalRoute

router = APIRouter(tags=["独立性规则登记册"], route_class=TransactionalRoute)


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise errors.forbidden(str(e), rule="UG-DAP-03")
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


class SetStateRequest(BaseModel):
    """未传的字段保持原值；传空串表示清空。"""
    source_state: str | None = Field(default=None, max_length=16)
    enforcement_kind: str | None = Field(default=None, max_length=32)
    enforcement_object: str | None = Field(default=None, max_length=256)
    invariant_code: str | None = Field(default=None, max_length=32)
    test_state: str | None = Field(default=None, max_length=16)
    test_ref: str | None = None
    manual_control: str | None = None
    gap_note: str | None = None


class ExecutionRequest(BaseModel):
    commit_ref: str = Field(min_length=1, max_length=64)
    batch_ref: str = Field(min_length=1, max_length=128)
    result: str = Field(min_length=1, max_length=8)
    evidence_ref: str | None = Field(default=None, max_length=512)


@router.get("/das/independence/matrix")
def matrix(conn: Conn, user: CurrentUser, verdict: str | None = Query(None)):
    """三栏矩阵：源码状态／用例状态／执行证据分列（判据 E2）。

    verdict 取值：CONTAINER（有子维度的父项，状态落在子项）、NOT_IMPLEMENTED、
    SEMANTIC_MISMATCH（代码在跑但校验的不是规章说的那件事）、OBJECT_MISSING、
    PARTIAL、NO_COUNTER_EXAMPLE（有实现但没有反例用例）、NOT_EXECUTED（用例写了
    但这一版没跑）、LAST_RUN_FAILED、VERIFIED。**只有 VERIFIED 才是三栏齐备。**
    """
    return svc.matrix(conn, verdict=verdict)


@router.get("/das/independence/summary")
def summary(conn: Conn, user: CurrentUser):
    """按 verdict 分组。**不给总数**（判据 I10：不得笼统计数）。"""
    return svc.summary(conn)


@router.get("/das/independence/gaps")
def gaps(conn: Conn, user: CurrentUser, kind: str | None = Query(None)):
    """缺口清单，自评要逐条如实写明的。

    gap_kind 分三类，处置方式完全不同：IMPLEMENTATION（实现或验证有缺口，须写明
    人工控制）、EVIDENCE（实现齐备、用例也写了，只是没跑过——补救是去跑，不是补
    线下控制）、DEFECT（登记册与系统对不上，或上次跑失败了，当场要查的问题）。
    """
    return _wrap(svc.gaps, conn, kind=kind)


@router.get("/das/independence/unexecuted")
def unexecuted(conn: Conn, user: CurrentUser):
    """用例已写、这一版还没跑过的（判据 E2：有用例 ≠ 跑过）。"""
    return svc.unexecuted(conn)


@router.get("/das/independence/health")
def health(conn: Conn, user: CurrentUser):
    """登记册与系统本身是否对得上。两张表平时都应为空。

    object_missing 非空意味着登记册声称某个触发器在保证某条规则，而系统目录里没有
    这个对象——这是最难发现的一类假覆盖，登记册看上去是满的。插入时有 INV-083 拦，
    但后续迁移删掉一个对象不会回来改登记册，所以要持续查。
    """
    return svc.health(conn)


@router.get("/das/independence/mutual-backup")
def mutual_backup(conn: Conn, user: CurrentUser):
    """互为备份的组合（判据 I5）。**可见性清单，不是违规清单**，交独立监督核对。"""
    return svc.mutual_backup(conn)


@router.get("/das/independence/rules/{code}")
def rule(conn: Conn, user: CurrentUser, code: str):
    """单条规则：当前状态、子维度、状态改动留痕、最近 50 条执行记录。"""
    return _wrap(svc.rule, conn, code)


@router.patch("/das/independence/rules/{code}")
def set_state(conn: Conn, user: CurrentUser, code: str, body: SetStateRequest):
    """登记一条规则的实现状态（须在任适航管理负责人）。

    容器项不接受状态：有子维度的父项状态只落在子维度上（判据 I10）。
    """
    return _wrap(svc.set_state, conn, code=code, actor=user,
                 **body.model_dump(exclude_unset=True))


@router.post("/das/independence/rules/{code}/executions", status_code=201)
def record_execution(conn: Conn, user: CurrentUser, code: str, body: ExecutionRequest):
    """登记一次反例用例的执行（判据 E2 第三栏，由 CI 调用）。

    不设任命门槛，防护是可追溯：提交号与批次号必填，写入后不得改写、不得删除。
    """
    return _wrap(svc.record_execution, conn, rule_code=code, actor=user,
                 **body.model_dump())
