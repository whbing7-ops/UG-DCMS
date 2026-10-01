"""独立性规则登记册（基础能力）。

依据 CCAR-21.137（五）(七)、AP-21-18 附录 C，手册 3.1／3.2，UG-DAP-01／03／13，
判据 I1～I10、I-总、E2。

【这个模块为什么存在】
独立性约束原先分散在 signers／appointments／ncr／audit 各自的触发器与服务里。
分散本身没错——校验就该贴着被校验的那张表。错的是**没有一处能回答"判据 I3 在哪里
实现、测过没有、上次跑过没有"**。符合性自评要逐条回答这三个问题，靠人翻代码回答，
翻漏一条就是一条假符合，而假符合比如实写"未实现"严重得多。

【判据 E2：三栏分列，不许合并】
源码状态（有没有实现）／用例状态（有没有反例用例）／执行证据（这一版跑过没有）
是三件独立的事。合成一个"已实现"的勾，就把"写了触发器"与"验证过它真拦得住"
混为一谈。verdict 由这三栏推导，少哪一栏就说少哪一栏，刻意不叫"已实现"。

【判据 I-总：用例内容必须是反例】
`test_ref` 里要写的是"试着违反、确认被拒"的那条用例，不是"正常流程走得通"。
正常流程走得通的用例一条也证明不了独立性约束存在——约束被整个删掉它照样通过。

【判据 I10：不给总数】
`summary()` 按 verdict 分组，**不返回"已实现几条"**。给一个总数就是把八种状态压成
一个数字，而 I10 的四个维度状态各不相同（有效期已实现、专业部分实现、文件类型语义
不符、产品范围完全未实现），"4 维中 3 维已实现"这种说法恰恰是自评里最不能写的。

【写入权限：两类写入门槛不同，这是刻意的】
- 状态登记（`set_state`）是**判断**："这条要求实现到什么程度"。它直接决定自评里
  怎么写，所以要在任适航管理负责人（AWM，设计保证系统归口）才能改，且每次改动
  由触发器自动留痕（das_independence_rule_change）。
- 执行证据（`record_execution`）是**事实**："某批次跑了，结果是 PASS"。记录者是
  CI，不是人，所以**不设任命门槛**——设了就没人能写，第三栏永远空着，整本登记册
  就成了摆设。它的防护是可追溯性而不是授权：提交号与批次号必填（ck_die_text），
  写入后不得改写、不得删除（DCMS-INV-084），任何一条 PASS 都能拿批次号回去对 CI
  日志。伪造一条 PASS 做得到，但会留下一条对不上日志的记录。
- 登记册本身的真实性由独立监督按 UG-DAP-13 核查，问题记入 das_audit_finding，
  不在本表另开"独立监督已确认"字段——那个字段只会变成又一个没人核对的勾。
"""
from __future__ import annotations

import psycopg

from .. import audit
from ..db import fetch_all, fetch_one, scalar

SOURCE_STATES = {
    "PRESENT": "已实现",
    "PARTIAL": "部分实现",
    "MISMATCHED": "代码在跑但语义不符",
    "ABSENT": "未实现",
}
TEST_STATES = {
    "DEFINED": "已有反例用例",
    "ABSENT": "无反例用例",
    "NOT_APPLICABLE": "不适用（语义不符时测了也不证明什么）",
}
ENFORCEMENT_KINDS = ("DB_TRIGGER", "DB_CONSTRAINT", "DB_INDEX", "DB_FUNCTION",
                     "SERVICE_CHECK", "UI_ONLY", "NONE")
RESULTS = ("PASS", "FAIL")
GAP_KINDS = ("IMPLEMENTATION", "EVIDENCE", "DEFECT")


def _require_awm(conn: psycopg.Connection, actor: dict) -> None:
    """状态登记须在任适航管理负责人。

    不用 RBAC 角色: 角色是"能点哪个按钮", 任命是"这个人现在是不是这个岗"。
    独立性登记册的内容会被逐条抄进符合性自评, 改它等于改对局方的陈述。
    """
    held = scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code='AWM'""",
                  (actor["user_id"],)) > 0
    if not held:
        raise PermissionError(
            "登记独立性规则的实现状态须由在任适航管理负责人执行（设计保证系统归口）。"
            "这一栏会被逐条抄进符合性自评，改它等于改对局方的陈述。"
            "请先按 UG-DAP-03 完成任命。")


def matrix(conn: psycopg.Connection, *, verdict: str | None = None) -> list[dict]:
    """三栏矩阵全表（判据 E2）。"""
    if verdict:
        return fetch_all(conn, """SELECT * FROM das_independence_matrix WHERE verdict=%s
                                   ORDER BY code""", (verdict,))
    return fetch_all(conn, "SELECT * FROM das_independence_matrix ORDER BY code")


def gaps(conn: psycopg.Connection, *, kind: str | None = None) -> list[dict]:
    """缺口清单。`gap_kind` 分 IMPLEMENTATION／EVIDENCE／DEFECT 三类，处置方式不同。"""
    if kind:
        if kind not in GAP_KINDS:
            raise ValueError(f"缺口类别须为 {'/'.join(GAP_KINDS)} 之一")
        return fetch_all(conn, "SELECT * FROM das_independence_gap WHERE gap_kind=%s",
                         (kind,))
    return fetch_all(conn, "SELECT * FROM das_independence_gap")


def summary(conn: psycopg.Connection) -> list[dict]:
    """按 verdict 分组。**不含总数**（判据 I10）。"""
    return fetch_all(conn, "SELECT * FROM das_independence_summary")


def unexecuted(conn: psycopg.Connection) -> list[dict]:
    """用例已写、这一版还没跑过的（判据 E2：两者不是一件事）。"""
    return fetch_all(conn, "SELECT * FROM das_independence_unexecuted")


def health(conn: psycopg.Connection) -> dict:
    """登记册与系统本身是否对得上。两张表平时都应为空。

    `object_missing` 非空 = 登记册声称某个触发器在保证某条规则, 而系统目录里没有这个
    对象。插入时由 DCMS-INV-083 拦住, 但**后续迁移删掉一个对象不会回来改登记册**,
    所以要持续查。这是最难发现的一类假覆盖: 登记册看上去是满的。
    `container_mismatch` 非空 = 层级与状态落点错位, 判据 I10 禁止的"笼统称已实现"
    就是从这种错位开始的。
    """
    return {
        "object_missing": fetch_all(conn,
                                    "SELECT * FROM das_independence_object_missing"),
        "container_mismatch": fetch_all(
            conn, "SELECT * FROM das_independence_container_mismatch"),
    }


def mutual_backup(conn: psycopg.Connection) -> list[dict]:
    """互为备份的组合（判据 I5）。

    **这是可见性清单, 不是违规清单。** 原文禁的是"互为备份**并**相互核查对方编制的
    资料"这个合取。系统判不了后半截(它不知道某份资料是谁编的), 硬禁互为备份又会在
    5～8 人编制下过度约束。所以列出来交独立监督按 UG-DAP-13 核对, I5 如实记为部分实现。
    """
    return fetch_all(conn, "SELECT * FROM das_independence_mutual_backup")


def rule(conn: psycopg.Connection, code: str) -> dict:
    """单条规则：当前状态、子维度、改动留痕、最近执行记录。"""
    row = fetch_one(conn, "SELECT * FROM das_independence_matrix WHERE code=%s", (code,))
    if not row:
        raise LookupError(f"没有编号为 {code} 的独立性规则")
    row["children"] = fetch_all(
        conn, """SELECT * FROM das_independence_matrix WHERE parent_code=%s
                  ORDER BY code""", (code,))
    row["changes"] = fetch_all(
        conn, """SELECT * FROM das_independence_rule_change WHERE rule_code=%s
                  ORDER BY changed_at DESC""", (code,))
    row["execution_log"] = fetch_all(
        conn, """SELECT id, commit_ref, batch_ref, result, evidence_ref, executed_at
                   FROM das_independence_execution WHERE rule_code=%s
                  ORDER BY executed_at DESC LIMIT 50""", (code,))
    return row


def set_state(conn: psycopg.Connection, *, code: str,
              source_state: str | None = None, enforcement_kind: str | None = None,
              enforcement_object: str | None = None, invariant_code: str | None = None,
              test_state: str | None = None, test_ref: str | None = None,
              manual_control: str | None = None, gap_note: str | None = None,
              actor: dict) -> dict:
    """改一条规则的实现状态。未传的字段保持原值；传空串表示清空。

    不接受容器项: 有子维度的父项状态只落在子项上（DCMS-INV-086 也会拦, 这里先给出
    能看懂的话）。
    """
    _require_awm(conn, actor)
    cur = fetch_one(conn, """SELECT code, is_container, source_state, test_state,
                                    test_ref, manual_control, enforcement_object
                               FROM das_independence_rule WHERE code=%s""", (code,))
    if not cur:
        raise LookupError(f"没有编号为 {code} 的独立性规则")
    if cur["is_container"]:
        raise ValueError(
            f"{code} 有子维度，状态只能落在子维度上（判据 I10）。"
            f"给父项填一个笼统状态，正是自评里最不能写的那种写法。")
    if source_state and source_state not in SOURCE_STATES:
        raise ValueError(f"源码状态须为 {'/'.join(SOURCE_STATES)} 之一")
    if test_state and test_state not in TEST_STATES:
        raise ValueError(f"用例状态须为 {'/'.join(TEST_STATES)} 之一")
    if enforcement_kind and enforcement_kind not in ENFORCEMENT_KINDS:
        raise ValueError(f"实现方式须为 {'/'.join(ENFORCEMENT_KINDS)} 之一")
    # 用例位置要在这里就拦住, 不等数据库: 这条拦的是"说有用例却说不出在哪",
    # 而说不出在哪的用例在自评里和没有用例是一回事。
    #
    # 【必须用**改完之后**的值判, 不能只看请求里传了什么】
    # 只看请求体的话, 一条本来就写好了用例位置的规则,
    # 只要再次传 test_state='DEFINED' 而不重复传 test_ref 就会被拒,
    # 报出来的还是"必须写明用例位置" —— 该做的都做了却说没做,
    # 接下来人就去猜格式了。数据库的 ck_dir_testref 本身是按行判的,
    # 服务层这道预检不能比它严。
    eff_test_state = test_state if test_state is not None else cur["test_state"]
    eff_test_ref = test_ref if test_ref is not None else cur["test_ref"]
    if eff_test_state == "DEFINED" and not (eff_test_ref or "").strip():
        raise ValueError(
            "称已有反例用例时必须写明用例位置与用例描述（判据 I-总）。"
            "写的要是「试着违反、确认被拒」那条，不是「正常流程走得通」——"
            "后者把约束整个删掉也照样通过。")

    # 同样按改完之后的行判: 实现不齐备就得说清在齐备前靠什么把关。
    # 不把这条留给数据库的 ck_dir_manual, 是因为约束报出来只有
    # "违反数据库约束 ck_dir_manual" —— 而这一条要填的正是人的判断,
    # 报一个约束名给他, 他不知道该补什么。
    eff_source_state = source_state if source_state is not None else cur["source_state"]
    eff_manual = manual_control if manual_control is not None else cur["manual_control"]
    if (eff_source_state is not None
            and (eff_source_state != "PRESENT" or eff_test_state == "ABSENT")
            and not (eff_manual or "").strip()):
        why = ("实现本身有缺口" if eff_source_state != "PRESENT"
               else "实现在、但从没有验证过它真拦得住")
        raise ValueError(
            f"{why}，必须同时写明在补齐前靠什么人工控制把关"
            f"（manual_control）。符合性自评里这一格空着，"
            f"等于对局方说“这条要求没人管”。")

    sets, params = [], []
    for col, val in (("source_state", source_state),
                     ("enforcement_kind", enforcement_kind),
                     ("enforcement_object", enforcement_object),
                     ("invariant_code", invariant_code),
                     ("test_state", test_state), ("test_ref", test_ref),
                     ("manual_control", manual_control), ("gap_note", gap_note)):
        if val is None:
            continue
        sets.append(f"{col}=%s")
        params.append(val.strip() or None)
    if not sets:
        raise ValueError("没有要改的字段")
    sets.append("updated_at=now()")
    params.append(code)
    row = fetch_one(conn, f"""UPDATE das_independence_rule SET {', '.join(sets)}
                               WHERE code=%s
                           RETURNING code, source_state, test_state,
                                     enforcement_object""", tuple(params))
    audit.write(conn, action="DAS_INDEPENDENCE_SET_STATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_INDEPENDENCE_RULE",
                object_id=code, object_code=code,
                old_value={"source_state": cur["source_state"],
                           "test_state": cur["test_state"],
                           "enforcement_object": cur["enforcement_object"]},
                new_value={"source_state": row["source_state"],
                           "test_state": row["test_state"],
                           "enforcement_object": row["enforcement_object"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, "SELECT * FROM das_independence_matrix WHERE code=%s", (code,))


def record_execution(conn: psycopg.Connection, *, rule_code: str, commit_ref: str,
                     batch_ref: str, result: str, evidence_ref: str | None = None,
                     actor: dict | None = None) -> dict:
    """登记一次反例用例的执行（判据 E2 第三栏）。

    **不设任命门槛**: 记录者是 CI。门槛设在这里, 第三栏就永远是空的。
    防护是可追溯: 提交号与批次号必填, 写入后不得改写也不得删除（DCMS-INV-084）,
    每一条 PASS 都能拿批次号回去对 CI 日志。
    """
    for name, val in (("提交号", commit_ref), ("批次号", batch_ref)):
        if not (val or "").strip():
            raise ValueError(
                f"{name}不得为空。执行证据的全部防护就是可追溯——不写{name}，"
                f"这条记录就无法回到 CI 日志核对，等于一句无法验证的「跑过了」。")
    if result not in RESULTS:
        raise ValueError(f"结果须为 {'/'.join(RESULTS)}")
    exists = scalar(conn, "SELECT count(*) FROM das_independence_rule WHERE code=%s",
                    (rule_code,)) > 0
    if not exists:
        raise LookupError(f"没有编号为 {rule_code} 的独立性规则")
    row = fetch_one(conn, """
        INSERT INTO das_independence_execution
               (rule_code, commit_ref, batch_ref, result, evidence_ref, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id, rule_code, commit_ref, batch_ref, result, executed_at
    """, (rule_code, commit_ref.strip(), batch_ref.strip(), result,
          (evidence_ref or "").strip() or None, (actor or {}).get("user_id")))
    if actor:
        audit.write(conn, action="DAS_INDEPENDENCE_EXECUTION",
                    user_id=str(actor["user_id"]), username=actor["username"],
                    object_type="DAS_INDEPENDENCE_EXECUTION", object_id=str(row["id"]),
                    object_code=rule_code,
                    new_value={"batch": batch_ref, "commit": commit_ref,
                               "result": result},
                    session_id=str(actor.get("session_id")),
                    client_ip=actor.get("client_ip"))
    return row
