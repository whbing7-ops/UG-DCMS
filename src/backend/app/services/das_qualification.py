"""资格评估（UG-DAF-06）与登记册月度核对（M1 的最后两块）。

判据 E1: 授权生效的前提不止"未过期"。UG-DAP-03 第 6 章要求同时满足资格评估、
培训有效、独立性核对通过、实际生效日期和适用的认可依据。培训那一半在 das_training,
这里补资格评估这一半——两个闸门一起挡在 signers.grant() 前面。

三处刻意的设计:

  · **三签用工号判, 不用账号判。**判据 I3 要的是三个不同**自然人**。账号能重开,
    人不能。0031 给 employee_no 加了唯一索引, 这里才判得出来; 没填工号的账号
    不能出现在评估表的签署栏里。

  · **表单的"授权文件类型"与系统的 level 不是一回事, 不假装能对上。**
    表单勾的是 4 类适航签署事项(更改分类/符合性声明/小改批准/CVE 核查),
    signer_authorization.level 是 REVIEW/APPROVE/CVE 三级文档签署。
    二者的映射是待澄清项第 1 条, 要等 M3／M4。所以闸门只校验"有无同意授权的评估",
    记录勾选但不据此自动放行某个 level。

  · **月度核对的差异比对是人做的。**要核对的是系统登记册与《授权人员名单》
    (受控 Word 文档), 系统拿不到那份文档的内容。系统给出自己这一侧的快照、
    记录核对结果与纠正、跟踪哪个月漏做。做成"系统自动核对通过"会是假的。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

SIGN_TYPES = ("更改分类", "符合性声明", "小改批准", "CVE核查")


# ---------------------------------------------------------------- 资格评估

def current(conn: psycopg.Connection, user_id: str | None = None) -> list[dict]:
    if user_id:
        return fetch_all(conn, "SELECT * FROM das_qualification_current WHERE user_id=%s",
                         (user_id,))
    return fetch_all(conn, "SELECT * FROM das_qualification_current ORDER BY full_name")


def history(conn: psycopg.Connection, user_id: str) -> list[dict]:
    return fetch_all(conn, """
        SELECT q.*, p.full_name AS prepared_by_name, r.full_name AS reviewed_by_name,
               a.full_name AS approved_by_name
          FROM das_qualification_assessment q
          JOIN app_user p ON p.id = q.prepared_by
          JOIN app_user r ON r.id = q.reviewed_by
          JOIN app_user a ON a.id = q.approved_by
         WHERE q.user_id=%s ORDER BY q.created_at DESC, q.id DESC""", (user_id,))


def qualification_gate(conn: psycopg.Connection, user_id: str) -> str | None:
    """授权前的资格评估闸门。不满足返回拒绝理由。

    UG-DAP-03 步骤 3～5: 资格评估与独立性核对通过后, 才由责任经理签发授权。
    """
    row = fetch_one(conn, "SELECT * FROM das_qualification_current WHERE user_id=%s", (user_id,))
    if row is None:
        return ("尚无资格评估记录, 不得授权。依据 UG-DAP-03 第 6 章步骤 3～5: "
                "须先依据 AP-21-18 附录 C 完成资格评估并填写 UG-DAF-06, "
                "经独立性核对后方可由责任经理签发授权。")
    if row["conclusion"] != "AGREE":
        return "最近一次资格评估的结论为「不同意授权」, 不得授权（UG-DAF-06）。"
    if not row["in_force"]:
        return (f'资格评估已过有效期（{row["valid_from"]} ~ {row["valid_to"]}）, '
                "须重新评估后方可授权（UG-DAF-06）。")
    return None


def assess(conn: psycopg.Connection, *, user_id: str, sign_types: list[str],
           education_experience: str, training_evidence: str, prior_work: str | None,
           indep_no_self_check: bool, indep_no_ism_conflict: bool,
           conclusion: str, intended_positions: str | None, intended_scope: str | None,
           scope_conditions: str | None, valid_from: dt.date | None, valid_to: dt.date | None,
           backup_user_id: str | None, form_ref: str | None,
           reviewed_by: str, approved_by: str, actor: dict) -> dict:
    """填写 UG-DAF-06。三签的互斥由数据库触发器判（用工号，见模块说明）。"""
    if conclusion not in ("AGREE", "REJECT"):
        raise ValueError("评估结论须为 AGREE 同意授权 或 REJECT 不同意")
    bad = [t for t in sign_types if t not in SIGN_TYPES]
    if bad:
        raise ValueError("授权文件类型须取自：" + "、".join(SIGN_TYPES) + f"；不认识：{bad}")
    if conclusion == "AGREE" and not sign_types:
        raise ValueError("同意授权的, 须勾选至少一项授权文件类型")
    for name, val in (("学历与专业经验", education_experience),
                      ("CCAR-21 及 DAS 培训", training_evidence)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空：AP-21-18 附录 C 要求逐项核对并留证")
    if conclusion == "AGREE" and not (indep_no_self_check and indep_no_ism_conflict):
        raise ValueError(
            "同意授权的, 独立性核对两项必须都已确认。不勾就同意等于跳过 UG-DAP-03 步骤 4。")
    if scalar(conn, "SELECT count(*) FROM app_user WHERE id=%s AND is_active", (user_id,)) == 0:
        raise ValueError("被评估人账户不存在或已停用")

    row = fetch_one(conn, """
        INSERT INTO das_qualification_assessment
               (user_id, form_ref, intended_positions, intended_scope, sign_types,
                education_experience, training_evidence, prior_work,
                indep_no_self_check, indep_no_ism_conflict, valid_from, valid_to,
                backup_user_id, conclusion, scope_conditions,
                prepared_by, reviewed_by, approved_by, reviewed_at, approved_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
        RETURNING id, user_id, conclusion, sign_types, valid_from, valid_to
    """, (user_id, form_ref, intended_positions, intended_scope, sign_types,
          education_experience, training_evidence, prior_work,
          indep_no_self_check, indep_no_ism_conflict, valid_from, valid_to,
          backup_user_id, conclusion, scope_conditions,
          actor["user_id"], reviewed_by, approved_by))
    audit.write(conn, action="DAS_QUALIFICATION", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_QUALIFICATION_ASSESSMENT",
                object_id=str(row["id"]), object_code=form_ref,
                new_value={"conclusion": conclusion, "sign_types": sign_types},
                reason=form_ref, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


# ---------------------------------------------------------------- 月度核对

def reconciliation_due(conn: psycopg.Connection) -> list[dict]:
    """哪些月份做了核对、哪些漏了（UG-DAW-005 第 4 章、判据 N13）。"""
    return fetch_all(conn, "SELECT * FROM das_reconciliation_due")


def system_snapshot(conn: psycopg.Connection) -> dict:
    """系统这一侧的快照，供人拿去与《授权人员名单》比对。

    只给条数和明细, 不判"是否一致"——另一侧是受控 Word 文档, 系统读不到,
    判一致与否是人的工作。
    """
    rows = fetch_all(conn, """
        SELECT u.full_name, u.username, u.employee_no, sa.level, sa.discipline_code,
               sa.file_type_code, sa.valid_from, sa.valid_to,
               b.full_name AS backup_full_name
          FROM signer_authorization sa
          JOIN app_user u ON u.id = sa.user_id
          LEFT JOIN app_user b ON b.id = sa.backup_user_id
         WHERE sa.revoked_at IS NULL AND sa.valid_from <= current_date
           AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)
         ORDER BY u.full_name, sa.level""")
    return {"taken_at": dt.datetime.now().isoformat(timespec="seconds"),
            "count": len(rows), "rows": rows}


def reconcile(conn: psycopg.Connection, *, period_month: dt.date | None, roster_ref: str,
              differences: int, difference_note: str | None, corrections: str | None,
              actor: dict) -> dict:
    if not (roster_ref or "").strip():
        raise ValueError("须写明比对的《授权人员名单》版次：不写版次就无从判断核对的是哪一版")
    if differences < 0:
        raise ValueError("差异数不得为负")
    if differences and not ((difference_note or "").strip() and (corrections or "").strip()):
        raise ValueError(
            "有差异的, 须写明差异内容与纠正动作。"
            "UG-DAW-005 第 4 章: 不一致的即时纠正并记录——只记一个数字等于没核对。")
    month = (period_month or dt.date.today()).replace(day=1)
    if scalar(conn, "SELECT count(*) FROM das_register_reconciliation WHERE period_month=%s",
              (month,)):
        raise ValueError(f"{month:%Y 年 %m 月}已有核对记录")
    cnt = scalar(conn, """
        SELECT count(*) FROM signer_authorization
         WHERE revoked_at IS NULL AND valid_from <= current_date
           AND (valid_to IS NULL OR valid_to >= current_date)""")
    row = fetch_one(conn, """
        INSERT INTO das_register_reconciliation
               (period_month, reconciled_by, system_count, roster_ref,
                differences, difference_note, corrections)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, period_month, system_count, differences
    """, (month, actor["user_id"], cnt, roster_ref, differences, difference_note, corrections))
    audit.write(conn, action="DAS_REGISTER_RECONCILE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_REGISTER_RECONCILIATION",
                object_id=str(row["id"]), object_code=roster_ref,
                new_value={"period": str(month), "system_count": cnt, "differences": differences},
                reason=difference_note, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


def review_reconciliation(conn: psycopg.Connection, *, recon_id: int, actor: dict) -> None:
    """判据 N12：登记操作由第二人复核。复核人不得是核对人本人（数据库约束）。"""
    n = execute(conn, """UPDATE das_register_reconciliation SET reviewed_by=%s
                          WHERE id=%s AND reviewed_by IS NULL""", (actor["user_id"], recon_id))
    if not n:
        raise ValueError("核对记录不存在或已复核")
