"""发号中心 — SRS-NUM-001~006 / INV-004 / AC-DASH-01 / AT-001。

两条容易做错的规则:

1. **禁止 MAX+1**(SRS-NUM-003)。下一个可用号不是"已有对象的最大号加一", 而是
   "号码占用记录中不存在的最小号"。二者在正常情况下结果相同, 一旦出现作废、预留或
   跳号就会分歧 —— 若按业务表算 MAX+1, 作废掉最大号之后该号会被重新发出, 直接违反
   INV-004。因此这里只查 number_allocation, 不查 part_number / basic_drawing_family。

2. **占用即永久**(INV-004)。ALLOCATED 与 CANCELLED 都算占用。AC-DASH-01 的场景是
   -001/-002 已分配、-003 作废, 下一个必须是 -004; 数据库触发器 trg_number_allocation_guard
   还会拒绝任何把已占号码退回可分配状态的尝试。

并发: 同一作用域(某个设计族的 Dash 序列, 或某一级类别的基本图号序列)内的取号必须
串行, 否则两个人会同时看到同一个"最小可用号"。用按作用域计算的事务级 advisory lock
实现, 锁在事务结束时自动释放。
"""
from __future__ import annotations

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

# Dash 号范围 — INV-003 / CK-01
DASH_MIN, DASH_MAX = 1, 999
# UG + 一级类别数字 + 类别内五位永久流水号
BASIC_MIN, BASIC_MAX = 1, 99999

CLASS_PREFIX = {"T1": "UG1", "T2": "UG2", "T3": "UG3"}


def basic_drawing_number(primary_class_code: str, sequence: int) -> str:
    """按 UG-DGS-000 的件号架构组装基本图号。"""
    prefix = CLASS_PREFIX.get(primary_class_code)
    if prefix is None:
        raise ValueError(f"未知一级技术类别: {primary_class_code}")
    if not BASIC_MIN <= sequence <= BASIC_MAX:
        raise ValueError("基本图号流水号必须为 1–99999")
    return f"{prefix}{sequence:05d}"


def next_basic(conn: psycopg.Connection, primary_class_code: str) -> int | None:
    prefix = CLASS_PREFIX.get(primary_class_code)
    if prefix is None:
        raise ValueError("该类别不允许普通新建设计族发号")
    # 已分配、预留、作废及旧版 UGTn 号码均永久占用原类别流水。
    return scalar(conn, """
        SELECT g.n FROM generate_series(%s::int,%s::int) g(n)
        WHERE NOT EXISTS (SELECT 1 FROM number_allocation na
          WHERE na.number_type='BASIC_DRAWING'
          AND (na.allocated_number LIKE %s OR na.allocated_number LIKE %s)
          AND na.numeric_sequence=g.n)
        ORDER BY g.n LIMIT 1
    """, (BASIC_MIN, BASIC_MAX, prefix + '%', 'UG' + primary_class_code + '%'))


def dash_suffix(dash: int) -> str:
    return f"{dash:03d}"


def _lock_scope(conn: psycopg.Connection, scope: str) -> None:
    """按作用域串行化取号。hashtext 把作用域字符串映射为锁键。"""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"dcms:number:{scope}",))


# ---------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------
def occupied_numbers(conn: psycopg.Connection, number_type: str,
                     family_id: str | None = None) -> list[dict]:
    return fetch_all(conn, """
        SELECT allocated_number, numeric_sequence, status, reserve_reason,
               cancel_reason, skip_reason, requested_at, allocated_at, cancelled_at,
               target_object_type, target_object_id
          FROM number_allocation
         WHERE number_type = %s
           AND basic_drawing_family_id IS NOT DISTINCT FROM %s
         ORDER BY numeric_sequence
    """, (number_type, family_id))


def next_available(conn: psycopg.Connection, number_type: str,
                   family_id: str | None, lo: int, hi: int) -> int | None:
    """占用记录中不存在的最小号。查的是占用记录, 不是业务表 —— 见模块说明第 1 条。"""
    return scalar(conn, """
        SELECT g.n FROM generate_series(%s::int, %s::int) AS g(n)
         WHERE NOT EXISTS (
                 SELECT 1 FROM number_allocation na
                  WHERE na.number_type = %s
                    AND na.basic_drawing_family_id IS NOT DISTINCT FROM %s
                    AND na.numeric_sequence = g.n)
         ORDER BY g.n LIMIT 1
    """, (lo, hi, number_type, family_id))


def gaps(conn: psycopg.Connection, family_id: str) -> list[dict]:
    """族内 Dash 序列的空缺 — SRS-NUM-004 / DQ-NUM-001。

    空缺分两类: 有占用记录但已作废(号码不可复用), 与完全无记录(可分配)。
    界面上必须区分, 否则使用者会以为作废号还能再用。
    """
    return fetch_all(conn, """
        WITH used AS (
            SELECT numeric_sequence AS n, status FROM number_allocation
             WHERE number_type = 'DASH' AND basic_drawing_family_id = %s),
        span AS (SELECT COALESCE(max(n), 0) AS hi FROM used)
        SELECT g.n AS dash_number,
               CASE WHEN u.n IS NULL THEN 'NEVER_ALLOCATED' ELSE u.status END AS state,
               (u.n IS NULL) AS reusable
          FROM span, generate_series(1, span.hi) AS g(n)
          LEFT JOIN used u ON u.n = g.n
         WHERE u.n IS NULL OR u.status = 'CANCELLED'
         ORDER BY g.n
    """, (family_id,))


# ---------------------------------------------------------------------
# 分配
# ---------------------------------------------------------------------
def allocate_basic_drawing(conn: psycopg.Connection, primary_class_code: str,
                           target_id: str, actor: dict) -> tuple[str, int]:
    """为设计族分配基本图号。返回 (基本图号, 流水号)。"""
    _lock_scope(conn, f"basic:{primary_class_code}")
    seq = next_basic(conn, primary_class_code)
    if seq is None:
        raise ValueError(f"{primary_class_code} 类别的基本图号已用尽 "
                         f"({BASIC_MIN}~{BASIC_MAX})")
    number = basic_drawing_number(primary_class_code, seq)

    execute(conn, """
        INSERT INTO number_allocation
            (number_type, allocated_number, numeric_sequence, status,
             requested_by, approved_by, approved_at, allocated_at,
             target_object_type, target_object_id)
        VALUES ('BASIC_DRAWING', %s, %s, 'ALLOCATED', %s, %s, now(), now(),
                'BASIC_DRAWING_FAMILY', %s)
    """, (number, seq, actor["user_id"], actor["user_id"], target_id))

    audit.write(conn, action="NUMBER_ALLOCATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="NUMBER_ALLOCATION",
                object_id=target_id, object_code=number,
                new_value={"number_type": "BASIC_DRAWING", "number": number,
                           "primary_class": primary_class_code},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return number, seq


def reserve_dash(conn: psycopg.Connection, family_id: str,
                 actor: dict, requested_dash: int | None = None) -> int:
    """预留一个 Dash 号(RESERVED), 返回号码。

    为什么分两步: Full P/N 需要 Dash 号才能拼出来, 而号码占用记录又要指向 P/N,
    二者互为前提。若先用占位 ID 发号再回填, 会被 INV-004 的"已分配号码不得改指
    其它对象"正确拦下 —— 那条规则本就是为了防止号码被偷偷挪用。
    因此改为: 先 RESERVED 占位(尚无归属) → 建对象 → confirm_dash 置为 ALLOCATED
    并写入归属。这也正是 SRS-NUM-002 描述的预留/分配两阶段语义。
    """
    _lock_scope(conn, f"dash:{family_id}")

    if requested_dash is None:
        dash = next_available(conn, "DASH", family_id, DASH_MIN, DASH_MAX)
        if dash is None:
            raise ValueError(f"该设计族的 Dash 号已用尽 ({DASH_MIN}~{DASH_MAX})")
    else:
        if not (DASH_MIN <= requested_dash <= DASH_MAX):
            raise ValueError(f"Dash 号必须在 {DASH_MIN}~{DASH_MAX} 之间"
                             f"(000 永远禁止 — INV-003)")
        existing = fetch_one(conn, """
            SELECT status, cancel_reason FROM number_allocation
             WHERE number_type = 'DASH' AND basic_drawing_family_id = %s
               AND numeric_sequence = %s
        """, (family_id, requested_dash))
        if existing is not None:
            raise ValueError(
                f"Dash -{dash_suffix(requested_dash)} 已被占用(状态 {existing['status']}), "
                f"号码一经占用永不复用 — INV-004")
        dash = requested_dash

    execute(conn, """
        INSERT INTO number_allocation
            (number_type, basic_drawing_family_id, allocated_number, numeric_sequence,
             status, reserve_reason, requested_by)
        VALUES ('DASH', %s, %s, %s, 'RESERVED', %s, %s)
    """, (family_id, dash_suffix(dash), dash, "新建 Dash P/N", actor["user_id"]))
    return dash


def confirm_dash(conn: psycopg.Connection, family_id: str, dash: int,
                 target_id: str, actor: dict) -> None:
    """把预留号确认为正式分配, 并写入归属对象。"""
    execute(conn, """
        UPDATE number_allocation
           SET status = 'ALLOCATED', approved_by = %s, approved_at = now(),
               allocated_at = now(), target_object_type = 'PART_NUMBER',
               target_object_id = %s
         WHERE number_type = 'DASH' AND basic_drawing_family_id = %s
           AND numeric_sequence = %s AND status = 'RESERVED'
    """, (actor["user_id"], target_id, family_id, dash))

    audit.write(conn, action="NUMBER_ALLOCATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="NUMBER_ALLOCATION",
                object_id=target_id, object_code=dash_suffix(dash),
                new_value={"number_type": "DASH", "dash": dash, "family_id": family_id},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


def cancel_dash(conn: psycopg.Connection, family_id: str, dash: int,
                reason: str, actor: dict) -> None:
    """作废一个 Dash 号。作废后该号永不复用 — INV-004 / AC-DASH-01。"""
    row = fetch_one(conn, """
        SELECT id, status FROM number_allocation
         WHERE number_type = 'DASH' AND basic_drawing_family_id = %s
           AND numeric_sequence = %s
    """, (family_id, dash))
    if row is None:
        raise LookupError(f"Dash -{dash_suffix(dash)} 不存在占用记录")
    if row["status"] == "CANCELLED":
        raise ValueError(f"Dash -{dash_suffix(dash)} 已是作废状态")

    execute(conn, """
        UPDATE number_allocation
           SET status = 'CANCELLED', cancel_reason = %s, cancelled_at = now()
         WHERE id = %s
    """, (reason, row["id"]))

    audit.write(conn, action="NUMBER_CANCEL", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="NUMBER_ALLOCATION",
                object_id=str(row["id"]), object_code=dash_suffix(dash),
                old_value={"status": row["status"]},
                new_value={"status": "CANCELLED"}, reason=reason,
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


def skip_dash(conn: psycopg.Connection, family_id: str, dash: int,
              reason: str, actor: dict) -> None:
    """跳号并记录理由 — SRS-NUM-004。

    跳号不是"什么都不做"。若只是不用某个号而不留记录, 号码序列就出现了无法解释的
    空缺, 日后无人知道那是历史遗留、误操作还是有意保留。这里为被跳过的号建立一条
    CANCELLED 记录并写明理由, 使空缺可解释、且该号不会被后续分配捡回去。
    """
    _lock_scope(conn, f"dash:{family_id}")
    exists = fetch_one(conn, """
        SELECT status FROM number_allocation
         WHERE number_type = 'DASH' AND basic_drawing_family_id = %s AND numeric_sequence = %s
    """, (family_id, dash))
    if exists is not None:
        raise ValueError(f"Dash -{dash_suffix(dash)} 已有占用记录(状态 {exists['status']}), "
                         f"无需跳号")
    execute(conn, """
        INSERT INTO number_allocation
            (number_type, basic_drawing_family_id, allocated_number, numeric_sequence,
             status, cancel_reason, skip_reason, requested_by, cancelled_at)
        VALUES ('DASH', %s, %s, %s, 'CANCELLED', %s, %s, %s, now())
    """, (family_id, dash_suffix(dash), dash, f"跳号: {reason}", reason, actor["user_id"]))

    audit.write(conn, action="NUMBER_SKIP", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="NUMBER_ALLOCATION",
                object_code=dash_suffix(dash),
                new_value={"dash": dash, "family_id": family_id, "status": "CANCELLED"},
                reason=reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))
