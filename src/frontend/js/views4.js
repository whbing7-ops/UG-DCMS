/* 基线、数据质量、报表、系统管理。 */
import { api } from "./api.js";
import { editor } from "./manage.js";
import { reasonAction, approvalSubmitButton } from "./extras.js";
import { recordView } from "./bom-tools.js";
import {
  el, table, tablePanel, panel, empty, status, statusText, field, input, select,
  toast, toastError, fmtDate, link, askReason,
} from "./ui.js";

const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
const ITEM_CN = { FILE_REVISION: "文件版次", BOM_SNAPSHOT: "BOM 快照",
                  EXTERNAL_TECHNICAL_STATE: "外部件技术状态", SOFTWARE_VERSION: "软件版本" };
const ROLE_CN = { PRIMARY_DEFINITION: "主设计定义", SUPPORTING_DEFINITION: "支持性定义",
                  INTERFACE_DEFINITION: "接口定义", QUALIFICATION_EVIDENCE: "鉴定证据" };
const BASELINE_CN = { FUNCTIONAL:'功能基线', ALLOCATED:'分配基线', DESIGN:'设计基线',
                      PRODUCT:'产品基线', AS_BUILT:'实造基线' };

/* ==================== 基线列表 ==================== */
export async function baselines(ctx, params, pn) {
  const rows = await api.get(`/parts/${encodeURIComponent(pn)}/baselines`);
  const acts = el("div", { class: "actions" },
    link("返回件号", "#/object/" + encodeURIComponent(pn), "btn"));
  if (ctx.can("draft_write")) {
    acts.append(el("button", { class: "btn primary", onclick: () => {
      const type=select([{value:'FUNCTIONAL',label:'功能基线'},{value:'ALLOCATED',label:'分配基线'},{value:'DESIGN',label:'设计基线'},{value:'PRODUCT',label:'产品基线'},{value:'AS_BUILT',label:'实物基线'}]);
      const reason=input({required:true,maxlength:500,placeholder:'为什么建立本基线'});
      const scope=el('textarea',{required:true,maxlength:1000,placeholder:'本基线覆盖的产品、BOM、文件和技术状态边界'});
      const change=input({maxlength:128,placeholder:'更改单、任务单或审定文件编号（可选）'});
      const project=input({required:true,maxlength:64,placeholder:'本基线所属项目编号'});
      editor('新建基线',el('div',{},field('项目编号',project),field('基线类型',type),field('建立原因',reason),field('范围说明',scope),field('变更依据',change)),async()=>{
        if(!project.value.trim()||!reason.value.trim()||!scope.value.trim()) throw Error('项目编号、建立原因和范围说明不能为空');
        const r=await api.post(`/parts/${encodeURIComponent(pn)}/baselines`,{json:{project_code:project.value.trim(),reason:reason.value.trim(),scope_note:scope.value.trim(),change_reference:change.value.trim()||null,baseline_type:type.value,copy_from_current:rows.some(x=>x.is_current)}});
        toast(r.items_copied?`已从当前基线复制 ${r.items_copied} 项`:'基线已建立'); location.hash='#/baseline/'+r.id;
      },{submit:'建立基线'});
    } }, "新建基线"));
  }
  const twoReleased = rows.filter(r => ["RELEASED", "SUPERSEDED"].includes(r.status))
    .sort((a, b) => a.baseline_sequence - b.baseline_sequence).slice(-2);
  if (twoReleased.length === 2)
    acts.append(link("比较最近两版", `#/baseline-compare/${twoReleased[0].id}/${twoReleased[1].id}`, "btn"));

  return el("div", {},
    el("h1", {}, "基线"),
    el("p", { class: "sub" }, el("span", { class: "mono" }, pn),
      " 的全部技术状态记录。已发布的基线内容冻结，改动只能通过新建基线。"),
    !ctx.can("draft_write") ? el("div", { class: "note warn" },
      "当前账户可查看基线；建立、编辑和移除明细需要“设计工程师”或“构型管理员”角色。") : null,
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
    api.get(`/baselines/${id}/validate`),
  ]);
  const editable = bl.status === "DRAFT";

  const acts = el("div", { class: "actions" },
    link("返回基线列表", "#/baselines/" + encodeURIComponent(bl.full_part_number), "btn"));
  if (bl.status === "DRAFT" && ctx.can("submit"))
    acts.append(approvalSubmitButton("提交审批", `/baselines/${id}/submit`, reload, 'CONFIGURATION_MANAGER'));
  if (bl.status === "IN_REVIEW" && ctx.can("baseline_release") && String(bl.approval_assignee_user_id||'')===String(ctx.user.id))
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
        cell("状态", statusText(bl.status)),
        cell("是否当前", bl.is_current ? "是" : "否"),
        cell("原因", bl.reason),
        cell("基线类型", BASELINE_CN[bl.baseline_type] || bl.baseline_type),
        cell("项目编号", bl.project_code, true),
        cell("范围说明", bl.scope_note),
        cell("变更依据", bl.change_reference),
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
      bl.status === "CANCELLED" ? "该基线已取消，不能继续编辑或发布。需要时请新建基线。"
        : "该基线已发布，内容不可更改。要调整锁定的版次，请新建一条基线。") : null,
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
  const [issues, rules, integrity] = await Promise.all([
    api.get("/quality/issues", { query: { limit: 200 } }),
    api.get("/quality/rules"),
    ctx.can('read_audit') ? api.get('/integrity/issues') : [],
  ]);
  const ruleName = Object.fromEntries(rules.map(r => [r.code, r.name_cn]));

  return el("div", {},
    el("h1", {}, "数据质量"),
    el("p", { class: "sub" }, "错误级问题会阻止基线发布。豁免需要理由和有效期，到期自动恢复。"),
    ctx.can('read_audit') ? panel('附件完整性问题', recordView(integrity), el('button', {class:'btn',onclick:async()=>{
      try { await api.post('/integrity/check'); toast('完整性检查完成'); reload(); } catch(e) {toastError(e);}
    }},'检查全部附件')) : null,
    el("div", { class: "actions" },
      ctx.can("read_audit") ? el("button", { class: "btn primary", onclick: async () => {
        try { const s = await api.post("/quality/scan");
              toast(`扫描完成：新增 ${s.issues_opened} 项，自动关闭 ${s.issues_auto_resolved} 项`);
              reload(); }
        catch (e) { toastError(e); } } }, "重新扫描") : null),

    issues.length ? tablePanel(`待处置（当前显示 ${issues.length} 项，最多 200 项）`,
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
    numbers.occupied_total > numbers.display_limit ? el("div", { class: "note warn" },
      `基本图号已占用 ${numbers.occupied_total} 个，当前仅展示前 ${numbers.display_limit} 个。请按分类查询明细。`) : null,
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
export { accounts as admin } from "./manage.js";
export { auditPage as audit } from "./extras.js";

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
