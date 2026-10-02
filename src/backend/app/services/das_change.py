"""M4 设计更改分类（第二批）。

依据 UG-DAP-06《设计更改分类程序》第 6 章（7 步）、UG-DAW-010《设计更改分类判据使用
说明和案例》第 1～5 章、表-21-173（UG-DAF-02），设计输入第 8.8 节。

【三项分类是三项，不是一个字段】
UG-DAP-06 第 3 步：按技术影响分别形成**大改／小改、声学／非声学、排放／非排放三项建议
和依据**。而后两项本单位**没有申请批准权**——第 4 步要求由适航管理负责人提交局方取得
所需结论，在 UG-DAF-02 记录编号、日期及限制。所以声学与排放在系统里不能由内部人员给
结论，只能登记局方结论：内部给了，就是在没有权利的事项上作了批准。

【两条看着矛盾、其实管的是两件事】
- UG-DAW-010 第 1 章：**无法判定的按重大更改处理**，直至适航管理负责人或局方确认。
- UG-DAP-06 第 4 步：**超出分类或批准权限时转局方办理，不能仅因超权限自动判为大改。**

第一条说技术上判不了，第二条说权限上不够。把两者合成一个"拿不准就判大改"的分支，
就会在签署人权限不足时自动升级为重大更改——那是用权限问题冒充技术判断，而重大更改要走
UG-DAP-09 向局方申请批准，等于凭一个权限缺口给产品加了一道审定。所以是两个状态：
`UNDETERMINED`（技术判不了，按大改处理、**不得签署**，待确认）与
`BEYOND_AUTHORITY`（超权限，**结论留空**、转局方办理）。

【判据里不放技术阈值】
"重量与平衡"原文曾有"单项重量变化超过【】kg／重心移动超过【】%MAC"，待澄清项第 9 条
已决策关闭：**不填，删除该子句**——体系文件是制度不是技术标准（判据 P5）。所以判据表
没有阈值字段：加一个就等于把那条被删掉的子句搬进系统，而且它会变成一个没人维护、
却被当成判定依据的数。

【累计影响】
UG-DAW-010 第 1 章：一系列小改累积后可能构成重大更改，须与此前的更改合并评估。所以
合并评估做成关联记录而不是一个"已考虑"的布尔——布尔勾完说不出跟哪几次一起评的，
而 `das_change_cumulative_gap` 列出的正是"一串小改永远是小改"会发生的地方。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

VERDICTS = {"SIGNIFICANT": "显著影响", "NO_IMPACT": "无影响",
            "NOT_APPLICABLE": "不适用"}
STATES = {
    "CLASSIFIED": "已分类并签署",
    "UNDETERMINED": "技术上判不了（按重大更改处理，待确认）",
    "BEYOND_AUTHORITY": "超出分类或批准权限（转局方办理，结论留空）",
    "CAAC_DISAGREED": "局方有不同意见（以局方意见为准）",
}
POSITION_CN = {"DE": "设计工程师", "AWM": "适航管理负责人", "DCM": "设计资料管理负责人",
               "PM": "项目负责人"}


def _holds(conn: psycopg.Connection, actor: dict, code: str) -> bool:
    return scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code=%s""",
                  (actor["user_id"], code)) > 0


def _require(conn: psycopg.Connection, actor: dict, codes, what: str,
             basis: str) -> None:
    if isinstance(codes, str):
        codes = (codes,)
    if not any(_holds(conn, actor, c) for c in codes):
        who = "或".join(POSITION_CN[c] for c in codes)
        raise PermissionError(f"{what}须由在任{who}执行（{basis}）。"
                              f"请先按 UG-DAP-03 完成任命。")


def criteria(conn: psycopg.Connection) -> list[dict]:
    """UG-DAW-010 第 2 章的 9 项判据。"""
    return fetch_all(conn, "SELECT * FROM das_change_criterion ORDER BY seq")


def changes(conn: psycopg.Connection, *, state: str | None = None) -> list[dict]:
    return fetch_all(conn, """
        SELECT d.*, p.project_no, cl.state, cl.major_minor, cl.form_no,
               cl.acoustic_result, cl.emission_result,
               cov.assessed, cov.criteria_total, cov.significant,
               cov.missing_items
          FROM das_design_change d
          LEFT JOIN das_project p ON p.id = d.project_id
          LEFT JOIN das_change_classification cl ON cl.change_id = d.id
          LEFT JOIN das_change_criterion_coverage cov ON cov.change_id = d.id
         WHERE (%s::text IS NULL OR cl.state = %s)
         ORDER BY d.change_no
    """, (state, state))


def change(conn: psycopg.Connection, change_no: str) -> dict:
    row = fetch_one(conn, """SELECT d.*, p.project_no FROM das_design_change d
                              LEFT JOIN das_project p ON p.id = d.project_id
                             WHERE d.change_no = %s""", (change_no,))
    if not row:
        raise LookupError(f"没有编号为 {change_no} 的更改申请")
    row["assessments"] = fetch_all(conn, """
        SELECT a.*, c.name_cn, c.seq, c.significant_when, c.evidence_required
          FROM das_change_criterion_assessment a
          JOIN das_change_criterion c ON c.code = a.criterion_code
         WHERE a.change_id = %s ORDER BY c.seq""", (row["id"],))
    row["coverage"] = fetch_one(conn, """SELECT * FROM das_change_criterion_coverage
                                          WHERE change_id = %s""", (row["id"],))
    row["classification"] = fetch_one(conn, """SELECT * FROM das_change_classification
                                                 WHERE change_id = %s""", (row["id"],))
    row["cumulative"] = fetch_all(conn, """
        SELECT cu.*, o.change_no AS prior_change_no, o.title AS prior_title
          FROM das_change_cumulative cu
          JOIN das_design_change o ON o.id = cu.prior_change_id
         WHERE cu.change_id = %s""", (row["id"],))
    if row["classification"]:
        row["classification_history"] = fetch_all(
            conn, """SELECT * FROM das_change_classification_change
                      WHERE classification_id = %s ORDER BY changed_at""",
            (row["classification"]["id"],))
    return row


def _by_no(conn: psycopg.Connection, change_no: str) -> dict:
    row = fetch_one(conn, """SELECT id, change_no, project_id, impact_list
                               FROM das_design_change WHERE change_no = %s""",
                    (change_no,))
    if not row:
        raise LookupError(f"没有编号为 {change_no} 的更改申请")
    return row


def raise_change(conn: psycopg.Connection, *, change_no: str, title: str, purpose: str,
                 content: str, products: str, drawings: str,
                 project_no: str | None = None, raised_on: dt.date | None = None,
                 actor: dict) -> dict:
    """提出更改（UG-DAP-06 第 1 步，设计工程师）。"""
    _require(conn, actor, "DE", "提出设计更改申请",
             "UG-DAP-06 第 1 步：设计工程师填写更改申请")
    for name, val in (("标题", title), ("目的", purpose), ("内容", content),
                      ("涉及产品", products), ("涉及图号", drawings)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（UG-DAP-06 第 1 步：说明目的、内容、"
                             f"涉及产品和图号）")
    pid = None
    if project_no:
        p = fetch_one(conn, "SELECT id FROM das_project WHERE project_no=%s",
                      (project_no,))
        if not p:
            raise LookupError(f"没有编号为 {project_no} 的项目")
        pid = p["id"]
    row = fetch_one(conn, """
        INSERT INTO das_design_change
               (change_no, project_id, title, purpose, content, products, drawings,
                raised_by, raised_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, change_no, title, raised_on
    """, (change_no.strip(), pid, title.strip(), purpose.strip(), content.strip(),
          products.strip(), drawings.strip(), actor["user_id"],
          raised_on or dt.date.today()))
    audit.write(conn, action="DAS_CHANGE_RAISE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DESIGN_CHANGE",
                object_id=str(row["id"]), object_code=change_no,
                new_value={"title": title.strip(), "project_no": project_no},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def record_impact(conn: psycopg.Connection, *, change_no: str, impact_list: str,
                  impact_baseline_ref: str, ad_checked: bool,
                  cert_basis_checked: bool, impact_on: dt.date | None = None,
                  actor: dict) -> dict:
    """影响分析（UG-DAP-06 第 2 步）。

    四项要齐: 影响清单、查过的基线与引用关系、AD 适用性、现行审定基础。
    做了一半的影响分析比没做更糟 —— 它会让下一步以为分析过了（ck_ddc_impact）。
    """
    _require(conn, actor, "DE", "登记影响分析",
             "UG-DAP-06 第 2 步：依据《构型项目清单》列出受影响对象")
    c = _by_no(conn, change_no)
    if c["impact_list"]:
        raise ValueError(f"{change_no} 的影响分析已登记，更正请按 UG-DAP-06 另提更改")
    for name, val in (("影响清单", impact_list), ("基线与引用关系", impact_baseline_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    if not ad_checked or not cert_basis_checked:
        no = [n for n, v in (("适航指令适用性", ad_checked),
                             ("现行审定基础", cert_basis_checked)) if not v]
        raise ValueError(
            f"「{'』『'.join(no)}」尚未核对完（UG-DAP-06 第 2 步：核对是否涉及适航指令"
            f"和现行审定基础）。没核对完就登记影响分析，下一步会以为分析过了。")
    execute(conn, """UPDATE das_design_change
                        SET impact_list=%s, impact_baseline_ref=%s, ad_checked=%s,
                            cert_basis_checked=%s, impact_by=%s, impact_on=%s
                      WHERE id=%s""",
            (impact_list.strip(), impact_baseline_ref.strip(), ad_checked,
             cert_basis_checked, actor["user_id"], impact_on or dt.date.today(),
             c["id"]))
    audit.write(conn, action="DAS_CHANGE_IMPACT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DESIGN_CHANGE",
                object_id=str(c["id"]), object_code=change_no,
                new_value={"impact_list": impact_list.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, """SELECT change_no, impact_list, impact_on
                                FROM das_design_change WHERE id=%s""", (c["id"],))


def assess_criterion(conn: psycopg.Connection, *, change_no: str, criterion_code: str,
                     verdict: str, rationale: str, evidence_ref: str | None = None,
                     actor: dict) -> dict:
    """对一项判据给结论（UG-DAW-010 第 3 章第 3 步）。

    原文: 按第 2 章逐条判据打勾并写明依据, **不得只写"无影响"而无理由**。
    """
    _require(conn, actor, "DE", "给判据结论",
             "UG-DAP-06 第 3 步：设计工程师形成分类建议和依据")
    c = _by_no(conn, change_no)
    if not c["impact_list"]:
        raise ValueError(
            f"{change_no} 还没有登记影响分析（UG-DAP-06 第 2 步）。"
            f"没有受影响对象清单就打判据，打的是什么影响？")
    cr = fetch_one(conn, """SELECT code, name_cn, significant_when, evidence_required
                              FROM das_change_criterion WHERE code=%s""",
                   (criterion_code,))
    if not cr:
        raise LookupError(f"没有编号为 {criterion_code} 的判据")
    if verdict not in VERDICTS:
        raise ValueError(f"结论须为 {'/'.join(VERDICTS)} 之一")
    if not (rationale or "").strip():
        raise ValueError(
            "依据不得为空。UG-DAW-010 第 3 章：逐条判据打勾并写明依据，"
            "**不得只写「无影响」而无理由**——没有理由的「无影响」在局方复查时"
            "等于没有分类依据。")
    if verdict == "SIGNIFICANT" and not (evidence_ref or "").strip():
        raise ValueError(
            f"判为显著影响时须指出证据。UG-DAW-010 第 2 章给「{cr['name_cn']}」列的"
            f"判定证据是：{cr['evidence_required']}。")
    if scalar(conn, """SELECT count(*) FROM das_change_criterion_assessment
                        WHERE change_id=%s AND criterion_code=%s""",
              (c["id"], criterion_code)):
        raise ValueError(
            f"「{cr['name_cn']}」已给过结论，不得改写（DCMS-INV-108）。"
            f"更正请按 UG-DAP-06 另提更改并重新分类。")
    row = fetch_one(conn, """
        INSERT INTO das_change_criterion_assessment
               (change_id, criterion_code, verdict, rationale, evidence_ref, assessed_by)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, criterion_code, verdict
    """, (c["id"], criterion_code, verdict, rationale.strip(),
          (evidence_ref or "").strip() or None, actor["user_id"]))
    audit.write(conn, action="DAS_CHANGE_CRITERION", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DESIGN_CHANGE",
                object_id=str(c["id"]), object_code=f"{change_no}/{criterion_code}",
                new_value={"verdict": verdict, "rationale": rationale.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def record_cumulative(conn: psycopg.Connection, *, change_no: str,
                      prior_change_no: str, assessment: str, actor: dict) -> dict:
    """登记与此前某次更改的合并评估（UG-DAW-010 第 1 章：影响须累计考虑）。"""
    _require(conn, actor, "DE", "登记累计影响评估",
             "UG-DAW-010 第 1 章：一系列小改累积后可能构成重大更改")
    c = _by_no(conn, change_no)
    o = _by_no(conn, prior_change_no)
    if c["id"] == o["id"]:
        raise ValueError("不能跟自己做累计评估")
    if not (assessment or "").strip():
        raise ValueError("合并评估的结论不得为空")
    if scalar(conn, """SELECT count(*) FROM das_change_cumulative
                        WHERE change_id=%s AND prior_change_id=%s""",
              (c["id"], o["id"])):
        raise ValueError(f"与 {prior_change_no} 的合并评估已登记")
    row = fetch_one(conn, """
        INSERT INTO das_change_cumulative
               (change_id, prior_change_id, assessment, assessed_by)
        VALUES (%s,%s,%s,%s) RETURNING id, change_id, prior_change_id
    """, (c["id"], o["id"], assessment.strip(), actor["user_id"]))
    row["prior_change_no"] = prior_change_no
    return row


def classify(conn: psycopg.Connection, *, change_no: str, state: str,
             conclusion_reason: str, major_minor: str | None = None,
             form_no: str | None = None, caac_referral_ref: str | None = None,
             signed_on: dt.date | None = None, actor: dict) -> dict:
    """出分类结论（UG-DAP-06 第 3～5 步，UG-DAF-02）。

    闸门在数据库里（DCMS-INV-104～107）, 这里先把话说清楚:
    9 项判据少一条就不能出结论; 任一项显著影响只能是大改; 超权限结论留空不得填大改;
    判不了按大改处理但不得签署。
    """
    if state not in STATES:
        raise ValueError(f"状态须为 {'/'.join(STATES)} 之一")
    # 已分类要由授权人员签（第 4 步）; 转局方与判不了由适航管理负责人归口。
    if state == "CLASSIFIED":
        _require(conn, actor, ("DE", "AWM"), "签署分类结论",
                 "UG-DAP-06 第 4 步：具备相应有效授权的人员仅在许可权利范围内批准分类")
    else:
        _require(conn, actor, "AWM", "登记转局方办理或待确认的分类",
                 "UG-DAP-06 第 4 步：由适航管理负责人提交局方取得所需结论")
    c = _by_no(conn, change_no)
    if not (conclusion_reason or "").strip():
        raise ValueError("结论及理由不得为空（UG-DAW-010 第 3 章第 5 步）")

    cov = fetch_one(conn, """SELECT * FROM das_change_criterion_coverage
                              WHERE change_id=%s""", (c["id"],))
    # 【两条路，二者之一】2026-10-02 的决定：系统实现不了的走线下审批，证据资料上传系统。
    # 所以这里拒的不是"没录进系统"，是"两边都拿不出判定依据"。
    offline = scalar(conn, "SELECT das_offline_covered('DAS_DESIGN_CHANGE', %s)",
                     (change_no,))
    if cov and cov["missing_items"] and not offline:
        raise ValueError(
            f"拿不出分类的判定依据，不得出分类结论。两条路选一条："
            f"① 在系统里逐条给出判据结论（还差：{cov['missing_items']}）；"
            f"② 走线下审批并把证据上传"
            f"（POST /das/offline/approvals，object_type=DAS_DESIGN_CHANGE，"
            f"object_key={change_no}）。"
            f"线下那条要求记录**已复核**且**至少一份证据文件**——判据 N-总：补录数据在"
            f"完成复核并发布之前不具有权威性；而没有扫描件的记录比没有记录更坏，"
            f"台账上看起来有。")
    if cov and cov["significant"] and major_minor == "MINOR":
        raise ValueError(
            f"「{cov['significant_items']}」已判为显著影响，不得分类为小改。"
            f"UG-DAW-010 第 1 章：判据中任一项达到「显著影响」即为重大更改。")
    if state == "BEYOND_AUTHORITY":
        if major_minor is not None:
            raise ValueError(
                "超出分类或批准权限时结论须留空，转局方办理。UG-DAP-06 第 4 步："
                "**不能仅因超权限自动判为大改**——那是用权限问题冒充技术判断，"
                "而重大更改要走 UG-DAP-09 向局方申请批准，"
                "等于凭一个权限缺口给产品加了一道审定。")
        if not (caac_referral_ref or "").strip():
            raise ValueError(
                "超权限时须写明转局方办理的依据（函件或咨询编号）。"
                "只标一个「超权限」而不转办，这条更改就卡在那里没人管。")
    if state == "UNDETERMINED" and major_minor != "MAJOR":
        raise ValueError(
            "无法判定的按重大更改处理（UG-DAW-010 第 1 章），直至适航管理负责人或"
            "局方确认。此处须填 MAJOR。注意这与「超权限」是两件事：前者是技术上判不了，"
            "后者是权限不够、要转局方且结论留空。")
    if state == "CLASSIFIED" and not major_minor:
        raise ValueError("已分类却没有大改／小改结论")

    exists = fetch_one(conn, "SELECT id, state FROM das_change_classification "
                             "WHERE change_id=%s", (c["id"],))
    signed = (actor["user_id"], signed_on or dt.date.today()) \
        if state == "CLASSIFIED" else (None, None)
    if exists:
        execute(conn, """UPDATE das_change_classification
                            SET state=%s, major_minor=%s, conclusion_reason=%s,
                                form_no=coalesce(%s, form_no),
                                caac_referral_ref=coalesce(%s, caac_referral_ref),
                                signed_by=%s, signed_on=%s
                          WHERE id=%s""",
                (state, major_minor, conclusion_reason.strip(),
                 (form_no or "").strip() or None,
                 (caac_referral_ref or "").strip() or None, signed[0], signed[1],
                 exists["id"]))
        cid = exists["id"]
    else:
        cid = fetch_one(conn, """
            INSERT INTO das_change_classification
                   (change_id, state, major_minor, conclusion_reason, form_no,
                    caac_referral_ref, signed_by, signed_on)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (c["id"], state, major_minor, conclusion_reason.strip(),
              (form_no or "").strip() or None,
              (caac_referral_ref or "").strip() or None, signed[0], signed[1]))["id"]
    audit.write(conn, action="DAS_CHANGE_CLASSIFY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CHANGE_CLASSIFICATION",
                object_id=str(cid), object_code=change_no,
                old_value={"state": exists["state"]} if exists else None,
                new_value={"state": state, "major_minor": major_minor,
                           "form_no": form_no},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_change_classification WHERE id=%s", (cid,))


def record_caac_result(conn: psycopg.Connection, *, change_no: str, aspect: str,
                       result: str, caac_ref: str, caac_on: dt.date,
                       limitation: str | None = None, actor: dict) -> dict:
    """登记局方给出的声学或排放分类结论（UG-DAP-06 第 4 步）。

    本单位**未申请这两项的批准权**, 所以这里只能登记局方结论, 不能由内部给 ——
    内部给了就是在没有权利的事项上作了批准。
    """
    _require(conn, actor, "AWM", "登记局方的声学／排放分类结论",
             "UG-DAP-06 第 4 步：由适航管理负责人提交局方取得所需结论")
    if aspect not in ("acoustic", "emission"):
        raise ValueError("aspect 须为 acoustic 或 emission")
    allowed = {"acoustic": ("ACOUSTIC", "NON_ACOUSTIC"),
               "emission": ("EMISSION", "NON_EMISSION")}[aspect]
    if result not in allowed:
        raise ValueError(f"结论须为 {'/'.join(allowed)} 之一")
    if not (caac_ref or "").strip():
        raise ValueError(
            "须记录局方编号。本单位未申请该分类的批准权，结论只能来自局方——"
            "没有局方编号的结论就是内部给的，而那是在没有权利的事项上作了批准。")
    c = _by_no(conn, change_no)
    if not scalar(conn, "SELECT count(*) FROM das_change_classification "
                        "WHERE change_id=%s", (c["id"],)):
        raise ValueError(f"{change_no} 还没有分类记录")
    col = "acoustic" if aspect == "acoustic" else "emission"
    execute(conn, f"""UPDATE das_change_classification
                         SET {col}_result=%s, {col}_caac_ref=%s, {col}_caac_on=%s,
                             {col}_limitation=%s
                       WHERE change_id=%s""",
            (result, caac_ref.strip(), caac_on,
             (limitation or "").strip() or None, c["id"]))
    audit.write(conn, action="DAS_CHANGE_CAAC_RESULT", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CHANGE_CLASSIFICATION",
                object_id=str(c["id"]), object_code=f"{change_no}/{aspect}",
                new_value={"result": result, "caac_ref": caac_ref.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, """SELECT * FROM das_change_classification
                               WHERE change_id=%s""", (c["id"],))


def record_deviation(conn: psycopg.Connection, *, deviation_no: str, requested_by: str,
                     description: str, assessment: str, affects_design_data: bool,
                     affects_key_characteristics: bool, affects_airworthiness: bool,
                     approved: bool, change_no: str | None = None,
                     requested_on: dt.date | None = None, actor: dict) -> dict:
    """制造偏离／让步的评估（UG-DAP-06 第 7 步）。

    原文: **影响适航的不得批准, 回到设计更改流程。**
    """
    _require(conn, actor, ("DE", "AWM"), "评估制造偏离",
             "UG-DAP-06 第 7 步：授权人员评估对设计数据、关键特性和适航的影响")
    for name, val in (("申请方", requested_by), ("偏离说明", description),
                      ("评估结论", assessment)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    cid = None
    if change_no:
        cid = _by_no(conn, change_no)["id"]
    if affects_airworthiness:
        if approved:
            raise ValueError(
                "影响适航的制造偏离**不得批准**，须回到设计更改流程"
                "（UG-DAP-06 第 7 步）。批准一个影响适航的偏离，等于绕过设计更改分类"
                "改了已批准的设计。")
        if cid is None:
            raise ValueError(
                "影响适航的偏离须指向回到设计更改流程的那条更改申请。"
                "只记一句「不批准」而不转设计更改，这个偏离就没有下文——"
                "而产品上的那个偏差还在。")
    row = fetch_one(conn, """
        INSERT INTO das_manufacturing_deviation
               (deviation_no, requested_by, requested_on, description,
                affects_design_data, affects_key_characteristics,
                affects_airworthiness, assessment, approved, change_id, assessed_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, deviation_no, approved, change_id
    """, (deviation_no.strip(), requested_by.strip(), requested_on or dt.date.today(),
          description.strip(), affects_design_data, affects_key_characteristics,
          affects_airworthiness, assessment.strip(), approved, cid,
          actor["user_id"]))
    audit.write(conn, action="DAS_DEVIATION", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_MFG_DEVIATION",
                object_id=str(row["id"]), object_code=deviation_no,
                new_value={"approved": approved,
                           "affects_airworthiness": affects_airworthiness,
                           "change_no": change_no},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def gaps(conn: psycopg.Connection) -> dict:
    """要盯的三张表。

    `awaiting_caac` 转局方或待确认而尚无结论的 —— 它们**不是"已分类"**, 结论是空的。
    `cumulative_gap` 判为小改、同项目此前另有更改、却没有任何合并评估记录的,
    这正是"一串小改永远是小改"会发生的地方（UG-DAW-010 第 1 章）。
    `deviation_to_change` 影响适航的偏离及其对应的设计更改。
    """
    return {
        "awaiting_caac": fetch_all(conn, "SELECT * FROM das_change_awaiting_caac"),
        "cumulative_gap": fetch_all(conn, "SELECT * FROM das_change_cumulative_gap"),
        "deviation_to_change": fetch_all(conn, "SELECT * FROM das_deviation_to_change"),
    }
