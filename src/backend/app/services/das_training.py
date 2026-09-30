"""培训与考核（M1 的第二块）。

UG-DAW-006 不只是一张记录表, 它规定了三条有系统后果的规则:

  1. 初始培训: 新入职、转岗、**首次授权前**必须完成并考核合格;
  2. 考核不合格的补考一次, **补考仍不合格的不得授权**;
  3. 已授权人员**复训考核不合格的, 暂停其签署权**直至重新合格。

第 1、2 条做成授权路径上的闸门(training_gate, 由 signers.grant 调用);
第 3 条做成清单而不是自动撤销——暂停签署权是管理动作, 须有人决定并留痕。
系统把该暂停的人列出来, 不替人做这个决定。

课程、学时、合格标准和课程与岗位的对应关系是**第三级作业文件的参数**(判据 P1),
由归口负责人按 UG-DAP-02 分类后在系统内维护, 不写死在代码里。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

KINDS = {"INITIAL": "初始培训", "RECURRENT": "复训",
         "SPECIAL": "专项培训", "MENTORING": "在岗带教"}
STATUS_CN = {"OK": "合格且在有效期内", "MISSING": "从未参加",
             "FAILED": "最近一次考核不合格", "EXPIRED": "已过复训期"}


def courses(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """
        SELECT c.*, (SELECT count(*) FROM das_course_requirement r
                      WHERE r.course_code = c.code AND r.level = 'REQUIRED') AS required_for
          FROM das_training_course c ORDER BY c.sort_order""")


def requirements(conn: psycopg.Connection, position_code: str | None = None) -> list[dict]:
    if position_code:
        return fetch_all(conn, """
            SELECT r.*, c.name_cn AS course_name, c.category, c.hours, c.valid_months
              FROM das_course_requirement r JOIN das_training_course c ON c.code = r.course_code
             WHERE r.position_code = %s ORDER BY r.level, c.sort_order""", (position_code,))
    return fetch_all(conn, """
        SELECT r.*, c.name_cn AS course_name, p.name_cn AS position_name
          FROM das_course_requirement r
          JOIN das_training_course c ON c.code = r.course_code
          JOIN das_position p ON p.code = r.position_code
         ORDER BY p.sort_order, c.sort_order""")


def status(conn: psycopg.Connection, user_id: str | None = None) -> list[dict]:
    """在任人员的必修课达标情况。MISSING / FAILED / EXPIRED 任一存在即不满足授权前提。"""
    if user_id:
        return fetch_all(conn, "SELECT * FROM das_training_status WHERE user_id=%s "
                               "ORDER BY status, course_code", (user_id,))
    return fetch_all(conn, "SELECT * FROM das_training_status ORDER BY full_name, status, course_code")


def suspend_due(conn: psycopg.Connection) -> list[dict]:
    """UG-DAW-006 第 3 章: 复训不合格或已过期、却仍持有有效签署授权的人。

    只列出不自动撤销。撤销签署权会触发已签文件复核(判据 A4-2), 后果由责任经理承担,
    这个决定不该由一条定时任务替人做出。
    """
    return fetch_all(conn, "SELECT * FROM das_training_suspend_due ORDER BY full_name")


def training_gate(conn: psycopg.Connection, user_id: str) -> str | None:
    """授权前的培训闸门。不满足返回拒绝理由, 满足返回 None。

    UG-DAW-006 第 1、3 章: 首次授权前必须完成初始培训并考核合格;
    补考仍不合格的不得授权。

    注意: 无在任岗位的人这里会**通过**——因为"必修课"是按岗位定的, 没有岗位就没有
    必修课。这不是漏洞, 是边界: 该不该给一个没有任何任命的人签署权, 是另一条规则,
    由 UG-DAP-03 步骤 2～5 的提名与资格评估把关, 不在本闸门内。
    """
    bad = fetch_all(conn, """
        SELECT course_code, course_name, status, position_name
          FROM das_training_status
         WHERE user_id = %s AND status <> 'OK'
         ORDER BY status, course_code""", (user_id,))
    if not bad:
        return None
    detail = "；".join(
        f'{b["course_name"]}（{STATUS_CN.get(b["status"], b["status"])}）' for b in bad[:4])
    more = f"，另有 {len(bad) - 4} 门" if len(bad) > 4 else ""
    return (f"培训未达标, 不得授权: {detail}{more}。"
            "依据 UG-DAW-006 第 1、3 章: 首次授权前须完成初始培训并考核合格, "
            "补考仍不合格的不得授权。")


def record(conn: psycopg.Connection, *, user_id: str, course_code: str, kind: str,
           trained_on: dt.date | None, hours: float | None, result: str,
           is_retake: bool, certificate_ref: str | None, actor: dict) -> dict:
    if kind not in KINDS:
        raise ValueError("培训类别须为 初始培训／复训／专项培训／在岗带教")
    if result not in ("PASS", "FAIL"):
        raise ValueError("考核结果须为 PASS 合格 或 FAIL 不合格")
    if scalar(conn, "SELECT count(*) FROM das_training_course WHERE code=%s AND is_active",
              (course_code,)) == 0:
        raise ValueError("课程不存在或已停用")
    if scalar(conn, "SELECT count(*) FROM app_user WHERE id=%s AND is_active", (user_id,)) == 0:
        raise ValueError("账户不存在或已停用")
    day = trained_on or dt.date.today()
    if day > dt.date.today():
        raise ValueError("培训日期不得晚于今天: 记录的是已发生的事")

    # 补考只能在有过不合格之后。没考过就标补考, 说明记录填错了。
    if is_retake:
        prior = scalar(conn, """
            SELECT count(*) FROM das_training_record
             WHERE user_id=%s AND course_code=%s AND kind=%s AND result='FAIL'""",
                       (user_id, course_code, kind))
        if not prior:
            raise ValueError("该课程此前没有不合格记录, 不应标为补考")

    row = fetch_one(conn, """
        INSERT INTO das_training_record
               (user_id, course_code, kind, trained_on, hours, result, is_retake,
                certificate_ref, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, user_id, course_code, kind, trained_on, result, is_retake
    """, (user_id, course_code, kind, day, hours, result, is_retake, certificate_ref,
          actor["user_id"]))
    audit.write(conn, action="DAS_TRAINING_RECORD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_TRAINING_RECORD",
                object_id=str(row["id"]), object_code=course_code,
                new_value={"kind": kind, "result": result, "is_retake": is_retake,
                           "trained_on": str(day)},
                reason=None, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def evaluate(conn: psycopg.Connection, *, record_id: int, effectiveness: str, actor: dict) -> None:
    """补填有效性评估结论（UG-DAW-006 第 4 章：上岗后 3 个月内的工作质量抽查等）。

    这是培训记录上唯一允许事后补填的字段——考核结果是事实, 有效性是后来才看得出的判断。
    """
    if not (effectiveness or "").strip():
        raise ValueError("有效性评估结论不得为空")
    n = execute(conn, """UPDATE das_training_record SET effectiveness=%s, evaluated_by=%s
                          WHERE id=%s""", (effectiveness, actor["user_id"], record_id))
    if not n:
        raise ValueError("培训记录不存在")
