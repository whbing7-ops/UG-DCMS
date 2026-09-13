"""审批中心 — SRS-ACC-006 / INV-025 / AC-SEC-04。

在此之前, 审批只能从各对象页面发起: 批准人要先知道「有个设计族在等我批」, 才能找到
那个页面点批准。实际使用中这意味着申请人得挨个去通知批准人 —— 系统里明明有
approval_request 表, 却没有「待我处理」这个入口。

本模块把散落在各处的审批统一成一个待办清单, 并提供撤回、退回、拒绝这些此前缺失的
处置方式。此前只有「批准」一条路: 批准人认为材料不足时, 唯一的选择是不操作,
申请就那么一直挂着, 申请人也不知道卡在哪。

**INV-025 由数据库触发器强制**, 本模块不重复实现权限分离判断 —— 应用层再写一遍,
两处规则迟早不一致, 而不一致时以哪边为准并不明确。这里只负责把触发器的拒绝翻译成
可读提示。
"""
from __future__ import annotations

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one
from ..db import scalar
from ..rbac import Perm, ROLE_PERMISSIONS

REQUEST_TYPE_CN = {
    "BASIC_DRAWING_NUMBER": "新建设计族",
    "DASH_NUMBER": "申请 Dash 号",
    "DESIGN_FILE_NUMBER": "申请文件号",
    "FILE_REVISION_RELEASE": "发布文件版次",
    "BASELINE_RELEASE": "发布设计基线",
    "EXTERNAL_TS_ACCEPT": "接受外部件技术状态",
    "EXTERNAL_PROJECT_APPROVAL": "外部件项目准入",
    "NUMBER_CANCELLATION": "作废号码",
    "DICTIONARY_CHANGE": "变更受控字典",
    "CHANGE_PACKAGE": "变更包",
    "FAMILY_CLOSE": "关闭设计族",
    "OBJECT_OBSOLETE": "废止对象",
}


def next_request_number(conn: psycopg.Connection) -> str:
    """事务级串行发审批号，避免并发提交得到同一编号。"""
    scalar(conn, "SELECT pg_advisory_xact_lock(811002)")
    year=int(scalar(conn,"SELECT EXTRACT(YEAR FROM current_date)"))
    prefix=f"AR-{year}-"
    seq=scalar(conn,"""SELECT COALESCE(max(substring(request_number from 9)::int),0)+1
      FROM approval_request WHERE request_number LIKE %s""",(prefix+"%",)) or 1
    return f"{prefix}{seq:06d}"


def approver_roles(required_role: str) -> list[str]:
    # 一般审批按既有 APPROVE 权限选人；基线等专门角色要求仍严格匹配。
    if required_role == "APPROVER":
        return [str(role) for role, permissions in ROLE_PERMISSIONS.items()
                if Perm.APPROVE in permissions]
    if required_role not in ROLE_PERMISSIONS:
        raise ValueError("审批角色无效")
    return [required_role]


def available_approvers(conn: psycopg.Connection, requester_id: str,
                        required_role: str = "APPROVER") -> list[dict]:
    """返回发起人当前可选择的审批人。

    只列启用账户、具备所需权限或专门角色且不是发起人本人。申请提交时会再次校验，
    避免页面打开后人员被停用或角色被撤销造成越权提交。
    """
    return fetch_all(conn, """
        SELECT u.id::text, u.username, u.full_name, u.employee_no,
               ur.role_code
          FROM app_user u
          JOIN LATERAL (
            SELECT role_code FROM user_role WHERE user_id=u.id AND role_code=ANY(%s)
             ORDER BY (role_code=%s) DESC, role_code LIMIT 1
          ) ur ON true
         WHERE u.is_active AND u.id<>%s
         ORDER BY u.full_name, u.username
    """, (approver_roles(required_role), required_role, requester_id))


def require_approver(conn: psycopg.Connection, approver_user_id: str,
                     requester_id: str, required_role: str = "APPROVER") -> dict:
    row = fetch_one(conn, """
        SELECT u.id, u.username, u.full_name, ur.role_code
          FROM app_user u
          JOIN LATERAL (
            SELECT role_code FROM user_role WHERE user_id=u.id AND role_code=ANY(%s)
             ORDER BY (role_code=%s) DESC, role_code LIMIT 1
          ) ur ON true
         WHERE u.id=%s AND u.is_active
    """, (approver_roles(required_role), required_role, approver_user_id))
    if row is None:
        raise ValueError("所选审批人已停用或不再具备审批权限，请重新选择")
    if str(row["id"]) == str(requester_id):
        raise ValueError("发起人不能选择自己作为审批人")
    return row


def require_assignee(conn: psycopg.Connection, request_id: str, actor_id: str) -> None:
    step = fetch_one(conn, """
        SELECT assignee_user_id FROM approval_step
         WHERE approval_request_id=%s AND decision='PENDING'
         ORDER BY step_order LIMIT 1
    """, (request_id,))
    if step is None:
        raise ValueError("该申请没有待处理的审批步骤")
    if step["assignee_user_id"] and str(step["assignee_user_id"]) != str(actor_id):
        raise ValueError("该申请已指定给其他审批人，不能代为审批")


def _base_query() -> str:
    return """
        SELECT ar.id, ar.request_number, ar.request_type, ar.object_type, ar.object_id,
               ar.object_code, ar.title, ar.status, ar.requested_at, ar.closed_at,
               u.username AS requester_username, u.full_name AS requester_name,
               ar.requester_id, so.software_number,
               s.id AS step_id, s.step_order, s.step_name, s.required_role_code,
               s.assignee_user_id,
               s.is_final, s.decision, s.comments, s.acted_at,
               du.username AS decided_by_username
          FROM approval_request ar
          JOIN app_user u ON u.id = ar.requester_id
          LEFT JOIN software_version sv ON ar.object_type='SOFTWARE_VERSION' AND sv.id=ar.object_id
          LEFT JOIN software_object so ON so.id=sv.software_object_id
          LEFT JOIN approval_step s ON s.approval_request_id = ar.id
                                   AND s.decision = 'PENDING'
          LEFT JOIN app_user du ON du.id = s.decided_by
    """


def inbox(conn: psycopg.Connection, user: dict) -> list[dict]:
    """待我处理的审批。

    过滤规则:
      1 申请仍为 PENDING;
      2 当前待决步骤要求的角色我具备;
      3 我不是申请人 —— INV-025 会在最终批准时拒绝自批, 但如果先把这些申请列给我看,
        点进去才被拒绝, 那是在浪费使用者的时间。
    """
    roles = list(user.get("roles") or [])
    return fetch_all(conn, _base_query() + """
         WHERE ar.status = 'PENDING'
           AND s.id IS NOT NULL
           AND ((s.assignee_user_id IS NOT NULL AND s.assignee_user_id = %s)
                OR (s.assignee_user_id IS NULL AND s.required_role_code = ANY(%s)))
           AND ar.requester_id <> %s
         ORDER BY ar.requested_at
         LIMIT 200
    """, (user["user_id"], roles, user["user_id"]))


def my_requests(conn: psycopg.Connection, user: dict, include_closed: bool = False) -> list[dict]:
    """我发起的申请 —— 申请人需要知道自己的东西卡在哪一步。"""
    return fetch_all(conn, _base_query() + """
         WHERE ar.requester_id = %s
           AND (%s OR ar.status = 'PENDING')
         ORDER BY ar.requested_at DESC
         LIMIT 200
    """, (user["user_id"], include_closed))


def all_pending(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, _base_query() + """
         WHERE ar.status = 'PENDING' ORDER BY ar.requested_at LIMIT 500
    """)


def get_request(conn: psycopg.Connection, request_id: str) -> dict | None:
    req = fetch_one(conn, """
        SELECT ar.*, u.username AS requester_username, u.full_name AS requester_name, so.software_number
          FROM approval_request ar JOIN app_user u ON u.id = ar.requester_id
          LEFT JOIN software_version sv ON ar.object_type='SOFTWARE_VERSION' AND sv.id=ar.object_id
          LEFT JOIN software_object so ON so.id=sv.software_object_id
         WHERE ar.id = %s
    """, (request_id,))
    if req is None:
        return None
    req["steps"] = fetch_all(conn, """
        SELECT s.id, s.step_order, s.step_name, s.required_role_code, s.is_final,
               s.assignee_user_id, s.decision, s.comments, s.acted_at,
               u.username AS decided_by_username, au.username AS assignee_username,
               au.full_name AS assignee_name
          FROM approval_step s LEFT JOIN app_user u ON u.id = s.decided_by
          LEFT JOIN app_user au ON au.id=s.assignee_user_id
         WHERE s.approval_request_id = %s ORDER BY s.step_order
    """, (request_id,))
    return req


def _decide(conn: psycopg.Connection, request_id: str, decision: str,
            comments: str, actor: dict) -> dict:
    req = get_request(conn, request_id)
    if req is None:
        raise LookupError("审批申请不存在")
    if req["status"] != "PENDING":
        raise ValueError(f"申请状态为 {req['status']}, 已结束")

    step = next((s for s in req["steps"] if s["decision"] == "PENDING"), None)
    if step is None:
        raise ValueError("该申请没有待决步骤")
    if step.get("assignee_user_id") and str(step["assignee_user_id"]) != str(actor["user_id"]):
        raise ValueError("该申请已指定给其他审批人，不能代为处理")

    # 数据库触发器 trg_approval_separation 会在此拒绝申请人自批(INV-025)
    execute(conn, """
        UPDATE approval_step SET decision=%s, decided_by=%s, acted_at=now(), comments=%s
         WHERE id=%s
    """, (decision, actor["user_id"], comments, step["id"]))

    # 退回不结束申请: 申请人补充材料后可再次提交, 无需重新走一遍全流程
    new_status = {"APPROVED": "APPROVED", "REJECTED": "REJECTED",
                  "RETURNED": "RETURNED"}[decision]
    execute(conn, "UPDATE approval_request SET status=%s, closed_at=now() WHERE id=%s",
            (new_status, request_id))
    if decision in ("RETURNED", "REJECTED"):
        _reopen_object(conn, req)

    audit.write(conn, action=f"APPROVAL_{decision}", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="APPROVAL_REQUEST",
                object_id=request_id, object_code=req["request_number"],
                old_value={"status": "PENDING"},
                new_value={"status": new_status, "step": step["step_name"],
                           "target_object": req["object_code"]},
                reason=comments, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return {"request_number": req["request_number"], "status": new_status,
            "object_code": req["object_code"], "object_type": req["object_type"]}


def _reopen_object(conn: psycopg.Connection, req: dict) -> None:
    """审批未通过后把业务对象退回可修改状态，并解除旧申请占用。"""
    mapping = {
        "BASIC_DRAWING_FAMILY": ("basic_drawing_family", "PENDING"),
        "FILE_REVISION": ("file_revision", "WORKING"),
        "DESIGN_BASELINE": ("design_baseline", "DRAFT"),
        "EXTERNAL_PROJECT_CONTROL": ("external_part_project_control", "DRAFT"),
        "EXTERNAL_TECHNICAL_STATE": ("external_technical_state", "DRAFT"),
        "SOFTWARE_VERSION": ("software_version", "DRAFT"),
    }
    target=mapping.get(req["object_type"])
    if target and req.get("object_id"):
        table,status=target
        execute(conn, f"UPDATE {table} SET status=%s, approval_request_id=NULL WHERE id=%s",
                (status,req["object_id"]))


def reject(conn: psycopg.Connection, request_id: str, reason: str, actor: dict) -> dict:
    """拒绝。申请就此结束, 申请人要另起一份新申请。"""
    if not reason.strip():
        raise ValueError("拒绝必须写明理由")
    return _decide(conn, request_id, "REJECTED", reason, actor)


def send_back(conn: psycopg.Connection, request_id: str, reason: str, actor: dict) -> dict:
    """退回补充。

    与拒绝的区别: 退回是「方向没问题但材料不足」, 申请人改完再提交即可; 拒绝是
    「这件事不该做」。此前系统只有批准一条路, 批准人遇到材料不足时只能干脆不操作,
    申请就一直挂着, 申请人还不知道卡在哪 —— 这不是流程严谨, 是流程缺了一环。
    """
    if not reason.strip():
        raise ValueError("退回必须写明需要补充什么")
    return _decide(conn, request_id, "RETURNED", reason, actor)


def withdraw(conn: psycopg.Connection, request_id: str, reason: str, actor: dict) -> dict:
    """申请人撤回自己的申请。

    只能撤回自己发起的, 且必须尚未有人作出决定 —— 已经有人批过的申请再撤回,
    等于抹掉一次已经发生的审批行为。
    """
    req = get_request(conn, request_id)
    if req is None:
        raise LookupError("审批申请不存在")
    if str(req["requester_id"]) != str(actor["user_id"]):
        raise PermissionError("只能撤回自己发起的申请")
    if req["status"] != "PENDING":
        raise ValueError(f"申请状态为 {req['status']}, 不可撤回")
    if any(s["decision"] != "PENDING" for s in req["steps"]):
        raise ValueError("已有审批步骤作出决定, 不可撤回")

    execute(conn, "UPDATE approval_request SET status='CANCELLED', closed_at=now() WHERE id=%s",
            (request_id,))
    _reopen_object(conn, req)
    audit.write(conn, action="APPROVAL_WITHDRAW", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="APPROVAL_REQUEST",
                object_id=request_id, object_code=req["request_number"],
                new_value={"status": "CANCELLED"}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return {"request_number": req["request_number"], "status": "CANCELLED"}


def summary(conn: psycopg.Connection, user: dict) -> dict:
    """首页角标用的计数。"""
    roles = list(user.get("roles") or [])
    return fetch_one(conn, """
        SELECT
          (SELECT count(*) FROM approval_request ar
             JOIN approval_step s ON s.approval_request_id = ar.id AND s.decision='PENDING'
            WHERE ar.status='PENDING' AND ar.requester_id <> %s
              AND ((s.assignee_user_id IS NOT NULL AND s.assignee_user_id=%s)
                   OR (s.assignee_user_id IS NULL AND s.required_role_code=ANY(%s)))) AS inbox,
          (SELECT count(*) FROM approval_request
            WHERE requester_id = %s AND status='PENDING') AS my_pending,
          (SELECT count(*) FROM approval_request
            WHERE requester_id = %s AND status='RETURNED') AS my_returned
    """, (user["user_id"], user["user_id"], roles, user["user_id"], user["user_id"]))
