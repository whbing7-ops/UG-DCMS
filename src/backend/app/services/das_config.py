"""M5 构型管理（UG-DAP-05，9 步）。

依据 UG-DAP-05 第 6 章、UG-DAW-009《构型标识和基线规则》、UG-DAF-10／UG-DAF-11。

【按 2026-10-02 的决定选闸门：只在便宜且不含糊的地方设】
这份程序里有四条写着"不得"的规则，都只需要一句话就能判，而判错的后果都很实在：

① 第 2 步：不可互换或单向互换的更改**必须更换件号**。
② 第 3 步：基线由授权人员批准后在系统中冻结，**冻结后只能通过批准的更改修改**。
③ 第 5 步：**未明确有效性范围的更改不得发布。**
④ 第 7 步 b：交付前核查的差异**未关闭的不得放行**。

其余不做逐项录入。第 7 步 a 的设计内部一致性核查要比对图纸、数模、BOM、规范、工艺、
手册六者之间的参数、版次和引用标准——这不是系统判得了的事，也不该逼人把六方比对录成
表格，按 2026-10-02 的决定走线下，结论记在 `das_config_audit`、证据挂
`das_offline_approval`。第 6 步的构型纪实做成视图（按产品、按日期查得出"某台产品由
哪些件号和版次构成"），那是查询能力，不是闸门。

【互换性那一条为什么值得一道硬闸门】
判错的后果不在系统里。一个不可互换的件沿用旧件号发出去，现场会把它装到装不上、
或装上去不安全的位置——**而装的人手里的件号是对的**。这正是件号制度要防的那件事，
而判它只要一个布尔。
"""
from __future__ import annotations

import datetime as dt

import psycopg

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar

ITEM_KINDS = {"DRAWING": "图纸", "BOM": "BOM", "SPEC": "规范",
              "SOFTWARE": "软件", "TECH_COND": "技术条件"}
BASELINE_KINDS = {"CERT": "审定基线（取证时向局方提交的构型）",
                  "DESIGN": "设计基线（图纸和 BOM 冻结、可向生产和供应商发放）",
                  "DELIVERY": "交付基线（每台产品交付时的实际构型）"}
VERDICTS = {"FULL": "完全互换（升版次）", "ONE_WAY": "单向互换（换件号）",
            "NONE": "不可互换（换件号）"}
AUDIT_KINDS = {"INTERNAL_CONSISTENCY": "设计内部一致性核查",
               "PRE_DELIVERY": "交付前核查（as-built 与 as-designed 逐项比对）"}
POSITION_CN = {"PM": "项目负责人", "CFG": "构型管理员", "DE": "设计工程师",
               "AWM": "适航管理负责人"}


def _require(conn: psycopg.Connection, actor: dict, codes, what: str,
             basis: str) -> None:
    if isinstance(codes, str):
        codes = (codes,)
    held = scalar(conn, """SELECT count(*) FROM das_appointment_in_force
                            WHERE user_id=%s AND position_code = ANY(%s)""",
                  (actor["user_id"], list(codes)))
    if not held:
        who = "或".join(POSITION_CN[c] for c in codes)
        raise PermissionError(f"{what}须由在任{who}执行（{basis}）。"
                              f"请先按 UG-DAP-03 完成任命。")


def _project(conn: psycopg.Connection, project_no: str) -> dict:
    row = fetch_one(conn, "SELECT id, project_no FROM das_project WHERE project_no=%s",
                    (project_no,))
    if not row:
        raise LookupError(f"没有编号为 {project_no} 的项目")
    return row


def items(conn: psycopg.Connection, project_no: str | None = None) -> list[dict]:
    if project_no:
        return fetch_all(conn, """SELECT i.*, p.project_no FROM das_config_item i
                                    JOIN das_project p ON p.id = i.project_id
                                   WHERE p.project_no=%s ORDER BY i.identifier""",
                         (project_no,))
    return fetch_all(conn, """SELECT i.*, p.project_no FROM das_config_item i
                                JOIN das_project p ON p.id = i.project_id
                               ORDER BY p.project_no, i.identifier""")


def add_item(conn: psycopg.Connection, *, project_no: str, item_kind: str,
             identifier: str, name_cn: str, inclusion_reason: str,
             form_ref: str | None = None, note: str | None = None,
             actor: dict) -> dict:
    """纳入构型控制（第 1 步，UG-DAF-10）。

    纳入理由必填: 原文"凡影响**形状、配合、功能、适航性或可追溯性**的…均须纳入",
    所以这一栏要指明是哪一类影响 —— 空着就说不出为什么这个项目在清单里、那个不在。
    """
    _require(conn, actor, ("PM", "CFG"), "纳入构型项目",
             "UG-DAP-05 第 1 步：项目负责人会同各专业确定")
    if item_kind not in ITEM_KINDS:
        raise ValueError(f"项目类别须为 {'/'.join(ITEM_KINDS)} 之一")
    p = _project(conn, project_no)
    for name, val in (("标识", identifier), ("名称", name_cn),
                      ("纳入理由", inclusion_reason)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    if scalar(conn, """SELECT count(*) FROM das_config_item
                        WHERE project_id=%s AND identifier=%s""",
              (p["id"], identifier.strip())):
        raise ValueError(f"{project_no} 的构型项目清单里已有 {identifier}")
    row = fetch_one(conn, """
        INSERT INTO das_config_item
               (project_id, item_kind, identifier, name_cn, inclusion_reason,
                form_ref, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, item_kind, identifier, name_cn
    """, (p["id"], item_kind, identifier.strip(), name_cn.strip(),
          inclusion_reason.strip(), (form_ref or "").strip() or None, note))
    audit.write(conn, action="DAS_CONFIG_ITEM_ADD", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CONFIG_ITEM",
                object_id=str(row["id"]), object_code=f"{project_no}/{identifier}",
                new_value={"kind": item_kind, "reason": inclusion_reason.strip()},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def confirm_item(conn: psycopg.Connection, *, project_no: str, identifier: str,
                 confirmed_on: dt.date | None = None, actor: dict) -> dict:
    """授权人员确认构型项目清单（第 1 步）。"""
    _require(conn, actor, ("PM", "AWM"), "确认构型项目清单",
             "UG-DAP-05 第 1 步：形成 UG-DAF-10，经授权人员确认")
    p = _project(conn, project_no)
    i = fetch_one(conn, """SELECT id, confirmed_on FROM das_config_item
                            WHERE project_id=%s AND identifier=%s""",
                  (p["id"], identifier))
    if not i:
        raise LookupError(f"{project_no} 的清单里没有 {identifier}")
    if i["confirmed_on"]:
        raise ValueError(f"{identifier} 已于 {i['confirmed_on']} 确认")
    execute(conn, """UPDATE das_config_item SET confirmed_by=%s, confirmed_on=%s
                      WHERE id=%s""",
            (actor["user_id"], confirmed_on or dt.date.today(), i["id"]))
    return fetch_one(conn, """SELECT identifier, confirmed_on FROM das_config_item
                               WHERE id=%s""", (i["id"],))


def baselines(conn: psycopg.Connection, project_no: str | None = None) -> list[dict]:
    if project_no:
        return fetch_all(conn, """SELECT b.*, p.project_no,
                                         (SELECT count(*) FROM das_config_baseline_item bi
                                           WHERE bi.baseline_id = b.id) AS item_count
                                    FROM das_config_baseline b
                                    JOIN das_project p ON p.id = b.project_id
                                   WHERE p.project_no=%s ORDER BY b.code""",
                         (project_no,))
    return fetch_all(conn, """SELECT b.*, p.project_no,
                                     (SELECT count(*) FROM das_config_baseline_item bi
                                       WHERE bi.baseline_id = b.id) AS item_count
                                FROM das_config_baseline b
                                JOIN das_project p ON p.id = b.project_id
                               ORDER BY p.project_no, b.code""")


def create_baseline(conn: psycopg.Connection, *, project_no: str, baseline_kind: str,
                    code: str, description: str, serial_no: str | None = None,
                    actor: dict) -> dict:
    """建立基线（第 3 步，三类）。建完还没冻结，冻结是单独一步。"""
    _require(conn, actor, ("CFG", "AWM"), "建立基线",
             "UG-DAP-05 第 3 步：授权人员／构型管理员")
    if baseline_kind not in BASELINE_KINDS:
        raise ValueError(f"基线类别须为 {'/'.join(BASELINE_KINDS)} 之一")
    p = _project(conn, project_no)
    if not (code or "").strip() or not (description or "").strip():
        raise ValueError("基线编号与说明不得为空")
    if baseline_kind == "DELIVERY" and not (serial_no or "").strip():
        raise ValueError(
            "交付基线是「每台（架）产品交付时的实际构型」（第 3 步），须给出序列号——"
            "没有序列号的交付基线说不出是哪一台。")
    row = fetch_one(conn, """
        INSERT INTO das_config_baseline
               (project_id, baseline_kind, code, description, serial_no)
        VALUES (%s,%s,%s,%s,%s)
        RETURNING id, baseline_kind, code, serial_no
    """, (p["id"], baseline_kind, code.strip(), description.strip(),
          (serial_no or "").strip() or None))
    audit.write(conn, action="DAS_BASELINE_CREATE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CONFIG_BASELINE",
                object_id=str(row["id"]), object_code=code.strip(),
                new_value={"kind": baseline_kind, "serial_no": serial_no},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def add_baseline_item(conn: psycopg.Connection, *, baseline_code: str,
                      identifier: str, revision: str, effectivity: str | None = None,
                      change_no: str | None = None, actor: dict) -> dict:
    """把一个构型项目连同版次放进基线。

    冻结后的改动必须凭一条**已批准**的更改（DCMS-INV-119, 第 3 步原文）。
    """
    _require(conn, actor, ("CFG", "AWM"), "维护基线内容", "UG-DAP-05 第 3 步")
    b = fetch_one(conn, """SELECT id, project_id, code, frozen_at
                             FROM das_config_baseline WHERE code=%s""", (baseline_code,))
    if not b:
        raise LookupError(f"没有编号为 {baseline_code} 的基线")
    i = fetch_one(conn, """SELECT id FROM das_config_item
                            WHERE project_id=%s AND identifier=%s""",
                  (b["project_id"], identifier))
    if not i:
        raise LookupError(f"该项目的构型清单里没有 {identifier}（第 1 步须先纳入）")
    if not (revision or "").strip():
        raise ValueError("版次不得为空")
    if b["frozen_at"] and not (change_no or "").strip():
        raise ValueError(
            f"基线 {baseline_code} 已冻结，改动须凭一条已批准的更改并记明更改单号"
            f"（UG-DAP-05 第 3 步）。冻结的意思就是只能这样改。")
    # 【同一个构型项目在一条基线里只有一行, 升版次是改那一行】
    # 原文（第 3 步）: 冻结后**只能通过批准的更改修改** —— 所以冻结基线里某项升版次,
    # 改的是那一行的版次, 并记上更改单号; 不是再加一行。
    # 当初按"每次加一行"写, 第二次就会撞上 uq_dcbi, 而撞上的时候报的是约束名,
    # 看不出该怎么做。历史版次在更改记录里（das_config_status 带出更改单号与分类）。
    existing = fetch_one(conn, """SELECT id, revision FROM das_config_baseline_item
                                   WHERE baseline_id=%s AND config_item_id=%s""",
                         (b["id"], i["id"]))
    if existing:
        if existing["revision"] == revision.strip()                 and not (change_no or "").strip():
            raise ValueError(
                f"{identifier} 在基线 {baseline_code} 里已经是 {revision}，没有要改的。")
        execute(conn, """UPDATE das_config_baseline_item
                            SET revision=%s, effectivity=%s, change_no=%s
                          WHERE id=%s""",
                (revision.strip(), (effectivity or "").strip() or None,
                 (change_no or "").strip() or None, existing["id"]))
        row = fetch_one(conn, """SELECT id, revision, effectivity, change_no
                                   FROM das_config_baseline_item WHERE id=%s""",
                        (existing["id"],))
        row["previous_revision"] = existing["revision"]
        return row
    row = fetch_one(conn, """
        INSERT INTO das_config_baseline_item
               (baseline_id, config_item_id, revision, effectivity, change_no)
        VALUES (%s,%s,%s,%s,%s)
        RETURNING id, revision, effectivity, change_no
    """, (b["id"], i["id"], revision.strip(),
          (effectivity or "").strip() or None, (change_no or "").strip() or None))
    return row


def freeze_baseline(conn: psycopg.Connection, *, baseline_code: str,
                    actor: dict) -> dict:
    """冻结基线（第 3 步：由授权人员批准后在系统中冻结）。

    冻结之后不得解冻 —— 解冻等于让"冻结"这件事不成立。构型要退回,
    应当另建一条基线并说明它取代了哪一条。
    """
    _require(conn, actor, "AWM", "冻结基线",
             "UG-DAP-05 第 3 步：基线由授权人员批准后在系统中冻结")
    b = fetch_one(conn, """SELECT id, code, frozen_at,
                                  (SELECT count(*) FROM das_config_baseline_item bi
                                    WHERE bi.baseline_id = das_config_baseline.id)
                                      AS item_count
                             FROM das_config_baseline WHERE code=%s""", (baseline_code,))
    if not b:
        raise LookupError(f"没有编号为 {baseline_code} 的基线")
    if b["frozen_at"]:
        raise ValueError(f"基线 {baseline_code} 已于 {b['frozen_at']} 冻结，且不得解冻")
    if not b["item_count"]:
        raise ValueError("基线里一个构型项目都没有，冻结它等于冻结了一张空表")
    execute(conn, """UPDATE das_config_baseline SET frozen_at=now(), frozen_by=%s
                      WHERE id=%s""", (actor["user_id"], b["id"]))
    audit.write(conn, action="DAS_BASELINE_FREEZE", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_CONFIG_BASELINE",
                object_id=str(b["id"]), object_code=baseline_code,
                new_value={"item_count": b["item_count"]},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return fetch_one(conn, """SELECT code, frozen_at FROM das_config_baseline
                               WHERE id=%s""", (b["id"],))


def decide_interchangeability(conn: psycopg.Connection, *, change_no: str,
                              verdict: str, rationale: str,
                              identifier: str | None = None,
                              old_part_number: str | None = None,
                              new_part_number: str | None = None,
                              old_revision: str | None = None,
                              new_revision: str | None = None,
                              decided_on: dt.date | None = None,
                              actor: dict) -> dict:
    """互换性判定（第 2 步）。

    原文: 完全互换的更改升版次; **不可互换或单向互换的更改必须更换件号**,
    并在更改单中说明互换性结论。

    判错的后果不在系统里: 一个不可互换的件沿用旧件号发出去, 现场会把它装到装不上、
    或装上去不安全的位置 —— 而装的人手里的件号是对的。
    """
    _require(conn, actor, ("DE", "AWM"), "作互换性判定",
             "UG-DAP-05 第 2 步：设计工程师")
    if verdict not in VERDICTS:
        raise ValueError(f"互换性结论须为 {'/'.join(VERDICTS)} 之一")
    d = fetch_one(conn, "SELECT id, change_no FROM das_design_change WHERE change_no=%s",
                  (change_no,))
    if not d:
        raise LookupError(f"没有编号为 {change_no} 的更改申请")
    if not (rationale or "").strip():
        raise ValueError("互换性结论的依据不得为空（第 2 步：在更改单中说明互换性结论）")
    item_id = None
    if identifier:
        i = fetch_one(conn, """SELECT ci.id FROM das_config_item ci
                                 JOIN das_design_change dc ON dc.project_id = ci.project_id
                                WHERE dc.change_no=%s AND ci.identifier=%s""",
                      (change_no, identifier))
        if not i:
            raise LookupError(f"该项目的构型清单里没有 {identifier}")
        item_id = i["id"]
    if verdict in ("ONE_WAY", "NONE"):
        if not (new_part_number or "").strip() \
                or (new_part_number or "").strip() == (old_part_number or "").strip():
            raise ValueError(
                f"互换性结论为「{VERDICTS[verdict]}」时**必须更换件号**"
                f"（UG-DAP-05 第 2 步）。判错的后果不在系统里：一个不可互换的件沿用旧件号"
                f"发出去，现场会把它装到装不上、或装上去不安全的位置——"
                f"而装的人手里的件号是对的。这正是件号制度要防的那件事。")
    else:
        if not (new_revision or "").strip():
            raise ValueError("完全互换的更改升版次（第 2 步），须记明新版次")
        if (new_part_number or "").strip() \
                and (new_part_number or "").strip() != (old_part_number or "").strip():
            raise ValueError(
                "结论是完全互换却换了件号——结论与做法不一致。"
                "要么结论不是完全互换，要么不该换件号；两者必有一个是错的。")
    row = fetch_one(conn, """
        INSERT INTO das_interchangeability
               (change_id, config_item_id, verdict, rationale, old_part_number,
                new_part_number, old_revision, new_revision, decided_by, decided_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, verdict, old_part_number, new_part_number, new_revision
    """, (d["id"], item_id, verdict, rationale.strip(),
          (old_part_number or "").strip() or None,
          (new_part_number or "").strip() or None,
          (old_revision or "").strip() or None,
          (new_revision or "").strip() or None,
          actor["user_id"], decided_on or dt.date.today()))
    audit.write(conn, action="DAS_INTERCHANGEABILITY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DESIGN_CHANGE",
                object_id=str(d["id"]), object_code=change_no,
                new_value={"verdict": verdict,
                           "old_part_number": old_part_number,
                           "new_part_number": new_part_number},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def decide_effectivity(conn: psycopg.Connection, *, change_no: str,
                       effective_from_unit: str, retrofit_delivered: bool,
                       transition_coexist: bool, notified_production: bool,
                       notified_procurement: bool, notified_airworthiness: bool,
                       retrofit_scope: str | None = None,
                       transition_note: str | None = None,
                       decided_on: dt.date | None = None, actor: dict) -> dict:
    """确定有效性范围（第 5 步）。**未明确有效性范围的更改不得发布。**"""
    _require(conn, actor, ("CFG", "AWM"), "确定有效性范围",
             "UG-DAP-05 第 5 步：授权人员／构型管理员")
    d = fetch_one(conn, "SELECT id FROM das_design_change WHERE change_no=%s",
                  (change_no,))
    if not d:
        raise LookupError(f"没有编号为 {change_no} 的更改申请")
    if not (effective_from_unit or "").strip():
        raise ValueError("须写明自哪个架次／序列号起实施（第 5 步）")
    if retrofit_delivered and not (retrofit_scope or "").strip():
        raise ValueError(
            "要追溯改装已交付产品，就得说清范围。只填一个「要追溯」而不写范围，"
            "等于说了要追溯但没人知道追到哪。")
    if transition_coexist and not (transition_note or "").strip():
        raise ValueError("有过渡期并存，就得说清怎么并存（哪些架次用旧构型、哪些用新的）")
    if scalar(conn, "SELECT count(*) FROM das_change_effectivity WHERE change_id=%s",
              (d["id"],)):
        raise ValueError(f"{change_no} 的有效性范围已确定，更正请按 UG-DAP-06 另提更改")
    row = fetch_one(conn, """
        INSERT INTO das_change_effectivity
               (change_id, effective_from_unit, retrofit_delivered, retrofit_scope,
                transition_coexist, transition_note, notified_production,
                notified_procurement, notified_airworthiness, decided_by, decided_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, effective_from_unit, retrofit_delivered, transition_coexist
    """, (d["id"], effective_from_unit.strip(), retrofit_delivered,
          (retrofit_scope or "").strip() or None, transition_coexist,
          (transition_note or "").strip() or None, notified_production,
          notified_procurement, notified_airworthiness, actor["user_id"],
          decided_on or dt.date.today()))
    audit.write(conn, action="DAS_EFFECTIVITY", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DESIGN_CHANGE",
                object_id=str(d["id"]), object_code=change_no,
                new_value={"from_unit": effective_from_unit.strip(),
                           "retrofit": retrofit_delivered},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def close_effectivity(conn: psycopg.Connection, *, change_no: str, closure_note: str,
                      closed_on: dt.date | None = None, actor: dict) -> dict:
    """贯彻销项（第 5 步：跟踪实施至完成并销项）。"""
    _require(conn, actor, ("CFG", "AWM"), "贯彻销项", "UG-DAP-05 第 5 步")
    e = fetch_one(conn, """SELECT e.id, e.closed_on FROM das_change_effectivity e
                             JOIN das_design_change d ON d.id = e.change_id
                            WHERE d.change_no=%s""", (change_no,))
    if not e:
        raise LookupError(f"{change_no} 还没有确定有效性范围")
    if e["closed_on"]:
        raise ValueError(f"{change_no} 已于 {e['closed_on']} 销项")
    if not (closure_note or "").strip():
        raise ValueError("销项要写明实施情况：只填一个日期的「已完成」说不出它是怎么完成的")
    execute(conn, """UPDATE das_change_effectivity SET closed_on=%s, closure_note=%s
                      WHERE id=%s""",
            (closed_on or dt.date.today(), closure_note.strip(), e["id"]))
    return fetch_one(conn, """SELECT closed_on, closure_note FROM das_change_effectivity
                               WHERE id=%s""", (e["id"],))


def record_audit(conn: psycopg.Connection, *, project_no: str, audit_kind: str,
                 scope_note: str, conclusion: str, serial_no: str | None = None,
                 audited_on: dt.date | None = None, actor: dict) -> dict:
    """登记构型核查（第 7 步）。

    设计内部一致性核查要比对图纸、数模、BOM、规范、工艺、手册六者之间的参数、版次和
    引用标准 —— 这不是系统判得了的事, 按 2026-10-02 的决定走线下, 这里记结论,
    证据挂 das_offline_approval（object_type=DAS_CONFIG_AUDIT）。
    """
    _require(conn, actor, ("CFG", "AWM"), "登记构型核查",
             "UG-DAP-05 第 7 步：构型管理员、检验／质量")
    if audit_kind not in AUDIT_KINDS:
        raise ValueError(f"核查类别须为 {'/'.join(AUDIT_KINDS)} 之一")
    p = _project(conn, project_no)
    for name, val in (("核查范围", scope_note), ("结论", conclusion)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空")
    if audit_kind == "PRE_DELIVERY" and not (serial_no or "").strip():
        raise ValueError("交付前核查针对具体一台产品（第 7 步 b），须给出序列号")
    row = fetch_one(conn, """
        INSERT INTO das_config_audit
               (project_id, audit_kind, serial_no, scope_note, conclusion,
                audited_by, audited_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, audit_kind, serial_no, audited_on
    """, (p["id"], audit_kind, (serial_no or "").strip() or None, scope_note.strip(),
          conclusion.strip(), actor["user_id"], audited_on or dt.date.today()))
    return row


def record_discrepancy(conn: psycopg.Connection, *, audit_id: int, description: str,
                       unapproved: bool = False, actor: dict) -> dict:
    """逐条记录差异（第 7 步 b）。未关闭的差异不得放行（DCMS-INV-120）。"""
    _require(conn, actor, ("CFG", "AWM"), "登记构型差异", "UG-DAP-05 第 7 步")
    if not scalar(conn, "SELECT count(*) FROM das_config_audit WHERE id=%s", (audit_id,)):
        raise LookupError(f"没有编号为 {audit_id} 的构型核查记录")
    if not (description or "").strip():
        raise ValueError("差异说明不得为空")
    return fetch_one(conn, """
        INSERT INTO das_config_discrepancy (audit_id, description, unapproved)
        VALUES (%s,%s,%s) RETURNING id, description, unapproved
    """, (audit_id, description.strip(), unapproved))


def close_discrepancy(conn: psycopg.Connection, *, discrepancy_id: int,
                      disposition: str, ncr_id: int | None = None,
                      occurrence_checked: bool = False,
                      closed_on: dt.date | None = None, actor: dict) -> dict:
    """关闭差异。

    第 8 步: 发现**未经批准的**构型差异（实物与设计不一致、擅自使用旧版次）,
    按 UG-DAP-14 记不符合项, 并按 UG-DAP-12 判断是否属应报告的事件。
    两件事都要做过才算处置完。
    """
    _require(conn, actor, ("CFG", "AWM"), "关闭构型差异", "UG-DAP-05 第 7～8 步")
    d = fetch_one(conn, """SELECT id, unapproved, closed_on FROM das_config_discrepancy
                            WHERE id=%s""", (discrepancy_id,))
    if not d:
        raise LookupError(f"没有编号为 {discrepancy_id} 的差异记录")
    if d["closed_on"]:
        raise ValueError("该差异已关闭")
    if not (disposition or "").strip():
        raise ValueError("关闭要写处置：只填个日期的「已关闭」说不出它是怎么关的")
    if d["unapproved"]:
        if ncr_id is None:
            raise ValueError(
                "未经批准的构型差异须按 UG-DAP-14 记不符合项（第 8 步），请给出 NCR 编号。"
                "实物与设计不一致、擅自使用旧版次，这两种本身就是不符合项。")
        if not occurrence_checked:
            raise ValueError(
                "未经批准的构型差异还须按 UG-DAP-12 判断是否属应报告的事件（第 8 步）。"
                "判过了就勾上——没判就关闭，等于跳过了 48 小时报告那条链的入口。")
        if not scalar(conn, "SELECT count(*) FROM das_ncr WHERE id=%s", (ncr_id,)):
            raise LookupError(f"没有编号为 {ncr_id} 的不符合项")
    execute(conn, """UPDATE das_config_discrepancy
                        SET disposition=%s, ncr_id=%s, occurrence_checked=%s,
                            closed_on=%s, closed_by=%s
                      WHERE id=%s""",
            (disposition.strip(), ncr_id, occurrence_checked,
             closed_on or dt.date.today(), actor["user_id"], discrepancy_id))
    return fetch_one(conn, """SELECT id, closed_on, disposition FROM das_config_discrepancy
                               WHERE id=%s""", (discrepancy_id,))


def deliver(conn: psycopg.Connection, *, project_no: str, serial_no: str,
            form_ref: str, part_list: str, implemented_changes: str,
            residual_discrepancies: str | None = None,
            no_residual_declared: bool = False, baseline_code: str | None = None,
            delivered_on: dt.date | None = None, actor: dict) -> dict:
    """交付构型记录（第 9 步，UG-DAF-11）。

    闸门在数据库里（DCMS-INV-120）: 该台产品的交付前核查还有未关闭的差异时不得放行。
    """
    _require(conn, actor, "CFG", "形成交付构型记录",
             "UG-DAP-05 第 9 步：构型管理员")
    p = _project(conn, project_no)
    for name, val in (("序列号", serial_no), ("UG-DAF-11 编号", form_ref),
                      ("件号与版次清单", part_list),
                      ("已实施的更改", implemented_changes)):
        if not (val or "").strip():
            raise ValueError(f"{name}不得为空（第 9 步：列出件号、版次、序列号、"
                             f"已实施的更改和遗留差异）")
    if not no_residual_declared and not (residual_discrepancies or "").strip():
        raise ValueError(
            "遗留差异要么写明、要么显式声明「无」。空着分不出「没有」与「忘了写」，"
            "而这一栏空着发出去，接收方以为这台产品没有遗留差异。")
    bid = None
    if baseline_code:
        b = fetch_one(conn, "SELECT id FROM das_config_baseline WHERE code=%s",
                      (baseline_code,))
        if not b:
            raise LookupError(f"没有编号为 {baseline_code} 的基线")
        bid = b["id"]
    # 先把未关闭的差异列出来, 给出能看懂的话; 数据库的 120 在后面兜底。
    open_d = fetch_all(conn, """
        SELECT d.id, d.description FROM das_config_discrepancy d
          JOIN das_config_audit a ON a.id = d.audit_id
         WHERE a.project_id=%s AND a.audit_kind='PRE_DELIVERY' AND a.serial_no=%s
           AND d.closed_on IS NULL
    """, (p["id"], serial_no.strip()))
    if open_d:
        lines = "；".join(f"#{d['id']} {d['description'][:40]}" for d in open_d)
        raise ValueError(
            f"本台产品（序列号 {serial_no}）的交付前核查还有未关闭的差异，不得放行：{lines}。"
            f"UG-DAP-05 第 7 步 b：差异逐条记录并处置，**未关闭的差异不得放行**。")
    row = fetch_one(conn, """
        INSERT INTO das_delivery_config
               (project_id, serial_no, form_ref, part_list, implemented_changes,
                residual_discrepancies, no_residual_declared, baseline_id,
                prepared_by, delivered_on)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id, serial_no, form_ref, delivered_on
    """, (p["id"], serial_no.strip(), form_ref.strip(), part_list.strip(),
          implemented_changes.strip(),
          (residual_discrepancies or "").strip() or None, no_residual_declared,
          bid, actor["user_id"], delivered_on or dt.date.today()))
    audit.write(conn, action="DAS_DELIVERY_CONFIG", user_id=str(actor["user_id"]),
                username=actor["username"], object_type="DAS_DELIVERY_CONFIG",
                object_id=str(row["id"]), object_code=f"{project_no}/{serial_no}",
                new_value={"form_ref": form_ref.strip(),
                           "no_residual": no_residual_declared},
                session_id=str(actor.get("session_id")), client_ip=actor.get("client_ip"))
    return row


def status(conn: psycopg.Connection, *, project_no: str | None = None,
           serial_no: str | None = None) -> list[dict]:
    """构型纪实（第 6 步）：某台产品由哪些件号和版次构成、已实施哪些更改。"""
    return fetch_all(conn, """
        SELECT * FROM das_config_status
         WHERE (%s::text IS NULL OR project_no = %s)
           AND (%s::text IS NULL OR serial_no = %s)
    """, (project_no, project_no, serial_no, serial_no))


def gaps(conn: psycopg.Connection) -> dict:
    """构型管理员要盯的几份清单。

    `effectivity_missing` 已分类却没有有效性记录的 —— 第 5 步: 未明确有效性范围的更改
    不得发布。闸门落在各自的发布点更准（发布这个动作散在多处）, 这张表是那份清单。
    """
    return {
        "effectivity_missing": fetch_all(
            conn, "SELECT * FROM das_change_effectivity_missing"),
        "effectivity_open": fetch_all(conn, "SELECT * FROM das_change_effectivity_open"),
        "discrepancy_open": fetch_all(conn, "SELECT * FROM das_config_discrepancy_open"),
        "item_unconfirmed": fetch_all(conn, "SELECT * FROM das_config_item_unconfirmed"),
    }


def interchangeability(conn: psycopg.Connection) -> list[dict]:
    """互换性结论台账。换件号的那些要单独看，因为它们影响现场。"""
    return fetch_all(conn, "SELECT * FROM das_interchangeability_register")
