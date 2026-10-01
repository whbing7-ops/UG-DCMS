"""DOA 符合性检查单（体系级）。

对象是 UG-DAM-01-附3: 66 条要求, 证明本单位的设计保证体系符合 CCAR-21 和
AP-21-18 附录D。它是设计保证运行的主数据锚点——其余模块产生的记录都要能挂回
这张表的某一条, 否则拼不成符合性论证。

【判据 S1】体系文件里有两个都叫"符合性检查单"的东西, 本模块只管前者:
  · UG-DAM-01-附3   体系级, 66 条, 保存期限"长期"            ← 本模块
  · UG-DAP-07 步骤1 项目级, 按审定基础逐条列, 产品全生命周期  ← 归 M3
不得合并。合并后体系级条目会被项目生命周期的保留规则误处置, 项目级条目会被
当作体系符合性证据提交局方。

四条最容易做丢的规则:
  · 自评只能新增不能改写(数据库触发器)。改判是新增一行推进生效日期, 原评价留痕。
  · 提交局方的只有前 5 列。内部辅助列(适用性、自评结论、监督覆盖、备注)不得外流。
  · "审核已覆盖"与"证据当前有效"是两回事。证据失效不回退覆盖记录, 只标记自评待复核。
  · 有引用不等于符合。结论由人判定并签署, 系统不据填写完整度自动改判。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

CONCLUSIONS = ("符合", "部分符合", "不符合")
FLAG_REASONS = {
    "doc_revised": "体系文件改版",
    "authorization_revoked": "授权撤销触发的文件复核",
    "record_unavailable": "记录冻结、作废或到期处置",
}


# ---------------------------------------------------------------- 读

def items(conn: psycopg.Connection) -> list[dict]:
    """66 条条目, 带当前有效的条款引用、最新自评和未关闭的待复核标记。"""
    return fetch_all(conn, """
        SELECT i.id, i.seq, i.req_code, i.req_name, i.req_source,
               i.applicable_stc, i.applicable_pma, i.na_reason,
               (SELECT count(*) FROM das_checklist_doc_ref r
                 WHERE r.item_id = i.id AND r.superseded_at IS NULL)        AS doc_ref_count,
               a.conclusion, a.statement, a.effective_from,
               u.full_name AS assessed_by_name, a.assessed_at,
               (SELECT count(*) FROM das_checklist_review_flag f
                 WHERE f.item_id = i.id AND f.cleared_at IS NULL)           AS open_flags
          FROM das_checklist_item i
          LEFT JOIN LATERAL (
                 SELECT * FROM das_checklist_assessment x
                  WHERE x.item_id = i.id AND x.effective_from <= current_date
                  ORDER BY x.effective_from DESC, x.id DESC LIMIT 1) a ON true
          LEFT JOIN app_user u ON u.id = a.assessed_by
         ORDER BY i.seq
    """)


def item_detail(conn: psycopg.Connection, item_id: int) -> dict | None:
    row = fetch_one(conn, "SELECT * FROM das_checklist_item WHERE id=%s", (item_id,))
    if row is None:
        return None
    row["doc_refs"] = fetch_all(conn, """
        SELECT id, doc_code, doc_name, doc_version, clause, valid_from, superseded_at
          FROM das_checklist_doc_ref WHERE item_id=%s ORDER BY superseded_at NULLS FIRST, doc_code
    """, (item_id,))
    # 全部自评按生效日期倒序: 改判留痕, 历史评价可回溯(判据 EV9-2)
    row["assessments"] = fetch_all(conn, """
        SELECT a.id, a.conclusion, a.statement, a.improvement_plan,
               a.effective_from, a.assessed_at, u.full_name AS assessed_by_name
          FROM das_checklist_assessment a JOIN app_user u ON u.id = a.assessed_by
         WHERE a.item_id=%s ORDER BY a.effective_from DESC, a.id DESC
    """, (item_id,))
    row["coverage"] = fetch_all(conn, """
        SELECT audit_ref, audit_kind, cycle_start, covered_at, result, ncr_ref
          FROM das_checklist_coverage WHERE item_id=%s ORDER BY covered_at DESC
    """, (item_id,))
    row["flags"] = fetch_all(conn, """
        SELECT f.id, f.reason, f.source_ref, f.raised_at, f.cleared_at, f.clear_note,
               r.full_name AS raised_by_name, c.full_name AS cleared_by_name
          FROM das_checklist_review_flag f
          LEFT JOIN app_user r ON r.id = f.raised_by
          LEFT JOIN app_user c ON c.id = f.cleared_by
         WHERE f.item_id=%s ORDER BY f.raised_at DESC
    """, (item_id,))
    return row


def doc_impact(conn: psycopg.Connection, doc_code: str, doc_version: str | None = None) -> list[dict]:
    """判据 S2: 变更评估时按"文件 + 版本"反查受影响的检查单条目。

    UG-DAP-02 步骤1 要求变更评估说明对 CCAR-21 和 AP-21-18 附录D 适用要求符合性的
    影响。这个清单就是那一栏的依据, 不能靠人记得改哪份文件会动到哪些条目。
    """
    if doc_version is None:
        return fetch_all(conn, """
            SELECT * FROM das_checklist_doc_impact WHERE doc_code=%s ORDER BY seq
        """, (doc_code,))
    return fetch_all(conn, """
        SELECT * FROM das_checklist_doc_impact WHERE doc_code=%s AND doc_version=%s ORDER BY seq
    """, (doc_code, doc_version))


def submission(conn: psycopg.Connection, actor: dict) -> dict:
    """判据 S3: 按提交口径导出——只有前 5 列。

    填写说明 A2: 正式提交局方的是序号、要求编号和名称、要求内容、
    设计保证系统文件编号名称版本、符合性自评说明。
    A3: 第 6 列及以后是内部辅助列, 提交前可隐藏或删除。

    导出件须标明导出日期、操作人和检查单当前状态, 否则收件方无从判断看的是哪一版。
    """
    rows = fetch_all(conn, "SELECT * FROM das_checklist_submission")
    return {
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "exported_by": actor["username"],
        "item_count": len(rows),
        "open_flag_count": scalar(conn, """
            SELECT count(*) FROM das_checklist_review_flag WHERE cleared_at IS NULL"""),
        "rows": rows,
    }


def coverage_stat(conn: psycopg.Connection) -> list[dict]:
    """判据 EV4、EV5: 覆盖率统计。分母只含适用项。"""
    return fetch_all(conn, "SELECT * FROM das_checklist_coverage_stat ORDER BY cycle_start DESC NULLS LAST")


def renewal_view(conn: psycopg.Connection) -> list[dict]:
    """判据 S4: 证件延续审查所需的覆盖与未关闭不符合项关联视图。"""
    return fetch_all(conn, "SELECT * FROM das_checklist_renewal_view")


# ---------------------------------------------------------------- 写

def assess(conn: psycopg.Connection, *, item_id: int, conclusion: str, statement: str,
           improvement_plan: str | None, evidence: list[dict] | None, actor: dict,
           effective_from: dt.date | None = None) -> dict:
    """逐项自评并签署（判据 EV4-3、C5、EV3）。

    填写说明 A26: 首次发布和申请前, 适航管理负责人须结合实际范围、证件和运行证据
    **逐项**签署确认。所以自评是一条一条签的, 不是整表一次性确认。

    判据 C5 的两半: 不得因为填写完成就自动改判为"符合"; 也不得因为挂了引用就自动
    改判。结论由人给, 系统只负责留痕。
    """
    if conclusion not in CONCLUSIONS:
        raise ValueError("自评结论须为 符合、部分符合 或 不符合")
    if not (statement or "").strip():
        raise ValueError("具体符合性说明不得为空: 只写结论不写理由的自评不成立")
    if conclusion != "符合" and not (improvement_plan or "").strip():
        raise ValueError("结论非'符合'的, 须给出完善计划")
    if scalar(conn, "SELECT count(*) FROM das_checklist_item WHERE id=%s", (item_id,)) == 0:
        raise ValueError("检查单条目不存在")

    row = fetch_one(conn, """
        INSERT INTO das_checklist_assessment
               (item_id, conclusion, statement, improvement_plan, assessed_by, effective_from)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, item_id, conclusion, effective_from
    """, (item_id, conclusion, statement, improvement_plan, actor["user_id"],
          effective_from or dt.date.today()))

    # 判据 EV3: 自评说明可引用运行证据(记录、表单、监督记录), 存的是索引不是记录本身。
    for ev in (evidence or []):
        kind = ev.get("kind")
        ref = (ev.get("ref") or "").strip()
        if kind not in ("record", "form", "surveillance", "other"):
            raise ValueError("证据类别须为 record / form / surveillance / other")
        if not ref:
            raise ValueError("证据索引不得为空")
        execute(conn, """
            INSERT INTO das_checklist_evidence (assessment_id, evidence_kind, evidence_ref, evidence_note)
            VALUES (%s,%s,%s,%s)""", (row["id"], kind, ref, ev.get("note")))

    audit.write(conn, action="DAS_CHECKLIST_ASSESS", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CHECKLIST_ITEM",
                object_id=str(item_id), object_code=None,
                new_value={"conclusion": conclusion, "effective_from": str(row["effective_from"]),
                           "evidence_count": len(evidence or [])},
                reason=None, session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def raise_flag(conn: psycopg.Connection, *, item_id: int, reason: str, source_ref: str,
               actor: dict) -> dict:
    """标记条目待复核（判据 EV9）。

    【判据 EV9-1】只标记自评待复核, **不动覆盖记录**——审核当时确实覆盖过, 那是
    历史事实; 证据后来失效不能让历史记录回退。两件事分别记录。
    """
    if reason not in FLAG_REASONS:
        raise ValueError("待复核原因须为 doc_revised / authorization_revoked / record_unavailable")
    if not (source_ref or "").strip():
        raise ValueError("须写明触发源(文件编号+版本、授权 ID 或记录编号)")
    dup = scalar(conn, """
        SELECT count(*) FROM das_checklist_review_flag
         WHERE item_id=%s AND reason=%s AND source_ref=%s AND cleared_at IS NULL""",
                 (item_id, reason, source_ref))
    if dup:
        return fetch_one(conn, """
            SELECT id, item_id, reason, source_ref FROM das_checklist_review_flag
             WHERE item_id=%s AND reason=%s AND source_ref=%s AND cleared_at IS NULL""",
                         (item_id, reason, source_ref))
    return fetch_one(conn, """
        INSERT INTO das_checklist_review_flag (item_id, reason, source_ref, raised_by)
        VALUES (%s,%s,%s,%s) RETURNING id, item_id, reason, source_ref
    """, (item_id, reason, source_ref, actor["user_id"]))


def clear_flag(conn: psycopg.Connection, *, flag_id: int, note: str, actor: dict) -> None:
    """关闭待复核标记。

    判据 A4-3: 撤销触发的文件复核未关闭前, 相关文件不得作为符合性证据被引用。
    因此关闭标记必须写明复核结论, 不能一键清空。
    """
    if not (note or "").strip():
        raise ValueError("关闭待复核标记须写明复核结论")
    n = execute(conn, """
        UPDATE das_checklist_review_flag
           SET cleared_at=now(), cleared_by=%s, clear_note=%s
         WHERE id=%s AND cleared_at IS NULL""", (actor["user_id"], note, flag_id))
    if not n:
        raise ValueError("标记不存在或已关闭")
    audit.write(conn, action="DAS_CHECKLIST_FLAG_CLEAR", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CHECKLIST_REVIEW_FLAG",
                object_id=str(flag_id), object_code=None, new_value={"note": note},
                reason=note, session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


def propagate_doc_revision(conn: psycopg.Connection, *, doc_code: str, old_version: str,
                           new_version: str, actor: dict) -> dict:
    """体系文件改版时的反查与传播（判据 EV2 + EV9）。

    UG-DAP-02 步骤7 原文: 更改涉及检查单条目的, 同步更新《DOA 符合性检查单》中
    受影响行的"设计保证系统文件编号、名称、版本"和"符合性自评说明"。

    这里做两件事, 都不擅自改自评:
      1. 旧版本引用置 superseded_at, 不删行——历史评价的依据要留着;
      2. 受影响条目挂 doc_revised 待复核标记, 提示人去更新两栏。
    引用的新版本由谁来建、自评怎么改, 是人的判断, 系统不代劳(判据 EV9-2)。
    """
    affected = fetch_all(conn, """
        SELECT DISTINCT item_id FROM das_checklist_doc_ref
         WHERE doc_code=%s AND doc_version=%s AND superseded_at IS NULL""",
                         (doc_code, old_version))
    execute(conn, """
        UPDATE das_checklist_doc_ref SET superseded_at=current_date
         WHERE doc_code=%s AND doc_version=%s AND superseded_at IS NULL""",
            (doc_code, old_version))
    src = f"{doc_code} {old_version}→{new_version}"
    for a in affected:
        raise_flag(conn, item_id=a["item_id"], reason="doc_revised", source_ref=src, actor=actor)
    audit.write(conn, action="DAS_CHECKLIST_DOC_REVISED", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CHECKLIST",
                object_id=doc_code, object_code=new_version,
                new_value={"affected_items": len(affected), "from": old_version, "to": new_version},
                reason=None, session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return {"affected_items": len(affected), "source_ref": src}


def add_doc_ref(conn: psycopg.Connection, *, item_id: int, doc_code: str, doc_name: str,
                doc_version: str, clause: str, actor: dict) -> dict:
    """新增条款级引用（判据 EV1）。版本必填——不记版本, 改版后无从判断引用是否还成立。"""
    for name, val in (("文件编号", doc_code), ("文件名称", doc_name),
                      ("版本", doc_version), ("条款号", clause)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空: 判据 EV1 要求引用到 编号+名称+版本+条款号")
    if scalar(conn, "SELECT count(*) FROM das_checklist_item WHERE id=%s", (item_id,)) == 0:
        raise ValueError("检查单条目不存在")
    return fetch_one(conn, """
        INSERT INTO das_checklist_doc_ref (item_id, doc_code, doc_name, doc_version, clause)
        VALUES (%s,%s,%s,%s,%s) RETURNING id, item_id, doc_code, doc_version, clause
    """, (item_id, doc_code, doc_name, doc_version, clause))
