"""线下审批与证据上传（判据 B1 的落点）。

依据 2026-10-02 的业务决策：**系统实现不了的走线下审批，证据资料上传系统即可。**
以及判据 B1（人工替代流程须逐项登记六项，缺一不得开展对应业务）、
判据 N-总（允许线下发生，但线下发生的事必须补进系统；补录数据在完成复核并发布之前
不具有权威性）、判据 N1（纸质签署的文件扫描件与原件一并归档）。

【这个决定把什么搬走了，又把什么留下了】
搬走的是**判定过程**——比如 9 项判据逐条录进系统，对 5～8 人的编制过重，而过重的负担
不会让符合性变好，只会让人绕过系统，于是系统里什么都没有、线下也没人管。
留下的是**结论与证据**。体系文件的要求没有变松，只是改了承接方式；而承接方式一改，
自评要指向的东西就从「系统里那 9 行」变成「系统里那份扫描件」。
**没有上传的证据，自评里那一格就是空的**——这是本模块存在的理由。

【三件缺一不可的事】
一条线下审批要能顶替系统内的审批，须同时满足：
① 挂在一条**已批准的**人工替代流程上（判据 B1：须由已批准的流程承接）；
② 有**至少一份证据文件**，并写明纸质原件在哪（判据 N1：扫描件与原件一并归档）；
③ **已复核**（判据 N-总：补录数据复核前不具有权威性）。
缺任一项，`das_offline_covered()` 就是 false，M4 那两道闸门照旧拦住。

【为什么复核人要与补录人分开看】
补录人自己复核，等于「走线下」变成「自己说一声就行」。两列分开存，
`das_offline_self_reviewed` 把同一人的列出来交独立监督核对——**不阻断**，
理由同判据 I5／I6 的处理：5～8 人编制下某些时候避不开，但要看得见。
"""
from __future__ import annotations

import datetime as dt
import re

import psycopg

from .. import audit, storage
from ..db import execute, fetch_all, fetch_one, scalar

MAX_EVIDENCE_BYTES = 50 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._\-]+")
B1_FIELDS = {
    "offline_owner": "线下责任人（具体岗位，不写部门）",
    "forms_and_ledger": "表单与台账（哪张 UG-DAF 表单、台账存放位置、保管岗位）",
    "reconcile_frequency": "核对频率（多久核对线下台账与系统的一致性）",
    "backfill_rule": "补录规则（何时补入系统、补录后由谁复核）",
    "exit_condition": "退出条件（模块上线后何时停用、历史数据怎么迁）",
    "self_assessment_text": "符合性自评表述（在 UG-DAM-01-附3 中如实写明的那句话）",
}


def processes(conn: psycopg.Connection) -> list[dict]:
    """人工替代流程的登记与状态（DRAFT／IN_FORCE／RETIRED）。"""
    return fetch_all(conn, "SELECT * FROM das_manual_process_status")


def register_process(conn: psycopg.Connection, *, code: str, scope_note: str,
                     requirement_ref: str, actor: dict, **six) -> dict:
    """登记一条人工替代流程（判据 B1 的六项，全部必填）。

    登记完还不能用: 判据 B1 要的是**已批准的**流程, 批准由责任经理做（approve_process）。
    """
    missing = [cn for f, cn in B1_FIELDS.items() if not (six.get(f) or "").strip()]
    if missing:
        raise ValueError(
            f"判据 B1 的六项缺：{'；'.join(missing)}。原文「逐项登记下列六项，"
            f"**缺一不得开展对应业务**」——只填一两项的登记，在局方面前等于没有替代流程。")
    if not (scope_note or "").strip() or not (requirement_ref or "").strip():
        raise ValueError("须写明承接哪一条体系要求，以及该要求的文件编号与条款号")
    if scalar(conn, "SELECT count(*) FROM das_manual_process WHERE code=%s", (code,)):
        raise ValueError(f"替代流程 {code} 已登记")
    row = fetch_one(conn, """
        INSERT INTO das_manual_process
               (code, scope_note, requirement_ref, offline_owner, forms_and_ledger,
                reconcile_frequency, backfill_rule, exit_condition,
                self_assessment_text)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING code, scope_note, requirement_ref
    """, (code.strip(), scope_note.strip(), requirement_ref.strip(),
          *[six[f].strip() for f in B1_FIELDS]))
    audit.write(conn, action="DAS_MANUAL_PROCESS_REGISTER", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_MANUAL_PROCESS",
                object_id=code, object_code=code,
                new_value={"scope": scope_note.strip(),
                           "requirement_ref": requirement_ref.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def approve_process(conn: psycopg.Connection, *, code: str,
                    approved_on: dt.date | None = None, actor: dict) -> dict:
    """批准人工替代流程（判据 B1：须由已批准的流程承接）。须在任责任经理。"""
    if not scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code='AM'""",
                  (actor["user_id"],)):
        raise PermissionError(
            "人工替代流程须由在任责任经理批准（判据 B1：每个尚未上线的模块，"
            "其对应的体系要求须由**已批准的**人工替代流程承接）。"
            "未批准就照着做，等于那条体系要求此刻既不在系统里、也不在一个受控的线下流程里。")
    p = fetch_one(conn, "SELECT code, approved_on FROM das_manual_process WHERE code=%s",
                  (code,))
    if not p:
        raise LookupError(f"没有编号为 {code} 的替代流程")
    if p["approved_on"]:
        raise ValueError(f"{code} 已于 {p['approved_on']} 批准")
    execute(conn, """UPDATE das_manual_process SET approved_by=%s, approved_on=%s
                      WHERE code=%s""",
            (actor["user_id"], approved_on or dt.date.today(), code))
    audit.write(conn, action="DAS_MANUAL_PROCESS_APPROVE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_MANUAL_PROCESS",
                object_id=code, object_code=code,
                new_value={"approved_on": str(approved_on or dt.date.today())},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_manual_process_status WHERE code=%s",
                     (code,))


def retire_process(conn: psycopg.Connection, *, code: str,
                   retired_on: dt.date | None = None, actor: dict) -> dict:
    """停用（判据 B1 第六项：退出条件）。对应模块上线并通过验证后停用。

    停用之后仍挂上来的线下审批由 das_manual_process_overrun 列出 ——
    系统和线下两套并行时, 两套说法迟早不一致。
    """
    if not scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code='AM'""",
                  (actor["user_id"],)):
        raise PermissionError("停用人工替代流程须由在任责任经理执行（判据 B1 第六项）")
    p = fetch_one(conn, """SELECT code, approved_on, retired_on FROM das_manual_process
                            WHERE code=%s""", (code,))
    if not p:
        raise LookupError(f"没有编号为 {code} 的替代流程")
    if not p["approved_on"]:
        raise ValueError(f"{code} 还没批准，无从停用")
    if p["retired_on"]:
        raise ValueError(f"{code} 已于 {p['retired_on']} 停用")
    execute(conn, "UPDATE das_manual_process SET retired_on=%s WHERE code=%s",
            (retired_on or dt.date.today(), code))
    audit.write(conn, action="DAS_MANUAL_PROCESS_RETIRE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_MANUAL_PROCESS",
                object_id=code, object_code=code,
                new_value={"retired_on": str(retired_on or dt.date.today())},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_manual_process_status WHERE code=%s",
                     (code,))


def register(conn: psycopg.Connection, *, object_type: str | None = None) -> list[dict]:
    if object_type:
        return fetch_all(conn, """SELECT * FROM das_offline_approval_register
                                   WHERE object_type=%s""", (object_type,))
    return fetch_all(conn, "SELECT * FROM das_offline_approval_register")


def unreviewed(conn: psycopg.Connection) -> list[dict]:
    """补录了但还没复核的。

    **不是"待办"**: 判据 N-总 说补录数据在完成复核并发布之前不具有权威性 ——
    所以这些线下结论现在还不能当依据用, M4 那两道闸门对它们仍然是关着的。
    """
    return fetch_all(conn, "SELECT * FROM das_offline_approval_unreviewed")


def coverage(conn: psycopg.Connection) -> dict:
    """自评要抄的那一栏：哪几类业务靠线下承接，各有多少、复核了没有。"""
    return {
        "by_object_type": fetch_all(conn, "SELECT * FROM das_offline_coverage"),
        "process_overrun": fetch_all(conn, "SELECT * FROM das_manual_process_overrun"),
        "change_path": fetch_all(conn, "SELECT * FROM das_change_evidence_path"),
        "minor_path": fetch_all(conn, "SELECT * FROM das_minor_evidence_path"),
    }


def approval(conn: psycopg.Connection, approval_id: int) -> dict:
    row = fetch_one(conn, """SELECT * FROM das_offline_approval_register WHERE id=%s""",
                    (approval_id,))
    if not row:
        raise LookupError(f"没有编号为 {approval_id} 的线下审批记录")
    row["evidence"] = fetch_all(conn, """
        SELECT id, filename, mime_type, size_bytes, sha256, original_location,
               uploaded_at
          FROM das_offline_evidence WHERE approval_id=%s ORDER BY uploaded_at
    """, (approval_id,))
    return row


def record(conn: psycopg.Connection, *, object_type: str, object_key: str,
           subject: str, approver: str, approved_on: dt.date, form_ref: str,
           conclusion: str, original_location: str, filename: str, mime_type: str,
           content: bytes, manual_process_code: str | None = None,
           note: str | None = None, actor: dict) -> dict:
    """登记一次线下审批**并同时上传证据文件**。

    【为什么是一个调用而不是两个】
    DCMS-INV-115 要求线下审批记录必须有证据文件, 它是个 DEFERRABLE 约束触发器,
    在事务末尾检查。分成"先建记录、再上传"两个请求的话, 第一个请求必然失败 ——
    那就等于逼调用方把两件事塞进一个事务, 而 HTTP 一个请求就是一个事务。
    所以这里一次做完: 没有证据就没有记录, 和这个决定本身一致。
    """
    for name, val in (("对象类型", object_type), ("对象编号", object_key),
                      ("事项", subject), ("线下批准人", approver),
                      ("表单编号", form_ref), ("结论", conclusion),
                      ("纸质原件存放位置", original_location)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    if not content:
        raise ValueError(
            "证据文件不得为空。2026-10-02 的决定是「系统实现不了的走线下审批，"
            "**证据资料上传系统**」——没有上传的证据就不成立，"
            "而只有结论没有扫描件的记录比没有记录更坏：台账上看起来有。")
    if len(content) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"证据文件超过 {MAX_EVIDENCE_BYTES // 1024 // 1024}MB 上限")
    if approved_on > dt.date.today():
        raise ValueError("线下审批日期不能是将来：补录的是已经发生的事")
    if manual_process_code:
        p = fetch_one(conn, """SELECT approved_on, retired_on FROM das_manual_process
                                WHERE code=%s""", (manual_process_code,))
        if not p:
            raise LookupError(f"没有编号为 {manual_process_code} 的替代流程")
        if not p["approved_on"]:
            raise ValueError(
                f"替代流程 {manual_process_code} 尚未经责任经理批准。判据 B1："
                f"须由**已批准的**人工替代流程承接——未批准就照着做，等于那条体系要求"
                f"此刻既不在系统里、也不在一个受控的线下流程里。")
        if p["retired_on"] and approved_on > p["retired_on"]:
            raise ValueError(
                f"替代流程 {manual_process_code} 已于 {p['retired_on']} 停用"
                f"（对应模块已上线），此后的审批应走系统（判据 B1 第六项：退出条件）。")

    row = fetch_one(conn, """
        INSERT INTO das_offline_approval
               (object_type, object_key, manual_process_code, subject, approver,
                approved_on, form_ref, conclusion, note, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, object_type, object_key, subject, approved_on, recorded_at
    """, (object_type.strip(), object_key.strip(), manual_process_code,
          subject.strip(), approver.strip(), approved_on, form_ref.strip(),
          conclusion.strip(), note, actor["user_id"]))

    safe_form = _SAFE.sub("_", form_ref.strip())[-80:] or "form"
    safe_name = _SAFE.sub("_", filename or "evidence.bin")[-120:] or "evidence.bin"
    key = f"das-offline/{approved_on:%Y}/{row['id']}-{safe_form}/{safe_name}"
    stored = storage.save(key, content)
    ev = fetch_one(conn, """
        INSERT INTO das_offline_evidence
               (approval_id, filename, storage_key, mime_type, size_bytes, sha256,
                original_location, uploaded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, filename, size_bytes, sha256
    """, (row["id"], (filename or "evidence.bin").strip(), stored.storage_key,
          (mime_type or "application/octet-stream").strip(), stored.size_bytes,
          stored.sha256, original_location.strip(), actor["user_id"]))
    row["evidence"] = [ev]
    audit.write(conn, action="DAS_OFFLINE_APPROVAL", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_OFFLINE_APPROVAL",
                object_id=str(row["id"]),
                object_code=f"{object_type.strip()}/{object_key.strip()}",
                new_value={"subject": subject.strip(), "approver": approver.strip(),
                           "form_ref": form_ref.strip(),
                           "approved_on": str(approved_on),
                           "evidence_sha256": stored.sha256,
                           "original_location": original_location.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def add_evidence(conn: psycopg.Connection, *, approval_id: int, filename: str,
                 mime_type: str, content: bytes, original_location: str,
                 actor: dict) -> dict:
    """给已有的线下审批再加一份证据（多页扫描、补充附件）。"""
    a = fetch_one(conn, """SELECT id, approved_on, form_ref FROM das_offline_approval
                            WHERE id=%s""", (approval_id,))
    if not a:
        raise LookupError(f"没有编号为 {approval_id} 的线下审批记录")
    if not content:
        raise ValueError("证据文件不得为空")
    if len(content) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"证据文件超过 {MAX_EVIDENCE_BYTES // 1024 // 1024}MB 上限")
    if not (original_location or "").strip():
        raise ValueError("须写明纸质原件存放位置（判据 N1：扫描件与原件一并归档）")
    safe_form = _SAFE.sub("_", a["form_ref"])[-80:] or "form"
    safe_name = _SAFE.sub("_", filename or "evidence.bin")[-120:] or "evidence.bin"
    n = scalar(conn, "SELECT count(*) FROM das_offline_evidence WHERE approval_id=%s",
               (approval_id,))
    key = f"das-offline/{a['approved_on']:%Y}/{approval_id}-{safe_form}/{n + 1}-{safe_name}"
    stored = storage.save(key, content)
    return fetch_one(conn, """
        INSERT INTO das_offline_evidence
               (approval_id, filename, storage_key, mime_type, size_bytes, sha256,
                original_location, uploaded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, filename, size_bytes, sha256
    """, (approval_id, (filename or "evidence.bin").strip(), stored.storage_key,
          (mime_type or "application/octet-stream").strip(), stored.size_bytes,
          stored.sha256, original_location.strip(), actor["user_id"]))


def review(conn: psycopg.Connection, *, approval_id: int,
           reviewed_on: dt.date | None = None, actor: dict) -> dict:
    """复核补录的线下审批（判据 N-总：复核前不具有权威性）。

    复核之后 das_offline_covered() 才为真, M4 那两道闸门才认这条线下承接。
    """
    a = fetch_one(conn, """SELECT id, subject, recorded_by, reviewed_by
                             FROM das_offline_approval WHERE id=%s""", (approval_id,))
    if not a:
        raise LookupError(f"没有编号为 {approval_id} 的线下审批记录")
    if a["reviewed_by"]:
        raise ValueError("该记录已复核")
    if not scalar(conn, "SELECT count(*) FROM das_offline_evidence WHERE approval_id=%s",
                  (approval_id,)):
        raise ValueError("该记录还没有证据文件，无从复核")
    execute(conn, """UPDATE das_offline_approval SET reviewed_by=%s, reviewed_on=%s
                      WHERE id=%s""",
            (actor["user_id"], reviewed_on or dt.date.today(), approval_id))
    audit.write(conn, action="DAS_OFFLINE_REVIEW", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_OFFLINE_APPROVAL",
                object_id=str(approval_id), object_code=a["subject"],
                new_value={"reviewed_on": str(reviewed_on or dt.date.today()),
                           "self_reviewed": str(a["recorded_by"]) == str(actor["user_id"])},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_offline_approval_register WHERE id=%s",
                     (approval_id,))


def self_reviewed(conn: psycopg.Connection) -> list[dict]:
    """补录人与复核人是同一人的。

    **可见性清单, 不阻断。** 补录人自己复核, "走线下"就变成了"自己说一声就行";
    但 5～8 人编制下某些时候避不开, 所以列出来交独立监督按 UG-DAP-13 核对,
    理由同判据 I5／I6 的处理。
    """
    return fetch_all(conn, """
        SELECT a.id, a.object_type, a.object_key, a.subject, a.approved_on,
               u.full_name, u.employee_no, a.recorded_at, a.reviewed_on
          FROM das_offline_approval a
          JOIN app_user u ON u.id = a.recorded_by
         WHERE a.reviewed_by = a.recorded_by
         ORDER BY a.approved_on DESC
    """)
