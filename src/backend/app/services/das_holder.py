"""M8 证后与对外发布（UG-DAP-11，17 步）。

依据 UG-DAP-11 第 6 章（17 步）、第 7 章（关键控制点）、CCAR-21.99／21.116／21.119，
设计输入第 8.3 节（一）。

【挂在设计批准上，不挂在项目上】
设计输入第 8.3 节：证后活动挂在**证件**上——一个产品可能有多个 STC；ICA 修订、服务通告、
适航指令建议、21.99 信息、权益转让（转的是证件）、撤销召回，都按证件走。项目有结束时间，
证件长期有效。

【按 2026-10-02 的决定选闸门：第 7 章那五条"不得"】
① 已发布的批准和资料**只能由原批准人或其上级撤销**，不得由编制人或使用部门自行作废；
② **止用通知未取得全部接收确认前，不得认为撤销已完成**；
③ **撤销期间，相关资料不得继续用于改装、生产或放行**；
④ 局方批准的资料标明实际批准依据，**不得加注虚假的本单位批准声明**；
⑤ 第 13 步 b)：偏离已批准设计类的询问**按 UG-DAP-06 走设计更改，不得以答复代替更改**，
   且**对外答复不得超出已批准的设计数据范围**。

其余 12 步做成记录，实质过程走线下并挂证据（0047）。

【"其上级"在系统里怎么判】
系统里没有组织层级表，而硬造一个会变成又一个与事实不符的常量（判据 A7：不得写死人数）。
所以判为：原批准人本人，或在任责任经理／在任适航管理负责人。这是个解释，写在拒绝理由里。

【第 16 步那个 30 天是法规时限】
原文标了"（法规）"，所以它在时限引擎里（`L11.STC_TRANSFER_NOTICE`），受规章修订闸门
保护，内部决定改不动它——不是本模块的一个字段。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

TRIGGERS = {
    "CAAC_FOUND_WRONG": "a) 局方认定本单位作出的分类或批准结论有误",
    "DOC_ERROR": "b) 已发布资料存在错误、遗漏或与经批准的设计不一致",
    "UNSAFE_FEATURE": "c) 已批准的设计存在不安全特征",
    "AUTHORISATION_REVOKED": "d) 授权人员的授权被撤销后，复核发现其签署的文件不成立",
}
CATEGORIES = {"USAGE": "a) 使用和安装方法",
              "DEVIATION": "b) 偏离已批准设计（须走设计更改）",
              "DEFECT": "c) 反映故障、失效或缺陷（转 UG-DAP-12）"}
RECIPIENT_KINDS = {"OPERATOR": "运营人", "MODIFIER": "改装单位",
                   "PRODUCER": "生产单位", "SUPPLIER": "供应商", "OTHER": "其他"}
POSITION_CN = {"AWM": "适航管理负责人", "DCM": "设计资料管理负责人",
               "AM": "责任经理", "PM": "项目负责人", "DE": "设计工程师"}


def _require(conn: psycopg.Connection, actor: dict, codes, what: str,
             basis: str) -> None:
    if isinstance(codes, str):
        codes = (codes,)
    if not scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code = ANY(%s)""",
                  (actor["user_id"], list(codes))):
        who = "或".join(POSITION_CN[c] for c in codes)
        raise PermissionError(f"{what}须由在任{who}执行（{basis}）。"
                              f"请先按 UG-DAP-03 完成任命。")


def _approval(conn: psycopg.Connection, certificate_no: str) -> dict:
    row = fetch_one(conn, """SELECT id, certificate_no, approval_kind
                               FROM das_design_approval WHERE certificate_no=%s""",
                    (certificate_no,))
    if not row:
        raise LookupError(f"没有编号为 {certificate_no} 的设计批准")
    return row


def _doc(conn: psycopg.Connection, doc_no: str, revision: str) -> dict:
    row = fetch_one(conn, """SELECT id, doc_no, revision, approved_by, revoked_at
                               FROM das_published_doc
                              WHERE doc_no=%s AND revision=%s""", (doc_no, revision))
    if not row:
        raise LookupError(f"没有 {doc_no} {revision} 这份发布资料")
    return row


def docs(conn: psycopg.Connection) -> list[dict]:
    """发布资料及其分发确认情况（第 7 章：发布对象名单和接收确认必须可追溯）。"""
    return fetch_all(conn, "SELECT * FROM das_doc_distribution_status")


def publish(conn: psycopg.Connection, *, certificate_no: str, doc_kind: str,
            doc_no: str, revision: str, title: str, approval_basis: str,
            caac_approval_ref: str | None = None, statement_text: str | None = None,
            approved_on: dt.date | None = None, released_on: dt.date | None = None,
            actor: dict) -> dict:
    """登记一份对外发布的资料（第 2～4 步）。

    局方批准的资料要标明实际批准依据, **不得加注本单位批准声明**（第 7 章）——
    加了就是把局方的批准说成了本单位在权利范围内的批准。
    """
    _require(conn, actor, "AWM", "批准对外发布的资料",
             "UG-DAP-11 第 3 步：适航管理负责人确认法规及审定计划要求")
    a = _approval(conn, certificate_no)
    if approval_basis not in ("DOA_SCOPE", "CAAC_APPROVED"):
        raise ValueError("批准依据须为 DOA_SCOPE（本单位在权利范围内批准）"
                         "或 CAAC_APPROVED（局方批准）")
    if not scalar(conn, "SELECT count(*) FROM das_release_doc_kind WHERE code=%s",
                  (doc_kind,)):
        raise LookupError(f"没有 {doc_kind} 这个资料种类")
    for name, val in (("资料编号", doc_no), ("版次", revision), ("标题", title)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    if approval_basis == "CAAC_APPROVED":
        if not (caac_approval_ref or "").strip():
            raise ValueError(
                "局方批准的资料须标明实际批准依据（第 7 章）——没有依据的「局方批准」"
                "说不出是哪一份批准。")
        if (statement_text or "").strip():
            raise ValueError(
                "局方批准的资料**不得加注本单位批准声明**（第 7 章：不得加注虚假的"
                "本单位批准声明）。加了就是把局方的批准说成了本单位在权利范围内的批准，"
                "而那两者的责任主体不同。")
    elif (caac_approval_ref or "").strip():
        raise ValueError("本单位在权利范围内批准的资料不记局方批准依据，二者取一")
    row = fetch_one(conn, """
        INSERT INTO das_published_doc
               (approval_id, doc_kind, doc_no, revision, title, approval_basis,
                caac_approval_ref, statement_text, approved_by, approved_on, released_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, doc_no, revision, approval_basis
    """, (a["id"], doc_kind, doc_no.strip(), revision.strip(), title.strip(),
          approval_basis, (caac_approval_ref or "").strip() or None,
          (statement_text or "").strip() or None, actor["user_id"],
          approved_on or dt.date.today(), released_on))
    audit.write(conn, action="DAS_DOC_PUBLISH", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_PUBLISHED_DOC",
                object_id=str(row["id"]), object_code=f"{doc_no} {revision}",
                new_value={"certificate_no": certificate_no, "kind": doc_kind,
                           "basis": approval_basis},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def distribute(conn: psycopg.Connection, *, doc_no: str, revision: str,
               recipient: str, recipient_kind: str, sent_on: dt.date | None = None,
               actor: dict) -> dict:
    """登记一个分发对象（第 4 步）。撤销期间的资料不得再分发（DCMS-INV-123）。"""
    _require(conn, actor, "DCM", "分发对外资料",
             "UG-DAP-11 第 4 步：资料管理负责人")
    d = _doc(conn, doc_no, revision)
    if recipient_kind not in RECIPIENT_KINDS:
        raise ValueError(f"接收方类别须为 {'/'.join(RECIPIENT_KINDS)} 之一")
    if not (recipient or "").strip():
        raise ValueError("接收方不得为空（第 7 章：发布对象名单必须可追溯）")
    if d["revoked_at"]:
        raise ValueError(f"{doc_no} {revision} 已撤销，不得继续分发（第 7 章）")
    if scalar(conn, """SELECT count(*) FROM das_doc_revocation
                        WHERE doc_id=%s AND closed_on IS NULL""", (d["id"],)):
        raise ValueError(
            f"{doc_no} {revision} 正在撤销流程中，**撤销期间相关资料不得继续用于改装、"
            f"生产或放行**（第 7 章），也不得再分发。")
    return fetch_one(conn, """
        INSERT INTO das_doc_distribution (doc_id, recipient, recipient_kind, sent_on)
        VALUES (%s,%s,%s,%s) RETURNING id, recipient, sent_on
    """, (d["id"], recipient.strip(), recipient_kind, sent_on or dt.date.today()))


def acknowledge(conn: psycopg.Connection, *, distribution_id: int, ack_ref: str,
                acknowledged_on: dt.date | None = None, actor: dict) -> dict:
    """登记接收确认（第 7 章：接收确认必须可追溯）。"""
    _require(conn, actor, "DCM", "登记接收确认", "UG-DAP-11 第 4 步")
    if not (ack_ref or "").strip():
        raise ValueError("接收确认的依据不得为空：没有依据的「已确认」查不到证据")
    r = fetch_one(conn, """SELECT id, acknowledged_on FROM das_doc_distribution
                            WHERE id=%s""", (distribution_id,))
    if not r:
        raise LookupError(f"没有编号为 {distribution_id} 的分发记录")
    if r["acknowledged_on"]:
        raise ValueError("该分发记录已确认")
    execute(conn, """UPDATE das_doc_distribution
                        SET acknowledged_on=%s, ack_ref=%s WHERE id=%s""",
            (acknowledged_on or dt.date.today(), ack_ref.strip(), distribution_id))
    return fetch_one(conn, """SELECT id, recipient, acknowledged_on, ack_ref
                                FROM das_doc_distribution WHERE id=%s""",
                     (distribution_id,))


def start_revocation(conn: psycopg.Connection, *, doc_no: str, revision: str,
                     trigger_kind: str, trigger_note: str,
                     initiated_on: dt.date | None = None, actor: dict) -> dict:
    """启动撤销与召回（第 7 步）。

    四种触发情形任一出现, **立即**启动。启动之后这份资料就进入"撤销期间",
    不得继续用于改装、生产或放行（DCMS-INV-123）。
    """
    _require(conn, actor, "AWM", "启动撤销与召回",
             "UG-DAP-11 第 7 步：适航管理负责人")
    if trigger_kind not in TRIGGERS:
        raise ValueError(f"触发情形须为 {'/'.join(TRIGGERS)} 之一")
    d = _doc(conn, doc_no, revision)
    if not (trigger_note or "").strip():
        raise ValueError("触发情形的说明不得为空")
    if scalar(conn, """SELECT count(*) FROM das_doc_revocation
                        WHERE doc_id=%s AND closed_on IS NULL""", (d["id"],)):
        raise ValueError(f"{doc_no} {revision} 已有未结案的撤销流程")
    row = fetch_one(conn, """
        INSERT INTO das_doc_revocation
               (doc_id, trigger_kind, trigger_note, initiated_by, initiated_on)
        VALUES (%s,%s,%s,%s,%s)
        RETURNING id, trigger_kind, initiated_on
    """, (d["id"], trigger_kind, trigger_note.strip(), actor["user_id"],
          initiated_on or dt.date.today()))
    # 止用通知的收件人取自分发清单（第 8 步: 向**所有已接收该资料的对象**发出）。
    execute(conn, """
        INSERT INTO das_stop_use_ack (revocation_id, distribution_id, sent_on)
        SELECT %s, dd.id, current_date FROM das_doc_distribution dd
         WHERE dd.doc_id = %s
    """, (row["id"], d["id"]))
    row["stop_use_recipients"] = scalar(
        conn, "SELECT count(*) FROM das_stop_use_ack WHERE revocation_id=%s",
        (row["id"],))
    audit.write(conn, action="DAS_REVOCATION_START", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DOC_REVOCATION",
                object_id=str(row["id"]), object_code=f"{doc_no} {revision}",
                new_value={"trigger": trigger_kind, "note": trigger_note.strip(),
                           "recipients": row["stop_use_recipients"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def send_stop_use(conn: psycopg.Connection, *, revocation_id: int, scope: str,
                  interim_measures: str, sent_on: dt.date | None = None,
                  actor: dict) -> dict:
    """发出立即止用通知（第 8 步）。

    **在完成评估前**先发 —— 它是这条流程的第一步, 不是最后一步。
    """
    _require(conn, actor, "DCM", "发出止用通知",
             "UG-DAP-11 第 8 步：资料管理负责人")
    r = fetch_one(conn, """SELECT id, stop_use_sent_on FROM das_doc_revocation
                            WHERE id=%s""", (revocation_id,))
    if not r:
        raise LookupError(f"没有编号为 {revocation_id} 的撤销记录")
    if r["stop_use_sent_on"]:
        raise ValueError("止用通知已发出")
    for name, val in (("暂停使用的范围", scope), ("临时措施", interim_measures)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（第 8 步：说明受影响的资料编号和版次、"
                             f"暂停使用的范围、临时措施）")
    execute(conn, """UPDATE das_doc_revocation
                        SET stop_use_sent_on=%s, stop_use_scope=%s, interim_measures=%s
                      WHERE id=%s""",
            (sent_on or dt.date.today(), scope.strip(), interim_measures.strip(),
             revocation_id))
    return fetch_one(conn, "SELECT * FROM das_revocation_pending_ack WHERE id=%s",
                     (revocation_id,))


def ack_stop_use(conn: psycopg.Connection, *, ack_id: int, ack_ref: str,
                 acknowledged_on: dt.date | None = None, actor: dict) -> dict:
    """登记止用通知的接收确认（第 8 步：须取得接收确认，未确认的逐一跟催）。"""
    _require(conn, actor, "DCM", "登记止用通知的接收确认", "UG-DAP-11 第 8 步")
    if not (ack_ref or "").strip():
        raise ValueError("接收确认的依据不得为空")
    a = fetch_one(conn, """SELECT id, acknowledged_on FROM das_stop_use_ack
                            WHERE id=%s""", (ack_id,))
    if not a:
        raise LookupError(f"没有编号为 {ack_id} 的止用通知记录")
    if a["acknowledged_on"]:
        raise ValueError("该止用通知已确认")
    execute(conn, """UPDATE das_stop_use_ack SET acknowledged_on=%s, ack_ref=%s
                      WHERE id=%s""",
            (acknowledged_on or dt.date.today(), ack_ref.strip(), ack_id))
    return fetch_one(conn, """SELECT id, acknowledged_on, ack_ref FROM das_stop_use_ack
                               WHERE id=%s""", (ack_id,))


def chase_stop_use(conn: psycopg.Connection, *, ack_id: int, actor: dict) -> dict:
    """跟催未确认的止用通知（第 8 步：未确认的逐一跟催）。"""
    _require(conn, actor, "DCM", "跟催止用通知", "UG-DAP-11 第 8 步")
    a = fetch_one(conn, """SELECT id, acknowledged_on, chase_count
                             FROM das_stop_use_ack WHERE id=%s""", (ack_id,))
    if not a:
        raise LookupError(f"没有编号为 {ack_id} 的止用通知记录")
    if a["acknowledged_on"]:
        raise ValueError("该止用通知已确认，无需跟催")
    execute(conn, """UPDATE das_stop_use_ack
                        SET chase_count = chase_count + 1, last_chased_on = current_date
                      WHERE id=%s""", (ack_id,))
    return fetch_one(conn, """SELECT id, chase_count, last_chased_on
                                FROM das_stop_use_ack WHERE id=%s""", (ack_id,))


def assess_revocation_impact(conn: psycopg.Connection, *, revocation_id: int,
                             affected_scope: str, retrofit_done_note: str,
                             unsafe_condition: bool,
                             occurrence_reported_ref: str | None = None,
                             assessed_on: dt.date | None = None,
                             actor: dict) -> dict:
    """影响评估（第 9 步）。

    构成 CCAR-21.5 报告情形的按 UG-DAP-12 报局方 —— 所以判为不安全状态时
    要给出事件报告编号, 否则这条链就断在这里。
    """
    _require(conn, actor, "AWM", "撤销的影响评估", "UG-DAP-11 第 9 步")
    r = fetch_one(conn, """SELECT id, impact_assessed_on FROM das_doc_revocation
                            WHERE id=%s""", (revocation_id,))
    if not r:
        raise LookupError(f"没有编号为 {revocation_id} 的撤销记录")
    if r["impact_assessed_on"]:
        raise ValueError("影响评估已登记")
    for name, val in (("受影响范围", affected_scope),
                      ("已实施改装的情况", retrofit_done_note)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（第 9 步：评估受影响的产品、"
                             f"架次／序列号范围、已实施改装的情况）")
    if unsafe_condition and not (occurrence_reported_ref or "").strip():
        raise ValueError(
            "判为构成不安全状态时，须按 UG-DAP-12 报局方并记明事件报告编号"
            "（第 9 步：构成 CCAR-21.5 报告情形的按 UG-DAP-12 报局方）。"
            "判了不安全却不报，这条链就断在这里。")
    execute(conn, """UPDATE das_doc_revocation
                        SET impact_assessed_on=%s, affected_scope=%s,
                            retrofit_done_note=%s, unsafe_condition=%s,
                            occurrence_reported_ref=%s
                      WHERE id=%s""",
            (assessed_on or dt.date.today(), affected_scope.strip(),
             retrofit_done_note.strip(), unsafe_condition,
             (occurrence_reported_ref or "").strip() or None, revocation_id))
    return fetch_one(conn, "SELECT * FROM das_revocation_progress WHERE id=%s",
                     (revocation_id,))


def decide_revocation(conn: psycopg.Connection, *, revocation_id: int,
                      revoked_approval_ref: str, revocation_reason: str,
                      corrected_doc_no: str | None = None,
                      corrected_revision: str | None = None,
                      decided_on: dt.date | None = None, actor: dict) -> dict:
    """签署撤销决定（第 10 步）。

    **只能由原批准人或其上级**（第 7 章）。"其上级"在系统里判为在任责任经理或
    在任适航管理负责人 —— 系统里没有组织层级表, 硬造一个会变成又一个与事实不符的常量。
    """
    r = fetch_one(conn, """SELECT r.id, r.decided_on, r.doc_id, d.approved_by,
                                  d.doc_no, d.revision
                             FROM das_doc_revocation r
                             JOIN das_published_doc d ON d.id = r.doc_id
                            WHERE r.id=%s""", (revocation_id,))
    if not r:
        raise LookupError(f"没有编号为 {revocation_id} 的撤销记录")
    if r["decided_on"]:
        raise ValueError("撤销决定已签署")
    is_approver = str(r["approved_by"]) == str(actor["user_id"])
    is_senior = scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                                 WHERE user_id=%s AND position_code IN ('AM','AWM')""",
                       (actor["user_id"],)) > 0
    if not is_approver and not is_senior:
        raise PermissionError(
            f"资料 {r['doc_no']} {r['revision']} 的撤销只能由**原批准人或其上级**签署"
            f"（第 7 章：不得由编制人或使用部门自行作废）。"
            f"「其上级」在系统里判为在任责任经理或在任适航管理负责人——"
            f"系统里没有组织层级表，硬造一个会变成又一个与事实不符的常量"
            f"（判据 A7：不得写死人数与层级）。")
    for name, val in (("被撤销的批准编号与版次", revoked_approval_ref),
                      ("撤销理由", revocation_reason)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（第 10 步：注明被撤销的批准编号、版次和理由）")
    cid = None
    if corrected_doc_no:
        c = _doc(conn, corrected_doc_no, corrected_revision or "")
        cid = c["id"]
    execute(conn, """UPDATE das_doc_revocation
                        SET decided_by=%s, decided_on=%s, revoked_approval_ref=%s,
                            revocation_reason=%s, corrected_doc_id=%s
                      WHERE id=%s""",
            (actor["user_id"], decided_on or dt.date.today(),
             revoked_approval_ref.strip(), revocation_reason.strip(), cid,
             revocation_id))
    # 资料标注"已撤销"并回收（第 10 步）。
    execute(conn, """UPDATE das_published_doc
                        SET revoked_at=now(), revocation_id=%s WHERE id=%s""",
            (revocation_id, r["doc_id"]))
    audit.write(conn, action="DAS_REVOCATION_DECIDE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DOC_REVOCATION",
                object_id=str(revocation_id),
                object_code=f"{r['doc_no']} {r['revision']}",
                new_value={"as_original_approver": is_approver,
                           "revoked_ref": revoked_approval_ref.strip(),
                           "reason": revocation_reason.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_revocation_progress WHERE id=%s",
                     (revocation_id,))


def close_revocation(conn: psycopg.Connection, *, revocation_id: int,
                     caac_report_ref: str, similar_review_note: str,
                     caac_reported_on: dt.date | None = None,
                     closed_on: dt.date | None = None, actor: dict) -> dict:
    """报局方与闭环（第 11 步）。

    闸门在数据库里（DCMS-INV-122）: 止用通知未取得**全部**接收确认前不得结案。
    这里先把还差谁列出来 —— 没确认就结案, 等于那边还在用这份资料,
    而我们这边记着已经撤销了。
    """
    _require(conn, actor, "AWM", "撤销的报局方与闭环", "UG-DAP-11 第 11 步")
    r = fetch_one(conn, """SELECT id, closed_on, stop_use_sent_on, decided_on
                             FROM das_doc_revocation WHERE id=%s""", (revocation_id,))
    if not r:
        raise LookupError(f"没有编号为 {revocation_id} 的撤销记录")
    if r["closed_on"]:
        raise ValueError("该撤销流程已结案")
    for name, val in (("报局方的文件编号", caac_report_ref),
                      ("同类排查结果", similar_review_note)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（第 11 步：报局方；举一反三检查同类批准）")
    pending = fetch_all(conn, """
        SELECT dd.recipient FROM das_stop_use_ack a
          JOIN das_doc_distribution dd ON dd.id = a.distribution_id
         WHERE a.revocation_id=%s AND a.acknowledged_on IS NULL
    """, (revocation_id,))
    if pending:
        who = "、".join(p["recipient"] for p in pending)
        raise ValueError(
            f"止用通知还有接收方未确认，**不得认为撤销已完成**（第 7 章）：{who}。"
            f"未确认的要逐一跟催（第 8 步）——没确认就结案，等于那边还在用这份资料，"
            f"而我们这边记着已经撤销了。")
    execute(conn, """UPDATE das_doc_revocation
                        SET caac_reported_on=%s, caac_report_ref=%s,
                            similar_review_note=%s, closed_on=%s
                      WHERE id=%s""",
            (caac_reported_on or dt.date.today(), caac_report_ref.strip(),
             similar_review_note.strip(), closed_on or dt.date.today(), revocation_id))
    audit.write(conn, action="DAS_REVOCATION_CLOSE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DOC_REVOCATION",
                object_id=str(revocation_id), object_code=str(revocation_id),
                new_value={"caac_report_ref": caac_report_ref.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_revocation_progress WHERE id=%s",
                     (revocation_id,))


def inquiries(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_tech_inquiry_register")


def record_inquiry(conn: psycopg.Connection, *, inquiry_no: str, received_from: str,
                   question: str, category: str,
                   received_on: dt.date | None = None, actor: dict) -> dict:
    """登记技术询问（第 13 步）。分类决定了后续走哪条路，所以它不是备注。"""
    _require(conn, actor, ("AWM", "DE"), "登记技术询问",
             "UG-DAP-11 第 13 步：适航管理负责人、设计工程师")
    if category not in CATEGORIES:
        raise ValueError(f"询问类别须为 {'/'.join(CATEGORIES)} 之一")
    for name, val in (("询问编号", inquiry_no), ("来源", received_from),
                      ("询问内容", question)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    return fetch_one(conn, """
        INSERT INTO das_tech_inquiry
               (inquiry_no, received_from, received_on, question, category)
        VALUES (%s,%s,%s,%s,%s)
        RETURNING id, inquiry_no, category
    """, (inquiry_no.strip(), received_from.strip(), received_on or dt.date.today(),
          question.strip(), category))


def answer_inquiry(conn: psycopg.Connection, *, inquiry_no: str, answer: str,
                   within_approved_data: bool, based_on_doc_no: str | None = None,
                   based_on_revision: str | None = None,
                   change_no: str | None = None, occurrence_ref: str | None = None,
                   designation_ref: str | None = None,
                   answered_on: dt.date | None = None, actor: dict) -> dict:
    """答复技术询问（第 13 步）。

    三类各走各的路, 其中 b) 类**不得以答复代替更改**: 用一封答复函允许对方偏离
    已批准的设计, 等于绕过设计更改分类改了设计, 而那份设计仍然是局方批准的那一份。
    另: **对外答复不得超出已批准的设计数据范围** —— 系统判不了"超没超",
    但判得了"有没有声明核对过", 所以做成必须声明, 不做成默认通过。
    """
    i = fetch_one(conn, """SELECT id, category, answered_on FROM das_tech_inquiry
                            WHERE inquiry_no=%s""", (inquiry_no,))
    if not i:
        raise LookupError(f"没有编号为 {inquiry_no} 的技术询问")
    if i["answered_on"]:
        raise ValueError("该询问已答复，更正请另记一条")
    is_awm = scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                              WHERE user_id=%s AND position_code='AWM'""",
                    (actor["user_id"],)) > 0
    if not is_awm and not (designation_ref or "").strip():
        raise PermissionError(
            "答复由适航管理负责人**或其书面指定的人员**审核后发出（第 13 步）。"
            "不是在任适航管理负责人时，须写明书面指定的依据。")
    if not (answer or "").strip():
        raise ValueError("答复内容不得为空")
    if not within_approved_data:
        raise ValueError(
            "核对结论为「超出已批准的设计数据范围」时不得发出（第 13 步：对外答复不得"
            "超出已批准的设计数据范围）。超出的那部分要走设计更改。")
    doc_id = None
    if i["category"] == "USAGE":
        if not based_on_doc_no:
            raise ValueError(
                "使用和安装方法类的询问**依据已发布资料答复**（第 13 步 a），"
                "须指明依据哪一份。凭记忆答复，答的可能是上一版。")
        d = _doc(conn, based_on_doc_no, based_on_revision or "")
        doc_id = d["id"]
        # 撤销期间的资料不得引为答复依据（第 7 章）。数据库的 INV-123 在后面兜底,
        # 但那条路返回 409; 本端点其它参数错误都是 400 带一句说明, 不该两种状态码。
        if d["revoked_at"]:
            raise ValueError(
                f"{based_on_doc_no} {based_on_revision} 已撤销，不得引为答复依据"
                f"（第 7 章：撤销期间相关资料不得继续用于改装、生产或放行）。")
        in_rev = scalar(conn, """SELECT id FROM das_doc_revocation
                                  WHERE doc_id=%s AND closed_on IS NULL
                                  LIMIT 1""", (doc_id,))
        if in_rev:
            raise ValueError(
                f"{based_on_doc_no} {based_on_revision} 正在撤销流程中"
                f"（撤销记录 #{in_rev}，未结案），**撤销期间相关资料不得继续用于改装、"
                f"生产或放行**（第 7 章），也不得引为答复依据。")
    elif i["category"] == "DEVIATION":
        if not (change_no or "").strip():
            raise ValueError(
                "偏离已批准设计类的询问**按 UG-DAP-06 走设计更改，不得以答复代替更改**"
                "（第 13 步 b）。用一封答复函允许对方偏离已批准的设计，等于绕过设计更改"
                "分类改了设计，而那份设计仍然是局方批准的那一份。")
        st = scalar(conn, """SELECT cl.state FROM das_design_change d
                               LEFT JOIN das_change_classification cl
                                      ON cl.change_id = d.id
                              WHERE d.change_no=%s""", (change_no,))
        if st is None:
            raise LookupError(
                f"更改单 {change_no} 不存在或还没有分类结论。"
                f"偏离类询问要等更改走完 UG-DAP-06 再答复。")
    else:
        if not (occurrence_ref or "").strip():
            raise ValueError(
                "反映故障、失效或缺陷类的询问须转 UG-DAP-12 登记（第 13 步 c），"
                "并记明事件报告编号——那是 48 小时报告链的入口。")
    execute(conn, """UPDATE das_tech_inquiry
                        SET answer=%s, based_on_doc_id=%s, change_no=%s,
                            occurrence_ref=%s, reviewed_by=%s, designation_ref=%s,
                            answered_on=%s, within_approved_data=%s
                      WHERE id=%s""",
            (answer.strip(), doc_id, (change_no or "").strip() or None,
             (occurrence_ref or "").strip() or None, actor["user_id"],
             (designation_ref or "").strip() or None,
             answered_on or dt.date.today(), within_approved_data, i["id"]))
    audit.write(conn, action="DAS_INQUIRY_ANSWER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_TECH_INQUIRY",
                object_id=str(i["id"]), object_code=inquiry_no,
                new_value={"category": i["category"], "as_awm": is_awm,
                           "designation_ref": designation_ref},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_tech_inquiry_register WHERE id=%s",
                     (i["id"],))


def record_similarity(conn: psycopg.Connection, *, source_kind: str, source_ref: str,
                      source_summary: str, comparison_note: str, verdict: str,
                      rationale: str, airworthiness_impact: str | None = None,
                      occurrence_ref: str | None = None,
                      action_taken: str | None = None,
                      reviewed_on: dt.date | None = None, actor: dict) -> dict:
    """在役问题的相似性评估（第 15 步）。

    原文: 判定"相关／可能相关／不相关"并说明理由, **评估结论无论是否相关均须记录**。
    所以判"不相关"也要进台账 —— 不记的话, 过后说不清这条通报到底看过没有。
    """
    _require(conn, actor, "AWM", "相似性评估", "UG-DAP-11 第 15 步")
    if source_kind not in ("EXTERNAL_EVENT", "CAAC_BULLETIN"):
        raise ValueError("来源须为 EXTERNAL_EVENT（其他产品的重大事件）"
                         "或 CAAC_BULLETIN（局方通报）")
    if verdict not in ("RELATED", "POSSIBLY", "UNRELATED"):
        raise ValueError("判定须为 RELATED／POSSIBLY／UNRELATED 之一")
    for name, val in (("来源编号", source_ref), ("来源摘要", source_summary),
                      ("比较结论", comparison_note), ("判定理由", rationale)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（第 15 步：比较结构形式、材料、工艺、"
                             f"系统原理、使用环境和失效模式，并说明理由）")
    if verdict != "UNRELATED" and not (airworthiness_impact or "").strip():
        raise ValueError(
            "判定为相关或可能相关的，须评估对本单位产品适航性的影响（第 15 步）。"
            "判了相关却不评估影响，这条评估就停在了判定上。")
    row = fetch_one(conn, """
        INSERT INTO das_similarity_review
               (source_kind, source_ref, source_summary, comparison_note, verdict,
                rationale, airworthiness_impact, occurrence_ref, action_taken,
                reviewed_by, reviewed_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, source_ref, verdict
    """, (source_kind, source_ref.strip(), source_summary.strip(),
          comparison_note.strip(), verdict, rationale.strip(),
          (airworthiness_impact or "").strip() or None,
          (occurrence_ref or "").strip() or None,
          (action_taken or "").strip() or None, actor["user_id"],
          reviewed_on or dt.date.today()))
    return row


def record_transfer(conn: psycopg.Connection, *, certificate_no: str,
                    transfer_kind: str, counterparty: str, scope_note: str,
                    condition_check_note: str,
                    continued_airworthiness_handover: str, agreement_ref: str,
                    effective_on: dt.date, counterparty_address: str | None = None,
                    actor: dict) -> dict:
    """STC 权益转让或终止（第 16 步）。

    **生效或终止后 30 天内**书面通知局方（法规时限, 见 L11.STC_TRANSFER_NOTICE）。
    **不得以内部协议免除法定持有人责任** —— 所以这里记的是通知与接收证据, 不是免责。
    """
    _require(conn, actor, "AM", "批准 STC 权益转让",
             "UG-DAP-11 第 16 步：适航管理负责人核对、责任经理批准")
    a = _approval(conn, certificate_no)
    if transfer_kind not in ("TRANSFER", "TERMINATION"):
        raise ValueError("类别须为 TRANSFER（转让）或 TERMINATION（终止）")
    for name, val in (("对方", counterparty), ("权限范围", scope_note),
                      ("CCAR-21.116 可转让条件的核对结论", condition_check_note),
                      ("持续适航责任衔接", continued_airworthiness_handover),
                      ("协议编号", agreement_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    row = fetch_one(conn, """
        INSERT INTO das_stc_transfer
               (approval_id, transfer_kind, counterparty, counterparty_address,
                scope_note, condition_check_note, continued_airworthiness_handover,
                agreement_ref, reviewed_by, approved_by, effective_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, transfer_kind, counterparty, effective_on
    """, (a["id"], transfer_kind, counterparty.strip(),
          (counterparty_address or "").strip() or None, scope_note.strip(),
          condition_check_note.strip(), continued_airworthiness_handover.strip(),
          agreement_ref.strip(), actor["user_id"], actor["user_id"], effective_on))
    row["caac_notice_due_on"] = effective_on + dt.timedelta(days=30)
    audit.write(conn, action="DAS_STC_TRANSFER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_STC_TRANSFER",
                object_id=str(row["id"]), object_code=certificate_no,
                new_value={"kind": transfer_kind, "counterparty": counterparty.strip(),
                           "effective_on": str(effective_on),
                           "notice_due_on": str(row["caac_notice_due_on"])},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def notify_transfer(conn: psycopg.Connection, *, transfer_id: int, notice_ref: str,
                    ack_ref: str | None = None,
                    notified_on: dt.date | None = None, actor: dict) -> dict:
    """书面通知局方（第 16 步，法规 30 天）。逾期的进 das_stc_transfer_notice_due。"""
    _require(conn, actor, "AWM", "通知局方权益转让", "UG-DAP-11 第 16 步")
    t = fetch_one(conn, """SELECT id, effective_on, caac_notified_on
                             FROM das_stc_transfer WHERE id=%s""", (transfer_id,))
    if not t:
        raise LookupError(f"没有编号为 {transfer_id} 的权益转让记录")
    if t["caac_notified_on"]:
        raise ValueError("已通知局方")
    if not (notice_ref or "").strip():
        raise ValueError("通知文件编号不得为空（须保存通知与接收证据）")
    on = notified_on or dt.date.today()
    execute(conn, """UPDATE das_stc_transfer
                        SET caac_notified_on=%s, caac_notice_ref=%s, caac_ack_ref=%s
                      WHERE id=%s""",
            (on, notice_ref.strip(), (ack_ref or "").strip() or None, transfer_id))
    due = t["effective_on"] + dt.timedelta(days=30)
    return {"transfer_id": transfer_id, "notified_on": str(on), "due_on": str(due),
            "late_days": max(0, (on - due).days),
            "note": ("已逾期——法规 30 天，见 L11.STC_TRANSFER_NOTICE"
                     if on > due else "在法规 30 天内")}


def record_licence(conn: psycopg.Connection, *, certificate_no: str, licensee: str,
                   products_and_scope: str, validity_note: str,
                   controlled_data_note: str, support_note: str,
                   config_feedback_note: str, termination_note: str,
                   agreement_ref: str, acceptability_note: str,
                   signed_on: dt.date | None = None,
                   delivered_on: dt.date | None = None, actor: dict) -> dict:
    """STC 书面许可（第 17 步，CCAR-21.119）。七项全部必填——原文逐项列明。"""
    _require(conn, actor, "AM", "签署 STC 书面许可",
             "UG-DAP-11 第 17 步：责任经理或书面授权人签署")
    a = _approval(conn, certificate_no)
    fields = {"许可方": licensee, "适用产品和改装范围": products_and_scope,
              "许可有效性": validity_note, "受控资料及修订传递": controlled_data_note,
              "持续适航支持": support_note, "构型反馈": config_feedback_note,
              "终止安排": termination_note, "协议编号": agreement_ref,
              "局方可接受性确认": acceptability_note}
    missing = [k for k, v in fields.items() if not (v or "").strip()]
    if missing:
        raise ValueError(
            f"下列各项不得为空：{'、'.join(missing)}。UG-DAP-11 第 17 步把要载明的各项"
            f"逐条列了出来——少一项，这份许可在局方面前就说不清许到哪为止。")
    row = fetch_one(conn, """
        INSERT INTO das_stc_licence
               (approval_id, licensee, products_and_scope, validity_note,
                controlled_data_note, support_note, config_feedback_note,
                termination_note, agreement_ref, acceptability_confirmed_by,
                acceptability_note, signed_by, signed_on, delivered_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, licensee, signed_on
    """, (a["id"], licensee.strip(), products_and_scope.strip(), validity_note.strip(),
          controlled_data_note.strip(), support_note.strip(),
          config_feedback_note.strip(), termination_note.strip(),
          agreement_ref.strip(), actor["user_id"], acceptability_note.strip(),
          actor["user_id"], signed_on or dt.date.today(), delivered_on))
    return row


def gaps(conn: psycopg.Connection) -> dict:
    """要盯的几份清单。

    `revocation_pending_ack` 止用通知还有接收方未确认的 —— 盯的是"那边还在用、
    我们这边以为已撤销"的那段窗口。
    """
    return {
        "revocation_pending_ack": fetch_all(
            conn, "SELECT * FROM das_revocation_pending_ack"),
        "revocation_progress": fetch_all(
            conn, "SELECT * FROM das_revocation_progress WHERE closed_on IS NULL"),
        "transfer_notice_due": fetch_all(
            conn, "SELECT * FROM das_stc_transfer_notice_due"),
        "open_inquiries": fetch_all(
            conn, "SELECT * FROM das_tech_inquiry_register WHERE open"),
    }


def similarity(conn: psycopg.Connection) -> list[dict]:
    """相似性评估台账。判「不相关」的也在里面——原文：无论是否相关均须记录。"""
    return fetch_all(conn, "SELECT * FROM das_similarity_register")
