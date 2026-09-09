"""命名 — UG-EDS-NAM-001 / SRS-NAM-001~003 / AC-NAM-01。

分工要说清楚:

**基本图号名称**由系统按【0~2 个稳定限定词】+【1 个核心工程实体名称】组合生成
(NAM §9: 用户只可确认, 不能自由改写)。生成逻辑落在数据库触发器
trg_z_family_compose_name 里, 应用层只负责在提交前给出预览。因为名称根本不接受
自由输入, 受限词在这一层已无从进入 —— NAM-AT-004 是靠结构保证的, 不是靠正则拦截。

**Dash P/N 名称**是自由文本(要写清与同族其它 Dash 的差异), 受限词校验的真正作用域
在这里。NAM §7 允许具体参数出现在 Dash 名称中, 因此参数类受限词在此放行。
"""
from __future__ import annotations

import re

import psycopg

from ..db import fetch_all, fetch_one

# 按 BCR-2026-001, 受限词控制级别以 UG-EDS-NAM-001 §7 为准(从严)
BLOCK = "BLOCK"
WARN_APPROVAL = "WARN_APPROVAL"
WARN = "WARN"

# NAM §7: 具体参数在基本图号名称中禁止, 但可用于 Dash 名称
PARAMETER_ALLOWED_IN_DASH = {"PARAMETER"}


def compose_names(conn: psycopg.Connection, core_term_id: str,
                  qualifier_1_id: str | None = None,
                  qualifier_2_id: str | None = None) -> dict:
    """预览系统将生成的中英文名称。

    这里的组合规则必须与数据库触发器一致, 否则界面预览与最终落库结果会不一样。
    为避免两处规则漂移, 预览直接调用数据库里同一套词典数据来拼, 而不是在应用层
    另建一份映射表。
    """
    core = fetch_one(conn, """
        SELECT code, name_cn, name_en, primary_class_code, status
          FROM naming_core_term WHERE id = %s
    """, (core_term_id,))
    if core is None:
        raise LookupError("核心实体词不存在")

    quals = []
    for qid in (qualifier_1_id, qualifier_2_id):
        if qid is None:
            continue
        q = fetch_one(conn, """
            SELECT code, name_cn, name_en, qualifier_type, status
              FROM naming_qualifier WHERE id = %s
        """, (qid,))
        if q is None:
            raise LookupError(f"稳定限定词不存在: {qid}")
        quals.append(q)

    name_cn = "".join(q["name_cn"] for q in quals) + core["name_cn"]
    name_en = " ".join([q["name_en"] for q in quals] + [core["name_en"]]).upper()

    return {
        "family_name_cn": name_cn,
        "family_name_en": name_en,
        "core_term": {"code": core["code"], "name_cn": core["name_cn"],
                      "status": core["status"]},
        "qualifiers": [{"code": q["code"], "name_cn": q["name_cn"],
                        "type": q["qualifier_type"], "status": q["status"]}
                       for q in quals],
        "primary_class_code": core["primary_class_code"],
        "deprecated_terms": [t["code"] for t in [core, *quals] if t["status"] == "DEPRECATED"],
    }


def check_restricted(conn: psycopg.Connection, text: str,
                     scope: str = "BASIC_DRAWING") -> dict:
    """对自由文本做受限词校验。

    scope='BASIC_DRAWING' 用于基本图号层面(最严);
    scope='DASH' 用于 Dash P/N 名称, 参数类受限词在此放行(NAM §7)。

    返回 blocked / needs_approval / warnings 三档, 由调用方决定是拒绝还是提示。
    """
    rules = fetch_all(conn, """
        SELECT restricted_type, control_level, pattern, is_regex, example, message_cn
          FROM restricted_term WHERE status = 'ACTIVE'
    """)
    hits: list[dict] = []
    for r in rules:
        if scope == "DASH" and r["restricted_type"] in PARAMETER_ALLOWED_IN_DASH:
            continue
        matched = False
        try:
            if r["is_regex"]:
                matched = re.search(r["pattern"], text) is not None
            else:
                matched = r["pattern"] in text
        except re.error:
            # 词典里的正则写坏了不应让整个提交流程崩掉, 记为未命中并继续
            continue
        if matched:
            hits.append({"restricted_type": r["restricted_type"],
                         "control_level": r["control_level"],
                         "pattern": r["pattern"],
                         "message": r["message_cn"]})

    return {
        "text": text,
        "scope": scope,
        "blocked": [h for h in hits if h["control_level"] == BLOCK],
        "needs_approval": [h for h in hits if h["control_level"] == WARN_APPROVAL],
        "warnings": [h for h in hits if h["control_level"] == WARN],
        "passed": not any(h["control_level"] == BLOCK for h in hits),
    }
