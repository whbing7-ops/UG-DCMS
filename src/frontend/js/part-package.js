/* 件号资料包: 生产、采购按件号一次看清"现在该用什么" —— 当前基线锁定的文件、BOM、外部件、软件,
   以及相对上一基线变了什么。文件以基线锁定的版次为准, 有更新版次时明确提示。 */
import { api } from "./api.js";
import { zipButton } from "./file-mgmt.js";
import { el, table, tablePanel, panel, empty, status, codeText, fmtDate, link, toast, toastError } from "./ui.js";

const enc = encodeURIComponent;
const sizeText = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;

export async function partPackage(ctx, params, pn) {
  const p = await api.get(`/parts/${enc(pn)}/release-package`);
  const cb = p.current_baseline;
  const head = el("div", { class: "page-head" },
    el("div", {}, el("h1", {}, p.full_part_number), el("p", { class: "sub" }, p.formal_name_cn, " · ", status(p.lifecycle_status))),
    el("div", { class: "page-head-actions" },
      cb ? el("button", { class: "btn", onclick: () => api.download(`/parts/${enc(pn)}/release-package.csv`, `${pn}-资料包.csv`)
        .catch(toastError) }, "导出 CSV") : null,
      link("返回资料库", "#/library", "btn")));

  if (!cb) return el("div", {}, head,
    el("div", { class: "note warn" }, "该件号目前没有已发布的基线，尚无可用于生产或采购的正式资料。"));

  const newer = p.documents.filter(d => d.newer_available);
  const chg = p.change_from_previous;

  return el("div", {}, head,
    el("div", { class: "note" }, `以下内容对应当前基线 ${cb.baseline_code}（发布于 ${fmtDate(cb.released_at)}）。建立原因：${cb.reason}`),
    newer.length ? el("div", { class: "note warn" },
      "有文件已出更新版次，但当前基线仍锁定旧版次；请以下表“基线锁定版次”为准，待新基线发布后再切换：",
      el("ul", {}, newer.map(d => el("li", {}, `${d.file_number}：基线锁定 Rev.${d.revision_number}，最新发布 Rev.${d.latest_revision}`)))) : null,
    chg ? changePanel(chg) : null,
    tablePanel(`有效设计文件 · ${p.documents.length} 份`,
      table([{ label: "文件号", mono: 1 }, { label: "名称" }, { label: "用途" }, { label: "基线锁定版次", mono: 1 }, { label: "下载" }],
        p.documents, d => [
          el("td", { class: "mono" }, link(d.file_number, "#/file/" + enc(d.file_number))),
          el("td", {}, d.title_cn), el("td", {}, codeText(d.item_role)),
          el("td", { class: "mono" }, "Rev." + d.revision_number, d.newer_available ? el("span", { class: "diff diff-CHANGED" }, "有更新版次") : null),
          el("td", {}, d.attachments.length ? d.attachments.map(a =>
            el("button", { class: "btn small", title: `${a.filename}（${sizeText(a.size_bytes)}）`,
              onclick: () => api.download(`/attachments/${a.id}/download`, a.filename).catch(toastError) }, codeText(a.attachment_role)))
            : el("span", { class: "muted" }, "无附件"),
            d.attachments.length > 1 ? zipButton(d.revision_id, d.revision_number, d.file_number) : null)]) || empty("基线未锁定设计文件")),
    tablePanel(`BOM · ${p.bom.length} 项`,
      table([{ label: "项号", mono: 1 }, { label: "件号", mono: 1 }, { label: "名称" }, { label: "数量" }, { label: "备注" }],
        p.bom, b => [
          el("td", { class: "mono" }, b.item_number),
          el("td", { class: "mono" }, link(b.child_object_code, "#/object/" + enc(b.child_object_code))),
          el("td", {}, b.child_display_name),
          el("td", { class: "num" }, Number(b.quantity) + (b.unit_code ? " " + b.unit_code : "")),
          el("td", { class: "muted" }, b.reference_designator || b.notes || "—")]) || empty("基线未锁定 BOM 快照（单件或尚未冻结）")),
    p.external.length ? tablePanel("外部件（采购）",
      table([{ label: "外部件号", mono: 1 }, { label: "技术状态" }, { label: "供应商版本" }, { label: "状态" }], p.external, e => [
        el("td", { class: "mono" }, link(e.external_code, "#/external/" + enc(e.external_code))),
        el("td", {}, e.label), el("td", {}, e.supplier_revision || "—"), el("td", {}, status(e.status))])) : null,
    p.software.length ? tablePanel("软件",
      table([{ label: "软件" }, { label: "版本" }, { label: "状态" }], p.software, s => [
        el("td", { class: "mono" }, link(s.software_number, "#/software/" + enc(s.software_number))),
        el("td", {}, s.version || "—"), el("td", {}, status(s.status))])) : null);
}

function changePanel(c) {
  const rows = [
    ...c.changed.map(x => ["变更", x.subject, `${x.from} → ${x.to}`]),
    ...c.added.map(x => ["新增", x, ""]), ...c.removed.map(x => ["移除", x, ""]),
  ];
  return panel(`相对上一基线 ${c.from_baseline} 的变化`, c.identical
    ? el("p", { class: "muted" }, "内容与上一基线一致。")
    : table([{ label: "类型" }, { label: "对象" }, { label: "说明" }], rows, r => [
        el("td", {}, el("span", { class: "diff diff-" + ({ 变更: "CHANGED", 新增: "ADDED", 移除: "REMOVED" })[r[0]] }, r[0])),
        el("td", { class: "mono" }, r[1]), el("td", { class: "mono" }, r[2])]));
}
