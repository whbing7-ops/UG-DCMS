"""上线前六项控制验证与投用闸门（基础能力）。

依据 UG-DAW-005 第 1 章（上线前须验证身份、授权、独立性、冻结、日志和备份恢复六项
控制并保留结果）、第 7 章，UG-DAW-001 第 3 章，判据 N16、N6、N15、B2。

【这个模块不是检查表】
"六项已验证"写成六个勾，谁都签得下去。N16 要的是"验证**并保留结果**"，而结果得能回到
证据：哪一次 CI、哪个提交、抽查了几份、每一项核对的结论是什么。所以每条验证记录都
必须带提交号与批次号，写入后不得改写也不得删除（DCMS-INV-089）。

【绑在构建上，不绑在时间上】
最容易出的错不是漏验，是**验过之后代码又改了**。验证记录带 `build_ref`，投用闸门要求
验证的构建与批次当前的构建相同；批次换了构建，之前的验证一律不算，由
`das_release_stale` 列出来。否则"上线前已验证"这句话指向的是另一个版本的系统，
而这种偏差没有任何外部迹象。

正因如此，`set_build` 的门槛和投用一样高：往前改构建只会让闸门更严（旧验证失效），
但**往回改**能让一个已经不成立的绿灯重新生效——把 build_ref 调回那个有 PASS 的旧构建，
闸门就放行了。这是本模块唯一一条能把闸门整体绕过去的路。

【"冻结"是两个机制】
UG-DAW-005 只写"冻结"两个字。本系统里版次冻结（判据 N2）与记录冻结（判据 R5）是两张表、
两个触发器、两种失效后果。只测一个而记"冻结已验证"是一条假符合，所以它是容器项，
结果落在两个子项上。父项编号保持与源文件一致，自评仍可答"六项"。

【执行人与记录人不是一回事】
`verified_by` 写的是**实际执行验证的人或单位**，可以是外部的（如第三方恢复演练）。
本模块的岗位门槛管的是**谁有权把结果记进系统**：适航管理负责人（设计保证系统归口）
或资料管理负责人（UG-DAW-004／005 的归口）。外部人员执行时须显式声明
`verifier_is_external`——留空账号不写理由，`das_release_self_verified` 那张表就形同虚设。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

RESULTS = ("PASS", "FAIL")
POSITION_CN = {"AWM": "适航管理负责人", "DCM": "资料管理负责人"}


def _holds(conn: psycopg.Connection, actor: dict, code: str) -> bool:
    return scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code=%s""",
                  (actor["user_id"], code)) > 0


def _require_recorder(conn: psycopg.Connection, actor: dict) -> None:
    """记录验证结果的岗位门槛。

    管的是"谁有权把结果记进系统", 不是"谁执行的验证"——后者写在 verified_by 里,
    可以是外部单位。
    """
    if not (_holds(conn, actor, "AWM") or _holds(conn, actor, "DCM")):
        raise PermissionError(
            "登记上线前验证结果须由在任适航管理负责人（设计保证系统归口）"
            "或资料管理负责人（UG-DAW-004／005 归口）执行。"
            "实际执行验证的人写在 verified_by 里，可以是外部单位；"
            "这道门槛管的是谁有权把结果记进系统。请先按 UG-DAP-03 完成任命。")


def _require_awm(conn: psycopg.Connection, actor: dict, what: str) -> None:
    if not _holds(conn, actor, "AWM"):
        raise PermissionError(
            f"{what}须由在任适航管理负责人执行（设计保证系统归口）。"
            f"请先按 UG-DAP-03 完成任命。")


def items(conn: psycopg.Connection) -> list[dict]:
    """UG-DAW-005 第 1 章的六项控制。容器项与子机制一并返回。"""
    return fetch_all(conn, """SELECT * FROM das_release_check_item
                               ORDER BY seq, code""")


def batches(conn: psycopg.Connection) -> list[dict]:
    """批次及其就绪情况（设计输入第 8.6 节）。"""
    return fetch_all(conn, """
        SELECT b.*,
               (SELECT count(*) FROM das_release_readiness r
                 WHERE r.batch_code = b.code AND r.state = 'VERIFIED') AS verified_items,
               (SELECT count(*) FROM das_release_readiness r
                 WHERE r.batch_code = b.code AND r.state <> 'VERIFIED') AS blocking_items,
               (SELECT count(*) FROM das_release_stale s
                 WHERE s.batch_code = b.code)                          AS stale_records,
               c.commissioned_on, c.build_ref AS commissioned_build, c.approval_ref,
               (SELECT count(*) FROM das_release_commissioning x
                 WHERE x.batch_code = b.code)                          AS commissionings,
               -- 当前构建本身投用了没有。批次投用过 ≠ 现在跑的这一版投用过:
               -- 前者一旦为真就永远为真, 后者才是"现在这一版走完了 N16"。
               (c.build_ref IS NOT NULL AND c.build_ref = b.build_ref) AS current_in_service
          FROM das_release_batch b
          -- 一个批次可投用多次（每个构建一次）, 这里取最近一次, 否则批次会出现多行
          LEFT JOIN LATERAL (
                SELECT * FROM das_release_commissioning x
                 WHERE x.batch_code = b.code
                 ORDER BY x.commissioned_on DESC, x.id DESC LIMIT 1) c ON true
         ORDER BY b.code
    """)


def readiness(conn: psycopg.Connection, batch_code: str | None = None) -> list[dict]:
    """批次 × 叶子控制项 → 本构建上最近一次验证。

    `state` 为 VERIFIED 以外的任一值（NO_BUILD／NOT_VERIFIED／FAILED）都意味着
    该批次不得投用。
    """
    if batch_code:
        return fetch_all(conn, """SELECT * FROM das_release_readiness
                                   WHERE batch_code=%s""", (batch_code,))
    return fetch_all(conn, "SELECT * FROM das_release_readiness")


def blockers(conn: psycopg.Connection, batch_code: str | None = None) -> list[dict]:
    if batch_code:
        return fetch_all(conn, "SELECT * FROM das_release_blocker WHERE batch_code=%s",
                         (batch_code,))
    return fetch_all(conn, "SELECT * FROM das_release_blocker")


def stale(conn: psycopg.Connection) -> list[dict]:
    """验过之后构建又变了的记录——不计入闸门，须在当前构建上重新验证。"""
    return fetch_all(conn, "SELECT * FROM das_release_stale")


def oversight(conn: psycopg.Connection) -> dict:
    """交独立监督核对的两张可见性清单，均不阻断。

    `self_verified`：执行人同时是投用批准人（判据 I6 的同形问题）。
    `external`：声明为外部人员执行的——正当做法，但也是绕过上一张表的唯一路径，
    所以要能一眼看到哪几项走的是这条路。
    """
    return {
        "self_verified": fetch_all(conn, "SELECT * FROM das_release_self_verified"),
        "external": fetch_all(conn, "SELECT * FROM das_release_external_verifier"),
        "item_mismatch": fetch_all(conn, "SELECT * FROM das_release_item_mismatch"),
    }


def set_build(conn: psycopg.Connection, *, batch_code: str, build_ref: str,
              actor: dict) -> dict:
    """登记本批次当前待验证的构建号。

    门槛与投用一样高, 理由在模块说明里: 往回改构建能让一个已经不成立的绿灯重新生效。
    改动前后都写审计, 这样"构建被调回去过"这件事本身有痕迹。
    """
    _require_awm(conn, actor, "登记批次的构建号")
    if not (build_ref or "").strip():
        raise ValueError("构建号不得为空：不写构建号而宣布「上线前已验证」，"
                         "这句话指向的是哪一版无人知道。")
    cur = fetch_one(conn, "SELECT code, build_ref FROM das_release_batch WHERE code=%s",
                    (batch_code,))
    if not cur:
        raise LookupError(f"没有编号为 {batch_code} 的批次")
    prior = fetch_all(conn, """SELECT item_code, result FROM das_release_verification
                                WHERE batch_code=%s AND build_ref=%s""",
                      (batch_code, build_ref.strip()))
    execute(conn, """UPDATE das_release_batch SET build_ref=%s, updated_at=now()
                      WHERE code=%s""", (build_ref.strip(), batch_code))
    audit.write(conn, action="DAS_RELEASE_SET_BUILD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RELEASE_BATCH",
                object_id=batch_code, object_code=batch_code,
                old_value={"build_ref": cur["build_ref"]},
                new_value={"build_ref": build_ref.strip(),
                           # 把"这个构建上已经有几条旧验证"一并记下来: 调回旧构建时
                           # 这个数不为零, 正是该引起注意的地方。
                           "existing_verifications_on_target": len(prior)},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_release_batch WHERE code=%s", (batch_code,))


def record_verification(conn: psycopg.Connection, *, batch_code: str, item_code: str,
                        method: str, commit_ref: str, batch_ref: str, result: str,
                        verified_by: str, verifier_is_external: bool = False,
                        verified_by_user: str | None = None,
                        evidence_ref: str | None = None, ncr_id: int | None = None,
                        sample_count: int | None = None,
                        content_ok: bool | None = None, version_ok: bool | None = None,
                        signature_ok: bool | None = None,
                        sample_note: str | None = None, actor: dict) -> dict:
    """登记一项控制的验证结果（判据 N16）。

    验的是**批次当前登记的那个构建**: build_ref 不由调用方给, 从批次读 ——
    让调用方自己填构建号, 填错了就会得到一条指向别的版本的"已验证"。
    """
    _require_recorder(conn, actor)
    batch = fetch_one(conn, "SELECT code, build_ref FROM das_release_batch WHERE code=%s",
                      (batch_code,))
    if not batch:
        raise LookupError(f"没有编号为 {batch_code} 的批次")
    if not (batch["build_ref"] or "").strip():
        raise ValueError(
            f"批次 {batch_code} 还没有登记构建号。先登记构建号再验证——"
            f"否则这条验证记录指向的是哪一版系统无从判断（判据 N16：每批次上线前执行）。")
    item = fetch_one(conn, """SELECT code, name_cn, is_container, min_sample
                                FROM das_release_check_item WHERE code=%s""", (item_code,))
    if not item:
        raise LookupError(f"没有编号为 {item_code} 的控制项")
    if item["is_container"]:
        kids = fetch_all(conn, """SELECT code, name_cn FROM das_release_check_item
                                   WHERE parent_code=%s ORDER BY code""", (item_code,))
        raise ValueError(
            f"「{item['name_cn']}」在本系统里是 {len(kids)} 个不同的机制"
            f"（{'、'.join(k['name_cn'] for k in kids)}），结果要按机制分别记。"
            f"笼统记一条「已验证」等于没验：只测了其中一个，另一个失效时没人会知道。")
    if result not in RESULTS:
        raise ValueError(f"结果须为 {'/'.join(RESULTS)}")
    for name, val in (("验证方法", method), ("提交号", commit_ref),
                      ("批次号", batch_ref), ("执行人", verified_by)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（判据 N16：验证并保留结果）")
    if result == "FAIL" and ncr_id is None:
        raise ValueError(
            "验证失败即为不符合项（判据 N6／N15），必须挂一条不符合项编号。"
            "只在这里记一句「失败」然后没有下文，失败就只是一行字。")
    if ncr_id is not None and not scalar(
            conn, "SELECT count(*) FROM das_ncr WHERE id=%s", (ncr_id,)):
        raise LookupError(f"没有编号为 {ncr_id} 的不符合项")
    if not verified_by_user and not verifier_is_external:
        raise ValueError(
            "执行人要么给出系统账号，要么显式声明为外部人员。"
            "留空账号又不说明是外部执行，自验自批的核对清单就形同虚设——"
            "不填账号就看不见，而清单还是干净的。")
    if item["min_sample"] is not None:
        if sample_count is None or sample_count < item["min_sample"]:
            raise ValueError(
                f"「{item['name_cn']}」须抽查至少 {item['min_sample']} 份并记明份数"
                f"（判据 N15）。恢复验证不是「能启动」，是还原到测试环境后抽查若干份"
                f"逐项核对内容、版次和签署记录。")
        if content_ok is None or version_ok is None or signature_ok is None:
            raise ValueError(
                "内容、版次、签署记录三项须各自给出结论（判据 N15）。"
                "少填一项就无从判断这次恢复验证到底核对了什么。")
        if result == "PASS" and not (content_ok and version_ok and signature_ok):
            failed = [n for n, v in (("内容", content_ok), ("版次", version_ok),
                                     ("签署记录", signature_ok)) if not v]
            raise ValueError(
                f"三项核对中「{'、'.join(failed)}」为否，结论不得写 PASS。"
                f"抽查发现问题却判通过，比不抽查更坏——它留下了一条说已核对过的记录。")
    elif any(v is not None for v in (sample_count, content_ok, version_ok,
                                     signature_ok, sample_note)):
        # 这一支数据库也拦（DCMS-INV-088）, 但那条路返回 409 ——
        # 本端点其它所有参数错误都是 400 带一句说明。少了这道预检, 同一个端点上
        # "填错了"会有两种状态码, 而 409 会被客户端当成"冲突, 待会儿重试"。
        raise ValueError(
            f"抽查份数与三项核对只适用于有抽查要求的控制项，「{item['name_cn']}」不是。"
            f"要抽查的是备份恢复（判据 N15：还原到测试环境抽查若干份）。")

    row = fetch_one(conn, """
        INSERT INTO das_release_verification
               (batch_code, item_code, build_ref, method, commit_ref, batch_ref,
                evidence_ref, result, ncr_id, sample_count, content_ok, version_ok,
                signature_ok, sample_note, verified_by, verified_by_user,
                verifier_is_external)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, batch_code, item_code, build_ref, result, verified_at
    """, (batch_code, item_code, batch["build_ref"], method.strip(), commit_ref.strip(),
          batch_ref.strip(), (evidence_ref or "").strip() or None, result, ncr_id,
          sample_count, content_ok, version_ok, signature_ok,
          (sample_note or "").strip() or None, verified_by.strip(),
          verified_by_user, verifier_is_external))
    audit.write(conn, action="DAS_RELEASE_VERIFY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RELEASE_VERIFICATION",
                object_id=str(row["id"]), object_code=f"{batch_code}/{item_code}",
                new_value={"build": batch["build_ref"], "result": result,
                           "commit": commit_ref.strip(), "batch": batch_ref.strip(),
                           "verified_by": verified_by.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def commission(conn: psycopg.Connection, *, batch_code: str,
               commissioned_on: dt.date | None = None, approval_ref: str | None = None,
               note: str | None = None, actor: dict) -> dict:
    """投用本批次（判据 N16 的前置条件、判据 B2）。

    闸门在数据库里（DCMS-INV-087）: 六项按机制展开后的每一项都要有本构建的 PASS,
    且取最近一次 —— 后一次 FAIL 必须能推翻前一次 PASS。这里先把阻断项列出来,
    给的是"还差哪几项"而不是一句"不满足条件"。
    """
    _require_awm(conn, actor, "批次投用")
    batch = fetch_one(conn, "SELECT code, build_ref FROM das_release_batch WHERE code=%s",
                      (batch_code,))
    if not batch:
        raise LookupError(f"没有编号为 {batch_code} 的批次")
    done = fetch_one(conn, """SELECT commissioned_on FROM das_release_commissioning
                               WHERE batch_code=%s AND build_ref=%s""",
                     (batch_code, batch["build_ref"]))
    if done:
        raise ValueError(
            f"批次 {batch_code} 的构建 {batch['build_ref']} 已于 "
            f"{done['commissioned_on']} 投用过，不重复登记。"
            f"发了新版本就先登记新的构建号，在那个构建上重新验证六项"
            f"（判据 N16：每批次上线前执行——验证不随构建迁移）。")
    blocking = blockers(conn, batch_code)
    if blocking:
        lines = "；".join(f"{b['seq']}. {b['item_name']}（{b['state']}）"
                          for b in blocking)
        raise ValueError(
            f"下列控制在构建 {batch['build_ref'] or '（未登记）'} 上还不满足，"
            f"本批次不得投用：{lines}。"
            f"（判据 N16：六项验证是该批次投用的前置条件；判据 B2：分批不得造成控制空档）")
    row = fetch_one(conn, """
        INSERT INTO das_release_commissioning
               (batch_code, build_ref, commissioned_on, approved_by, approval_ref, note)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, batch_code, build_ref, commissioned_on, approval_ref
    """, (batch_code, batch["build_ref"], commissioned_on or dt.date.today(),
          actor["user_id"], (approval_ref or "").strip() or None, note))
    audit.write(conn, action="DAS_RELEASE_COMMISSION", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_RELEASE_COMMISSIONING",
                object_id=batch_code, object_code=batch_code,
                new_value={"build": batch["build_ref"],
                           "commissioned_on": str(row["commissioned_on"]),
                           "approval_ref": row["approval_ref"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def verifications(conn: psycopg.Connection, batch_code: str) -> list[dict]:
    """某批次的全部验证记录，含已失效（构建不同）的那些。"""
    return fetch_all(conn, """
        SELECT v.*, i.name_cn AS item_name, i.seq,
               (v.build_ref = b.build_ref) AS current_build
          FROM das_release_verification v
          JOIN das_release_check_item i ON i.code = v.item_code
          JOIN das_release_batch b ON b.code = v.batch_code
         WHERE v.batch_code = %s
         ORDER BY i.seq, v.verified_at DESC
    """, (batch_code,))
