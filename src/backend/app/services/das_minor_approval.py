"""M5 设计小改批准（第二批，UG-DAP-08／AP-21-18 表-21-174）。

依据 UG-DAP-08 第 2 章（适用范围）、第 6 章（6 步）、第 7 章（关键控制点）。

【第 1 步的三个条件是三条，任一不满足不得批准】
原文：a) 已有签署的分类表且为小改；b) 在本单位设计机构许可项目单的权利范围内；
c) 批准人在授权范围内。**任一项不满足，不得批准。** 所以三条各自落一行记录并写明怎么
核的——一个"条件已核对"的勾说不出哪一条是怎么核的。

【与 M4 的硬连接，以及它会失效这件事】
条件 a) 要求指向一条已签署且为小改的分类结论。**而分类结论会变**：UG-DAP-06 的
"局方对分类有不同意见时以局方意见为准"。一旦底下那条不再是已签署的小改，建立在它上面
的小改批准就失去了前提——而**资料已经发出去了**。这不是历史问题，是现在外面有一份带着
不成立批准的资料。所以 `invalidated()` 持续列出这些，交 M7 按 UG-DAP-14 处理；
只在插入时校验一次是不够的。

【批准声明必须是局方规定的原文】
第 5 步给了原文，第 7 章再强调"批准声明必须使用局方规定的原文"。所以声明文本是**受控
配置**而不是代码里的字符串常量，发布时逐字比对。须附声明的是服务通告、飞行手册补充、
持续适航文件；**符合性文件、审定计划、生产用设计数据不使用此声明**——给它们附上"按权利
范围进行批准"的声明，是把不代表设计批准的资料说成了设计批准。

【声明里的许可证编号现在填不出来】
原文的声明带占位符【设计机构许可证编号】，而本单位的 DOA 尚在申请中——这个号还不存在。
于是须附声明的资料暂时发不出去，这是**如实状态而不是缺陷**：硬塞一个编号进去，那份资料
会带着一个假的许可证编号发出去。`blocker()` 把这件事摆明，省得有人以为是程序坏了去绕。

【超权限不得改判，与 M4 同一条规则】
第 7 章：超出本单位许可范围或批准权限的更改，不得自行批准；按技术影响保留大改／小改
分类建议，提交局方办理或先申请权限变更，**不得仅因无批准权限自动改判大改**。
本模块这一侧的落点是：条件 b)／c) 不满足时不得批准，而**分类结论保持原样**。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import fetch_all, fetch_one, scalar

CONDITIONS = {
    "CLASSIFIED_MINOR": "a) 已有签署的分类表且为小改",
    "WITHIN_DOA_SCOPE": "b) 在本单位设计机构许可项目单的权利范围内",
    "APPROVER_AUTHORISED": "c) 批准人在授权范围内",
}
POSITION_CN = {"AWM": "适航管理负责人", "DCM": "设计资料管理负责人", "DE": "设计工程师"}


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


def statement(conn: psycopg.Connection) -> dict:
    """局方规定的批准声明原文（受控配置）与它的改动史。"""
    return {
        "current": fetch_one(conn, """SELECT * FROM das_approval_statement
                                       WHERE code='DOA_MINOR_APPROVAL'"""),
        "history": fetch_all(conn, """SELECT * FROM das_approval_statement_history
                                       ORDER BY changed_at DESC"""),
        "doc_kinds": fetch_all(conn, """SELECT * FROM das_release_doc_kind
                                         ORDER BY requires_statement DESC, code"""),
    }


def blocker(conn: psycopg.Connection) -> list[dict]:
    """许可证编号这道门。

    声明原文带占位符, 而本单位的 DOA 尚在申请中。**这是如实状态, 不是缺陷** ——
    把它摆明, 省得有人以为是程序坏了去绕。
    """
    return fetch_all(conn, "SELECT * FROM das_approval_statement_blocker")


def register(conn: psycopg.Connection) -> list[dict]:
    """小改批准台账（UG-DAF-04）。"""
    return fetch_all(conn, "SELECT * FROM das_minor_approval_register")


def invalidated(conn: psycopg.Connection) -> list[dict]:
    """底下那条分类已不是"已签署的小改"的小改批准。

    **这是本模块最要紧的一张表。** 插入时校验过一次是不够的: 分类结论会变,
    而资料已经发出去了。交 M7 按 UG-DAP-14 记不符合项处理。
    """
    return fetch_all(conn, "SELECT * FROM das_minor_approval_invalidated")


def condition_gaps(conn: psycopg.Connection) -> list[dict]:
    """条件没核全、或核了不满足的批准。没发布之前不发生对外效果，但要看得见。"""
    return fetch_all(conn, "SELECT * FROM das_minor_approval_condition_gap")


def approval(conn: psycopg.Connection, form_no: str) -> dict:
    row = fetch_one(conn, """SELECT * FROM das_minor_approval_register
                              WHERE form_no=%s""", (form_no,))
    if not row:
        raise LookupError(f"没有编号为 {form_no} 的小改批准")
    row["conditions"] = fetch_all(conn, """SELECT * FROM das_minor_approval_condition
                                            WHERE approval_id=%s ORDER BY condition_code""",
                                  (row["id"],))
    row["releases"] = fetch_all(conn, """SELECT r.*, k.name_cn AS doc_kind_name,
                                                k.requires_statement
                                           FROM das_minor_approval_release r
                                           JOIN das_release_doc_kind k ON k.code = r.doc_kind
                                          WHERE r.approval_id=%s ORDER BY r.released_on""",
                                (row["id"],))
    return row


def approve(conn: psycopg.Connection, *, change_no: str, form_no: str,
            approved_scope: str, cve_check_ref: str, change_doc_ref: str,
            limitations: str | None = None, no_limitation_declared: bool = False,
            authority_ref: str | None = None, cve_user_id: str | None = None,
            approved_on: dt.date | None = None, actor: dict) -> dict:
    """批准（UG-DAP-08 第 4 步，UG-DAF-04，编号后缀 PZ）。

    条件记录要引用 approval_id, 所以批准行先建、条件后录;
    **未过条件不得发布**（DCMS-INV-109）, 而没发布的批准不发生对外效果。
    """
    _require(conn, actor, ("AWM", "DE"), "签署小改批准",
             "UG-DAP-08 第 4 步：授权人员填写并签署 UG-DAF-04")
    cl = fetch_one(conn, """SELECT cl.id, cl.state, cl.major_minor, d.change_no
                              FROM das_change_classification cl
                              JOIN das_design_change d ON d.id = cl.change_id
                             WHERE d.change_no = %s""", (change_no,))
    if not cl:
        raise LookupError(f"没有编号为 {change_no} 的更改，或它还没有分类结论")
    if cl["state"] != "CLASSIFIED" or cl["major_minor"] != "MINOR":
        raise ValueError(
            f"小改批准的前提是「已有签署的分类表且为小改」（UG-DAP-08 第 1 步 a），"
            f"而 {change_no} 的分类结论现在是 "
            f"{cl['state']} / {cl['major_minor'] or '结论留空'}。"
            f"不是已签署的小改就批准，批的是一个还没定性或定性为大改的更改。")
    if "PZ" not in (form_no or ""):
        raise ValueError(
            "小改批准的编号后缀是 PZ（UG-DAP-08 第 4 步）。"
            "编号后缀分得出这是哪一类批准——FL 是更改分类，PZ 是小改批准，"
            "SM 是符合性声明。")
    for name, val in (("批准范围", approved_scope), ("CVE 核查记录", cve_check_ref),
                      ("小改资料", change_doc_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（UG-DAP-08 第 4 步：载明范围和限制条件；"
                             f"第 3 步：CVE 核查符合性）")
    if not no_limitation_declared and not (limitations or "").strip():
        raise ValueError(
            "限制条件为空时须显式声明「无限制」。小改批准是在权利范围内作的批准，"
            "限制栏空着可能是无限制，也可能是忘了写——这两种在局方复查时完全不同，"
            "而一个空格分不出来。")
    if no_limitation_declared and (limitations or "").strip():
        raise ValueError("既声明无限制又写了限制内容，二者取一")
    is_awm = _holds(conn, actor, "AWM")
    if not is_awm and not (authority_ref or "").strip():
        raise PermissionError(
            "批准人不是在任适航管理负责人时，须写明纸面授权依据。"
            "「小改批准」是 UG-DAM-01-附2 的 5 类适航签署事项之一，而系统判不了某份授权"
            "是否覆盖这件事（授权的第三维校验的是 DCMS 文档种类，待澄清项第 1 条）——"
            "所以这里要的是说得出依据，由独立监督核对。")
    row = fetch_one(conn, """
        INSERT INTO das_minor_approval
               (classification_id, form_no, approved_scope, limitations,
                no_limitation_declared, cve_check_ref, cve_user_id, change_doc_ref,
                approved_by, approved_on, authority_ref)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, form_no, approved_on
    """, (cl["id"], form_no.strip(), approved_scope.strip(),
          (limitations or "").strip() or None, no_limitation_declared,
          cve_check_ref.strip(), cve_user_id, change_doc_ref.strip(),
          actor["user_id"], approved_on or dt.date.today(),
          (authority_ref or "").strip() or None))
    audit.write(conn, action="DAS_MINOR_APPROVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_MINOR_APPROVAL",
                object_id=str(row["id"]), object_code=form_no,
                new_value={"change_no": change_no, "scope": approved_scope.strip(),
                           "as_awm": is_awm, "authority_ref": authority_ref},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def check_condition(conn: psycopg.Connection, *, form_no: str, condition_code: str,
                    satisfied: bool, evidence: str, actor: dict) -> dict:
    """登记第 1 步的一个核对条件（UG-DAP-08 第 1 步）。"""
    _require(conn, actor, ("AWM", "DE"), "登记核对条件",
             "UG-DAP-08 第 1 步：授权人员核对三个条件")
    if condition_code not in CONDITIONS:
        raise ValueError(f"条件编号须为 {'/'.join(CONDITIONS)} 之一")
    a = fetch_one(conn, "SELECT id FROM das_minor_approval WHERE form_no=%s",
                  (form_no,))
    if not a:
        raise LookupError(f"没有编号为 {form_no} 的小改批准")
    if not (evidence or "").strip():
        raise ValueError(
            f"须写明「{CONDITIONS[condition_code]}」是怎么核的。"
            f"一个「条件已核对」的勾说不出哪一条是怎么核的，而局方要问的正是这个。")
    if scalar(conn, """SELECT count(*) FROM das_minor_approval_condition
                        WHERE approval_id=%s AND condition_code=%s""",
              (a["id"], condition_code)):
        raise ValueError(
            f"「{CONDITIONS[condition_code]}」已登记，不得改写（DCMS-INV-113）。"
            f"核对结论变了要另建一条批准。")
    row = fetch_one(conn, """
        INSERT INTO das_minor_approval_condition
               (approval_id, condition_code, satisfied, evidence, checked_by)
        VALUES (%s,%s,%s,%s,%s) RETURNING id, condition_code, satisfied
    """, (a["id"], condition_code, satisfied, evidence.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_MINOR_CONDITION", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_MINOR_APPROVAL",
                object_id=str(a["id"]), object_code=f"{form_no}/{condition_code}",
                new_value={"satisfied": satisfied, "evidence": evidence.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def release(conn: psycopg.Connection, *, form_no: str, doc_kind: str, doc_ref: str,
            distributed_to: str, archived_ref: str, statement_text: str | None = None,
            caac_notified: bool = False, released_on: dt.date | None = None,
            actor: dict) -> dict:
    """发布与归档（UG-DAP-08 第 5～6 步）。

    三个条件要全部核过且满足才放行（DCMS-INV-109）, 须附声明的种类必须附且逐字相符,
    不使用声明的种类不得附（DCMS-INV-112）。
    """
    _require(conn, actor, "DCM", "受控发布与归档",
             "UG-DAP-08 第 5～6 步：资料管理负责人负责发布、通知与归档")
    a = fetch_one(conn, "SELECT id FROM das_minor_approval WHERE form_no=%s",
                  (form_no,))
    if not a:
        raise LookupError(f"没有编号为 {form_no} 的小改批准")
    kind = fetch_one(conn, "SELECT * FROM das_release_doc_kind WHERE code=%s",
                     (doc_kind,))
    if not kind:
        raise LookupError(f"没有 {doc_kind} 这个资料种类")
    for name, val in (("资料编号与版次", doc_ref), ("分发对象", distributed_to),
                      ("归档记录", archived_ref)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（第 6 步：通知使用者、生产和采购；归档）")

    conds = {c["condition_code"]: c for c in fetch_all(
        conn, """SELECT condition_code, satisfied FROM das_minor_approval_condition
                  WHERE approval_id=%s""", (a["id"],))}
    missing = [cn for code, cn in CONDITIONS.items() if code not in conds]
    if missing:
        raise ValueError(f"第 1 步的核对条件还差：{'、'.join(missing)}。"
                         f"三条各自要有核对记录与依据。")
    bad = [cn for code, cn in CONDITIONS.items() if not conds[code]["satisfied"]]
    if bad:
        raise ValueError(
            f"下列条件不满足，不得批准发布：{'、'.join(bad)}。"
            f"UG-DAP-08 第 1 步：任一项不满足，不得批准。"
            f"第 7 章：超出许可范围或批准权限的，提交局方办理或先申请权限变更，"
            f"**不得仅因无批准权限自动改判大改**——分类结论保持原样。")

    st = fetch_one(conn, """SELECT template, placeholder FROM das_approval_statement
                             WHERE code='DOA_MINOR_APPROVAL'""")
    text = (statement_text or "").strip()
    if kind["requires_statement"]:
        if not text:
            raise ValueError(
                f"「{kind['name_cn']}」对外发布须附局方规定的批准声明"
                f"（UG-DAP-08 第 5 步）。")
        if st["placeholder"] and st["placeholder"] in text:
            raise ValueError(
                f"声明里的占位符「{st['placeholder']}」还没替换成设计机构许可证编号。"
                f"本单位的 DOA 尚在申请中，这个号还不存在——所以须附声明的资料暂时发不出去，"
                f"这是如实状态。硬塞一个编号进去才是问题：那份资料会带着一个假的"
                f"许可证编号发出去。")
    elif text:
        raise ValueError(
            f"「{kind['name_cn']}」**不使用**此声明（UG-DAP-08 第 5 步原文）。"
            f"给符合性文件、审定计划或生产用设计数据附上「按权利范围进行批准」的声明，"
            f"是把不代表设计批准的资料说成了设计批准。")

    row = fetch_one(conn, """
        INSERT INTO das_minor_approval_release
               (approval_id, doc_kind, doc_ref, statement_text, distributed_to,
                caac_notified, archived_ref, released_by, released_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, doc_kind, doc_ref, released_on
    """, (a["id"], doc_kind, doc_ref.strip(), text or None, distributed_to.strip(),
          caac_notified, archived_ref.strip(), actor["user_id"],
          released_on or dt.date.today()))
    audit.write(conn, action="DAS_MINOR_RELEASE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_MINOR_APPROVAL",
                object_id=str(a["id"]), object_code=f"{form_no}/{doc_ref.strip()}",
                new_value={"doc_kind": doc_kind, "caac_notified": caac_notified,
                           "has_statement": bool(text)},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row
