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
               sa.discipline_code, d.name_cn AS discipline_name,
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
          discipline_code: str | None = None,
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
    # UG-DAW-006 第 1、3 章: 首次授权前须完成初始培训并考核合格; 补考仍不合格的不得授权。
    # 放在这里而不是任命处: 手册要求的是"授权前", 任命与授权是两回事——
    # 一个人可以先被任命到岗位上接受培训, 但不能在培训未达标时取得签署权。
    from . import das_training
    why = das_training.training_gate(conn, str(user_id))
    if why:
        raise ValueError(why)

    # 判据 E1 的另一半: UG-DAP-03 步骤 3～5 要求资格评估与独立性核对通过后才签发授权。
    #
    # 只对 CVE 级校验, 不对 REVIEW/APPROVE 校验 —— 这不是偷懒, 是范围问题:
    # UG-DAF-06 是"**授权人员**评估表", 它的"授权文件类型"栏是 4 类**适航签署事项**
    # (更改分类/符合性声明/小改批准/CVE 核查); 而本系统的 level REVIEW/APPROVE 是
    # **文档签署级别**, 两者不是一回事——待澄清项第 1 条写的就是这件事, 要等 M3／M4。
    # 对每一个文档审核人都要求一份 UG-DAF-06, 是把适航授权的门槛套到文档流转上。
    # 等签署事项这一维落地, 本闸门的适用范围随之扩到那几类事项。
    if level == "CVE":
        from . import das_qualification
        why = das_qualification.qualification_gate(conn, str(user_id))
        if why:
            raise ValueError(why)

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
                                          granted_by, discipline_code, backup_user_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, level, file_type_code, valid_from, valid_to, discipline_code
    """, (user_id, level, file_type_code, start, valid_to, note, actor["user_id"],
          discipline_code, backup_user_id))
    audit.write(conn, action="SIGNER_GRANT", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="SIGNER_AUTHORIZATION", object_id=str(row["id"]), object_code=user["username"],
                new_value={"level": level, "file_type_code": file_type_code,
                           "discipline_code": discipline_code,
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

    # 判据 CV1: 撤销前先看他是不是别人的备份人。
    # 名册按需动态增减时, 撤销甲会让乙的备份安排凭空落空 —— 而乙的授权还在、
    # 名单上还写着, 看起来一切正常, 但 AC-21-48 3.2.4 要的备份已经没有了。
    # 所以宁可拦在这里, 也不让"有备份"退化成一句名单上的话。
    orphaned = fetch_all(conn, """
        SELECT u.full_name, u.username, sa.level, sa.discipline_code
          FROM signer_authorization sa
          JOIN app_user u ON u.id = sa.user_id
         WHERE sa.backup_user_id = (SELECT user_id FROM signer_authorization WHERE id = %s)
           AND sa.revoked_at IS NULL
           AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)
         ORDER BY u.full_name""", (auth_id,))
    if orphaned:
        who = "、".join(f'{o["full_name"]}（{LEVELS.get(o["level"], o["level"])}'
                       + (f'·{o["discipline_code"]}' if o["discipline_code"] else '') + '）'
                       for o in orphaned)
        raise ValueError(
            f"撤销前请先为下列人员指定新的备份人, 否则他们的备份安排会落空: {who}。"
            "AC-21-48 3.2.4 要求每名 CVE 和授权人员都有备份。")

    execute(conn, """UPDATE signer_authorization SET revoked_at = now(), revoked_by = %s, revoke_reason = %s
                      WHERE id = %s""", (actor["user_id"], reason, auth_id))
    audit.write(conn, action="SIGNER_REVOKE", user_id=str(actor["user_id"]), username=actor["username"],
                object_type="SIGNER_AUTHORIZATION", object_id=auth_id, object_code=row["username"],
                new_value={"level": row["level"]}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


# 范围匹配: 授权上该维度为空 = 不限; 资料上该维度为空 = 未标注, 无从比对则不拦截。
# 后一条是给升级留的路: 0028 之前建的文件没有专业, 不能让它们的在途审批突然签不下去。
# 补标专业由 UG-DAP-01 的四性核查推动, 不靠在这里卡人。
#
# 参数必须显式 ::text: 裸写 "%s IS NULL" 时 Postgres 无从推断参数类型,
# 会以 "could not determine data type of parameter" 拒绝整条语句。
_SCOPE_SQL = """
             AND (sa.file_type_code IS NULL OR sa.file_type_code = %s::text)
             AND (sa.discipline_code IS NULL OR %s::text IS NULL OR sa.discipline_code = %s::text)
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


def backup_status(conn: psycopg.Connection) -> list[dict]:
    """判据 CV2: 当前备份完整性。

    返回全部有效授权, gap 为空表示备份安排完整, 非空即说明缺在哪:
    未指定备份人、备份人账户已停用、备份人在该级别上没有有效授权。

    返回全部而不只返回有缺口的, 是为了让界面能说出"10 项里 2 项有缺口" ——
    只列缺口看不出分母, 而判据 CV2 要的是"一眼看出完整性"。
    """
    return fetch_all(conn, """
        SELECT sa.id, u.full_name, u.username, sa.level, sa.discipline_code,
               d.name_cn AS discipline_name, sa.valid_to,
               b.full_name AS backup_full_name, b.is_active AS backup_active,
               CASE
                 WHEN sa.backup_user_id IS NULL THEN '未指定备份人'
                 WHEN NOT b.is_active           THEN '备份人账户已停用'
                 WHEN NOT EXISTS (SELECT 1 FROM signer_authorization x
                                   WHERE x.user_id = sa.backup_user_id AND x.level = sa.level
                                     AND x.revoked_at IS NULL
                                     AND (x.valid_to IS NULL OR x.valid_to >= current_date))
                                                THEN '备份人在该级别上没有有效授权'
               END AS gap
          FROM signer_authorization sa
          JOIN app_user u ON u.id = sa.user_id
          LEFT JOIN app_user b ON b.id = sa.backup_user_id
          LEFT JOIN das_discipline d ON d.code = sa.discipline_code
         WHERE sa.revoked_at IS NULL
           AND sa.valid_from <= current_date
           AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)
         ORDER BY u.full_name, sa.level
    """)
