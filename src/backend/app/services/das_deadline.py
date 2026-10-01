"""时限与周期引擎（基础能力）。

依据 UG-RPT-2026-003 第二节五个正交属性、判据 T0～T4、T1-2，第四之二节判据 P1、P2、
P2-1，手册 0.4.1 末段的硬边界，以及 2026-10-01 的业务决策：**法规要求的时限也可能变，
全部做成可设置的参数**。

【两条不同的闸门】
原设计把法规硬时限做成不可配置，值写在代码里。那样 CCAR-21 一改数，就得改代码发版本
——正是"三级文件体系在系统里塌成一级"的反面。改成可配置之后，防护从"做不到"降成了
"要走手续"，所以两条路的闸门不一样：

  · **规章规定的那个数（上限）** —— 只能由规章修订驱动。改它要先登记一条修订记录，
    写明条款出处、修订文号、规章生效日期和证据留存位置；没有这条记录数据库拒绝
    （DCMS-INV-073）。内部决定改不了它。
  · **本单位实际执行值** —— 走 UG-DAF-09 分类闸门，但不得超过上限（DCMS-INV-065），
    分类为非重大也不例外。只能更严。

于是"法规硬时限不得通过任何路线降低"仍然成立：对内部决定路线是关的，对规章是开的。

【判据 P2-1 的正反两面】
正面：重大更改在认可证据到手前不得生效，"已批准但未生效"是一个能查到的状态。
反面同样要成立：分类为**非重大**的变更**不应**被要求提供认可证据而卡住——把所有变更
都当重大处理是另一种错误，会让人绕开系统改。所以认可证据只在 MAJOR 上是启用前提。

【写入授权来自岗位任命】
参数变更不是"管理员改个设置"（判据 P2-1）。分类须在任适航管理负责人，批准与启用须在
任责任经理。挂在账号角色上就等于谁有那个角色谁能改时限。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

UNITS = {"HOUR": "小时", "DAY": "天", "MONTH": "月", "YEAR": "年"}
CLASSIFICATIONS = {"MAJOR": "重大更改", "MINOR": "非重大更改"}
FIELDS = {"CEILING": "规章规定的上限", "CALENDAR_BASIS": "日历口径"}
POSITION_CN = {"AWM": "适航管理负责人", "AM": "责任经理"}


def _require_position(conn: psycopg.Connection, actor: dict, code: str) -> None:
    if scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                        WHERE user_id=%s AND position_code=%s""",
              (actor["user_id"], code)) == 0:
        raise PermissionError(
            f"本操作须由在任的{POSITION_CN[code]}执行。改一个时限参数不是"
            f'"管理员改个设置"（判据 P2-1）: 填 UG-DAF-09 → 适航管理负责人分类 → '
            f"非重大则内部批准后生效, 重大则报 DPI 取得认可后方可生效。"
            f"请先按 UG-DAP-03 完成任命。")


def _require_trigger(conn: psycopg.Connection, param_code: str, expected: str) -> None:
    """判据 T3：事件触发期限不得由周期调度器生成，反之亦然。

    数据库里有同名的 DCMS-INV-069 挡着（绕过服务层也不行）。这里再判一次, 是为了
    在**算到期时刻之前**就把理由说对 —— 两个方向的错误都是设计错误, 不是数据问题。
    """
    p = fetch_one(conn, "SELECT trigger_mode FROM das_deadline_param WHERE code=%s",
                  (param_code,))
    if p is None:
        raise LookupError("时限控制项不存在")
    mode = p["trigger_mode"]
    if mode == expected:
        return
    if mode == "PRECONDITION":
        raise ValueError(
            f"{param_code} 是前置条件类, 不生成任务。判据 T2: 前置条件实现为**阻断**, "
            "不是提醒 —— 把它放进到期视图就变成了提醒。")
    if expected == "PERIODIC":
        raise ValueError(
            f"{param_code} 的触发方式是 {mode}, 不得由周期调度器生成任务（判据 T3）。"
            "按周期生成会两头错: 没有事件的时候每个周期凭空冒出一条任务, "
            "真实事件发生时又不会自动起算。请改用事件起算入口。")
    raise ValueError(
        f"{param_code} 的触发方式是 {mode}, 任务须按期间生成, 不挂事件实例（判据 T3）。"
        "周期任务挂到某个事件上之后, 它的下一期就没有了。")


# ---------------------------------------------------------------- 读

def registry(conn: psycopg.Connection, module: str | None = None) -> list[dict]:
    """追溯矩阵：判据 T1-2 的八个字段加当前实际取值。

    判据 T1 的注: 程序里的"见 UG-DAW-001"不是取值, 矩阵须把实际取值 join 进来
    —— 只写"见 UG-DAW-001"等于没给值。
    """
    if module:
        return fetch_all(conn, "SELECT * FROM das_deadline_registry WHERE module_code=%s",
                         (module,))
    return fetch_all(conn, "SELECT * FROM das_deadline_registry")


def unset(conn: psycopg.Connection) -> list[dict]:
    """尚无生效取值的控制项。判据 T1：不得默认按数值时长实现，所以缺值要列得出来。"""
    return fetch_all(conn, "SELECT * FROM das_deadline_unset")


def pending_activation(conn: psycopg.Connection) -> list[dict]:
    """判据 P2-1 的中间状态：已批准但未生效，以及卡在哪一步。"""
    return fetch_all(conn, "SELECT * FROM das_deadline_pending_activation")


def amendment_history(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM das_deadline_amendment_history")


def tasks(conn: psycopg.Connection, state: str | None = None) -> list[dict]:
    if state:
        return fetch_all(conn, "SELECT * FROM das_deadline_task_status WHERE state=%s",
                         (state,))
    return fetch_all(conn, "SELECT * FROM das_deadline_task_status")


def param_detail(conn: psycopg.Connection, code: str) -> dict:
    row = fetch_one(conn, "SELECT * FROM das_deadline_registry WHERE code=%s", (code,))
    if row is None:
        raise LookupError("时限控制项不存在")
    row["values"] = fetch_all(conn, """
        SELECT v.*, c.full_name AS classified_by_name, a.full_name AS approved_by_name
          FROM das_deadline_value v
          LEFT JOIN app_user c ON c.id = v.classified_by
          LEFT JOIN app_user a ON a.id = v.approved_by
         WHERE v.param_code=%s ORDER BY v.effective_from DESC, v.id DESC""", (code,))
    row["amendments"] = fetch_all(conn, """
        SELECT * FROM das_deadline_amendment_history WHERE param_code=%s""", (code,))
    return row


def due_at(conn: psycopg.Connection, code: str, start: dt.datetime) -> dict:
    """判据 T1：一处取值。日历口径取自控制项，不由调用方传入（判据 L-总）。"""
    row = fetch_one(conn, """
        SELECT das_deadline_due_at(%s, %s) AS due_at,
               das_deadline_value_active(%s, %s::date) AS value_num,
               das_deadline_unit_active(%s, %s::date)  AS value_unit""",
                    (code, start, code, start, code, start))
    p = fetch_one(conn, "SELECT calendar_basis, trigger_mode FROM das_deadline_param"
                        " WHERE code=%s", (code,))
    if p is None:
        raise LookupError("时限控制项不存在")
    out = {"param_code": code, "started_at": start, **(row or {}), **p}
    if out["due_at"] is None:
        out["note"] = ("算不出来: 该控制项当前无生效取值, 或为工作日口径而日历未加载。"
                       "不回落默认常量 —— 那等于又把值写回了代码里, 而那个常量会在"
                       "规章改了之后继续悄悄生效。")
    return out


# ---------------------------------------------------------------- 执行值的变更闸门

def propose_value(conn: psycopg.Connection, *, param_code: str, value_num: float,
                  value_unit: str, effective_from: dt.date, change_request_ref: str,
                  classification: str, actor: dict) -> dict:
    """提出新的执行值并分类（判据 P2-1 的第 1、2 步）。须在任适航管理负责人。"""
    _require_position(conn, actor, "AWM")
    if value_unit not in UNITS:
        raise ValueError("单位须取自：" + "、".join(f"{k} {v}" for k, v in UNITS.items()))
    if classification not in CLASSIFICATIONS:
        raise ValueError("分类结论须为 MAJOR 重大更改 或 MINOR 非重大更改")
    if not (change_request_ref or "").strip():
        raise ValueError(
            "须填写 UG-DAF-09 单据号。判据 P2-1: 参数变更入口必须内建分类闸门, "
            '不是"管理员改个设置"的页面 —— 没有单据号就无从追溯这次变更评估过什么。')
    if value_num <= 0:
        raise ValueError("时长须为正数")
    p = fetch_one(conn, "SELECT * FROM das_deadline_param WHERE code=%s", (param_code,))
    if p is None:
        raise LookupError("时限控制项不存在")
    row = fetch_one(conn, """
        INSERT INTO das_deadline_value
               (param_code, value_num, value_unit, effective_from, change_request_ref,
                classification, classified_by, classified_at, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,now(),%s)
        RETURNING id, param_code, value_num, value_unit, effective_from, classification
    """, (param_code, value_num, value_unit, effective_from, change_request_ref.strip(),
          classification, actor["user_id"], actor["user_id"]))
    audit.write(conn, action="DAS_DEADLINE_PROPOSE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DEADLINE_VALUE",
                object_id=str(row["id"]), object_code=param_code,
                new_value={"value": f"{value_num} {value_unit}",
                           "classification": classification},
                reason=change_request_ref, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    row["note"] = ("已分类为重大更改: 须报 DPI, 认可到手前新值不得生效。"
                   if classification == "MAJOR" else
                   "已分类为非重大更改: 内部批准后即可生效, **不需要**局方认可证据。")
    return row


def approve_value(conn: psycopg.Connection, *, value_id: int, actor: dict) -> dict:
    """批准新值（判据 P2-1 第 3 步）。批准不等于生效。"""
    _require_position(conn, actor, "AM")
    n = execute(conn, """UPDATE das_deadline_value SET approved_by=%s, approved_at=now()
                          WHERE id=%s AND approved_at IS NULL AND NOT is_baseline""",
                (actor["user_id"], value_id))
    if not n:
        raise ValueError("取值记录不存在、已批准, 或为初值（初值不走批准流程）")
    row = fetch_one(conn, """
        SELECT v.id, v.param_code, v.value_num, v.value_unit, v.classification,
               v.approved_at, v.caac_ack_at
          FROM das_deadline_value v WHERE v.id=%s""", (value_id,))
    audit.write(conn, action="DAS_DEADLINE_APPROVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DEADLINE_VALUE",
                object_id=str(value_id), object_code=row["param_code"],
                new_value={"approved": True},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    row["note"] = ("已批准但未生效。本次为重大更改, 还须登记局方认可证据后方可启用。"
                   if row["classification"] == "MAJOR" and row["caac_ack_at"] is None
                   else "已批准, 可启用。")
    return row


def record_caac_ack(conn: psycopg.Connection, *, value_id: int, ack_ref: str,
                    actor: dict) -> dict:
    """登记局方对重大更改的认可证据（UG-DAP-02 步骤 4）。"""
    _require_position(conn, actor, "AWM")
    if not (ack_ref or "").strip():
        raise ValueError("须写明认可证据（DPI 认可函号、局方邮件日期等）, 否则无从核对")
    v = fetch_one(conn, "SELECT id, classification FROM das_deadline_value WHERE id=%s",
                  (value_id,))
    if v is None:
        raise LookupError("取值记录不存在")
    if v["classification"] != "MAJOR":
        raise ValueError(
            "本次变更已分类为非重大更改, 不需要局方认可证据。第四之二节的反向用例: "
            "把所有变更都当重大处理是另一种错误, 会让人绕开系统改。")
    n = execute(conn, """UPDATE das_deadline_value SET caac_ack_ref=%s, caac_ack_at=now()
                          WHERE id=%s AND caac_ack_at IS NULL""", (ack_ref.strip(), value_id))
    if not n:
        raise ValueError("该取值已登记认可证据")
    return fetch_one(conn, "SELECT id, param_code, caac_ack_ref, caac_ack_at"
                           " FROM das_deadline_value WHERE id=%s", (value_id,))


def activate_value(conn: psycopg.Connection, *, value_id: int, actor: dict) -> dict:
    """启用新值。认可证据缺失时由 DCMS-INV-067 拒绝（判据 P2-1）。"""
    _require_position(conn, actor, "AM")
    v = fetch_one(conn, "SELECT * FROM das_deadline_value WHERE id=%s", (value_id,))
    if v is None:
        raise LookupError("取值记录不存在")
    if v["activated_at"] is not None:
        raise ValueError("该取值已启用")
    # 启用新值的同时把同一控制项上一条仍在生效的取值收到新值生效日的前一天。
    # 不收的话两条会在同一天同时生效, 取值函数只能靠 ORDER BY 碰巧选对一条。
    execute(conn, """
        UPDATE das_deadline_value SET effective_to = %s
         WHERE param_code = %s AND id <> %s AND activated_at IS NOT NULL
           AND effective_to IS NULL AND effective_from < %s""",
            (v["effective_from"] - dt.timedelta(days=1), v["param_code"], value_id,
             v["effective_from"]))
    execute(conn, "UPDATE das_deadline_value SET activated_at=now() WHERE id=%s", (value_id,))
    audit.write(conn, action="DAS_DEADLINE_ACTIVATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DEADLINE_VALUE",
                object_id=str(value_id), object_code=v["param_code"],
                new_value={"value": f'{v["value_num"]} {v["value_unit"]}',
                           "effective_from": str(v["effective_from"])},
                reason=v["change_request_ref"], session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_deadline_registry WHERE code=%s",
                     (v["param_code"],))


# ---------------------------------------------------------------- 规章修订

def amend_statutory(conn: psycopg.Connection, *, param_code: str, field_changed: str,
                    new_ceiling_value: float | None, new_ceiling_unit: str | None,
                    new_calendar_basis: str | None, regulation_ref: str,
                    regulation_revision_ref: str, regulation_effective_from: dt.date,
                    evidence_ref: str, actor: dict) -> dict:
    """登记规章修订并据此改动上限或日历口径。

    这是改动"规章规定的那个数"的**唯一**途径。内部决定改不了它 —— 否则"法规硬时限
    不得通过任何路线降低"就只剩一句话。修订记录与参数改动在同一事务里完成,
    触发器核对两者一致（DCMS-INV-073）。
    """
    _require_position(conn, actor, "AWM")
    if field_changed not in FIELDS:
        raise ValueError("修订对象须为 CEILING 规章规定的上限 或 CALENDAR_BASIS 日历口径")
    for name, val in (("条款出处", regulation_ref),
                      ("修订文号", regulation_revision_ref),
                      ("证据留存位置", evidence_ref)):
        if not (val or "").strip():
            raise ValueError(
                f"{name}不得为空。法规时限确实会变, 但改它必须说得出是哪一次修订改的、"
                "规章什么时候生效、原文存在哪里 —— 否则这就不是修订, 是内部决定。")
    p = fetch_one(conn, "SELECT * FROM das_deadline_param WHERE code=%s", (param_code,))
    if p is None:
        raise LookupError("时限控制项不存在")

    if field_changed == "CEILING":
        if new_ceiling_value is None or new_ceiling_unit not in UNITS:
            raise ValueError("须写明新的上限数值与单位")
        fetch_one(conn, """
            INSERT INTO das_deadline_statutory_amendment
                   (param_code, field_changed, old_ceiling_value, old_ceiling_unit,
                    new_ceiling_value, new_ceiling_unit, regulation_ref,
                    regulation_revision_ref, regulation_effective_from, evidence_ref,
                    recorded_by)
            VALUES (%s,'CEILING',%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (param_code, p["ceiling_value"], p["ceiling_unit"], new_ceiling_value,
              new_ceiling_unit, regulation_ref.strip(), regulation_revision_ref.strip(),
              regulation_effective_from, evidence_ref.strip(), actor["user_id"]))
        execute(conn, """UPDATE das_deadline_param SET ceiling_value=%s, ceiling_unit=%s,
                                ceiling_basis=%s WHERE code=%s""",
                (new_ceiling_value, new_ceiling_unit,
                 f"{regulation_ref.strip()}（{regulation_revision_ref.strip()}）", param_code))
    else:
        if new_calendar_basis not in ("CALENDAR", "WORKING"):
            raise ValueError("日历口径须为 CALENDAR 日历日 或 WORKING 工作日")
        fetch_one(conn, """
            INSERT INTO das_deadline_statutory_amendment
                   (param_code, field_changed, old_calendar_basis, new_calendar_basis,
                    regulation_ref, regulation_revision_ref, regulation_effective_from,
                    evidence_ref, recorded_by)
            VALUES (%s,'CALENDAR_BASIS',%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (param_code, p["calendar_basis"], new_calendar_basis, regulation_ref.strip(),
              regulation_revision_ref.strip(), regulation_effective_from,
              evidence_ref.strip(), actor["user_id"]))
        execute(conn, "UPDATE das_deadline_param SET calendar_basis=%s WHERE code=%s",
                (new_calendar_basis, param_code))

    audit.write(conn, action="DAS_DEADLINE_AMEND", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DEADLINE_PARAM",
                object_id=param_code, object_code=regulation_revision_ref,
                old_value={"ceiling": f'{p["ceiling_value"]} {p["ceiling_unit"]}',
                           "calendar_basis": p["calendar_basis"]},
                new_value={"field": field_changed,
                           "ceiling": (f"{new_ceiling_value} {new_ceiling_unit}"
                                       if field_changed == "CEILING" else None),
                           "calendar_basis": new_calendar_basis,
                           "regulation": regulation_revision_ref},
                reason=evidence_ref, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
    out = fetch_one(conn, "SELECT * FROM das_deadline_registry WHERE code=%s", (param_code,))
    out["note"] = ("上限已按规章修订改动。注意本单位的**执行值**不会自动跟着变: "
                   "放宽执行值仍须走 UG-DAF-09 分类闸门。")
    return out


# ---------------------------------------------------------------- 任务

def generate_periodic(conn: psycopg.Connection, *, param_code: str, period_key: str,
                      started_at: dt.datetime, actor: dict) -> dict:
    """按周期生成一条任务。

    判据 T3: 事件触发类不得由周期调度器生成 —— 由 DCMS-INV-069 拒绝。按周期生成会
    两头错: 没有 NCR 的时候每 21 个工作日凭空冒出一条"NCR 整改"任务; 真实事件发生时
    又不会自动起算。
    """
    _require_position(conn, actor, "AM")
    # 先判触发方式, 再算到期时刻。顺序反了的后果: 一个工作日口径而日历未加载的
    # 事件触发类参数, 会先报"算不出到期时刻", 把"你用错了生成器"这个更根本的理由
    # 盖住 —— 调用方会去加载日历, 而真正该改的是别拿周期调度器去生成事件期限。
    _require_trigger(conn, param_code, "PERIODIC")
    if not (period_key or "").strip():
        raise ValueError("周期任务须写明期间键（如 2026Q4、2026-10）, 用于去重")
    due = scalar(conn, "SELECT das_deadline_due_at(%s,%s)", (param_code, started_at))
    if due is None:
        raise ValueError(
            f"{param_code} 当前算不出到期时刻: 无生效取值, 或为工作日口径而日历未加载。"
            "判据 T1 要求不得默认按数值时长实现, 所以这里不生成一条到期时刻是猜的任务。")
    return fetch_one(conn, """
        INSERT INTO das_deadline_task (param_code, period_key, started_at, due_at)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (param_code, period_key) WHERE period_key IS NOT NULL DO NOTHING
        RETURNING id, param_code, period_key, started_at, due_at
    """, (param_code, period_key.strip(), started_at, due)) or {
        "param_code": param_code, "period_key": period_key, "note": "该期间已有任务"}


def start_event_task(conn: psycopg.Connection, *, param_code: str, object_type: str,
                     object_id: str, started_at: dt.datetime, actor: dict) -> dict:
    """由一个真实事件实例起算（判据 T3）。没有事件就没有任务。"""
    _require_position(conn, actor, "AM")
    _require_trigger(conn, param_code, "EVENT")
    if not (object_type or "").strip() or not (object_id or "").strip():
        raise ValueError(
            "事件任务须指向具体的事件实例。判据 T3: 没有事件就没有任务 —— "
            "不挂实例的任务既不知道为什么存在, 也无从判断该不该存在。")
    due = scalar(conn, "SELECT das_deadline_due_at(%s,%s)", (param_code, started_at))
    if due is None:
        raise ValueError(f"{param_code} 当前算不出到期时刻（无生效取值或日历未加载）")
    return fetch_one(conn, """
        INSERT INTO das_deadline_task (param_code, event_object_type, event_object_id,
                                       started_at, due_at)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (param_code, event_object_type, event_object_id)
            WHERE event_object_id IS NOT NULL DO NOTHING
        RETURNING id, param_code, event_object_type, event_object_id, started_at, due_at
    """, (param_code, object_type.strip(), object_id.strip(), started_at, due)) or {
        "param_code": param_code, "note": "该事件已有任务"}


def complete_task(conn: psycopg.Connection, *, task_id: int, note: str | None,
                  actor: dict) -> dict:
    n = execute(conn, """UPDATE das_deadline_task SET completed_at=now(), completed_by=%s,
                                note=%s WHERE id=%s AND completed_at IS NULL""",
                (actor["user_id"], note, task_id))
    if not n:
        raise ValueError("任务不存在或已完成")
    return fetch_one(conn, "SELECT * FROM das_deadline_task_status WHERE id=%s", (task_id,))
