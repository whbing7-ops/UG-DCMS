"""记录的保存、冻结与销毁（基础能力）。

依据 UG-DAW-004 第 1 章通则与第 2 章期限表（97 类）、UG-DAP-01 第 6 章步骤 16、
CCAR-21.137（十三），判据 R1～R8 与 R-总。

【判据 R-总：销毁是带前置条件的受控动作，不是定时任务】
本模块**没有任何自动销毁**，也不提供"批量清理到期记录"的入口。到期只让记录出现在
候选清单里；销毁要人建批次、做三项核查、两人批准、再执行，每步留痕。
做成定时删除就会在冻结期、关联期限未满、缺双人批准的情况下照删不误。

【判据 R2：保留截止日是计算值】
`retention_until` 取"本记录自身期限"与"全部关联对象期限"的最大值，由数据库函数算，
服务层不另算一份——两处各算一遍迟早会不一致，而不一致的那一次就是误删。
任一关联对象终点未到时为 NULL，**NULL 按不得销毁处理**。

【冻结门槛低、解除门槛高（刻意的不对称）】
冻结是保护性动作：调查、诉讼、争议、局方要求或未关闭事项一出现就该冻上，门槛高到
让人来不及冻就失去了意义。所以资料管理负责人、适航管理负责人、独立监督负责人任一
在任即可发起。解除是风险方向——解除之后记录就可能被销毁——所以只有在任适航管理
负责人能解除，且必须写明解除说明。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

ANCHORS = {
    "CREATED": "自创建起算",
    "AUTHORISATION_END": "授权终止后",
    "TOOL_DECOMMISSION": "工具停用后",
    "NEW_VERSION_RELEASE": "新版发布后",
    "RESPONSIBILITY": "责任存续期间（长期）",
    "PRODUCT_LIFECYCLE": "产品/设计全生命周期",
    "PRODUCT_RETIREMENT": "至产品永久退役",
}
LINK_KINDS = ("PRODUCT", "PROJECT", "AUTHORISATION", "RESPONSIBILITY", "TOOL", "DOCUMENT")
FREEZE_KINDS = {
    "INVESTIGATION": "调查", "LITIGATION": "诉讼", "DISPUTE": "争议",
    "CAAC_REQUEST": "局方要求", "OPEN_ITEM": "未关闭事项",
}
POSITION_CN = {"DCM": "资料管理负责人", "AWM": "适航管理负责人", "ISM": "独立监督负责人"}


def _holds(conn: psycopg.Connection, actor: dict, code: str) -> bool:
    return scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code=%s""",
                  (actor["user_id"], code)) > 0


def _require(conn: psycopg.Connection, actor: dict, code: str) -> None:
    if not _holds(conn, actor, code):
        raise PermissionError(
            f"本操作须由在任的{POSITION_CN[code]}执行。UG-DAW-004 的归口负责人是"
            f"资料管理负责人，销毁的第二批准人是适航管理负责人（判据 R6）。"
            f"请先按 UG-DAP-03 完成任命。")


def _require_any(conn: psycopg.Connection, actor: dict, codes: tuple[str, ...]) -> None:
    if not any(_holds(conn, actor, c) for c in codes):
        raise PermissionError(
            "本操作须由在任的" + "、".join(POSITION_CN[c] for c in codes) + "之一执行。")


# ---------------------------------------------------------------- 类别与记录

def classes(conn: psycopg.Connection, procedure: str | None = None) -> list[dict]:
    """UG-DAW-004 第 2 章的 97 类记录。raw_period_text 是期限栏原文，供对照复核。"""
    if procedure:
        return fetch_all(conn, """SELECT * FROM das_retention_class
                                   WHERE source_procedure=%s ORDER BY code""", (procedure,))
    return fetch_all(conn, "SELECT * FROM das_retention_class ORDER BY code")


def class_summary(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_retention_class_summary")


def records(conn: psycopg.Connection, state: str | None = None) -> list[dict]:
    if state:
        return fetch_all(conn, "SELECT * FROM das_retention_status WHERE state=%s"
                               " ORDER BY retention_until NULLS LAST, id", (state,))
    return fetch_all(conn, "SELECT * FROM das_retention_status"
                           " ORDER BY retention_until NULLS LAST, id")


def due(conn: psycopg.Connection) -> list[dict]:
    """已过保留截止日的**候选**记录。

    判据 R-总: 这不是待删除队列。它只说"期限到了", 不说"可以销毁" ——
    销毁仍要三项核查与双人批准。
    """
    return fetch_all(conn, "SELECT * FROM das_retention_due")


def unanchored(conn: psycopg.Connection) -> list[dict]:
    """锚点未落地、期限算不出来的记录。既不能销毁，也不该被当成"永久保存"忘掉。"""
    return fetch_all(conn, "SELECT * FROM das_retention_unanchored")


def register(conn: psycopg.Connection, *, class_code: str, object_type: str,
             object_id: str, label: str | None, created_on: dt.date | None,
             anchor_on: dt.date | None, actor: dict) -> dict:
    _require(conn, actor, "DCM")
    c = fetch_one(conn, "SELECT * FROM das_retention_class WHERE code=%s", (class_code,))
    if c is None:
        raise LookupError("记录类别不存在（见 UG-DAW-004 第 2 章）")
    if not (object_type or "").strip() or not (object_id or "").strip():
        raise ValueError("须写明记录指向的实体类型与标识")
    if anchor_on and anchor_on > dt.date.today():
        raise ValueError("锚点事件日期不得晚于今天: 记录的是已发生的事")
    if c["anchor_kind"] == "CREATED" and anchor_on is not None:
        raise ValueError(
            f'{class_code} 的期限自创建起算（「{c["raw_period_text"]}」）, 不需要另填锚点日期。'
            "填了会让同一条记录有两个起算点。")
    row = fetch_one(conn, """
        INSERT INTO das_retention_record (class_code, object_type, object_id, label,
                                          created_on, anchor_on)
        VALUES (%s,%s,%s,%s,COALESCE(%s, current_date),%s)
        ON CONFLICT (object_type, object_id, class_code) DO NOTHING
        RETURNING id, class_code, object_type, object_id, created_on, anchor_on
    """, (class_code, object_type.strip(), object_id.strip(), label, created_on, anchor_on))
    if row is None:
        raise ValueError("该实体在本类别下已登记")
    row["status"] = fetch_one(conn, "SELECT * FROM das_retention_status WHERE id=%s",
                              (row["id"],))
    return row


def set_anchor(conn: psycopg.Connection, *, record_id: int, anchor_on: dt.date,
               actor: dict) -> dict:
    """回填锚点事件日期（授权终止、工具停用、新版发布）。

    回填之前期限算不出来, 记录会出现在 das_retention_unanchored 里 —— 那不是遗漏,
    是事件确实还没发生。
    """
    _require(conn, actor, "DCM")
    r = fetch_one(conn, """
        SELECT r.*, c.anchor_kind, c.raw_period_text FROM das_retention_record r
          JOIN das_retention_class c ON c.code = r.class_code WHERE r.id=%s""", (record_id,))
    if r is None:
        raise LookupError("记录不存在")
    if r["anchor_kind"] == "CREATED":
        raise ValueError(f'{r["class_code"]} 的期限自创建起算, 没有另外的锚点事件')
    if r["anchor_kind"] in ("RESPONSIBILITY", "PRODUCT_LIFECYCLE", "PRODUCT_RETIREMENT"):
        raise ValueError(
            f'{r["class_code"]}（「{r["raw_period_text"]}」）的终点由关联对象决定, '
            "不是一个锚点日期。请登记关联对象（das_retention_link）或责任移交记录。")
    if anchor_on > dt.date.today():
        raise ValueError("锚点事件日期不得晚于今天")
    execute(conn, "UPDATE das_retention_record SET anchor_on=%s WHERE id=%s",
            (anchor_on, record_id))
    audit.write(conn, action="DAS_RETENTION_ANCHOR", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RETENTION_RECORD",
                object_id=str(record_id), new_value={"anchor_on": str(anchor_on)},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_retention_status WHERE id=%s", (record_id,))


def link(conn: psycopg.Connection, *, record_id: int, link_kind: str, link_ref: str,
         required_until: dt.date | None, note: str | None, actor: dict) -> dict:
    """登记关联对象（判据 R2）。

    required_until 留空表示该对象的终点**还没到**（在役产品、未终止授权）, 不是
    "没有要求" —— 留空之后整条记录就没有截止日, 一律按不得销毁处理。
    """
    _require(conn, actor, "DCM")
    if link_kind not in LINK_KINDS:
        raise ValueError("关联类型须取自：" + "、".join(LINK_KINDS))
    if not (link_ref or "").strip():
        raise ValueError("须写明关联对象的标识")
    if scalar(conn, "SELECT count(*) FROM das_retention_record WHERE id=%s", (record_id,)) == 0:
        raise LookupError("记录不存在")
    fetch_one(conn, """
        INSERT INTO das_retention_link (record_id, link_kind, link_ref, required_until, note)
        VALUES (%s,%s,%s,%s,%s) RETURNING id
    """, (record_id, link_kind, link_ref.strip(), required_until, note))
    out = fetch_one(conn, "SELECT * FROM das_retention_status WHERE id=%s", (record_id,))
    if out["retention_until"] is None:
        out["note"] = ("关联对象的终点还没到, 本记录当前没有保留截止日。"
                       "算不出来按**不得销毁**处理（判据 R2）。")
    return out


# ---------------------------------------------------------------- 冻结（判据 R5）

def freezes(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_retention_freeze_active")


def freeze(conn: psycopg.Connection, *, record_id: int | None, scope_kind: str | None,
           scope_ref: str | None, freeze_kind: str, reason: str,
           release_condition: str, actor: dict) -> dict:
    """发起冻结。

    门槛刻意放低: 资料管理负责人、适航管理负责人、独立监督负责人任一在任即可。
    冻结是保护性动作, 调查或局方要求一出现就该冻上; 门槛高到让人来不及冻,
    这条控制就失去了意义。
    """
    _require_any(conn, actor, ("DCM", "AWM", "ISM"))
    if freeze_kind not in FREEZE_KINDS:
        raise ValueError("冻结情形须取自：" + "、".join(f"{k} {v}" for k, v in FREEZE_KINDS.items()))
    if not (reason or "").strip():
        raise ValueError("冻结原因不得为空")
    if not (release_condition or "").strip():
        raise ValueError(
            "须写明解除条件（判据 R5）。没有解除条件的冻结会永远挂着, "
            "而永远挂着与没冻结一样不可管理 —— 到时候没人知道什么情况下可以解。")
    if (record_id is None) == (scope_kind is None):
        raise ValueError("冻结对象须是单条记录或一个范围（产品／项目／类别）之一")
    row = fetch_one(conn, """
        INSERT INTO das_retention_freeze (record_id, scope_kind, scope_ref, freeze_kind,
                                          reason, release_condition, initiated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, record_id, scope_kind, scope_ref, freeze_kind, initiated_at
    """, (record_id, scope_kind, (scope_ref or "").strip() or None, freeze_kind,
          reason.strip(), release_condition.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_RETENTION_FREEZE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RETENTION_FREEZE",
                object_id=str(row["id"]), object_code=scope_ref,
                new_value={"kind": freeze_kind, "record_id": record_id,
                           "scope": scope_ref}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def release_freeze(conn: psycopg.Connection, *, freeze_id: int, release_note: str,
                   actor: dict) -> dict:
    """解除冻结。

    只有在任适航管理负责人能解除 —— 解除是风险方向, 解除之后记录就可能被销毁。
    与发起时的低门槛是刻意的不对称。
    """
    _require(conn, actor, "AWM")
    if not (release_note or "").strip():
        raise ValueError("须写明解除说明: 解除条件是否已满足、依据是什么")
    n = execute(conn, """UPDATE das_retention_freeze
                            SET released_at=now(), released_by=%s, release_note=%s
                          WHERE id=%s AND released_at IS NULL""",
                (actor["user_id"], release_note.strip(), freeze_id))
    if not n:
        raise ValueError("冻结记录不存在或已解除")
    audit.write(conn, action="DAS_RETENTION_UNFREEZE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RETENTION_FREEZE",
                object_id=str(freeze_id), new_value={"released": True},
                reason=release_note, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return fetch_one(conn, """SELECT id, record_id, scope_ref, released_at, release_note
                                FROM das_retention_freeze WHERE id=%s""", (freeze_id,))


# ---------------------------------------------------------------- 责任移交（判据 R3）

def handovers(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """
        SELECT h.*, u.full_name AS recorded_by_name FROM das_responsibility_handover h
          JOIN app_user u ON u.id = h.recorded_by ORDER BY h.effective_on DESC""")


def record_handover(conn: psycopg.Connection, *, scope_ref: str, event_kind: str,
                    effective_on: dt.date, transferred_to: str | None,
                    handover_ref: str, evidence_ref: str, actor: dict) -> dict:
    """登记责任终止或转让的可追溯移交（判据 R3）。

    「长期」类记录在有这条记录之前不得销毁。UG-DAW-004: 「长期」指责任存续期间持续
    保存, 终止或转让时安排可追溯移交, **不得因停业自动销毁**。
    """
    _require(conn, actor, "AWM")
    if event_kind not in ("TERMINATED", "TRANSFERRED"):
        raise ValueError("事件须为 TERMINATED 责任终止 或 TRANSFERRED 责任转让")
    for name, val in (("责任范围", scope_ref), ("移交清单/协议编号", handover_ref),
                      ("移交证据留存位置", evidence_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空: 移交要可追溯, 说不出清单和证据就不算移交")
    if event_kind == "TRANSFERRED" and not (transferred_to or "").strip():
        raise ValueError("转让须写明接收方。不写接收方的「转让」等于无人接手, "
                         "那是终止不是转让。")
    row = fetch_one(conn, """
        INSERT INTO das_responsibility_handover (scope_ref, event_kind, effective_on,
                                                 transferred_to, handover_ref,
                                                 evidence_ref, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, scope_ref, event_kind, effective_on
    """, (scope_ref.strip(), event_kind, effective_on, transferred_to,
          handover_ref.strip(), evidence_ref.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_RESPONSIBILITY_HANDOVER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RESPONSIBILITY_HANDOVER",
                object_id=str(row["id"]), object_code=handover_ref,
                new_value={"scope": scope_ref, "kind": event_kind},
                reason=evidence_ref, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return row


# ---------------------------------------------------------------- 销毁（判据 R4、R6）

def batches(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_retention_destruction_readiness")


def batch_detail(conn: psycopg.Connection, batch_id: int) -> dict:
    row = fetch_one(conn, "SELECT * FROM das_retention_destruction_readiness WHERE id=%s",
                    (batch_id,))
    if row is None:
        raise LookupError("销毁批次不存在")
    row["items"] = fetch_all(conn, """
        SELECT i.record_id, i.retention_until_at_approval, s.class_code, s.class_name,
               s.object_type, s.object_id, s.label, s.retention_until, s.frozen, s.state
          FROM das_retention_destruction_item i
          JOIN das_retention_status s ON s.id = i.record_id
         WHERE i.destruction_id=%s ORDER BY i.record_id""", (batch_id,))
    return row


def create_batch(conn: psycopg.Connection, *, batch_ref: str, record_ids: list[int],
                 actor: dict) -> dict:
    """建立销毁批次并放入记录。

    放进批次不等于会被销毁 —— 三项核查、双人批准都在后面, 执行时数据库再逐条校验
    冻结状态与保留截止日（判据 R-总）。
    """
    _require(conn, actor, "DCM")
    if not (batch_ref or "").strip():
        raise ValueError("须写明销毁批次编号（销毁清单按 UG-DAW-004 保存 5 年）")
    if not record_ids:
        raise ValueError("批次须至少包含一条记录")
    row = fetch_one(conn, """
        INSERT INTO das_retention_destruction (batch_ref, created_by)
        VALUES (%s,%s) RETURNING id, batch_ref, created_at
    """, (batch_ref.strip(), actor["user_id"]))
    for rid in record_ids:
        if scalar(conn, "SELECT count(*) FROM das_retention_record WHERE id=%s", (rid,)) == 0:
            raise LookupError(f"记录 {rid} 不存在")
        execute(conn, """
            INSERT INTO das_retention_destruction_item
                   (destruction_id, record_id, retention_until_at_approval)
            VALUES (%s,%s,das_retention_until(%s)) ON CONFLICT DO NOTHING""",
                (row["id"], rid, rid))
    return batch_detail(conn, row["id"])


CHECKS = {
    "inventory": ("清单核对", "inventory"),
    "related_period": ("关联期限核查", "related_period"),
    "freeze": ("冻结状态核查", "freeze"),
}


def record_check(conn: psycopg.Connection, *, batch_id: int, check: str, result: str,
                 actor: dict) -> dict:
    """登记三项核查之一的结果（判据 R4）。

    每项都要留结果文本, 不是一个"已核查"布尔值 —— 只记一个布尔值等于没核查,
    事后也无从判断当时核的是什么。
    """
    _require(conn, actor, "DCM")
    if check not in CHECKS:
        raise ValueError("核查项须为 inventory 清单核对 / related_period 关联期限核查 / "
                         "freeze 冻结状态核查")
    if not (result or "").strip():
        raise ValueError(f"{CHECKS[check][0]}须写明结果: 核了什么、结论是什么")
    col = CHECKS[check][1]
    n = execute(conn, f"""UPDATE das_retention_destruction
                             SET {col}_check_result=%s, {col}_checked_by=%s,
                                 {col}_checked_at=now()
                           WHERE id=%s AND executed_at IS NULL""",
                (result.strip(), actor["user_id"], batch_id))
    if not n:
        raise ValueError("批次不存在或已执行销毁")
    return batch_detail(conn, batch_id)


def approve_batch(conn: psycopg.Connection, *, batch_id: int, actor: dict) -> dict:
    """批准销毁。按操作人的在任岗位决定占哪一个批准位（判据 R6）。

    两个批准位必须是两个不同自然人, 由数据库按工号判（DCMS-INV-077）——
    一人兼两角在 5～8 人编制下是常态, 所以这条必须挡在库里, 不能只靠界面。
    """
    is_dcm = _holds(conn, actor, "DCM")
    is_awm = _holds(conn, actor, "AWM")
    if not is_dcm and not is_awm:
        raise PermissionError(
            "销毁批准须由在任的资料管理负责人或适航管理负责人执行（判据 R6）")
    b = fetch_one(conn, "SELECT * FROM das_retention_destruction WHERE id=%s", (batch_id,))
    if b is None:
        raise LookupError("销毁批次不存在")
    if b["executed_at"] is not None:
        raise ValueError("该批次已执行销毁")
    # 同时持两个岗位的人只能占一个位 —— 占哪个都不影响, 因为另一位必须是别人。
    if is_dcm and b["approved_by_dcm"] is None:
        col, who = "dcm", "资料管理负责人"
    elif is_awm and b["approved_by_awm"] is None:
        col, who = "awm", "适航管理负责人"
    else:
        raise ValueError("您对应的批准位已有批准人；另一位须由另一自然人批准（判据 R6）")
    execute(conn, f"""UPDATE das_retention_destruction
                         SET approved_by_{col}=%s, approved_by_{col}_at=now()
                       WHERE id=%s""", (actor["user_id"], batch_id))
    audit.write(conn, action="DAS_RETENTION_APPROVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RETENTION_DESTRUCTION",
                object_id=str(batch_id), object_code=b["batch_ref"],
                new_value={"role": col}, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    out = batch_detail(conn, batch_id)
    out["note"] = f"已以{who}身份批准。" + (
        "两位批准齐备。" if out["approved_dcm"] and out["approved_awm"]
        else "另一位批准须由另一自然人完成（判据 R6）。")
    return out


def execute_batch(conn: psycopg.Connection, *, batch_id: int, actor: dict) -> dict:
    """执行销毁。

    这里不做任何校验的"预检" —— 三项核查、双人批准、冻结状态、保留截止日、「长期」类
    的责任移交, 全部由数据库触发器在写入那一刻逐条判（DCMS-INV-075～079）。
    预检会给出两套判断, 而两套判断迟早不一致, 不一致的那一次就是误删。
    """
    _require(conn, actor, "DCM")
    b = fetch_one(conn, "SELECT * FROM das_retention_destruction WHERE id=%s", (batch_id,))
    if b is None:
        raise LookupError("销毁批次不存在")
    if b["executed_at"] is not None:
        raise ValueError("该批次已执行销毁")
    execute(conn, """UPDATE das_retention_destruction
                        SET executed_at=now(), executed_by=%s WHERE id=%s""",
            (actor["user_id"], batch_id))
    execute(conn, """
        UPDATE das_retention_record SET destroyed_at=now(), destruction_id=%s
         WHERE id IN (SELECT record_id FROM das_retention_destruction_item
                       WHERE destruction_id=%s)""", (batch_id, batch_id))
    audit.write(conn, action="DAS_RETENTION_DESTROY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RETENTION_DESTRUCTION",
                object_id=str(batch_id), object_code=b["batch_ref"],
                new_value={"executed": True}, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return batch_detail(conn, batch_id)


# ---------------------------------------------------------------- 恢复核验（判据 R7）

def restore_checks(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """
        SELECT c.*, u.full_name AS checked_by_name FROM das_retention_restore_check c
          JOIN app_user u ON u.id = c.checked_by ORDER BY c.restored_at DESC""")


def record_restore_check(conn: psycopg.Connection, *, restore_ref: str, backup_ref: str,
                         retention_recheck_result: str, access_recheck_result: str,
                         destroyed_items_noted: int, destroyed_items_note: str | None,
                         actor: dict) -> dict:
    """登记一次从备份恢复后的重新核验（判据 R7）。

    共享备份不得为删除单项记录而破坏整体可恢复性, 所以备份里仍然有已批准销毁的记录。
    恢复时必须重新跑一遍保留与访问条件核验, 并在恢复清单中标明已批准销毁项及限制 ——
    做不到这一点, 一次恢复就能把销毁决定整批撤销, 而没有任何人知道。
    """
    _require(conn, actor, "DCM")
    for name, val in (("恢复作业编号", restore_ref), ("所用备份", backup_ref),
                      ("保留条件重新核验结果", retention_recheck_result),
                      ("访问条件重新核验结果", access_recheck_result)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（判据 R7：恢复时重新核验保留与访问条件）")
    if destroyed_items_noted < 0:
        raise ValueError("已批准销毁项数不得为负")
    if destroyed_items_noted and not (destroyed_items_note or "").strip():
        raise ValueError(
            "恢复清单中有已批准销毁项时, 须写明限制（判据 R7）。只记一个数字等于没标明 ——"
            "那几条会在恢复后变成可访问的副本, 销毁决定就被悄悄撤销了。")
    row = fetch_one(conn, """
        INSERT INTO das_retention_restore_check
               (restore_ref, backup_ref, retention_recheck_result, access_recheck_result,
                destroyed_items_noted, destroyed_items_note, checked_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, restore_ref, backup_ref, restored_at, destroyed_items_noted
    """, (restore_ref.strip(), backup_ref.strip(), retention_recheck_result.strip(),
          access_recheck_result.strip(), destroyed_items_noted, destroyed_items_note,
          actor["user_id"]))
    audit.write(conn, action="DAS_RETENTION_RESTORE_CHECK", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RETENTION_RESTORE_CHECK",
                object_id=str(row["id"]), object_code=restore_ref,
                new_value={"backup": backup_ref,
                           "destroyed_items": destroyed_items_noted},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row
