/* 各页面视图。每个视图返回一个 DOM 节点, 由 app.js 的路由挂载。 */
import { api, ApiError } from "./api.js";
import { definitionButton } from "./extras.js";
import { hardwareSoftwarePanel } from "./software-hardware.js";
import {
  el, table, tablePanel, panel, empty, status, statusText, codeText, field, input, select,
  toast, toastError, fmtDate, link, askReason, clear,
} from "./ui.js";

const q = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v));

/* ============================ 首页 ============================ */
export async function home(ctx) {
  const [dash, recent, issues] = await Promise.all([
    api.get("/reports/dashboard"),
    api.get("/recent", { query: { limit: 10 } }),
    api.get("/quality/issues", { query: { severity: "ERROR", limit: 5 } }),
  ]);
  const o = dash.objects, qy = dash.quality, ig = dash.integrity;

  const stat = (n, label, alert) =>
    el("div", { class: "stat" + (alert ? " alert" : "") },
       el("b", {}, String(n ?? 0)), el("span", {}, label));

  return el("div", {},
    el("h1", {}, `你好，${ctx.user.full_name}`),
    el("p", { class: "sub" }, "设计构型管理系统。这里是当前需要注意的内容。"),

    qy.open_errors > 0 ? el("div", { class: "note error" },
      `有 ${qy.open_errors} 个阻止发布的数据质量问题待处置。`,
      el("div", {}, link("查看清单", "#/quality"))) : null,
    ig.mismatch + ig.missing > 0 ? el("div", { class: "note error" },
      `${ig.mismatch + ig.missing} 个已发布附件的完整性校验未通过——文件可能被绕过系统改动过。`,
      el("div", {}, link("查看完整性问题", "#/quality"))) : null,

    el("div", { class: "actions home-shortcuts" }, link("BOM 管理", "#/bom", "btn primary"), link("查找设计数据", "#/search", "btn"), ctx.can("user_manage") ? link("账户管理", "#/admin", "btn") : null),
    el("div", { class: "grid4" },
      stat(o.internal_parts, "内部件号"),
      stat(o.external_parts, "外部件"),
      stat(dash.families.active, "生效设计族"),
      stat(dash.baselines.current, "当前基线"),
      stat(o.draft, "草稿对象"),
      stat(qy.open_errors, "待处置错误", qy.open_errors > 0),
      stat(qy.open_warnings, "待处置提醒"),
      stat(qy.active_waivers, "有效豁免")),

    el("div", { class: "split" },
      tablePanel("最近访问",
        table([{ label: "编号", mono: 1 }, { label: "名称" }, { label: "时间" }],
          recent, r => [
            el("td", { class: "mono" }, link(r.object_code, "#/object/" + encodeURIComponent(r.object_code))),
            el("td", {}, r.display_name || "—"),
            el("td", { class: "muted nowrap" }, fmtDate(r.accessed_at))])),
      tablePanel("待处置错误",
        table([{ label: "规则" }, { label: "说明" }], issues, i => [
          el("td", { class: "nowrap" }, i.rule_code),
          el("td", {}, i.message)]))));
}

/* ============================ 检索 ============================ */
export async function search(ctx, params) {
  const term = params.get("q") || "";
  const kinds = params.get("kinds") || "";
  const box = el("div", {});
  const inp = input({ value: term, placeholder: "件号、图号、文件号、附件名或附件正文", class: "mono" });
  const kindSel = select([
    { value: "", label: "全部类型" },
    { value: "PART_NUMBER", label: "内部件号" },
    { value: "EXTERNAL_PART", label: "外部件" },
    { value: "SOFTWARE", label: "软件对象" },
    { value: "FAMILY", label: "设计族" },
    { value: "FILE", label: "设计文件" },
    { value: "ATTACHMENT", label: "附件" },
  ].map(o => ({ ...o, selected: o.value === kinds })));

  const go = () => { location.hash = "#/search?" + new URLSearchParams(q({ q: inp.value, kinds: kindSel.value })); };
  inp.addEventListener("keydown", e => { if (e.key === "Enter") go(); });

  const results = el("div", {});
  if (term) {
    const r = await api.get("/search", { query: q({ q: term, kinds, limit: 100 }) });
    results.append(r.total === 0
      ? empty("没有找到匹配的对象", "换一个片段试试。历史件号也能用来查找当前对象。")
      : tablePanel(`匹配到 ${r.total} 个对象`,
          table([{ label: "编号", mono: 1 }, { label: "名称" }, { label: "类型" },
                 { label: "状态" }, { label: "匹配方式" }],
            r.results, x => [
              el("td", { class: "mono" }, x.kind === "FAMILY"
                ? link(x.object_code, "#/family/" + x.id)
                : x.kind === "FILE"
                ? link(x.object_code, "#/file/" + encodeURIComponent(x.object_code))
                : x.kind === "ATTACHMENT"
                ? link(x.display_name, "#/revision/" + x.revision_id)
                : link(x.object_code, "#/object/" + encodeURIComponent(x.object_code))),
              el("td", {}, x.display_name || "—"),
              el("td", { class: "muted nowrap" }, KIND_CN[x.kind] || x.kind),
              el("td", {}, status(x.lifecycle_status)),
              el("td", { class: "muted nowrap" },
                 MATCH_CN[x.match_type] || x.match_type,
                 x.matched_via ? el("div", { class: "mono muted" }, codeText(x.matched_via)) : null)])));
  } else {
    results.append(empty("输入内容开始查找",
      "支持完整编号、名称片段、历史件号、附件文件名，以及可提取的附件正文。"));
  }

  box.append(
    el("h1", {}, "查找"),
    el("p", { class: "sub" }, "精确命中优先，其次是编号归一、历史件号，最后才是模糊匹配。"),
    panel("检索条件", el("div", { class: "inline-form" },
      field("关键词", inp), field("类型", kindSel),
      el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary", onclick: go }, "查找")))),
    results);
  return box;
}

const KIND_CN = { PART_NUMBER: "内部件号", EXTERNAL_PART: "外部件", SOFTWARE: "软件对象",
                  FAMILY: "设计族", FILE: "设计文件", ATTACHMENT: "附件" };
const MATCH_CN = { EXACT: "编号完全一致", NORMALIZED: "编号归一后一致",
                   CROSS_REFERENCE: "历史件号", FUZZY: "名称或编号相近" };

/* ============================ 对象详情 ============================ */
export async function objectDetail(ctx, params, code) {
  const obj = await api.get("/objects/" + encodeURIComponent(code));
  const definitions = await api.get('/definitions/' + encodeURIComponent(code));
  const isPart = obj.object_type === "INTERNAL_PART";
  const softwarePanel = (isPart || obj.object_type === "EXTERNAL_PART")
    ? await hardwareSoftwarePanel(code) : null;

  let cfg = null, wu = null;
  if (isPart) {
    [cfg, wu] = await Promise.all([
      api.get(`/parts/${encodeURIComponent(obj.full_part_number)}/configuration`),
      api.get("/where-used/" + encodeURIComponent(code)),
    ]);
  }

  const cell = (label, value, mono) =>
    el("div", { class: "tb-cell" }, el("b", {}, label),
       el("span", { class: mono ? "mono" : null }, value ?? "—"));

  return el("div", {},
    el("div", { class: "actions" }, link("BOM 管理", "#/bom/" + encodeURIComponent(code), "btn primary"),
      isPart ? link('设计基线', '#/baselines/' + encodeURIComponent(obj.full_part_number), 'btn') : null,
      ctx.can("draft_write") ? definitionButton(code) : null),
    // 标题栏: 与工程师在图纸上看到的格子结构一致
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" },
        el("span", { class: "tb-code" }, obj.object_code),
        el("span", { class: "tb-name" }, obj.display_name)),
      el("div", { class: "tb-grid" },
        cell("生命周期状态", statusText(obj.lifecycle_status)),
        cell("对象类型", KIND_CN[obj.object_type] || obj.object_type),
        cell("基本图号", obj.basic_drawing_number, true),
        cell("设计族名称", obj.family_name_cn),
        cell("当前基线", obj.current_baseline_code, true),
        cell("数据来源", obj.data_origin === "LEGACY" ? "历史迁移" : "系统内建立"),
        cell("数据成熟度", codeText(obj.data_maturity)),
        cell("建立时间", fmtDate(obj.created_at)))),

    obj.quality_issues.length ? el("div", { class: "note error" },
      `该对象有 ${obj.quality_issues.length} 个待处置数据质量问题：`,
      el("ul", {}, obj.quality_issues.map(i => el("li", {}, `${i.rule_code} ${i.message}`)))) : null,

    el("div", { class: "split" },
      tablePanel("功能分类",
        table([{ label: "代码", mono: 1 }, { label: "功能" }, { label: "角色" }],
          obj.functions, f => [
            el("td", { class: "mono" }, f.code),
            el("td", {}, `${f.name_cn}（${f.domain_name}）`),
            el("td", {}, f.function_role === "PRIMARY" ? el("b", {}, "主功能") : "辅助功能")])),
      tablePanel("交叉引用",
        table([{ label: "类型" }, { label: "编号", mono: 1 }],
          obj.cross_references, c => [
            el("td", { class: "nowrap" }, codeText(c.reference_type)),
            el("td", { class: "mono" }, c.reference_value)]))),

    tablePanel('关联设计文件', table([{label:'文件号'},{label:'名称'},{label:'关联角色'}], definitions, d => [
      el('td',{},link(d.file_number,'#/file/'+encodeURIComponent(d.file_number))),
      el('td',{},d.title_cn),el('td',{},ROLE_CN[d.relation_type] || d.relation_type)])),
    softwarePanel,
    tablePanel("属性",
      table([{ label: "属性" }, { label: "值" }, { label: "单位" }],
        obj.attributes, a => [
          el("td", {}, a.name_cn),
          el("td", { class: "mono" }, attrValue(a)),
          el("td", { class: "muted" }, a.unit_code || "—")])),

    cfg && cfg.current_baseline ? tablePanel(
      `当前技术状态 · ${cfg.current_baseline.baseline_code}`,
      table([{ label: "类型" }, { label: "内容", mono: 1 }, { label: "角色" }, { label: "状态" }],
        cfg.items, i => [
          el("td", { class: "nowrap muted" }, ITEM_CN[i.item_type] || i.item_type),
          el("td", { class: "mono" }, i.item_label),
          el("td", { class: "muted" }, ROLE_CN[i.item_role] || i.item_role || "—"),
          el("td", {}, status(i.item_status))]),
      link("全部基线", `#/baselines/${encodeURIComponent(obj.full_part_number)}`)) : null,

    isPart ? tablePanel("BOM",
      null, link("查看与编辑", "#/bom/" + encodeURIComponent(code))) : null,

    wu && wu.working.length ? tablePanel("装机关系（工作 BOM）",
      table([{ label: "层级" }, { label: "父项", mono: 1 }, { label: "项号" },
             { label: "数量" }, { label: "路径" }],
        wu.working, w => [
          el("td", { class: "num" }, w.level),
          el("td", { class: "mono" }, link(w.parent_object_code, "#/object/" + encodeURIComponent(w.parent_object_code))),
          el("td", { class: "mono" }, w.item_number),
          el("td", { class: "num" }, w.quantity),
          el("td", { class: "mono muted" }, w.path)])) : null,
  );
}

const ITEM_CN = { FILE_REVISION: "文件版次", BOM_SNAPSHOT: "BOM 快照",
                  EXTERNAL_TECHNICAL_STATE: "外部件技术状态", SOFTWARE_VERSION: "软件版本" };
const ROLE_CN = { PRIMARY_DEFINITION: "主设计定义", SUPPORTING_DEFINITION: "支持性定义",
                  INTERFACE_DEFINITION: "接口定义", QUALIFICATION_EVIDENCE: "鉴定证据" };

function attrValue(a) {
  for (const k of ["value_text", "value_number", "value_integer", "value_enum_code", "value_date"])
    if (a[k] !== null && a[k] !== undefined) return String(a[k]);
  if (a.value_boolean !== null && a.value_boolean !== undefined) return a.value_boolean ? "是" : "否";
  return "—";
}
