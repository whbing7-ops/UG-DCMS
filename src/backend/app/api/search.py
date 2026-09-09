"""检索、数据质量、报表端点。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import errors
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import quality as q_svc, search as search_svc

router = APIRouter(tags=["检索与报表"], route_class=TransactionalRoute)

KINDS = {"PART_NUMBER", "EXTERNAL_PART", "SOFTWARE", "FAMILY", "FILE", "ATTACHMENT"}


@router.get("/search")
def search(conn: Conn, user: CurrentUser,
           q: str = Query(..., min_length=1, max_length=128),
           kinds: str | None = Query(None, description="逗号分隔, 缺省全部"),
           limit: int = Query(50, ge=1, le=100)):
    """统一检索 — AC-SEARCH-01 / AT-008。

    精确、归一、交叉引用、模糊四层依次匹配, 精确命中永远排在最前。
    历史件号(如 ZD850-3)经归一后可定位到当前对象。
    """
    kl = None
    if kinds:
        kl = [k.strip().upper() for k in kinds.split(",") if k.strip()]
        bad = [k for k in kl if k not in KINDS]
        if bad:
            raise errors.bad_request(f"未知类型: {', '.join(bad)}; "
                                     f"可用: {', '.join(sorted(KINDS))}")
    return search_svc.search(conn, q, kinds=kl, limit=limit)


@router.get("/objects/{object_code}")
def object_overview(object_code: str, conn: Conn, user: CurrentUser):
    """对象全貌: 身份、功能、属性、交叉引用、未决质量问题。"""
    obj = search_svc.object_overview(conn, object_code)
    if obj is None:
        raise errors.not_found(f"设计对象不存在: {object_code}")
    search_svc.record_access(conn, str(user["user_id"]), obj)
    return obj


@router.get("/recent")
def recent(conn: Conn, user: CurrentUser, limit: int = Query(20, ge=1, le=50)):
    return search_svc.recent(conn, str(user["user_id"]), limit)


# ---------------- 数据质量 ----------------
@router.post("/quality/scan")
def scan(conn: Conn, rule_code: str | None = Query(None),
         actor: dict = Depends(require(Perm.READ_AUDIT))):
    """执行数据质量规则并登记问题。"""
    return q_svc.run_rules(conn, actor, [rule_code] if rule_code else None)


@router.get("/quality/issues")
def issues(conn: Conn, user: CurrentUser,
           severity: str | None = Query(None, pattern="^(ERROR|WARNING|INFO)$"),
           rule_code: str | None = None,
           status: str = Query("OPEN", pattern="^(OPEN|RESOLVED|WAIVED)$"),
           limit: int = Query(200, ge=1, le=1000)):
    return q_svc.list_issues(conn, severity=severity, rule_code=rule_code,
                             status=status, limit=limit)


@router.get("/quality/rules")
def rules(conn: Conn, user: CurrentUser):
    from ..db import fetch_all
    return fetch_all(conn, """
        SELECT code, name_cn, severity, object_type, description, blocks_release, status
          FROM data_quality_rule ORDER BY code
    """)


@router.post("/quality/issues/{issue_id}/resolve")
def resolve(issue_id: str, conn: Conn,
            actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        q_svc.resolve_issue(conn, issue_id, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    return {"message": "问题已标记为处置完成"}


@router.post("/quality/issues/{issue_id}/waive")
def waive(issue_id: str, conn: Conn,
          reason: str = Query(..., min_length=1, max_length=500),
          expires_days: int = Query(..., ge=1, le=365),
          actor: dict = Depends(require(Perm.BASELINE_RELEASE))):
    """豁免一个质量问题。必须给理由与有效期——无期限豁免等于把规则关掉。"""
    try:
        q_svc.waive_issue(conn, issue_id, reason, expires_days, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": f"问题已豁免 {expires_days} 天, 到期后自动恢复"}


# ---------------- 报表 ----------------
@router.get("/reports/dashboard")
def dashboard(conn: Conn, user: CurrentUser):
    return q_svc.dashboard(conn)


@router.get("/reports/number-utilization")
def number_utilization(conn: Conn, user: CurrentUser):
    return q_svc.number_utilization(conn)


@router.get("/reports/function-distribution")
def function_distribution(conn: Conn, user: CurrentUser):
    return q_svc.function_distribution(conn)


@router.get("/reports/classification-distribution")
def classification_distribution(conn: Conn, user: CurrentUser):
    return q_svc.classification_distribution(conn)


@router.get("/reports/release-activity")
def release_activity(conn: Conn, user: CurrentUser,
                     days: int = Query(90, ge=7, le=730)):
    return q_svc.release_activity(conn, days)
