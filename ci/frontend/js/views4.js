/* 基线、数据质量、报表、系统管理。 */
import { api } from "./api.js";
import { reasonAction } from "./extras.js";
import { recordView } from "./bom-tools.js";
import {
  el, table, tablePanel, panel, empty, status, field, input, select,
  toast, toastError, fmtDate, link, askReason,
} from "./ui.js";

const reload = () => { const h = location.hash; location.hash = "#/"; setTimeout(() => location.hash = h, 0); };
const ITEM_CN = { FILE_REVISION: "文件版次", BOM_SNAPSHOT: "BOM 快照",
                  EXTERNAL_TECHNICAL_STATE: "外部件技术状态", SOFTWARE_VERSION: "软件版本" };
const ROLE_CN = { PRIMARY_DEFINITION: "主设计定义", SUPPORTING_DEFINITION: "支持性定义",
                  INTERFACE_DEFINITION: "接口定义", QUALIFICATION_EVIDENCE: "鉴定证据" };

/* ==================== 基线列表 ==================== */
export async function baselines(ctx, params, pn) {
  const rows = await api.get(`/parts/${encodeURIComponent(pn)}/baselines`);
  const acts = el("div", { class: "actions" },
    link("返回件号", "#/object/" + encodeURIComponent(pn), "btn"));
  if (ctx.can("draft_write")) {
    acts.append(el("button", { class: "btn primary", onclick: async () => {
      const reason = await askReason("新建基线", "本次基线的原因是什么");
      if (!reason) return;
      try { const r = await api.post(`/parts/${encodeURIComponent(pn)}/baselines`,
              { json: { reason, copy_from_current: rows.some(x => x.is_current) } });
            toast(r.items_copied ? `已从当前基线复制 ${r.items_copied} 项` : "基线已建立");
            location.hash = "#/baseline/" + r.id; }
      catch (e) { toastError(e); } } }, "新建基线"));
  }
  const twoReleased = rows.filter(r => r.status !== "DRAFT").slice(-2);
  if (twoReleased.length === 2)
    acts.append(link("比较最近两版", `#/baseline-compare/${twoReleased[0].id}/${twoReleased[1].id}`, "btn"));

  return el("div", {},
    el("h1", {}, "基线"),
    el("p", { class: "sub" }, el("span", { class: "mono" }, pn),
      " 的全部技术状态记录。已发布的基线内容冻结，改动只能通过新建基线。"),
    acts,
    rows.length ? tablePanel("基线",
      table([{ label: "编号", mono: 1 }, { label: "状态" }, { label: "当前" },
             { label: "原因" }, { label: "明细数" }, { label: "发布时间" }, { label: "" }],
        rows, r => [
          el("td", { class: "mono" }, r.baseline_code),
          el("td", {}, status(r.status)),
          el("td", {}, r.is_current ? el("span", { class: "st-current" }, "当前") : ""),
          el("td", {}, r.reason),
          el("td", { class: "num" }, r.item_count),
          el("td", { class: "muted nowrap" }, fmtDate(r.released_at)),
          el("td", { class: "right" }, link("打开", "#/baseline/" + r.id, "btn small"))]))
      : empty("还没有基线", "基线把件号锁定到确定的文件版次和 BOM 快照上。"));
}

/* ==================== 基线详情 ==================== */
export async function baselineDetail(ctx, params, id) {
  const [bl, val] = await Promise.all([
    api.get("/baselines/" + id),
    api.get(`/baselines/${id}/validate`).catch(() => null),
  ]);
  const editable = ["DRAFT", "IN_REVIEW"].includes(bl.status);

  const acts = el("div", { class: "actions" },
    link("返回基线列表", "#/baselines/" + encodeURIComponent(bl.full_part_number), "btn"));
  if (bl.status === "DRAFT" && ctx.can("submit"))
    acts.append(el("button", { class: "btn", onclick: async () => {
      try { await api.post(`/baselines/${id}/submit`); toast("已提交审批"); reload(); }
      catch (e) { toastError(e); } } }, "提交审批"));
  if (bl.status === "IN_REVIEW" && ctx.can("approve") && ctx.can("baseline_release"))
    acts.append(el("button", { class: "btn primary", onclick: async () => {
      try { await api.post(`/baselines/${id}/release`, { query: { comments: "同意发布" } });
            toast("基线已发布并成为当前技术状态"); reload(); }
      catch (e) { toastError(e); } } }, "批准发布"));

  if (editable && ctx.can("draft_write")) acts.append(reasonAction("取消基线", `/baselines/${id}/cancel`, reload));
  const itemForm = editable && ctx.can("draft_write") ? addItemForm(id) : null;

  const cell = (l, v, mono) => el("div", { class: "tb-cell" },
    el("b", {}, l), el("span", { class: mono ? "mono" : null }, v ?? "—"));

  return el("div", {},
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" },
        el("span", { class: "tb-code" }, bl.baseline_code),
        el("span", { class: "tb-name" }, `${bl.full_part_number} · ${bl.formal_name_cn}`)),
      el("div", { class: "tb-grid" },
        cell("状态", bl.status),
        cell("是否当前", bl.is_current ? "是" : "否"),
        cell("原因", bl.reason),
        cell("内容摘要", bl.content_hash ? bl.content_hash.slice(0, 16) + "…" : "未发布", true),
        cell("发布时间", fmtDate(bl.released_at)),
        cell("取代时间", fmtDate(bl.superseded_at)),
        cell("明细数", bl.items.length),
        cell("序号", bl.baseline_sequence))),

    val && (val.errors.length || val.warnings.length) ? el("div", {
        class: val.errors.length ? "note error" : "note warn" },
      val.errors.length ? "以下问题会阻止发布：" : "以下内容请确认：",
      el("ul", {}, val.errors.concat(val.warnings).map(x => el("li", {}, x)))) : null,
    !editable ? el("div", { class: "note" },
      "该基线已发布，内容不可更改。要调整锁定的版次，请新建一条基线。") : null,
    acts, itemForm,

    tablePanel("基线明细",
      table([{ label: "类型" }, { label: "内容", mono: 1 }, { label: "角色" },
             { label: "状态" }, { label: "" }],
        bl.items, i => [
          el("td", { class: "nowrap muted" }, ITEM_CN[i.item_type] || i.item_type),
          el("td", { class: "mono" }, i.item_label),
          el("td", {}, ROLE_CN[i.item_role] || i.item_role || "—"),
          el("td", {}, status(i.item_status)),
          el("td", { class: "right" }, editable && ctx.can("draft_write")
            ? el("button", { class: "btn small danger", onclick: async () => {
                try { await api.del("/baselines/items/" + i.id); toast("已移除"); reload(); }
                catch (e) { toastError(e); } } }, "移除") : null)])
      || empty("基线还没有明细", "至少要有一项主设计定义才能提交。")));
}

function addItemForm(id) {
  const typeSel = select(Object.entries(ITEM_CN).map(([v, l]) => ({ value: v, label: l })));
  const targetIn = input({ class: "mono", placeholder: "UG-A10001M001 Rev.00" });
  const roleSel = select([{ value: "", label: "（不适用）" }].concat(
    Object.entries(ROLE_CN).map(([v, l]) => ({ value: v, label: l }))));
  typeSel.addEventListener("change", () => {
    targetIn.placeholder = {
      FILE_REVISION: "UG-A10001M001 Rev.00",
      BOM_SNAPSHOT: "SNAP-000001",
      EXTERNAL_TECHNICAL_STATE: "MOLEX::43025-0400 TS1",
      SOFTWARE_VERSION: "UG-SW0001 1.2.0",
    }[typeSel.value] || "";
  });
  return panel("添加明细", el("div", {},
    el("div", { class: "inline-form" },
      field("类型", typeSel), field("内容标识", targetIn), field("角色", roleSel),
      el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary", onclick: async () => {
        try { await api.post(`/baselines/${id}/items`, {
                json: { item_type: typeSel.value, target: targetIn.value,
                        item_role: roleSel.value || null } });
              toast("已添加"); reload(); }
        catch (e) { toastError(e); } } }, "添加"))),
    el("p", { class: "muted", style: "margin:8px 0 0" },
      "基线只能引用确定的版次。文件必须已发布、外部件技术状态必须已接受。")));
}

export async function baselineCompare(ctx, params, a, b) {
  const r = await api.get("/baselines/compare", { query: { a, b } });
  const row = (x, kind) => [
    el("td", {}, kind),
    el("td", { class: "nowrap muted" }, ITEM_CN[x.item_type] || x.item_type),
    el("td", { class: "mono" }, x.item_label || x.subject || "—"),
    el("td", { class: "mono" }, x.from || "—"),
    el("td", { class: "mono" }, x.to || x.item_label || "—")];
  const rows = [
    ...r.changed.map(x => row(x, "变更")),
    ...r.added.map(x => row(x, "新增")),
    ...r.removed.map(x => row(x, "移除")),
  ];
  return el("div", {},
    el("h1", {}, "基线比较"),
    el("p", { class: "sub" }, `${r.from.baseline_code} → ${r.to.baseline_code}`),
    r.identical ? el("div", { class: "note" }, "两条基线锁定的内容完全一致。") : null,
    rows.length ? tablePanel("差异",
      el("table", {},
        el("thead", {}, el("tr", {},
          ["类别", "明细类型", "对象", "原内容", "新内容"].map(h => el("th", {}, h)))),
        el("tbody", {}, rows.map(cells => el("tr", {}, cells)))))
      : null);
}

/* ==================== 数据质量 ==================== */
export async function quality(ctx) {
  const [issues, rules] = await Promise.all([
    api.get("/quality/issues", { query: { limit: 500 } }),
    api.get("/quality/rules"),
  ]);
  const ruleName = Object.fromEntries(rules.map(r => [r.code, r.name_cn]));

  return el("div", {},
    el("h1", {}, "数据质量"),
    el("p", { class: "sub" }, "错误级问题会阻止基线发布。豁免需要理由和有效期，到期自动恢复。"),
    el("div", { class: "actions" },
      ctx.can("read_audit") ? el("button", { class: "btn primary", onclick: async () => {
        try { const s = await api.post("/quality/scan");
              toast(`扫描完成：新增 ${s.issues_opened} 项，自动关闭 ${s.issues_auto_resolved} 项`);
              reload(); }
        catch (e) { toastError(e); } } }, "重新扫描") : null),

    issues.length ? tablePanel(`待处置 ${issues.length} 项`,
      table([{ label: "级别" }, { label: "规则" }, { label: "说明" },
             { label: "发现时间" }, { label: "" }],
        issues, i => [
          el("td", {}, status(i.severity)),
          el("td", { class: "nowrap" }, el("div", {}, i.rule_code),
            el("div", { class: "muted" }, ruleName[i.rule_code] || "")),
          el("td", {}, i.message),
          el("td", { class: "muted nowrap" }, fmtDate(i.detected_at)),
          el("td", { class: "right nowrap" },
            ctx.can("draft_write") ? el("button", { class: "btn small", onclick: async () => {
              try { await api.post(`/quality/issues/${i.id}/resolve`); toast("已标记处置完成"); reload(); }
              catch (e) { toastError(e); } } }, "已处理") : null,
            " ",
            ctx.can("baseline_release") ? el("button", { class: "btn small", onclick: async () => {
              const reason = await askReason("豁免该问题", "为什么暂时不处理？将记入审计并设定有效期");
              if (!reason) return;
              const days = Number(window.prompt("豁免多少天？（1–365）", "30"));
              if (!days) return;
              try { await api.post(`/quality/issues/${i.id}/waive`,
                      { query: { reason, expires_days: days } });
                    toast(`已豁免 ${days} 天，到期后自动恢复`); reload(); }
              catch (e) { toastError(e); } } }, "豁免") : null)]))
      : empty("没有待处置的问题", "点击“重新扫描”检查当前数据。"),

    tablePanel("规则清单",
      table([{ label: "规则", mono: 1 }, { label: "名称" }, { label: "级别" },
             { label: "阻止发布" }, { label: "说明" }],
        rules, r => [
          el("td", { class: "mono" }, r.code),
          el("td", {}, r.name_cn),
          el("td", {}, status(r.severity)),
          el("td", {}, r.blocks_release ? "是" : "否"),
          el("td", { class: "muted" }, r.description)])));
}

/* ==================== 报表 ==================== */
export async function reports() {
  const [util, fn, cls, activity, numbers] = await Promise.all([
    api.get("/reports/number-utilization"),
    api.get("/reports/function-distribution"),
    api.get("/reports/classification-distribution"),
    api.get("/reports/release-activity"),
    api.get("/numbers/basic-drawing"),
  ]);
  const fallback = cls.filter(c => c.is_fallback && c.family_count > 0);

  return el("div", {},
    el("h1", {}, "统计"),
    el("p", { class: "sub" }, "用于判断号码空间是否吃紧、分类词典是否需要复查。"),
    panel("最近 90 天发布活动", recordView(activity)),
    panel("基本图号占用", recordView(numbers)),
    fallback.length ? el("div", { class: "note warn" },
      "有设计族选用了“其他”类分类：",
      el("ul", {}, fallback.map(c => el("li", {}, `${c.code} ${c.name_cn}：${c.family_count} 个族`))),
      "这类使用应定期复查——数量增长说明现有分类可能需要扩充。") : null,

    tablePanel("Dash 号占用",
      table([{ label: "基本图号", mono: 1 }, { label: "名称" }, { label: "已分配" },
             { label: "已作废" }, { label: "剩余" }, { label: "占用率" }],
        util, u => [
          el("td", { class: "mono" }, u.basic_drawing_number),
          el("td", {}, u.family_name_cn),
          el("td", { class: "num" }, u.allocated),
          el("td", { class: "num" }, u.cancelled),
          el("td", { class: "num" }, u.remaining),
          el("td", { class: "num" }, u.utilization_pct + "%")])),

    tablePanel("功能域分布",
      table([{ label: "功能域", mono: 1 }, { label: "名称" }, { label: "作为主功能" },
             { label: "作为辅助功能" }],
        fn, f => [
          el("td", { class: "mono" }, f.domain_code),
          el("td", {}, f.domain_name),
          el("td", { class: "num" }, f.primary_count),
          el("td", { class: "num" }, f.auxiliary_count)])),

    tablePanel("二级分类分布",
      table([{ label: "分类", mono: 1 }, { label: "名称" }, { label: "一级类别" },
             { label: "设计族数" }],
        cls.filter(c => c.family_count > 0), c => [
          el("td", { class: "mono" }, c.code),
          el("td", {}, c.name_cn),
          el("td", { class: "mono muted" }, c.primary_class_code),
          el("td", { class: "num" }, c.family_count)])));
}

/* ==================== 系统管理 ==================== */
export async function admin(ctx) {
  const [users, license, sessions] = await Promise.all([
    api.get("/admin/users"),
    api.get("/admin/license").catch(() => null),
    api.get("/admin/sessions").catch(() => []),
  ]);

  return el("div", {},
    el("h1", {}, "系统管理"),
    license ? el("div", { class: license.available === 0 ? "note error" : "note" },
      `同时在线账户 ${license.active_accounts} / ${license.limit}，剩余 ${license.available} 个名额。`,
      license.available === 0 ? " 已满员，其他账户暂时无法登录。" : "") : null,

    tablePanel("在线会话",
      table([{ label: "账户", mono: 1 }, { label: "姓名" }, { label: "登录时间" },
             { label: "最近活动" }, { label: "" }],
        sessions, s => [
          el("td", { class: "mono" }, s.username),
          el("td", {}, s.full_name),
          el("td", { class: "muted nowrap" }, fmtDate(s.issued_at)),
          el("td", { class: "muted nowrap" }, fmtDate(s.last_seen_at)),
          el("td", { class: "right" }, el("button", { class: "btn small danger",
            onclick: async () => {
              const reason = await askReason("强制下线", "请说明原因（将记入审计）");
              if (!reason) return;
              try { await api.del("/admin/sessions/" + s.id, { query: { reason } });
                    toast("会话已撤销"); reload(); }
              catch (e) { toastError(e); } } }, "强制下线"))])
      || empty("当前没有在线会话")),

    tablePanel("账户",
      table([{ label: "账户", mono: 1 }, { label: "姓名" }, { label: "角色" },
             { label: "在线" }, { label: "状态" }, { label: "最近登录" }],
        users, u => [
          el("td", { class: "mono" }, u.username),
          el("td", {}, u.full_name),
          el("td", { class: "muted" }, (u.roles || []).join("、") || "—"),
          el("td", {}, u.is_online ? el("span", { class: "st-current" }, "在线") : ""),
          el("td", {}, u.is_active ? status("ACTIVE") : status("CANCELLED")),
          el("td", { class: "muted nowrap" }, fmtDate(u.last_login_at))])));
}

export async function audit(ctx) {
  const rows = await api.get("/admin/audit", { query: { limit: 200 } });
  return el("div", {},
    el("h1", {}, "审计记录"),
    el("p", { class: "sub" }, "审计记录不可修改也不可删除，由数据库层强制。"),
    tablePanel("最近 200 条",
      table([{ label: "时间" }, { label: "账户", mono: 1 }, { label: "动作" },
             { label: "对象", mono: 1 }, { label: "结果" }, { label: "原因" }],
        rows, a => [
          el("td", { class: "muted nowrap" }, fmtDate(a.occurred_at)),
          el("td", { class: "mono" }, a.username || "—"),
          el("td", { class: "nowrap" }, a.action),
          el("td", { class: "mono" }, a.object_code || "—"),
          el("td", {}, status(a.result)),
          el("td", { class: "muted" }, a.reason || "—")])));
}

export async function dictionary(ctx) {
  const names = await api.get("/dictionary");
  const sel = select(names.map(n => ({ value: n, label: n })));
  const out = el("div", {});
  async function load() {
    const rows = await api.get("/dictionary/" + sel.value, { query: { active_only: false } });
    const cols = rows.length ? Object.keys(rows[0]).filter(k => k !== "id").slice(0, 7) : [];
    out.replaceChildren(tablePanel(`${sel.value}（${rows.length} 条）`,
      table([...cols.map(c => ({ label: c, mono: c.includes("code") })), { label: "操作" }], rows, r => [
        ...cols.map(c => el("td", { class: c.includes("code") ? "mono" : null },
          c === "status" ? status(r[c]) : String(r[c] ?? "—"))),
        el("td", {}, ctx.can("dictionary_write") ? reasonAction(r.status === "ACTIVE" ? "废止" : "启用",
          `/dictionary/${sel.value}/${encodeURIComponent(sel.value === "restricted-term" ? r.id : r.code)}/${r.status === "ACTIVE" ? "deprecate" : "reactivate"}`, load) : "只读")])));
  }
  sel.addEventListener("change", () => load().catch(toastError));
  await load();
  return el("div", {},
    el("h1", {}, "受控字典"),
    el("p", { class: "sub" }, "字典只能启用或废止，不提供删除。废止后新对象不能再选用，历史数据不受影响。"),
    panel("选择字典", el("div", { class: "inline-form" }, field("字典", sel))),
    out);
}
