"""有权签署人清单。

三级签署里的"审核"和"批准"不是谁有审批权限谁就能做, 而是必须在这张清单里被明确授权:
哪个人、哪一级、哪类文件、什么时候到什么时候。清单是设计保证里"授权签署人"的系统体现。

两条规则:
  · 授权记录只能撤销, 不能改写或删除(数据库触发器), 历史授权永远可查。
  · 不得给自己授权(数据库约束)。授权由另一名构型管理员做出。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar
from ..rbac import ROLE_PERMISSIONS, Perm

LEVELS = {"REVIEW": "审核", "APPROVE": "批准", "CVE": "符合性核查"}


def _signing_roles() -> list[str]:
    return [str(r) for r, perms in ROLE_PERMISSIONS.items() if Perm.SIGN in perms]


def list_authorizations(conn: psycopg.Connection, include_revoked: bool = False) -> list[dict]:
    return fetch_all(conn, """
        SELECT sa.id, sa.level, sa.file_type_code, ft.name_cn AS file_type_name, sa.valid_from, sa.valid_to,
               sa.note, sa.granted_at, sa.revoked_at, sa.revoke_reason,
               sa.discipline_code, d.name_cn AS discipline_name, sa.product_scope,
               sa.backup_user_id, b.full_name AS backup_full_name,
               u.id AS user_id, u.username, u.full_name, u.is_active,
               g.full_name AS granted_by_name, r.full_name AS revoked_by_name,
               (sa.revoked_at IS NULL AND sa.valid_from <= current_date
                 AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)) AS in_force
          FROM signer_authorization sa
          JOIN app_user u ON u.id = sa.user_id
          JOIN app_user g ON g.id = sa.granted_by
          LEFT JOIN app_user r ON r.id = sa.revoked_by
          LEFT JOIN app_user b ON b.id = sa.backup_user_id
          LEFT JOIN file_type ft ON ft.code = sa.file_type_code
          LEFT JOIN das_discipline d ON d.code = sa.discipline_code
         WHERE (%s OR sa.revoked_at IS NULL)
         ORDER BY (sa.revoked_at IS NOT NULL), u.full_name, sa.level, sa.granted_at DESC
    """, (include_revoked,))


def grant(conn: psycopg.Connection, *, user_id: str, level: str, file_type_code: str | None,
          valid_from: dt.date | None, valid_to: dt.date | None, note: str | None, actor: dict,
          discipline_code: str | None = None, product_scope: str | None = None,
          backup_user_id: str | None = None) -> dict:
    if level not in LEVELS:
        raise ValueError("签署级别须为 审核、批准 或 符合性核查")
    if str(user_id) == str(actor["user_id"]):
        raise ValueError("不得给自己授权, 请由另一名构型管理员操作")
    if backup_user_id is not None and str(backup_user_id) == str(user_id):
        raise ValueError("备份人不得是本人, 自己给自己做备份等于没有备份")
    user = fetch_one(conn, """
        SELECT u.id, u.username, u.full_name FROM app_user u
         WHERE u.id = %s AND u.is_active
           AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s))
    """, (user_id, _signing_roles()))
    if user is None:
        raise ValueError("该账户已停用, 或没有可签署的角色(设计工程师/审批员/构型管理员)")
    if file_type_code and scalar(conn, "SELECT count(*) FROM file_type WHERE code=%s", (file_type_code,)) == 0:
        raise ValueError("文件类型不存在")
    if discipline_code and scalar(conn, "SELECT count(*) FROM das_discipline WHERE code=%s AND status='ACTIVE'",
                                  (discipline_code,)) == 0:
        raise ValueError("专业不存在或已停用")
    if backup_user_id is not None:
        backup = fetch_one(conn, """
            SELECT u.id FROM app_user u
             WHERE u.id = %s AND u.is_active
               AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s))
        """, (backup_user_id, _signing_roles()))
        if backup is None:
            raise ValueError("备份人已停用, 或没有可签署的角色, 不能作为备份")
    start = valid_from or dt.date.today()
    if valid_to is not None and valid_to < start:
        raise ValueError("有效期截止日不得早于起始日")
    dup = scalar(conn, """
        SELECT count(*) FROM signer_authorization
         WHERE user_id=%s AND level=%s AND file_type_code IS NOT DISTINCT FROM %s
           AND discipline_code IS NOT DISTINCT FROM %s AND revoked_at IS NULL
           AND (valid_to IS NULL OR valid_to >= current_date)""",
                  (user_id, level, file_type_code, discipline_code))
    if dup:
        raise ValueError("该账户已有同级别、同文件类型、同专业的有效授权")
    row = fetch_one(conn, """
        INSERT INTO signer_authorization (user_id, level, file_type_code, valid_from, valid_to, note,
                                          granted_by, discipline_code, product_scope, backup_user_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, level, file_type_code, valid_from, valid_to, discipline_code, product_scope
    """, (user_id, level, file_type_code, start, valid_to, note, actor["user_id"],
          discipline_code, product_scope, backup_user_id))
    audit.write(conn, action="SIGNER_GRANT", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="SIGNER_AUTHORIZATION", object_id=str(row["id"]), object_code=user["username"],
                new_value={"level": level, "file_type_code": file_type_code,
                           "discipline_code": discipline_code, "product_scope": product_scope,
                           "backup_user_id": str(backup_user_id) if backup_user_id else None,
                           "valid_from": str(start), "valid_to": str(valid_to) if valid_to else None},
                reason=note, session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def disciplines(conn: psycopg.Connection) -> list[dict]:
    """适航专业字典(启用项), 供授权和文件标注选择。"""
    return fetch_all(conn, """
        SELECT code, name_cn, name_en, definition FROM das_discipline
         WHERE status = 'ACTIVE' ORDER BY sort_order, code
    """)


def revoke(conn: psycopg.Connection, auth_id: str, reason: str, actor: dict) -> None:
    row = fetch_one(conn, """
        SELECT sa.id, sa.level, sa.revoked_at, u.username FROM signer_authorization sa
          JOIN app_user u ON u.id = sa.user_id WHERE sa.id = %s""", (auth_id,))
    if row is None:
        raise LookupError("授权记录不存在")
    if row["revoked_at"] is not None:
        raise ValueError("该授权已撤销")
    execute(conn, """UPDATE signer_authorization SET revoked_at = now(), revoked_by = %s, revoke_reason = %s
                      WHERE id = %s""", (actor["user_id"], reason, auth_id))
    audit.write(conn, action="SIGNER_REVOKE", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="SIGNER_AUTHORIZATION", object_id=auth_id, object_code=row["username"],
                new_value={"level": row["level"]}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


# 范围匹配: 授权上该维度为空 = 不限; 资料上该维度为空 = 未标注, 无从比对则不拦截。
# 后一条是给升级留的路: 0028 之前建的文件没有专业, 不能让它们的在途审批突然签不下去。
# 补标专业由 UG-DAP-01 的四性核查推动, 不靠在这里卡人。
_SCOPE_SQL = """
             AND (sa.file_type_code IS NULL OR sa.file_type_code = %s)
             AND (sa.discipline_code IS NULL OR %s IS NULL OR sa.discipline_code = %s)
"""


def is_authorized(conn: psycopg.Connection, user_id: str, level: str, file_type_code: str | None,
                  discipline_code: str | None = None) -> bool:
    """此刻该用户能否签署这份资料。

    按 UG-DAP-03 第 8 节, 逐项校验: 在册、账户启用且有可签署角色、授权未撤销、
    在有效期内、文件类型相符、专业相符。任一不符即不得签署。
    """
    return bool(scalar(conn, f"""
        SELECT EXISTS (
          SELECT 1 FROM signer_authorization sa JOIN app_user u ON u.id = sa.user_id
           WHERE sa.user_id = %s AND sa.level = %s AND sa.revoked_at IS NULL AND u.is_active
             AND sa.valid_from <= current_date AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)
             {_SCOPE_SQL}
             AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s)))
    """, (user_id, level, file_type_code, discipline_code, discipline_code, _signing_roles())))


def refusal_reason(conn: psycopg.Connection, user_id: str, level: str, file_type_code: str | None,
                   discipline_code: str | None = None) -> str | None:
    """不能签署时说清楚是哪一项不符, 而不是只回一句"不在清单内"。"""
    if is_authorized(conn, user_id, level, file_type_code, discipline_code):
        return None
    rows = fetch_all(conn, """
        SELECT sa.file_type_code, ft.name_cn AS file_type_name, sa.discipline_code,
               d.name_cn AS discipline_name, sa.valid_to, sa.revoked_at,
               (sa.valid_to IS NOT NULL AND sa.valid_to < current_date) AS expired
          FROM signer_authorization sa
          LEFT JOIN file_type ft ON ft.code = sa.file_type_code
          LEFT JOIN das_discipline d ON d.code = sa.discipline_code
         WHERE sa.user_id = %s AND sa.level = %s
         ORDER BY sa.granted_at DESC
    """, (user_id, level))
    if not rows:
        return f"该人员没有{LEVELS.get(level, level)}级别的签署授权"
    live = [r for r in rows if r["revoked_at"] is None and not r["expired"]]
    if not live:
        return f"该人员的{LEVELS.get(level, level)}授权已撤销或已过期"
    if discipline_code and all(r["discipline_code"] not in (None, discipline_code) for r in live):
        have = "、".join(sorted({r["discipline_name"] or r["discipline_code"] for r in live if r["discipline_code"]}))
        return f"授权专业不符: 本资料属其它专业, 该人员获授权的专业为 {have}"
    if file_type_code and all(r["file_type_code"] not in (None, file_type_code) for r in live):
        have = "、".join(sorted({r["file_type_name"] or r["file_type_code"] for r in live if r["file_type_code"]}))
        return f"授权文件类型不符: 该人员获授权的文件类型为 {have}"
    return "账户已停用, 或没有可签署的角色"


def candidates(conn: psycopg.Connection, level: str, file_type_code: str | None,
               exclude_user_ids: list[str], discipline_code: str | None = None) -> list[dict]:
    """可被选为该级签署人的账户: 已授权且在有效期内, 排除已占用其它级别的人。"""
    return fetch_all(conn, f"""
        SELECT DISTINCT u.id::text, u.username, u.full_name, u.employee_no
          FROM signer_authorization sa JOIN app_user u ON u.id = sa.user_id
         WHERE sa.level = %s AND sa.revoked_at IS NULL AND u.is_active
           AND sa.valid_from <= current_date AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)
           {_SCOPE_SQL}
           AND EXISTS (SELECT 1 FROM user_role ur WHERE ur.user_id = u.id AND ur.role_code = ANY(%s))
           AND NOT (u.id = ANY(%s::uuid[]))
         ORDER BY u.full_name, u.username
    """, (level, file_type_code, discipline_code, discipline_code, _signing_roles(), exclude_user_ids))
