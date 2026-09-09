"""数据质量与报表 — SRS-DQ-001~003 / SRS-RPT-001。

数据质量规则的执行结果落库为 data_quality_issue, 而不是每次现算。理由是:
ERROR 级问题会阻止基线发布, 使用者需要一份"待处置清单"来安排工作; 现算的结果
只存在于某一次请求里, 既无法分配责任人, 也看不出问题是新出现的还是一直没解决。

豁免必须带理由、处置人与有效期。无期限的豁免等于把规则关掉, 而且关掉的痕迹会随时间
被遗忘 —— 半年后没人知道这条 ERROR 为什么不再报。过期豁免由视图
v_data_quality_effective 自动回到 OPEN, 原始记录不改动以保留豁免历史。
"""
from __future__ import annotations

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar


# ---------------------------------------------------------------------
# 规则执行
# ---------------------------------------------------------------------
def run_rules(conn: psycopg.Connection, actor: dict | None = None,
              rule_codes: list[str] | None = None) -> dict:
    """执行数据质量规则并登记问题。

    已存在的 OPEN 问题不重复登记; 已消失的问题自动关闭 —— 否则清单会越积越长,
    最终没人再看它。
    """
    rules = fetch_all(conn, """
        SELECT code, name_cn, severity, object_type, blocks_release
          FROM data_quality_rule
         WHERE status = 'ACTIVE' AND (%s::text[] IS NULL OR code = ANY(%s))
         ORDER BY code
    """, (rule_codes, rule_codes))

    found: dict[str, list[dict]] = {}
    for r in rules:
        fn = _CHECKS.get(r["code"])
        if fn is None:
            continue          # 未实现的规则跳过, 不伪造结果
        found[r["code"]] = fn(conn)

    opened = closed = 0
    for code, rows in found.items():
        rule = next(r for r in rules if r["code"] == code)
        current_ids = {str(x["object_id"]) for x in rows}

        for x in rows:
            exists = scalar(conn, """
                SELECT EXISTS (SELECT 1 FROM data_quality_issue
                                WHERE rule_code=%s AND object_id=%s AND status='OPEN')
            """, (code, x["object_id"]))
            if exists:
                continue
            execute(conn, """
                INSERT INTO data_quality_issue
                    (rule_code, object_type, object_id, severity, message, status)
                VALUES (%s,%s,%s,%s,%s,'OPEN')
            """, (code, rule["object_type"], x["object_id"], rule["severity"],
                  x["message"]))
            opened += 1

        # 已不再命中的 OPEN 问题自动关闭
        stale = fetch_all(conn, """
            SELECT id, object_id FROM data_quality_issue
             WHERE rule_code=%s AND status='OPEN'
        """, (code,))
        for s in stale:
            if str(s["object_id"]) not in current_ids:
                execute(conn, """
                    UPDATE data_quality_issue
                       SET status='RESOLVED', resolved_at=now()
                     WHERE id=%s
                """, (s["id"],))
                closed += 1

    summary = {"rules_run": len(found), "issues_opened": opened,
               "issues_auto_resolved": closed,
               "by_rule": {k: len(v) for k, v in found.items()}}

    if actor is not None:
        audit.write(conn, action="QUALITY_SCAN", user_id=str(actor["user_id"]),
                    username=actor["username"], object_type="DATA_QUALITY",
                    new_value=summary, session_id=str(actor.get("session_id")),
                    client_ip=actor.get("client_ip"))
    return summary


# --- 各规则的具体检查。返回 [{object_id, message}] ---

def _dq_pn_001(conn):
    """Released P/N 缺少主设计定义 — INV-022。"""
    return fetch_all(conn, """
        SELECT pn.id AS object_id,
               pn.full_part_number || ' 已发布但缺少 PRIMARY_DEFINITION 主设计定义'
                 AS message
          FROM part_number pn
         WHERE pn.lifecycle_status = 'RELEASED'
           AND NOT EXISTS (
                 SELECT 1 FROM design_baseline db
                   JOIN baseline_item bi ON bi.design_baseline_id = db.id
                  WHERE db.id = pn.current_baseline_id
                    AND bi.item_type='FILE_REVISION'
                    AND bi.item_role='PRIMARY_DEFINITION')
    """)


def _dq_pn_002(conn):
    """Released P/N 缺少 Current Baseline — INV-013。"""
    return fetch_all(conn, """
        SELECT id AS object_id,
               full_part_number || ' 已发布但没有 Current Baseline' AS message
          FROM part_number
         WHERE lifecycle_status='RELEASED' AND current_baseline_id IS NULL
    """)


def _dq_pn_003(conn):
    """P/N 缺少关键属性。"""
    return fetch_all(conn, """
        SELECT pn.id AS object_id,
               pn.full_part_number || ' 缺少关键属性: ' ||
               string_agg(ad.name_cn, ', ' ORDER BY ad.code) AS message
          FROM part_number pn
          JOIN basic_drawing_family f ON f.id = pn.basic_drawing_family_id
          JOIN attribute_template t ON t.physical_class_id = f.physical_class_id
                                   AND t.status='ACTIVE'
          JOIN attribute_template_item ti ON ti.attribute_template_id = t.id
                                         AND ti.is_key_attribute
          JOIN attribute_definition ad ON ad.id = ti.attribute_definition_id
         WHERE NOT EXISTS (
                 SELECT 1 FROM object_attribute_value av
                  WHERE av.design_object_id = pn.design_object_id
                    AND av.attribute_definition_id = ad.id)
         GROUP BY pn.id, pn.full_part_number
    """)


def _dq_pn_004(conn):
    """P/N 缺少主功能。"""
    return fetch_all(conn, """
        SELECT pn.id AS object_id,
               pn.full_part_number || ' 未指定 PRIMARY 主功能' AS message
          FROM part_number pn
         WHERE NOT EXISTS (
                 SELECT 1 FROM object_function of
                  WHERE of.design_object_id = pn.design_object_id
                    AND of.function_role='PRIMARY')
    """)


def _dq_fam_001(conn):
    """设计族边界未定义。"""
    return fetch_all(conn, """
        SELECT id AS object_id,
               basic_drawing_number || ' 的允许/排除变化范围未填写' AS message
          FROM basic_drawing_family
         WHERE status='ACTIVE'
           AND (btrim(coalesce(allowed_variation,''))=''
                OR btrim(coalesce(excluded_variation,''))='')
    """)


def _dq_bom_001(conn):
    """BOM 子项重复出现。"""
    return fetch_all(conn, """
        SELECT bh.id AS object_id,
               d.object_code || ' 的 BOM 中存在重复子项: ' ||
               string_agg(DISTINCT c.object_code, ', ') AS message
          FROM bom_header bh
          JOIN design_object d ON d.id = bh.parent_design_object_id
          JOIN bom_line bl ON bl.bom_header_id = bh.id
          JOIN design_object c ON c.id = bl.child_design_object_id
         WHERE bh.status='WORKING'
         GROUP BY bh.id, d.object_code
        HAVING count(*) > count(DISTINCT bl.child_design_object_id)
    """)


def _dq_bom_002(conn):
    """BOM 子项为草稿状态。"""
    return fetch_all(conn, """
        SELECT bh.id AS object_id,
               d.object_code || ' 的 BOM 含草稿状态子项: ' ||
               string_agg(DISTINCT c.object_code, ', ') AS message
          FROM bom_header bh
          JOIN design_object d ON d.id = bh.parent_design_object_id
          JOIN bom_line bl ON bl.bom_header_id = bh.id
          JOIN design_object c ON c.id = bl.child_design_object_id
         WHERE bh.status='WORKING' AND c.lifecycle_status='DRAFT'
         GROUP BY bh.id, d.object_code
    """)


def _dq_ext_001(conn):
    """外部件无 ACCEPTED 技术状态。"""
    return fetch_all(conn, """
        SELECT ep.id AS object_id,
               ep.external_part_number || ' 没有任何 ACCEPTED 技术状态' AS message
          FROM external_part ep
         WHERE NOT EXISTS (SELECT 1 FROM external_technical_state ets
                            WHERE ets.external_part_id = ep.id AND ets.status='ACCEPTED')
    """)


def _dq_ext_002(conn):
    """外部件缺少制造商。"""
    return fetch_all(conn, """
        SELECT id AS object_id,
               external_part_number || ' 未指定制造商' AS message
          FROM external_part WHERE manufacturer_id IS NULL
    """)


def _dq_file_002(conn):
    """已发布版次缺少 Released PDF。"""
    return fetch_all(conn, """
        SELECT fr.id AS object_id,
               df.file_number || ' Rev.' || fr.revision_number ||
               ' 已发布但未附 RELEASED_PDF 表达' AS message
          FROM file_revision fr JOIN design_file df ON df.id = fr.design_file_id
         WHERE fr.status='RELEASED'
           AND NOT EXISTS (SELECT 1 FROM revision_attachment ra
                            WHERE ra.file_revision_id = fr.id
                              AND ra.attachment_role='RELEASED_PDF')
    """)



def _dq_fam_002(conn):
    """设计族名称命中 BLOCK 级受限词。正则词由 PostgreSQL ~* 处理，普通词用 position。"""
    return fetch_all(conn, """
        SELECT DISTINCT f.id AS object_id,
               f.basic_drawing_number || ' 名称命中 BLOCK 级受限词: ' || rt.pattern AS message
          FROM basic_drawing_family f
          JOIN restricted_term rt ON rt.status='ACTIVE' AND rt.control_level='BLOCK'
         WHERE (rt.is_regex AND (f.family_name_cn ~* rt.pattern OR f.family_name_en ~* rt.pattern))
            OR (NOT rt.is_regex AND (position(lower(rt.pattern) in lower(f.family_name_cn))>0
                                  OR position(lower(rt.pattern) in lower(f.family_name_en))>0))
    """)


def _dq_bom_003(conn):
    """历史数据循环兜底。正常情况下新写入已由数据库触发器阻止。"""
    return fetch_all(conn, """
        WITH RECURSIVE walk AS (
            SELECT bh.id AS root_header, bh.parent_design_object_id AS root_obj,
                   bl.child_design_object_id AS node, ARRAY[bh.parent_design_object_id, bl.child_design_object_id] AS path,
                   (bl.child_design_object_id = bh.parent_design_object_id) AS cycle
              FROM bom_header bh JOIN bom_line bl ON bl.bom_header_id=bh.id
             WHERE bh.status='WORKING'
            UNION ALL
            SELECT w.root_header, w.root_obj, bl.child_design_object_id,
                   w.path || bl.child_design_object_id, bl.child_design_object_id = ANY(w.path)
              FROM walk w
              JOIN bom_header bh ON bh.parent_design_object_id=w.node AND bh.status='WORKING'
              JOIN bom_line bl ON bl.bom_header_id=bh.id
             WHERE NOT w.cycle AND cardinality(w.path) < 100
        )
        SELECT DISTINCT w.root_header AS object_id, d.object_code || ' 的工作 BOM 存在循环' AS message
          FROM walk w JOIN design_object d ON d.id=w.root_obj WHERE w.cycle
    """)


def _dq_file_001(conn):
    return fetch_all(conn, """
        SELECT ra.id AS object_id, df.file_number || ' / ' || ra.filename ||
               ' 附件完整性异常: ' || ra.integrity_status AS message
          FROM revision_attachment ra
          JOIN file_revision fr ON fr.id=ra.file_revision_id
          JOIN design_file df ON df.id=fr.design_file_id
         WHERE fr.status IN ('RELEASED','SUPERSEDED')
           AND ra.integrity_status IN ('MISMATCH','MISSING')
    """)


def _dq_num_001(conn):
    """每个设计族用一条 issue 汇总未占用 Dash 空缺。"""
    return fetch_all(conn, """
        WITH used AS (
            SELECT basic_drawing_family_id AS fid, numeric_sequence AS n
              FROM number_allocation WHERE number_type='DASH' AND numeric_sequence IS NOT NULL
        ), span AS (SELECT fid, max(n) hi FROM used GROUP BY fid),
        gaps AS (
            SELECT s.fid, count(*) AS n
              FROM span s CROSS JOIN LATERAL generate_series(1,s.hi) g(x)
             WHERE NOT EXISTS (SELECT 1 FROM used u WHERE u.fid=s.fid AND u.n=g.x)
             GROUP BY s.fid
        )
        SELECT f.id AS object_id, f.basic_drawing_number || ' 存在 ' || gaps.n || ' 个未占用 Dash 空缺' AS message
          FROM gaps JOIN basic_drawing_family f ON f.id=gaps.fid WHERE gaps.n>0
    """)

_CHECKS = {
    "DQ-PN-001": _dq_pn_001, "DQ-PN-002": _dq_pn_002, "DQ-PN-003": _dq_pn_003,
    "DQ-PN-004": _dq_pn_004, "DQ-FAM-001": _dq_fam_001, "DQ-FAM-002": _dq_fam_002,
    "DQ-BOM-001": _dq_bom_001, "DQ-BOM-002": _dq_bom_002, "DQ-BOM-003": _dq_bom_003,
    "DQ-EXT-001": _dq_ext_001, "DQ-EXT-002": _dq_ext_002,
    "DQ-FILE-001": _dq_file_001, "DQ-FILE-002": _dq_file_002, "DQ-NUM-001": _dq_num_001,
}


# ---------------------------------------------------------------------
# 问题处置
# ---------------------------------------------------------------------
def list_issues(conn: psycopg.Connection, *, severity: str | None = None,
                rule_code: str | None = None, status: str = "OPEN",
                limit: int = 200) -> list[dict]:
    return fetch_all(conn, """
        SELECT i.id, i.rule_code, r.name_cn AS rule_name, i.object_type, i.object_id,
               i.severity, i.message, i.effective_status AS status, i.detected_at,
               i.waiver_reason, i.waiver_expires_at, r.blocks_release
          FROM v_data_quality_effective i
          JOIN data_quality_rule r ON r.code = i.rule_code
         WHERE (%s::text IS NULL OR i.severity = %s)
           AND (%s::text IS NULL OR i.rule_code = %s)
           AND (%s::text IS NULL OR i.effective_status = %s)
         ORDER BY CASE i.severity WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END,
                  i.detected_at DESC
         LIMIT %s
    """, (severity, severity, rule_code, rule_code, status, status, limit))


def resolve_issue(conn: psycopg.Connection, issue_id: str, actor: dict) -> None:
    row = fetch_one(conn, "SELECT rule_code, message, status FROM data_quality_issue "
                          "WHERE id=%s", (issue_id,))
    if row is None:
        raise LookupError("数据质量问题不存在")
    execute(conn, """
        UPDATE data_quality_issue SET status='RESOLVED', resolved_at=now(), resolved_by=%s
         WHERE id=%s
    """, (actor["user_id"], issue_id))
    audit.write(conn, action="QUALITY_RESOLVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DATA_QUALITY_ISSUE",
                object_id=issue_id, object_code=row["rule_code"],
                old_value={"status": row["status"]}, new_value={"status": "RESOLVED"},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))


def waive_issue(conn: psycopg.Connection, issue_id: str, reason: str,
                expires_days: int, actor: dict) -> None:
    """豁免一个问题。必须给理由与有效期 —— 无期限豁免等于把规则关掉。"""
    if expires_days < 1 or expires_days > 365:
        raise ValueError("豁免有效期须在 1~365 天之间")
    row = fetch_one(conn, "SELECT rule_code, severity FROM data_quality_issue WHERE id=%s",
                    (issue_id,))
    if row is None:
        raise LookupError("数据质量问题不存在")
    execute(conn, """
        UPDATE data_quality_issue
           SET status='WAIVED', waiver_reason=%s, waived_by=%s, waived_at=now(),
               waiver_expires_at = now() + make_interval(days => %s)
         WHERE id=%s
    """, (reason, actor["user_id"], expires_days, issue_id))
    audit.write(conn, action="QUALITY_WAIVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DATA_QUALITY_ISSUE",
                object_id=issue_id, object_code=row["rule_code"],
                new_value={"status": "WAIVED", "expires_days": expires_days},
                reason=reason, session_id=str(actor.get("session_id")),
                client_ip=actor.get("client_ip"))


# ---------------------------------------------------------------------
# 报表 — SRS-RPT-001
# ---------------------------------------------------------------------
def dashboard(conn: psycopg.Connection) -> dict:
    return {
        "objects": fetch_one(conn, """
            SELECT count(*) FILTER (WHERE object_type='INTERNAL_PART') AS internal_parts,
                   count(*) FILTER (WHERE object_type='EXTERNAL_PART') AS external_parts,
                   count(*) FILTER (WHERE object_type='SOFTWARE') AS software,
                   count(*) FILTER (WHERE lifecycle_status='DRAFT') AS draft,
                   count(*) FILTER (WHERE lifecycle_status='RELEASED') AS released,
                   count(*) FILTER (WHERE lifecycle_status='OBSOLETE') AS obsolete,
                   count(*) FILTER (WHERE data_origin='LEGACY') AS legacy
              FROM design_object
        """),
        "families": fetch_one(conn, """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status='PENDING') AS pending,
                   count(*) FILTER (WHERE status='ACTIVE') AS active,
                   count(*) FILTER (WHERE status='CLOSED') AS closed
              FROM basic_drawing_family
        """),
        "baselines": fetch_one(conn, """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status='DRAFT') AS draft,
                   count(*) FILTER (WHERE status='IN_REVIEW') AS in_review,
                   count(*) FILTER (WHERE status='RELEASED') AS released,
                   count(*) FILTER (WHERE is_current) AS current
              FROM design_baseline
        """),
        "quality": fetch_one(conn, """
            SELECT count(*) FILTER (WHERE effective_status='OPEN' AND severity='ERROR')
                     AS open_errors,
                   count(*) FILTER (WHERE effective_status='OPEN' AND severity='WARNING')
                     AS open_warnings,
                   count(*) FILTER (WHERE status='WAIVED'
                                     AND waiver_expires_at > now()) AS active_waivers
              FROM v_data_quality_effective
        """),
        "integrity": fetch_one(conn, """
            SELECT count(*) FILTER (WHERE integrity_status='OK') AS ok,
                   count(*) FILTER (WHERE integrity_status='MISMATCH') AS mismatch,
                   count(*) FILTER (WHERE integrity_status='MISSING') AS missing,
                   count(*) FILTER (WHERE integrity_status='UNKNOWN') AS never_checked
              FROM revision_attachment
        """),
    }


def number_utilization(conn: psycopg.Connection) -> list[dict]:
    """号码占用率 — 帮助判断某个族的 Dash 空间是否快用尽。"""
    return fetch_all(conn, """
        SELECT f.basic_drawing_number, f.family_name_cn, f.status,
               count(*) FILTER (WHERE na.status='ALLOCATED') AS allocated,
               count(*) FILTER (WHERE na.status='RESERVED') AS reserved,
               count(*) FILTER (WHERE na.status='CANCELLED') AS cancelled,
               count(*) AS occupied,
               999 - count(*) AS remaining,
               round(count(*)::numeric * 100 / 999, 1) AS utilization_pct
          FROM basic_drawing_family f
          LEFT JOIN number_allocation na
                 ON na.basic_drawing_family_id = f.id AND na.number_type='DASH'
         GROUP BY f.id, f.basic_drawing_number, f.family_name_cn, f.status
        HAVING count(na.id) > 0
         ORDER BY occupied DESC
    """)


def function_distribution(conn: psycopg.Connection) -> list[dict]:
    """按功能域统计对象分布 — FUN-SYS-007, 也是功能词典复查的依据。"""
    return fetch_all(conn, """
        SELECT fd.code AS domain_code, fd.name_cn AS domain_name,
               count(*) FILTER (WHERE of.function_role='PRIMARY') AS primary_count,
               count(*) FILTER (WHERE of.function_role='AUXILIARY') AS auxiliary_count
          FROM function_domain fd
          LEFT JOIN function_item fi ON fi.domain_code = fd.code
          LEFT JOIN object_function of ON of.function_item_id = fi.id
         GROUP BY fd.code, fd.name_cn, fd.sort_order
         ORDER BY fd.sort_order
    """)


def classification_distribution(conn: psycopg.Connection) -> list[dict]:
    """按二级物理分类统计 — CLS-SYS-007 要求 XX-99 使用频次可统计。"""
    return fetch_all(conn, """
        SELECT pc.code, pc.name_cn, pc.primary_class_code,
               count(f.id) AS family_count,
               (pc.code LIKE '%-99') AS is_fallback
          FROM physical_class pc
          LEFT JOIN basic_drawing_family f ON f.physical_class_id = pc.id
         GROUP BY pc.id, pc.code, pc.name_cn, pc.primary_class_code, pc.sort_order
         ORDER BY pc.sort_order
    """)


def release_activity(conn: psycopg.Connection, days: int = 90) -> list[dict]:
    """近期发布活动 — 按周统计基线与版次发布量。"""
    return fetch_all(conn, """
        SELECT date_trunc('week', d)::date AS week,
               count(*) FILTER (WHERE kind='BASELINE') AS baselines,
               count(*) FILTER (WHERE kind='REVISION') AS revisions
          FROM (
            SELECT released_at AS d, 'BASELINE' AS kind FROM design_baseline
             WHERE released_at > now() - make_interval(days => %s)
            UNION ALL
            SELECT released_at, 'REVISION' FROM file_revision
             WHERE released_at > now() - make_interval(days => %s)
          ) x
         GROUP BY 1 ORDER BY 1
    """, (days, days))
