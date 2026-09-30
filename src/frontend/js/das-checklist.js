/* DOA 符合性检查单（体系级，UG-DAM-01-附3）。66 条要求 → 体系文件条款 → 自评结论。

   界面上有三件事是刻意做成这样的，改之前先看一眼：
   1. 自评不提供"编辑"，只提供"重新评定"——改判是新增一条留痕，不覆盖历史（判据 EV9-2）。
   2. 挂了条款引用不会让结论自动变成"符合"。结论永远是人选的（判据 C5）。
   3. 导出只出前 5 列，页面上的适用性、自评结论、监督覆盖是内部辅助列，不随导出走（判据 EV4-2）。 */
import { api } from "./api.js";
import { editor } from "./manage.js";
import { el, field, input, select, panel, table, tablePanel, empty, status, fmtDate, link, toast, toastError } from "./ui.js";

const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
const FLAG_CN = {
  doc_revised: "体系文件改版",
  authorization_revoked: "授权撤销，待复核已签文件",
  record_unavailable: "记录冻结／作废／到期处置",
};
const EV_KIND = { record: "记录", form: "表单", surveillance: "监督记录", other: "其他" };
const concStatus = c => c === "符合" ? "ACTIVE" : c === "部分符合" ? "SUPERSEDED" : c ? "OBSOLETE" : null;
const applCell = v => el("td", { class: v === "否" ? "muted" : v === "部分" ? "" : "" },
  v === "部分" ? el("strong", {}, "部分") : (v || "—"));

/* 自评对话框。结论、说明、完善计划、证据索引。 */
function assessDialog(item, after) {
  const conclusion = select([
    { value: "", label: "— 请选择结论 —" },
    { value: "符合", label: "符合" }, { value: "部分符合", label: "部分符合" }, { value: "不符合", label: "不符合" }]);
  const statement = el("textarea", { rows: 5, required: true, placeholder: "说明本单位如何满足该要求，引用文件的具体章节和做法" });
  const plan = el("textarea", { rows: 3, placeholder: "结论非「符合」时必填：打算怎么补、什么时候补" });
  const evRows = el("div", {});
  const addEv = () => {
    const kind = select(Object.entries(EV_KIND).map(([value, label]) => ({ value, label })));
    const ref = input({ placeholder: "记录／表单／监督记录编号" });
    const row = el("div", { class: "row gap" }, kind, ref,
      el("button", { type: "button", class: "btn small", onclick: () => row.remove() }, "移除"));
    row._get = () => ref.value.trim() ? { kind: kind.value, ref: ref.value.trim() } : null;
    evRows.append(row);
  };
  editor(`自评 · 第 ${item.seq} 条 ${item.req_code}`, el("div", {},
    el("p", { class: "muted" }, item.req_name),
    field("自评结论", conclusion),
    field("具体符合性说明", statement),
    field("完善计划", plan),
    panel("运行证据索引（选填）", [evRows,
      el("button", { type: "button", class: "btn small", onclick: addEv }, "＋ 添加证据")]),
    el("p", { class: "muted small" },
      "证据是索引不是正文：这里填记录或表单的编号，检查单不复制记录内容。",
      el("br"), "提交即以本人身份对该条作出自评；改判请再次提交，原评价不会被覆盖。")),
  async () => {
    if (!conclusion.value) throw Error("请选择自评结论：系统不会替你判定");
    const evidence = [...evRows.children].map(r => r._get()).filter(Boolean);
    await api.post(`/das/checklist/${item.id}/assess`, {
      json: {
        conclusion: conclusion.value, statement: statement.value,
        improvement_plan: plan.value.trim() || null, evidence,
      },
    });
    toast("自评已记录"); await after();
  }, { submit: "签署并记录" });
}

/* 条款引用对话框（判据 EV1：编号+名称+版本+条款号，四样齐全）。 */
function docRefDialog(item, after) {
  const code = input({ required: true, placeholder: "UG-DAP-12" });
  const name = input({ required: true, placeholder: "故障失效缺陷和不安全事件报告程序" });
  const version = input({ required: true, placeholder: "00" });
  const clause = input({ required: true, placeholder: "第 6 章第 4 步" });
  editor(`添加条款引用 · 第 ${item.seq} 条`, el("div", {},
    item.source_doc_ref_raw
      ? panel("附3 草案原文（逐字保留，供对照转录）",
          [el("p", { class: "mono small" }, item.source_doc_ref_raw)])
      : null,
    field("文件编号", code), field("文件名称", name),
    field("版本", version), field("条款号", clause),
    el("p", { class: "muted small" },
      "四样都要：不记版本，体系文件改版后就无从判断这条引用是否还成立。")),
  async () => {
    await api.post(`/das/checklist/${item.id}/doc-refs`, {
      json: { doc_code: code.value.trim(), doc_name: name.value.trim(),
              doc_version: version.value.trim(), clause: clause.value.trim() },
    });
    toast("引用已添加"); await after();
  }, { submit: "添加" });
}

/* 条目详情：引用、自评历史、监督覆盖、待复核标记。 */
export async function dasChecklistItemPage(ctx, params) {
  const id = params.get("id");
  const canManage = ctx.can("das_checklist_manage");
  const d = await api.get(`/das/checklist/${id}`);
  const refresh = async () => reload();

  const openFlags = (d.flags || []).filter(f => !f.cleared_at);

  return el("div", {},
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, `第 ${d.seq} 条 · ${d.req_code}`),
        el("p", { class: "sub" }, d.req_name)),
      el("div", { class: "page-head-actions" }, link("返回检查单", "#/das-checklist", "btn"))),

    openFlags.length ? panel("待复核", [
      el("p", {}, "本条自评已被标记待复核，复核关闭前不应继续作为符合性证据引用。"),
      ...openFlags.map(f => el("p", { class: "muted" },
        `${FLAG_CN[f.reason] || f.reason} · 触发源 ${f.source_ref} · ${fmtDate(f.raised_at)}`,
        canManage ? el("button", { class: "btn small", style: "margin-left:8px", onclick: async () => {
          const note = prompt("复核结论（必填）：");
          if (!note) return;
          try { await api.post(`/das/checklist/flags/${f.id}/clear`, { json: { note } }); toast("已关闭"); reload(); }
          catch (e) { toastError(e); }
        } }, "关闭") : null))]) : null,

    panel("要求原文", [el("p", {}, d.req_text || "—"),
      el("p", { class: "muted small" }, `来源 ${d.req_source} · 适用性 STC ${d.applicable_stc} / PMA ${d.applicable_pma}`,
        d.na_reason ? ` · 不适用理由：${d.na_reason}` : "")]),

    tablePanel(`体系文件条款引用 · ${(d.doc_refs || []).length} 条`,
      (d.doc_refs || []).length
        ? table([{ label: "文件编号" }, { label: "名称" }, { label: "版本" }, { label: "条款" }, { label: "状态" }],
            d.doc_refs, r => [
              el("td", {}, r.doc_code), el("td", {}, r.doc_name),
              el("td", {}, r.doc_version), el("td", {}, r.clause),
              el("td", {}, r.superseded_at
                ? el("span", { class: "muted" }, `已失效 ${fmtDate(r.superseded_at)}`)
                : status("ACTIVE"))])
        : empty("尚无引用", d.source_doc_ref_raw
            ? "附3 草案的原文已保留在下方，请对照逐条转录。"
            : "按判据 EV1 填写：文件编号 + 名称 + 版本 + 条款号。"),
      canManage ? el("button", { class: "btn small", onclick: () => docRefDialog(d, refresh) }, "＋ 添加引用") : null),

    d.source_doc_ref_raw ? panel("附3 草案原文（未转录）",
      [el("p", { class: "mono small" }, d.source_doc_ref_raw),
       el("p", { class: "muted small" },
         "该栏在 00 草案里有多种写法，无法可靠机器解析，故逐字保留由人转录——解析错的引用和对的长得一样，挂上去比留空更糟。")]) : null,

    tablePanel(`符合性自评 · ${(d.assessments || []).length} 次`,
      (d.assessments || []).length
        ? table([{ label: "结论" }, { label: "说明" }, { label: "完善计划" }, { label: "生效日期" }, { label: "签署人" }],
            d.assessments, (a, i) => [
              el("td", {}, status(concStatus(a.conclusion)), " ", a.conclusion,
                i === 0 ? el("span", { class: "muted small" }, " 现行") : null),
              el("td", {}, a.statement), el("td", { class: "muted" }, a.improvement_plan || "—"),
              el("td", { class: "nowrap" }, a.effective_from),
              el("td", { class: "muted" }, `${a.assessed_by_name} · ${fmtDate(a.assessed_at)}`)])
        : empty("尚无自评", "有引用不等于符合：结论要由适航管理负责人逐条判定并签署。"),
      canManage ? el("button", { class: "btn small primary", onclick: () => assessDialog(d, refresh) },
        (d.assessments || []).length ? "重新评定" : "自评") : null),

    tablePanel(`独立监督覆盖 · ${(d.coverage || []).length} 次`,
      (d.coverage || []).length
        ? table([{ label: "审核编号" }, { label: "类型" }, { label: "周期起" }, { label: "覆盖日期" }, { label: "结果" }, { label: "关联 NCR" }],
            d.coverage, c => [
              el("td", {}, c.audit_ref), el("td", {}, c.audit_kind === "internal" ? "独立监督" : "局方监督"),
              el("td", { class: "nowrap" }, c.cycle_start), el("td", { class: "nowrap" }, c.covered_at),
              el("td", {}, c.result), el("td", { class: "muted" }, c.ncr_ref || "—")])
        : empty("本周期尚未覆盖", "覆盖记录由独立监督模块从审核记录带出，不在此页手工录入。")));
}

/* 检查单总览。 */
export async function dasChecklistPage(ctx) {
  const canManage = ctx.can("das_checklist_manage");
  const [rows, stat] = await Promise.all([
    api.get("/das/checklist"),
    api.get("/das/checklist/stat/coverage").catch(() => []),
  ]);
  const assessed = rows.filter(r => r.conclusion).length;
  const flagged = rows.filter(r => Number(r.open_flags) > 0).length;
  const noRef = rows.filter(r => !Number(r.doc_ref_count)).length;
  const cur = stat[0];

  const head = panel("状态", [
    el("p", {}, `共 ${rows.length} 条 · 已自评 ${assessed} 条 · 未自评 ${rows.length - assessed} 条`,
      noRef ? el("span", {}, ` · 尚无条款引用 ${noRef} 条`) : null,
      flagged ? el("strong", {}, ` · 待复核 ${flagged} 条`) : null),
    cur ? el("p", { class: "muted" },
      `本周期（${cur.cycle_start || "未开始"}）独立监督覆盖 ${cur["已覆盖项数"] || 0}/${cur["适用项数"] || 0}，不符合项 ${cur["不符合项数"] || 0}`)
      : el("p", { class: "muted" }, "尚无独立监督覆盖记录。12 个月内须覆盖全部适用项。"),
    el("p", { class: "muted small" },
      "适用性中的「部分」计入覆盖率分母——部分适用仍须覆盖，只有 STC 与 PMA 都是「否」才排除。")]);

  return el("div", {},
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, "DOA 符合性检查单"),
        el("p", { class: "sub" }, "体系级，66 条：本单位的设计保证体系是否符合 CCAR-21 和 AP-21-18 附录D。项目级的符合性检查单是另一回事，不在此页。")),
      el("div", { class: "page-head-actions" },
        canManage ? el("button", { class: "btn", onclick: async () => {
          try {
            const r = await api.get("/das/checklist/export/submission");
            const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
            const a = el("a", { href: URL.createObjectURL(blob), download: `DOA符合性检查单-提交件-${r.exported_at.slice(0, 10)}.json` });
            a.click(); URL.revokeObjectURL(a.href);
            /* toast 只区分 error 与默认两种，所以把待复核提示放进 detail 作副行，
               而不是传一个不存在的 warn 类型被静默忽略。 */
            toast(`已导出 ${r.item_count} 条`, "ok", r.open_flag_count
              ? `注意：有 ${r.open_flag_count} 项待复核未关闭，这份提交件包含尚未复核的自评`
              : undefined);
          } catch (e) { toastError(e); }
        } }, "导出提交件（前 5 列）") : null)),
    head,
    rows.length
      ? tablePanel(`条目 · ${rows.length} 条`,
          table([{ label: "序号" }, { label: "要求编号" }, { label: "名称" }, { label: "来源" },
                 { label: "STC" }, { label: "PMA" }, { label: "引用" }, { label: "自评结论" }, { label: "" }],
            rows, r => [
              el("td", {}, String(r.seq)), el("td", { class: "nowrap" }, r.req_code),
              el("td", {}, r.req_name), el("td", { class: "muted" }, r.req_source),
              applCell(r.applicable_stc), applCell(r.applicable_pma),
              el("td", { class: Number(r.doc_ref_count) ? "" : "muted" }, String(r.doc_ref_count || 0)),
              el("td", {}, r.conclusion ? el("span", {}, status(concStatus(r.conclusion)), " ", r.conclusion)
                                        : el("span", { class: "muted" }, "未自评"),
                Number(r.open_flags) ? el("div", { class: "small" }, el("strong", {}, "待复核")) : null),
              el("td", { class: "right" }, link("查看", `#/das-checklist-item?id=${r.id}`, "btn small"))]))
      : empty("检查单为空", "运行迁移 0030 导入 UG-DAM-01-附3 的 66 条要求。"));
}
