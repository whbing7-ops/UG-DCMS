"""岗位任命（M1 人员培训与授权）。

设计输入从 A 版起就指出: 岗位不能等同于账号角色。5～8 人编制必然一人多岗,
所以要有 岗位 → 任命 → 互斥规则 三层, 让系统**在任命的那一刻**就拒绝冲突组合,
而不是等某人去签署时才发现他本来就不该同时坐这两个位子。

两件容易做错的事:

  · **岗位级与文件级的独立性不是一回事。**"独立监督负责人不得兼任运行管理人员"
    与具体文件无关, 任命时就能判; "编制人不得核查本文件"取决于这一份资料是谁编的,
    任命时无从判断。前者在这里拦, 后者在签署与审批环节拦。把文件级做成岗位互斥
    会过度限制——一人多岗本来就是常态。

  · **临时授权与正式任命并行, 不是二选一。**出缺当日必须有人(UG-DAP-03 步骤 11,
    法规时限不顺延), 而正式任命要走提名→资格评估→独立性核对→授权四步, 一天走不完。
    所以当日先发临时授权, 同时启动正式流程; 正式任命生效之日临时授权**自动终止**,
    不靠人记得去撤(判据 O3-3)。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

KINDS = {"FORMAL": "正式任命", "TEMPORARY": "临时授权"}


def positions(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """
        SELECT p.code, p.name_cn, p.is_exclusive, p.is_ops_mgmt, p.is_supervised, p.basis,
               (SELECT count(*) FROM das_appointment_in_force f WHERE f.position_code = p.code)
                   AS in_force_count
          FROM das_position p ORDER BY p.sort_order""")


def in_force(conn: psycopg.Connection, user_id: str | None = None) -> list[dict]:
    if user_id:
        return fetch_all(conn, """
            SELECT * FROM das_appointment_in_force WHERE user_id = %s
             ORDER BY position_code""", (user_id,))
    return fetch_all(conn, "SELECT * FROM das_appointment_in_force ORDER BY full_name, position_code")


def history(conn: psycopg.Connection, user_id: str) -> list[dict]:
    """含已撤销与已被接替的全部任命。任命记录只能撤销不能删除, 历史永远可查。"""
    return fetch_all(conn, """
        SELECT a.id, a.position_code, p.name_cn AS position_name, a.kind,
               a.valid_from, a.valid_to, a.appointment_ref, a.formal_process_ref,
               a.superseded_by, a.revoked_at, a.revoke_reason,
               b.full_name AS appointed_by_name, r.full_name AS revoked_by_name
          FROM das_appointment a
          JOIN das_position p ON p.code = a.position_code
          JOIN app_user b ON b.id = a.appointed_by
          LEFT JOIN app_user r ON r.id = a.revoked_by
         WHERE a.user_id = %s ORDER BY a.valid_from DESC, a.id DESC""", (user_id,))


def temp_pending(conn: psycopg.Connection) -> list[dict]:
    """判据 O3-2: 只发了临时授权、正式流程迟迟没结果的。

    出缺当日发临时授权是对的; 临时授权一直挂着就不是了 —— 那等于用一个本该几天的
    应急措施长期替代正式任命, 而临时授权并不检验资格评估与独立性核对是否真做过。
    """
    return fetch_all(conn, "SELECT * FROM das_appointment_temp_pending")


def vacant(conn: psycopg.Connection) -> list[dict]:
    """无人在任的岗位。

    判据 A7: 这里只列"有没有人", 不判"够不够人"——够不够是管理评审的判断,
    取决于当期业务量, 不是系统里的常量。
    """
    return fetch_all(conn, "SELECT * FROM das_position_vacant")


def appoint(conn: psycopg.Connection, *, user_id: str, position_code: str, kind: str,
            valid_from: dt.date | None, valid_to: dt.date | None,
            appointment_ref: str | None, formal_process_ref: str | None, actor: dict) -> dict:
    """任命。互斥校验由数据库触发器做, 这里只做能给出更好提示的前置检查。"""
    if kind not in KINDS:
        raise ValueError("任命类型须为 FORMAL 正式任命 或 TEMPORARY 临时授权")
    if scalar(conn, "SELECT count(*) FROM das_position WHERE code=%s", (position_code,)) == 0:
        raise ValueError("岗位不存在")
    user = fetch_one(conn, "SELECT id, username, full_name, employee_no, is_active "
                           "FROM app_user WHERE id=%s", (user_id,))
    if user is None or not user["is_active"]:
        raise ValueError("账户不存在或已停用")
    # 判据 A1: 工号是自然人标识。没有工号就无法判定"不同自然人", 任命前先补。
    if not (user["employee_no"] or "").strip():
        raise ValueError(
            f'{user["full_name"]} 尚未填写工号。工号是判定"不同自然人"的唯一依据, '
            "任命前须先补齐（判据 A1）。")
    start = valid_from or dt.date.today()
    if kind == "TEMPORARY":
        if valid_to is None:
            raise ValueError("临时授权必须写明截止日期: 到期自动失效, 不依赖人工撤销（判据 O3-1）")
        if not (formal_process_ref or "").strip():
            raise ValueError(
                "临时授权必须关联正式任命流程的单据号（判据 O3-2）。"
                "只发临时授权而不启动正式流程, 等于用应急措施长期替代任命。")
    dup = scalar(conn, """
        SELECT count(*) FROM das_appointment
         WHERE user_id=%s AND position_code=%s AND kind=%s
           AND revoked_at IS NULL AND superseded_by IS NULL
           AND (valid_to IS NULL OR valid_to >= current_date)""", (user_id, position_code, kind))
    if dup:
        raise ValueError(f'该员已有在任的{KINDS[kind]}（{position_code}）')

    row = fetch_one(conn, """
        INSERT INTO das_appointment (user_id, position_code, kind, valid_from, valid_to,
                                     appointed_by, appointment_ref, formal_process_ref)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, user_id, position_code, kind, valid_from, valid_to
    """, (user_id, position_code, kind, start, valid_to, actor["user_id"],
          appointment_ref, formal_process_ref))

    superseded = 0
    if kind == "FORMAL":
        # 判据 O3-3: 正式任命生效之日, 同人同岗的临时授权自动终止。
        # 收紧截止日而不是撤销 —— 临时授权当时是有效的, 那段时间的签署不应被追溯否定。
        superseded = execute(conn, """
            UPDATE das_appointment
               SET valid_to = LEAST(COALESCE(valid_to, %s), %s), superseded_by = %s
             WHERE user_id=%s AND position_code=%s AND kind='TEMPORARY'
               AND revoked_at IS NULL AND superseded_by IS NULL
               AND (valid_to IS NULL OR valid_to >= current_date)""",
                             (start - dt.timedelta(days=1), start - dt.timedelta(days=1),
                              row["id"], user_id, position_code))

    audit.write(conn, action="DAS_APPOINT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_APPOINTMENT",
                object_id=str(row["id"]), object_code=user["username"],
                new_value={"position": position_code, "kind": kind,
                           "valid_from": str(start), "valid_to": str(valid_to) if valid_to else None,
                           "superseded_temporary": superseded},
                reason=appointment_ref, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    row["superseded_temporary"] = superseded
    return row


def revoke(conn: psycopg.Connection, appointment_id: int, reason: str, actor: dict) -> None:
    if not (reason or "").strip():
        raise ValueError("撤销任命须写明理由")
    row = fetch_one(conn, """
        SELECT a.id, a.position_code, a.revoked_at, u.username
          FROM das_appointment a JOIN app_user u ON u.id = a.user_id
         WHERE a.id=%s""", (appointment_id,))
    if row is None:
        raise LookupError("任命记录不存在")
    if row["revoked_at"] is not None:
        raise ValueError("该任命已撤销")
    execute(conn, """UPDATE das_appointment SET revoked_at=now(), revoked_by=%s, revoke_reason=%s
                      WHERE id=%s""", (actor["user_id"], reason, appointment_id))
    audit.write(conn, action="DAS_APPOINT_REVOKE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_APPOINTMENT",
                object_id=str(appointment_id), object_code=row["username"],
                new_value={"position": row["position_code"]}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
