"""签署授权的产品／型号／件号范围与权限限制（判据 I10.PRODUCT_SCOPE）。

依据手册 3.2「授权范围按产品类别分别确定，**未列入的产品类别不得签署**」、
UG-DAM-01-附2「【批准类型；产品／型号／件号；限制】」、UG-DAW-005 第 4 章、
UG-DAP-03 第 8 节，设计输入第九之二节的 C 版更正。

【这一次为什么不是重犯 PR #5 的错】
PR #5 给 `signer_authorization` 加过自由文本 `product_scope`，被否决并移除。否决的理由
不是"不该有范围"，而是**那个字段只写入、只显示，`is_authorized()` 从不读它**：手册要求
强制执行的一条控制在系统里是个装饰，而且它会出现在授权名单上，让审查者以为系统在管。
所以这一版的硬要求是：范围必须是可比对的结构化引用，并且必须有一条真的会读它的校验路径；
读不到的那一部分一律如实记为未施加，不计入已实现。

【范围维度与关联维度是两件事】
设计输入第九之二节 C 版更正：一个项目可以包含多个产品、多个型号件号和不同限制条件，
**因此"同属一个项目"不能证明"签署范围相符"——项目引用是关联维度，不是范围维度。**
所以 `das_signer_scope`（范围）与 `das_signer_project_bind`（关联）各自独立，谁也不替代谁。
后者的典型用途是：受托项目的授权不得顺带签自家 STC 的资料。

【覆盖判断是三值的，NULL 不等于放行】
true 覆盖／false 不覆盖（白名单外，手册 3.2 不得签署）／**NULL 判不了**（调用方没给标的，
或该授权没有范围条目）。合成布尔的话，判不了只能折成 true 或 false 之一：折成 true 就是
PR #5 那个装饰字段的翻版，折成 false 会把说不出标的的正常签署全拦死。

【落地的强制点只有一处，这是如实状态】
范围校验只在调用方说得出标的时才有意义。全系统目前只有**符合性声明的签署**说得出标的
（声明签给某个项目，项目带着批准类型与航空器型号）。其余路径——文件三级签署、制造符合性
声明、小改批准——调用方传不出标的，**不得默默放行**：那正是判据 I10.DISCIPLINE 已有的
缺口（调用方未传专业时不施加专业限制），明知故犯地再来一次是不行的。所以逐条列在
`das_signer_scope_unenforced` 里，登记册记为部分实现而不是已实现。
"""
from __future__ import annotations

import psycopg

from .. import audit
from ..db import fetch_all, fetch_one, scalar

SCOPE_KINDS = {
    "PART_NUMBER": "件号",
    "FAMILY": "图号族",
    "AIRCRAFT_TYPE": "航空器型号",
    "PRODUCT_CATEGORY": "产品类别",
}
REF_COLUMN = {
    "PART_NUMBER": "part_number_id",
    "FAMILY": "family_id",
    "AIRCRAFT_TYPE": "aircraft_type",
    "PRODUCT_CATEGORY": "product_category",
}


def _auth(conn: psycopg.Connection, auth_id: str) -> dict:
    row = fetch_one(conn, """SELECT sa.id, sa.user_id, sa.level, sa.revoked_at,
                                    u.full_name
                               FROM signer_authorization sa
                               JOIN app_user u ON u.id = sa.user_id
                              WHERE sa.id = %s""", (auth_id,))
    if not row:
        raise LookupError(f"没有这条签署授权：{auth_id}")
    if row["revoked_at"]:
        raise ValueError(
            f"该授权已于 {row['revoked_at'].date()} 撤销，不得再登记范围。"
            f"范围变了要重新授权（判据 N14：授权条目只可撤销不可删除）。")
    return row


def registry(conn: psycopg.Connection, *, state: str | None = None) -> list[dict]:
    """每条签署授权的范围登记情况。

    `scope_state`：SYSTEM_ENFORCED 系统管范围／PAPER_ONLY 已声明由纸面把关／
    **UNDECLARED 既没登记也没声明**——最后一档必须清零。
    """
    if state:
        return fetch_all(conn, """SELECT * FROM das_signer_scope_registry
                                   WHERE scope_state = %s""", (state,))
    return fetch_all(conn, "SELECT * FROM das_signer_scope_registry")


def undeclared(conn: psycopg.Connection) -> list[dict]:
    """既没登记范围、也没声明由纸面把关的在册授权。**这一档必须清零。**

    它不是"还没来得及"的待办, 是一条说不出依据的授权: 按手册 3.2 的白名单口径
    它什么都不该能签, 而在落地强制点之外它什么都能签。
    """
    return fetch_all(conn, "SELECT * FROM das_signer_scope_undeclared")


def paper_only(conn: psycopg.Connection) -> list[dict]:
    """声明"范围由纸面授权书把关"的，交独立监督核对纸面授权书。"""
    return fetch_all(conn, "SELECT * FROM das_signer_scope_paper_only")


def unenforced(conn: psycopg.Connection) -> list[dict]:
    """范围校验尚未施加的签署路径及原因（判据 I10.PRODUCT_SCOPE 的如实状态）。"""
    return fetch_all(conn, "SELECT * FROM das_signer_scope_unenforced")


def statement_gap(conn: psycopg.Connection) -> list[dict]:
    """没有校验范围就签出去的符合性声明。自评要数得出这个数。"""
    return fetch_all(conn, "SELECT * FROM das_compliance_statement_scope_gap")


def scope_of(conn: psycopg.Connection, auth_id: str) -> dict:
    a = _auth(conn, auth_id) if not scalar(
        conn, "SELECT count(*) FROM signer_authorization WHERE id=%s AND revoked_at IS NOT NULL",
        (auth_id,)) else fetch_one(
        conn, """SELECT sa.id, sa.level, u.full_name FROM signer_authorization sa
                   JOIN app_user u ON u.id = sa.user_id WHERE sa.id=%s""", (auth_id,))
    return {
        "authorization": a,
        "scope": fetch_all(conn, """SELECT s.*, pn.full_part_number
                                      FROM das_signer_scope s
                                      LEFT JOIN part_number pn ON pn.id = s.part_number_id
                                     WHERE s.auth_id = %s ORDER BY s.id""", (auth_id,)),
        "project_binds": fetch_all(conn, """SELECT b.*, p.project_no, p.type_code
                                              FROM das_signer_project_bind b
                                              JOIN das_project p ON p.id = b.project_id
                                             WHERE b.auth_id = %s""", (auth_id,)),
        "waiver": fetch_one(conn, "SELECT * FROM das_signer_scope_waiver WHERE auth_id=%s",
                            (auth_id,)),
    }


def add_scope(conn: psycopg.Connection, *, auth_id: str, approval_type_code: str,
              scope_kind: str, source_ref: str,
              part_number_id: str | None = None, family_id: str | None = None,
              aircraft_type: str | None = None, product_category: str | None = None,
              limitation: str | None = None, no_limitation_declared: bool = False,
              actor: dict) -> dict:
    """给一条授权登记一条范围条目（白名单的一行）。

    不得给自己的授权登记范围 —— 理由同 signers.grant 的"不得给自己授权"（判据 I9）:
    能给自己的授权加范围, 就等于能给自己扩权, 而那正是 I9 要拦的。
    """
    a = _auth(conn, auth_id)
    if str(a["user_id"]) == str(actor["user_id"]):
        raise ValueError(
            "不得给自己的授权登记范围，请由另一名构型管理员操作。"
            "能给自己的授权加范围就等于能给自己扩权（判据 I9：任何人不得为自己授权）。")
    if scope_kind not in SCOPE_KINDS:
        raise ValueError(f"范围粒度须为 {'/'.join(SCOPE_KINDS)} 之一")
    if not scalar(conn, "SELECT count(*) FROM das_project_type WHERE code=%s",
                  (approval_type_code,)):
        raise LookupError(f"没有 {approval_type_code} 这个批准类型")
    refs = {"part_number_id": part_number_id, "family_id": family_id,
            "aircraft_type": (aircraft_type or "").strip() or None,
            "product_category": (product_category or "").strip() or None}
    given = [k for k, v in refs.items() if v is not None]
    want = REF_COLUMN[scope_kind]
    if given != [want]:
        raise ValueError(
            f"范围粒度为「{SCOPE_KINDS[scope_kind]}」时只能给 {want}，"
            f"实际给了 {given or '（空）'}。一行挂两种粒度就是一行两义，"
            f"它的覆盖判断迟早被当成取并集或取交集，看谁先读到。")
    if not (source_ref or "").strip():
        raise ValueError("须写明纸面授权书中对应的条目：系统里的范围要能回到纸面依据")
    if not no_limitation_declared and not (limitation or "").strip():
        raise ValueError(
            "限制栏为空时须显式声明「无限制」。附2 的括号里写着「限制」，"
            "空着可能是无限制，也可能是忘了抄——这两种在符合性自评里完全不同，"
            "而一个空格分不出来。")
    if no_limitation_declared and (limitation or "").strip():
        raise ValueError("既声明无限制又写了限制内容，二者取一")
    row = fetch_one(conn, f"""
        INSERT INTO das_signer_scope
               (auth_id, approval_type_code, scope_kind, {want}, limitation,
                no_limitation_declared, source_ref, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, auth_id, approval_type_code, scope_kind, source_ref
    """, (auth_id, approval_type_code, scope_kind, refs[want],
          (limitation or "").strip() or None, no_limitation_declared,
          source_ref.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_SIGNER_SCOPE_ADD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="SIGNER_AUTHORIZATION",
                object_id=str(auth_id), object_code=a["full_name"],
                new_value={"approval_type": approval_type_code, "kind": scope_kind,
                           "ref": str(refs[want]), "limitation": limitation,
                           "source_ref": source_ref.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def bind_project(conn: psycopg.Connection, *, auth_id: str, project_no: str,
                 reason: str, actor: dict) -> dict:
    """把一条授权限定在某个项目上（**关联维度，不替代范围**）。

    设计输入第九之二节: 受托授权只限该受托项目, 不得顺带签自家 STC 的资料。
    """
    a = _auth(conn, auth_id)
    if str(a["user_id"]) == str(actor["user_id"]):
        raise ValueError("不得给自己的授权设限定，请由另一名构型管理员操作")
    p = fetch_one(conn, "SELECT id, project_no, type_code FROM das_project WHERE project_no=%s",
                  (project_no,))
    if not p:
        raise LookupError(f"没有编号为 {project_no} 的项目")
    if not (reason or "").strip():
        raise ValueError("须写明为什么限定在这个项目上")
    if scalar(conn, """SELECT count(*) FROM das_signer_project_bind
                        WHERE auth_id=%s AND project_id=%s""", (auth_id, p["id"])):
        raise ValueError(f"该授权已限定在 {project_no} 上，不重复登记")
    row = fetch_one(conn, """
        INSERT INTO das_signer_project_bind (auth_id, project_id, reason, created_by)
        VALUES (%s,%s,%s,%s)
        RETURNING id, auth_id, project_id, reason
    """, (auth_id, p["id"], reason.strip(), actor["user_id"]))
    row["project_no"] = p["project_no"]
    audit.write(conn, action="DAS_SIGNER_PROJECT_BIND", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="SIGNER_AUTHORIZATION",
                object_id=str(auth_id), object_code=a["full_name"],
                new_value={"project_no": project_no, "reason": reason.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def declare_paper_only(conn: psycopg.Connection, *, auth_id: str, paper_ref: str,
                       reason: str, actor: dict) -> dict:
    """声明某条授权的范围由纸面授权书把关。

    **这不是默认值, 是一条签了名的陈述。** 存量授权一条范围都没有; 按手册 3.2 的白名单
    口径它们什么都不该能签, 但一刀切会让现有签署全部停摆, 而停摆不会让符合性变好,
    只会让人绕过系统。所以给一条显式声明的路, 带声明人与日期, 并全部进
    das_signer_scope_paper_only 交独立监督核对纸面授权书。
    """
    a = _auth(conn, auth_id)
    for name, val in (("纸面授权书编号", paper_ref), ("理由", reason)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空：声明要能回到那份纸面授权书")
    n = scalar(conn, "SELECT count(*) FROM das_signer_scope WHERE auth_id=%s", (auth_id,))
    if n:
        raise ValueError(
            f"该授权已有 {n} 条范围条目，不得再声明「由纸面授权书把关」。"
            f"声明的意思是系统不管范围；系统已经在管了，再声明一句不管，"
            f"两者必有一个是假的。")
    if scalar(conn, "SELECT count(*) FROM das_signer_scope_waiver WHERE auth_id=%s",
              (auth_id,)):
        raise ValueError("该授权已有声明，不重复声明")
    row = fetch_one(conn, """
        INSERT INTO das_signer_scope_waiver (auth_id, paper_ref, reason, declared_by)
        VALUES (%s,%s,%s,%s)
        RETURNING auth_id, paper_ref, declared_on
    """, (auth_id, paper_ref.strip(), reason.strip(), actor["user_id"]))
    audit.write(conn, action="DAS_SIGNER_SCOPE_WAIVER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="SIGNER_AUTHORIZATION",
                object_id=str(auth_id), object_code=a["full_name"],
                new_value={"paper_ref": paper_ref.strip(), "reason": reason.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def covers(conn: psycopg.Connection, *, auth_id: str, approval_type_code: str | None = None,
           part_number_id: str | None = None, family_id: str | None = None,
           aircraft_type: str | None = None,
           product_category: str | None = None) -> dict:
    """范围是否覆盖某标的。**三值**，NULL 不等于放行。"""
    v = scalar(conn, """SELECT das_signer_scope_covers(%s,%s,%s,%s,%s,%s)""",
               (auth_id, approval_type_code, part_number_id, family_id,
                (aircraft_type or "").strip() or None,
                (product_category or "").strip() or None))
    return {
        "covered": v,
        "meaning": {True: "覆盖", False: "不覆盖（白名单外，手册 3.2：不得签署）",
                    None: "判不了（没给标的，或该授权没有范围条目）"}[v],
    }
