/* 设计文件管理: 文件列表、文件详情(版次时间线/对比/引用关系)、发布资料库。
   发布资料库面向生产、采购等只读使用者: 只呈现"当前有效"的已发布资料, 一步下载。 */
import { api } from "./api.js";
import {
  el, table, tablePanel, panel, empty, status, statusText, codeText, field, input, select,
  toast, toastError, fmtDate, link, askReason, pageControls,
} from "./ui.js";

const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
const enc = encodeURIComponent;

const STAGE_OPTIONS = [
  { value: "", label: "全部" }, { value: "RELEASED", label: "已有发布版次" },
  { value: "OPEN", label: "有编制/审核中版次" }, { value: "NONE", label: "尚无发布版次" },
];
const STATUS_OPTIONS = [
  { value: "", label: "全部状态" }, { value: "ACTIVE", label: "有效" }, { value: "OBSOLETE", label: "已作废" },
];
const RELATION_OPTIONS = [
  ["PRIMARY_DEFINITION", "主设计定义"], ["SUPPORTING_DEFINITION", "支持性定义"],
  ["INTERFACE_DEFINITION", "接口定义"], ["QUALIFICATION_EVIDENCE", "鉴定证据"],
].map(([value, label]) => ({ value, label }));

const sizeText = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;

/* ---------------- 文件列表 ---------------- */
export async function files(ctx, params) {
  const q = params.get("q") || "", type = params.get("file_type_code") || "";
  const st = params.get("status") || "", stage = params.get("stage") || "";
  const page = Number(params.get("page") || 1);
  const [result, types] = await Promise.all([
    api.get("/files", { query: { page, page_size: 100, q, file_type_code: type, status: st, stage } }),
    api.get("/dictionary/file-type"),
  ]);

  const qIn = input({ value: q, placeholder: "文件号或名称" });
  const typeSel = select([{ value: "", label: "全部类型" },
    ...types.map(t => ({ value: t.code, label: `${t.code} ${t.name_cn}` }))]);
  const stSel = select(STATUS_OPTIONS), stageSel = select(STAGE_OPTIONS);
  typeSel.value = type; stSel.value = st; stageSel.value = stage;
  const search = e => {
    e.preventDefault();
    location.hash = "#/files?" + new URLSearchParams({ q: qIn.value.trim(), file_type_code: typeSel.value,
      status: stSel.value, stage: stageSel.value, page: 1 });
  };

  const numIn = input({ class: "mono", placeholder: "UG-A10001M001" });
  const newType = select(types.map(t => ({ value: t.code, label: `${t.code} ${t.name_cn}` })));
  const titleIn = input({ placeholder: "文件名称" });

  return el("div", {},
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, "设计文件"),
        el("p", { class: "sub" }, "文件身份与版次分离：图号不变、内容改了，是新版次而不是新文件。")),
      el("div", { class: "page-head-actions" }, link("发布资料库", "#/library", "btn"))),
    el("form", { class: "filter-bar", onsubmit: search },
      field("关键词", qIn), field("文件类型", typeSel), field("状态", stSel), field("版次进展", stageSel),
      el("div", { class: "filter-actions" },
        el("button", { class: "btn primary", type: "submit" }, "查询"),
        link("重置", "#/files", "btn"))),
    ctx.can("draft_write") ? el("details", { class: "panel fold" },
      el("summary", {}, "新建设计文件"),
      el("div", { class: "body" }, el("div", { class: "inline-form" },
        field("文件号", numIn), field("文件类型", newType), field("名称", titleIn),
        el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary", onclick: async () => {
          try {
            const created = await api.post("/files", { json: { file_number: numIn.value.trim(),
              file_type_code: newType.value, title_cn: titleIn.value.trim() } });
            toast("文件已建立"); location.hash = "#/file/" + enc(created.file_number);
          } catch (e) { toastError(e); } } }, "建立"))))) : null,
    result.items.length ? tablePanel(`文件 · 共 ${result.total} 个`,
      table([{ label: "文件号", mono: 1 }, { label: "名称" }, { label: "类型" }, { label: "状态" },
             { label: "当前发布版次" }, { label: "发布时间" }, { label: "进行中" }, { label: "版次数" }],
        result.items, f => [
          el("td", { class: "mono" }, link(f.file_number, "#/file/" + enc(f.file_number))),
          el("td", {}, f.title_cn),
          el("td", { class: "muted" }, codeText(f.file_type_code)),
          el("td", {}, f.status === "OBSOLETE" ? status("OBSOLETE") : el("span", { class: "muted" }, "有效")),
          el("td", { class: "mono" }, f.current_released_revision ? "Rev." + f.current_released_revision : "—"),
          el("td", { class: "muted nowrap" }, f.released_at ? fmtDate(f.released_at) : "—"),
          el("td", {}, f.open_revision
            ? el("span", {}, "Rev." + f.open_revision.split(":")[0], " ", status(f.open_revision.split(":")[1])) : "—"),
          el("td", { class: "num" }, f.revision_count)]),
      pageControls(result, "/files", { q, file_type_code: type, status: st, stage }))
      : empty("没有符合条件的设计文件", "请调整查询条件，或新建文件。"));
}

/* ---------------- 文件详情 ---------------- */
export async function fileDetail(ctx, params, num) {
  const [f, usage] = await Promise.all([
    api.get("/files/" + enc(num)),
    api.get(`/files/${enc(num)}/where-used`),
  ]);
  const open = f.revisions.find(r => ["WORKING", "IN_REVIEW"].includes(r.status));
  const obsolete = f.status === "OBSOLETE";
  const current = f.revisions.find(r => r.status === "RELEASED");

  const acts = el("div", { class: "actions" });
  if (!obsolete && !open && ctx.can("draft_write"))
    acts.append(el("button", { class: "btn primary", onclick: async () => {
      const s = await askReason("新建版次", "本次修改内容是什么");
      if (!s) return;
      try { await api.post(`/files/${enc(num)}/revisions`, { json: { change_summary: s } });
            toast("版次已建立"); reload(); } catch (e) { toastError(e); } } }, "新建版次"));
  if (!obsolete && ctx.can("draft_write"))
    acts.append(el("button", { class: "btn", onclick: async () => {
      const t = window.prompt("文件名称（中文）", f.title_cn);
      if (!t || !t.trim() || t.trim() === f.title_cn) return;
      try { await api.patch(`/files/${enc(num)}`, { json: { title_cn: t.trim(), title_en: f.title_en || null } });
            toast("名称已更新"); reload(); } catch (e) { toastError(e); } } }, "修改名称"));
  if (ctx.can("baseline_release"))
    acts.append(el("button", { class: obsolete ? "btn" : "btn danger", onclick: async () => {
      const r = await askReason(obsolete ? "恢复文件" : "作废文件",
        obsolete ? "恢复后可继续出新版次" : "作废后不能再出新版次；已发布版次与已冻结基线不受影响");
      if (!r) return;
      try { await api.post(`/files/${enc(num)}/${obsolete ? "reactivate" : "obsolete"}`, { json: { reason: r } });
            toast(obsolete ? "文件已恢复" : "文件已作废"); reload(); } catch (e) { toastError(e); } } },
      obsolete ? "恢复文件" : "作废文件"));

  const cell = (l, v, mono) => el("div", { class: "tb-cell" }, el("b", {}, l), el("span", { class: mono ? "mono" : null }, v ?? "—"));

  /* 当前发布版次: 使用者最常需要的一步 —— 直接拿到有效文件 */
  const currentPanel = current
    ? await currentRevisionPanel(current)
    : el("div", { class: "note" }, obsolete ? "该文件已作废，无当前有效版次。" : "尚无已发布版次，正式发布前请勿用于生产或采购。");

  return el("div", {},
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" }, el("span", { class: "tb-code" }, f.file_number), el("span", { class: "tb-name" }, f.title_cn)),
      el("div", { class: "tb-grid" },
        cell("文件类型", codeText(f.file_type_code)), cell("状态", statusText(f.status)),
        cell("当前发布版次", current ? "Rev." + current.revision_number : null, true),
        cell("建立时间", fmtDate(f.created_at)))),
    acts,
    obsolete ? el("div", { class: "note warn" }, "该文件已作废，不再允许新增版次。历史版次与基线引用仍可查阅。") : null,
    currentPanel,
    impactPanel(ctx, num),
    revisionTimeline(f, num),
    ctx.can("draft_write") || usage.objects.length || usage.baselines.length ? usagePanels(ctx, num, usage, obsolete) : null);
}

async function currentRevisionPanel(rev) {
  const r = await api.get("/revisions/" + rev.id);
  return panel(`当前有效版次 · Rev.${r.revision_number}`, el("div", {},
    el("p", { class: "muted" }, "发布于 ", fmtDate(r.released_at), r.change_summary ? " · " + r.change_summary : ""),
    r.attachments.length ? el("div", { class: "file-chips" }, r.attachments.map(a =>
      el("button", { class: "file-chip", title: a.filename,
          onclick: () => api.download(`/attachments/${a.id}/download`, a.filename).catch(toastError) },
        el("b", {}, codeText(a.attachment_role)), el("span", {}, a.filename),
        el("small", {}, sizeText(a.size_bytes) + (a.integrity_status === "OK" ? " · 已校验" : ""))))) : empty("该版次无附件")),
    link("打开版次详情", "#/revision/" + rev.id, "btn small"));
}

/* 变更影响分析: 点开才查询(要逐级向上展开 BOM, 不必每次打开详情页都算) */
function impactPanel(ctx, num) {
  const box = el("div", { class: "body" }, el("p", { class: "muted" }, "点击“展开分析”，查看这份文件出新版次会波及哪些件号、上层组件和基线。"));
  const btn = el("button", { class: "btn small", onclick: async () => {
    btn.disabled = true; btn.textContent = "分析中…";
    try {
      const d = await api.get(`/files/${enc(num)}/impact`);
      const s = d.summary;
      const stat = (n, l, warn) => el("div", { class: "impact-stat" + (warn && n ? " is-warn" : "") }, el("b", {}, n), el("span", {}, l));
      box.replaceChildren(
        el("div", { class: "impact-stats" },
          stat(s.objects, "关联的设计对象"), stat(s.upstream_parents, "受影响的上层组件"),
          stat(s.baselines, "锁定它的基线"), stat(s.current_baselines_behind, "落后于最新版次的当前基线", true)),
        el("div", { class: "note" }, d.note),
        d.objects.length ? table([{ label: "设计对象", mono: 1 }, { label: "关系" }, { label: "状态" }, { label: "向上被这些组件使用" }],
          d.objects, o => [
            el("td", { class: "mono" }, link(o.object_code, "#/object/" + enc(o.object_code))),
            el("td", {}, codeText(o.relation_type)), el("td", {}, status(o.lifecycle_status)),
            el("td", {}, o.upstream.length
              ? o.upstream.map(u => el("span", { class: "chip mono", title: `第 ${u.level} 级 · ${u.parent_name}` }, u.parent_object_code))
              : el("span", { class: "muted" }, "无（顶层或未纳入 BOM）"))]) : el("p", { class: "muted" }, "尚未关联任何设计对象。"),
        d.objects_truncated ? el("p", { class: "muted" }, "关联对象较多，仅展示前 30 个。") : null,
        d.baselines.length ? table([{ label: "件号", mono: 1 }, { label: "基线", mono: 1 }, { label: "锁定版次", mono: 1 }, { label: "是否需要处理" }],
          d.baselines, b => [
            el("td", { class: "mono" }, link(b.full_part_number, "#/baselines/" + enc(b.full_part_number))),
            el("td", { class: "mono" }, b.baseline_code + (b.is_current ? "（当前）" : "")),
            el("td", { class: "mono" }, "Rev." + b.revision_number),
            el("td", {}, b.behind ? el("span", { class: "diff diff-CHANGED" }, `落后于 Rev.${d.latest_released_revision}，需发布新基线才会采用`)
              : el("span", { class: "muted" }, "—"))]) : null);
      btn.remove();
    } catch (e) { btn.disabled = false; btn.textContent = "展开分析"; toastError(e); }
  } }, "展开分析");
  return panel("变更影响分析", box, btn);
}

function revisionTimeline(f, num) {
  const revs = f.revisions;
  const box = el("div", {});
  /* 已取消的版次没有交付内容, 不参与对比, 否则默认对比会落在一个空版次上 */
  const comparable = revs.filter(r => r.status !== "CANCELLED");
  const opts = comparable.map(r => ({ value: r.id, label: `Rev.${r.revision_number}（${statusText(r.status)}）` }));
  const a = select(opts), b = select(opts);
  if (comparable.length > 1) { a.value = comparable[comparable.length - 2].id; b.value = comparable[comparable.length - 1].id; }
  const compare = async () => {
    if (a.value === b.value) return toast("请选择两个不同的版次", "error");
    try {
      const c = await api.get(`/files/${enc(num)}/compare`, { query: { a: a.value, b: b.value } });
      const stCn = { SAME: "未变化", CHANGED: "内容已变", ADDED: "新增", REMOVED: "移除" };
      box.replaceChildren(
        el("p", { class: "muted" }, `Rev.${c.a.revision_number} → Rev.${c.b.revision_number}：${c.changed} 项有差异`),
        c.b.change_summary ? el("p", {}, "变更说明：", c.b.change_summary) : null,
        table([{ label: "附件用途" }, { label: "结果" }, { label: "旧版文件" }, { label: "新版文件" }], c.rows, r => [
          el("td", {}, codeText(r.role)),
          el("td", {}, el("span", { class: "diff diff-" + r.state }, stCn[r.state])),
          el("td", {}, r.a ? `${r.a.filename}（${sizeText(r.a.size_bytes)}）` : "—"),
          el("td", {}, r.b ? `${r.b.filename}（${sizeText(r.b.size_bytes)}）` : "—")]) || empty("两个版次都没有附件"));
    } catch (e) { toastError(e); }
  };
  return el("div", {},
    tablePanel("版次历史",
      table([{ label: "版次", mono: 1 }, { label: "状态" }, { label: "变更说明" }, { label: "附件数" },
             { label: "发布时间" }, { label: "" }],
        [...revs].reverse(), r => [
          el("td", { class: "mono" }, "Rev." + r.revision_number),
          el("td", {}, status(r.status)),
          el("td", {}, r.change_summary || "—"),
          el("td", { class: "num" }, r.attachment_count),
          el("td", { class: "muted nowrap" }, fmtDate(r.released_at)),
          el("td", { class: "right" }, link("打开", "#/revision/" + r.id, "btn small"))])),
    comparable.length > 1 ? panel("版次对比", el("div", {},
      el("div", { class: "inline-form" }, field("旧版", a), field("新版", b),
        el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn", onclick: compare }, "对比附件"))), box)) : null);
}

function usagePanels(ctx, num, usage, obsolete) {
  const canLink = ctx.can("draft_write") && !obsolete;
  const codeIn = input({ class: "mono", placeholder: "内部件号 / 外部件号 / 软件编号" });
  const relSel = select(RELATION_OPTIONS), noteIn = input({ maxlength: 256, placeholder: "适用性说明（可选）" });
  const linkForm = canLink ? panel("关联到设计对象", el("div", { class: "inline-form" },
    field("对象编号", codeIn), field("关联角色", relSel), field("说明", noteIn),
    el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary", onclick: async () => {
      try {
        await api.post("/definitions", { json: { object_code: codeIn.value.trim(), file_number: num,
          relation_type: relSel.value, applicability_note: noteIn.value.trim() || null } });
        toast("已关联"); reload();
      } catch (e) { toastError(e); } } }, "关联")))) : null;

  return el("div", {},
    tablePanel("被这些设计对象引用（逻辑关系）",
      table([{ label: "对象编号", mono: 1 }, { label: "名称" }, { label: "关系" }, { label: "对象状态" },
             { label: "说明" }, { label: "" }], usage.objects, o => [
        el("td", { class: "mono" }, link(o.object_code, "#/object/" + enc(o.object_code))),
        el("td", {}, o.display_name), el("td", {}, codeText(o.relation_type)),
        el("td", {}, o.is_active ? status(o.lifecycle_status) : el("span", { class: "muted" }, "关联已解除")),
        el("td", { class: "muted" }, o.applicability_note || "—"),
        el("td", { class: "right" }, o.is_active && ctx.can("draft_write") ? el("button", { class: "btn small", onclick: async () => {
          const r = await askReason("解除关联", `解除 ${o.object_code} 与本文件的关联`);
          if (!r) return;
          try { await api.del(`/definitions/${o.link_id}`, { query: { reason: r } }); toast("关联已解除"); reload(); }
          catch (e) { toastError(e); } } }, "解除") : null)]) || empty("尚未关联任何设计对象")),
    linkForm,
    tablePanel("被这些基线锁定（具体版次的硬引用）",
      table([{ label: "件号", mono: 1 }, { label: "名称" }, { label: "基线", mono: 1 }, { label: "基线状态" }, { label: "锁定版次", mono: 1 }],
        usage.baselines, b => [
          el("td", { class: "mono" }, link(b.full_part_number, "#/baselines/" + enc(b.full_part_number))),
          el("td", {}, b.formal_name_cn), el("td", { class: "mono" }, b.baseline_code),
          el("td", {}, status(b.baseline_status), b.is_current ? " · 当前" : ""),
          el("td", { class: "mono" }, "Rev." + b.revision_number)]) || empty("尚无基线锁定本文件的版次")));
}

/* ---------------- 发布资料库(生产/采购/只读用户) ---------------- */
export async function releasedLibrary(ctx, params) {
  const q = params.get("q") || "", type = params.get("file_type_code") || "";
  const role = params.get("attachment_role") ?? "RELEASED_PDF";
  const page = Number(params.get("page") || 1);
  const query = { q, file_type_code: type, revision_status: "RELEASED", current_only: "true", attachment_role: role };
  const [result, types] = await Promise.all([
    api.get("/design-materials", { query: { ...query, page, page_size: 50 } }),
    api.get("/dictionary/file-type"),
  ]);
  const pnIn = input({ class: "mono", placeholder: "例如 UG200001-001" });
  const qIn = input({ value: q, placeholder: "文件号、名称或附件名" });
  const typeSel = select([{ value: "", label: "全部类型" }, ...types.map(t => ({ value: t.code, label: t.name_cn }))]);
  const roleSel = select([{ value: "RELEASED_PDF", label: "发布版 PDF" }, { value: "", label: "全部附件" },
    { value: "PRIMARY_NATIVE", label: "原生文件" }, { value: "DERIVED_STEP", label: "STEP" }, { value: "DERIVED_DXF", label: "DXF" }]);
  typeSel.value = type; roleSel.value = role;
  const search = e => {
    e.preventDefault();
    location.hash = "#/library?" + new URLSearchParams({ q: qIn.value.trim(), file_type_code: typeSel.value,
      attachment_role: roleSel.value, page: 1 });
  };
  return el("div", {},
    el("h1", {}, "发布资料库"),
    el("p", { class: "sub" }, "这里只列已批准发布、当前有效的资料，可直接查阅和下载。草稿、审核中和已被取代的版次不会出现。"),
    el("form", { class: "filter-bar", onsubmit: e => { e.preventDefault(); const v = pnIn.value.trim();
        if (v) location.hash = "#/part/" + enc(v); } },
      field("按件号查资料包（当前基线、有效文件、BOM）", pnIn),
      el("div", { class: "filter-actions" }, el("button", { class: "btn primary", type: "submit" }, "查看资料包"))),
    el("form", { class: "filter-bar", onsubmit: search },
      field("关键词", qIn), field("文件类型", typeSel), field("附件类型", roleSel),
      el("div", { class: "filter-actions" }, el("button", { class: "btn primary", type: "submit" }, "查询"),
        link("重置", "#/library", "btn"))),
    result.items.length ? tablePanel(`当前有效资料 · 共 ${result.total} 份`,
      table([{ label: "文件号", mono: 1 }, { label: "名称" }, { label: "类型" }, { label: "版次", mono: 1 },
             { label: "附件" }, { label: "大小" }, { label: "" }], result.items, r => [
        el("td", { class: "mono" }, link(r.file_number, "#/file/" + enc(r.file_number))),
        el("td", {}, r.title_cn), el("td", { class: "muted" }, r.file_type_name),
        el("td", { class: "mono" }, "Rev." + r.revision_number),
        el("td", {}, r.filename, el("div", { class: "muted small" }, codeText(r.attachment_role))),
        el("td", { class: "nowrap" }, sizeText(r.size_bytes)),
        el("td", { class: "right" }, el("button", { class: "btn small primary",
          onclick: () => api.download(`/attachments/${r.id}/download`, r.filename).catch(toastError) }, "下载"))]),
      pageControls(result, "/library", { q, file_type_code: type, attachment_role: role }))
      : empty("没有符合条件的有效资料", "可以换个关键词，或把附件类型改为“全部附件”。"));
}
