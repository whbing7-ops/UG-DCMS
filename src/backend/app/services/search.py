"""统一检索 — SRS-SRH-001~003 / AC-SEARCH-01 / AT-008。

检索分三层, 按确定性从高到低依次尝试:

  1. **精确命中**  完整件号、文件号、基本图号完全相等 —— 使用者输入完整编号时,
     他要的就是那一个对象, 不该被一堆相似结果淹没。
  2. **归一命中**  去掉分隔符与前导零后相等。AT-008 的场景: 现场记的是 ZD850-3,
     库里存的是 ZD8503 或 0ZD8503, 三者必须互相找得到。
  3. **模糊匹配**  pg_trgm 子串与相似度。中文靠这一层, 因为 PostgreSQL 内置分词器
     不切分中文(见迁移 0011 的说明)。

三层结果合并后按 match_type 与 similarity 排序, 精确命中永远排在最前。
"""
from __future__ import annotations

import psycopg

from ..db import execute, fetch_all, fetch_one

# 相似度下限。太低会让"支"匹配到几百个无关对象, 太高则错一个字就找不到。
SIMILARITY_THRESHOLD = 0.25
MAX_RESULTS = 100


def search(conn: psycopg.Connection, q: str, *, kinds: list[str] | None = None,
           limit: int = 50) -> dict:
    """跨对象类型统一检索。

    kinds 可选值: PART_NUMBER / EXTERNAL_PART / SOFTWARE / FAMILY / FILE / ATTACHMENT。
    不传则全部检索。
    """
    q = (q or "").strip()
    if not q:
        return {"query": q, "total": 0, "results": []}

    kinds = kinds or ["PART_NUMBER", "EXTERNAL_PART", "SOFTWARE", "FAMILY", "FILE", "ATTACHMENT"]
    limit = min(limit, MAX_RESULTS)
    results: list[dict] = []

    if any(k in kinds for k in ("PART_NUMBER", "EXTERNAL_PART", "SOFTWARE")):
        # 先跑精确/归一/交叉引用三类等值查找 —— 它们都走索引, 代价极低。
        # 命中即返回, 不再做模糊扫描: 输入完整件号的人要的就是那一个对象,
        # 而模糊分支要对全表算 similarity(), 在 5 万件规模下要几百毫秒,
        # 并发时会成为整个系统的瓶颈。
        results += _search_objects_precise(conn, q, kinds, limit)
        if not results:
            results += _search_objects_fuzzy(conn, q, kinds, limit)
    if "FAMILY" in kinds:
        results += _search_families(conn, q, limit)
    if "FILE" in kinds:
        results += _search_files(conn, q, limit)
    if "ATTACHMENT" in kinds:
        results += _search_attachments(conn, q, limit)

    order = {"EXACT": 0, "NORMALIZED": 1, "CROSS_REFERENCE": 2, "FUZZY": 3}
    results.sort(key=lambda r: (order.get(r["match_type"], 9), -r["score"],
                                r["object_code"]))
    return {"query": q, "total": len(results), "results": results[:limit]}


_TYPE_MAP = {"PART_NUMBER": "INTERNAL_PART", "EXTERNAL_PART": "EXTERNAL_PART",
             "SOFTWARE": "SOFTWARE"}


def _search_objects_precise(conn: psycopg.Connection, q: str, kinds: list[str],
                            limit: int) -> list[dict]:
    """等值查找: 编号完全一致 / 归一后一致 / 交叉引用命中。全部走索引。"""
    types = [_TYPE_MAP[k] for k in kinds if k in _TYPE_MAP]
    return fetch_all(conn, """
        WITH cand AS (
            -- 1 精确命中
            SELECT d.id, d.object_code, d.display_name, d.object_type,
                   d.lifecycle_status, 'EXACT'::text AS match_type, 1.0::real AS score,
                   NULL::text AS matched_via
              FROM design_object d
             WHERE d.object_type = ANY(%(types)s) AND d.object_code = %(q)s

            UNION ALL
            -- 2 归一命中: 分隔符与前导零差异
            SELECT d.id, d.object_code, d.display_name, d.object_type,
                   d.lifecycle_status, 'NORMALIZED', 0.95, d.object_code
              FROM design_object d
             WHERE d.object_type = ANY(%(types)s)
               AND d.object_code <> %(q)s
               AND dcms_normalize_identifier(d.object_code)
                   = dcms_normalize_identifier(%(q)s)

            UNION ALL
            -- 3 交叉引用命中 — AT-008: 历史件号定位当前对象
            SELECT d.id, d.object_code, d.display_name, d.object_type,
                   d.lifecycle_status, 'CROSS_REFERENCE', 0.9,
                   cr.reference_type || ': ' || cr.reference_value
              FROM cross_reference cr
              JOIN design_object d ON d.id = cr.design_object_id
             WHERE d.object_type = ANY(%(types)s)
               AND cr.normalized_value = dcms_normalize_identifier(%(q)s)

            UNION ALL
            -- 4 外部件号命中(含归一)
            SELECT d.id, d.object_code, d.display_name, d.object_type,
                   d.lifecycle_status, 'NORMALIZED', 0.92,
                   'External P/N: ' || ep.external_part_number
              FROM external_part ep
              JOIN design_object d ON d.id = ep.design_object_id
             WHERE d.object_type = ANY(%(types)s)
               AND dcms_normalize_identifier(ep.external_part_number)
                   = dcms_normalize_identifier(%(q)s)

        ),
        -- DISTINCT ON 必须按 id 排序, 但截断必须按匹配质量排序。若把 LIMIT 直接放在
        -- DISTINCT ON 查询上, 截断依据就成了 id(实为随机 uuid)顺序 —— 候选多时
        -- 精确命中会被直接丢掉, 表现为"输入完整件号反而搜不到"。故分两层:
        -- 内层按 id 去重并保留最优匹配类型, 外层按质量排序后再截断。
        dedup AS (
            SELECT DISTINCT ON (id) id, object_code, display_name, object_type,
                   lifecycle_status, match_type, score, matched_via,
                   CASE match_type WHEN 'EXACT' THEN 0 WHEN 'NORMALIZED' THEN 1
                                   WHEN 'CROSS_REFERENCE' THEN 2 ELSE 3 END AS rank
              FROM cand
             ORDER BY id, rank, score DESC
        )
        SELECT id::text, object_code, display_name, object_type, lifecycle_status,
               match_type, score::real, matched_via,
               CASE object_type WHEN 'INTERNAL_PART' THEN 'PART_NUMBER'
                                WHEN 'EXTERNAL_PART' THEN 'EXTERNAL_PART'
                                ELSE 'SOFTWARE' END AS kind
          FROM dedup
         ORDER BY rank, score DESC, object_code
         LIMIT %(limit)s
    """, {"types": types, "q": q, "limit": limit})


def _search_families(conn: psycopg.Connection, q: str, limit: int) -> list[dict]:
    return fetch_all(conn, """
        SELECT f.id::text, f.basic_drawing_number AS object_code,
               f.family_name_cn AS display_name, 'BASIC_DRAWING_FAMILY' AS object_type,
               f.status AS lifecycle_status,
               CASE WHEN f.basic_drawing_number = %(q)s THEN 'EXACT' ELSE 'FUZZY' END
                 AS match_type,
               CASE WHEN f.basic_drawing_number = %(q)s THEN 1.0
                    ELSE GREATEST(similarity(f.basic_drawing_number, %(q)s),
                                  similarity(f.family_name_cn, %(q)s)) END::real AS score,
               NULL::text AS matched_via, 'FAMILY' AS kind
          FROM basic_drawing_family f
         WHERE f.basic_drawing_number = %(q)s
            OR f.basic_drawing_number ILIKE %(like)s
            OR f.family_name_cn ILIKE %(like)s
            OR similarity(f.family_name_cn, %(q)s) > %(thr)s
         ORDER BY score DESC LIMIT %(limit)s
    """, {"q": q, "like": f"%{q}%", "thr": SIMILARITY_THRESHOLD, "limit": limit})


def _search_files(conn: psycopg.Connection, q: str, limit: int) -> list[dict]:
    return fetch_all(conn, """
        SELECT df.id::text, df.file_number AS object_code,
               df.title_cn AS display_name, 'DESIGN_FILE' AS object_type,
               df.status AS lifecycle_status,
               CASE WHEN df.file_number = %(q)s THEN 'EXACT' ELSE 'FUZZY' END AS match_type,
               CASE WHEN df.file_number = %(q)s THEN 1.0
                    ELSE GREATEST(similarity(df.file_number, %(q)s),
                                  similarity(df.title_cn, %(q)s)) END::real AS score,
               NULL::text AS matched_via, 'FILE' AS kind
          FROM design_file df
         WHERE df.file_number = %(q)s
            OR df.file_number ILIKE %(like)s
            OR df.title_cn ILIKE %(like)s
            OR similarity(df.title_cn, %(q)s) > %(thr)s
         ORDER BY score DESC LIMIT %(limit)s
    """, {"q": q, "like": f"%{q}%", "thr": SIMILARITY_THRESHOLD, "limit": limit})


def _search_attachments(conn: psycopg.Connection, q: str, limit: int) -> list[dict]:
    return fetch_all(conn, """
        SELECT ra.id::text, df.file_number AS object_code,
               ra.filename AS display_name, 'REVISION_ATTACHMENT' AS object_type,
               fr.status AS lifecycle_status,
               CASE WHEN ra.filename=%(q)s THEN 'EXACT' ELSE 'FUZZY' END AS match_type,
               CASE WHEN ra.filename=%(q)s THEN 1.0
                    ELSE GREATEST(similarity(ra.filename,%(q)s),
                                  similarity(COALESCE(ra.search_text,''),%(q)s)) END::real AS score,
               df.file_number || ' Rev.' || fr.revision_number || ' · ' ||
                 ra.attachment_role AS matched_via,
               'ATTACHMENT' AS kind, fr.id::text AS revision_id
          FROM revision_attachment ra
          JOIN file_revision fr ON fr.id=ra.file_revision_id
          JOIN design_file df ON df.id=fr.design_file_id
         WHERE ra.filename ILIKE %(like)s
            OR COALESCE(ra.search_text,'') ILIKE %(like)s
            OR similarity(ra.filename,%(q)s)>%(thr)s
         ORDER BY score DESC LIMIT %(limit)s
    """, {"q": q, "like": f"%{q}%", "thr": SIMILARITY_THRESHOLD, "limit": limit})


# ---------------------------------------------------------------------
# 对象全貌 — 检索结果点进去看到的东西
# ---------------------------------------------------------------------
def object_overview(conn: psycopg.Connection, object_code: str) -> dict | None:
    obj = fetch_one(conn, """
        SELECT d.*, pn.full_part_number, pn.formal_name_cn, pn.dash_number,
               f.basic_drawing_number, f.family_name_cn,
               db.baseline_code AS current_baseline_code
          FROM design_object d
          LEFT JOIN part_number pn ON pn.design_object_id = d.id
          LEFT JOIN basic_drawing_family f ON f.id = pn.basic_drawing_family_id
          LEFT JOIN design_baseline db ON db.id = pn.current_baseline_id
         WHERE d.object_code = %s
    """, (object_code,))
    if obj is None:
        return None

    oid = str(obj["id"])
    obj["functions"] = fetch_all(conn, """
        SELECT fi.code, fi.name_cn, of.function_role, fd.code AS domain_code,
               fd.name_cn AS domain_name
          FROM object_function of
          JOIN function_item fi ON fi.id = of.function_item_id
          JOIN function_domain fd ON fd.code = fi.domain_code
         WHERE of.design_object_id = %s ORDER BY of.function_role DESC, fi.code
    """, (oid,))
    obj["cross_references"] = fetch_all(conn, """
        SELECT reference_type, reference_value, normalized_value
          FROM cross_reference WHERE design_object_id = %s ORDER BY reference_type
    """, (oid,))
    obj["attributes"] = fetch_all(conn, """
        SELECT ad.code, ad.name_cn, ad.data_type, av.value_text, av.value_number,
               av.value_integer, av.value_boolean, av.value_date, av.value_enum_code,
               av.unit_code
          FROM object_attribute_value av
          JOIN attribute_definition ad ON ad.id = av.attribute_definition_id
         WHERE av.design_object_id = %s ORDER BY ad.code
    """, (oid,))
    obj["quality_issues"] = fetch_all(conn, """
        SELECT rule_code, severity, message, effective_status AS status, detected_at
          FROM v_data_quality_effective
         WHERE object_id = %s AND effective_status = 'OPEN'
         ORDER BY severity, detected_at DESC
    """, (oid,))
    return obj


def record_access(conn: psycopg.Connection, user_id: str, obj: dict) -> None:
    """记录最近访问 — UI §3。冲突时只更新时间, 不堆积历史。"""
    execute(conn, """
        INSERT INTO recent_access (user_id, object_type, object_id, object_code, display_name)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (user_id, object_type, object_id)
        DO UPDATE SET accessed_at = now()
    """, (user_id, obj["object_type"], obj["id"], obj["object_code"],
          obj["display_name"]))


def recent(conn: psycopg.Connection, user_id: str, limit: int = 20) -> list[dict]:
    return fetch_all(conn, """
        SELECT object_type, object_id::text, object_code, display_name, accessed_at
          FROM recent_access WHERE user_id = %s
         ORDER BY accessed_at DESC LIMIT %s
    """, (user_id, limit))


def _search_objects_fuzzy(conn: psycopg.Connection, q: str, kinds: list[str],
                          limit: int) -> list[dict]:
    """模糊匹配。只在等值查找无结果时执行。

    条件写成两段 UNION 而非四个 OR: OR 会让规划器放弃索引改走全表扫描,
    在 5 万件规模下从毫秒级退化到几百毫秒。分开写让每段各自用上 trgm 索引。
    """
    types = [_TYPE_MAP[k] for k in kinds if k in _TYPE_MAP]
    return fetch_all(conn, """
        WITH cand AS (
            -- 每段各自 LIMIT 需要用子查询包住: SQL 里 LIMIT 之后不能直接接 UNION
            (SELECT * FROM (
                SELECT d.id, d.object_code, d.display_name, d.object_type, d.lifecycle_status
                  FROM design_object d
                 WHERE d.object_type = ANY(%(types)s) AND d.object_code ILIKE %(like)s
                 LIMIT %(scan)s) a)
            UNION
            (SELECT * FROM (
                SELECT d.id, d.object_code, d.display_name, d.object_type, d.lifecycle_status
                  FROM design_object d
                 WHERE d.object_type = ANY(%(types)s) AND d.display_name ILIKE %(like)s
                 LIMIT %(scan)s) b)
        )
        SELECT id::text, object_code, display_name, object_type, lifecycle_status,
               'FUZZY'::text AS match_type,
               GREATEST(similarity(object_code, %(q)s),
                        similarity(display_name, %(q)s))::real AS score,
               NULL::text AS matched_via,
               CASE object_type WHEN 'INTERNAL_PART' THEN 'PART_NUMBER'
                                WHEN 'EXTERNAL_PART' THEN 'EXTERNAL_PART'
                                ELSE 'SOFTWARE' END AS kind
          FROM cand
         ORDER BY score DESC, object_code
         LIMIT %(limit)s
    """, {"types": types, "q": q, "like": f"%{q}%",
          "scan": MAX_RESULTS * 10, "limit": limit})
