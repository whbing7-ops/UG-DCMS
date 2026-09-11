/* BOM、设计文件、基线、数据质量、报表、系统管理。 */
import { api } from "./api.js";
import { editLine, rulePanel, snapshotTools, createApplicability } from "./bom-tools.js";
import { reasonAction, approvalSubmitButton } from "./extras.js";
import {
  el, table, tablePanel, panel, empty, status, statusText, codeText, field, input, select,
  toast, toastError, fmtDate, link, askReason, pageControls,
} from "./ui.js";

const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

/* ==================== BOM ==================== */
export async function bom(ctx, params, code) {
  const [data, expanded, validation, appRules, contexts] = await Promise.all([
    api.get("/bom/" + encodeURIComponent(code)),
    api.get(`/bom/${encodeURIComponent(code)}/expand`, { query: { max_depth: 10 } }),
    api.get(`/bom/${encodeURIComponent(code)}/validate`),
    api.get("/applicability/rules"),
    api.get("/configuration/contexts"),
  ]);

  const childIn = input({ placeholder: "输入内部件号、外部件号或名称后选择", class: "mono", autocomplete:"off", "aria-label":"子件号搜索" });
  const childResults=el("div",{class:"part-picker-results"});
  const childSelected=el("div",{class:"muted part-picker-selected"},"尚未选择子件");
  let selectedChild="",searchTimer,searchSequence=0;
  const searchChildren=async()=>{
    const sequence=++searchSequence;
    const rows=await api.get(`/bom-candidates/${encodeURIComponent(code)}`,{query:{q:childIn.value.trim(),limit:30}});
    if(sequence!==searchSequence)return;
    childResults.replaceChildren(...rows.map(x=>{
      const external=x.object_kind==="EXTERNAL_PART";
      const shown=external?(x.external_part_number||x.object_code):x.object_code;
      const disabled=x.lifecycle_status==="OBSOLETE";
      return el("button",{type:"button",class:"part-picker-option",disabled,onclick:()=>{
        ++searchSequence;clearTimeout(searchTimer);selectedChild=x.object_code;childIn.value=shown;childResults.replaceChildren();
        childSelected.textContent=`已选择：${shown} · ${x.display_name} · ${external?`外部件（${x.namespace_code}）`:"内部件"}`;
      }},el("b",{class:"mono"},shown),el("span",{},x.display_name),
        el("small",{},`${external?`外部件 · ${x.namespace_code}`:"内部件"} · ${statusText(x.lifecycle_status)}${disabled?" · 不可选":""}`));
    }));
    if(!rows.length)childResults.append(el("div",{class:"muted",style:"padding:10px"},"没有匹配的可用件号"));
    if(rows.length===30)childResults.append(el("div",{class:"muted"},"显示前30条，请补充关键词缩小范围"));
  };
  childIn.addEventListener("input",()=>{++searchSequence;selectedChild="";childResults.replaceChildren();childSelected.textContent="请从搜索结果中选择子件";clearTimeout(searchTimer);searchTimer=setTimeout(()=>searchChildren().catch(toastError),250);});
  childIn.addEventListener("focus",()=>searchChildren().catch(toastError));
  const itemIn = input({ placeholder: "项号，例如 010" });
  const qtyIn = input({ type: "number", step: "0.001", min: "0.001", value: "1" });
  const unitIn = input({ value: "EA", class: "mono" });
  const desigIn = input({ placeholder: "位号，可空" });
  const ruleSel = select([{ value: "", label: "ALL（全部构型）" }].concat(
    appRules.map(r => ({ value: r.rule_code, label: `${r.rule_code} ${r.name_cn}` }))));

  const addRow = ctx.can("draft_write") ? panel("新增 BOM 子件", el("div", {},
    el("div", { class: "inline-form" },
      field("项号", itemIn), field("子件号", el("div",{class:"part-picker"},childIn,childResults,childSelected)), field("数量", qtyIn),
      field("单位", unitIn), field("位号", desigIn), field("适用性", ruleSel),
      el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary", onclick: async () => {
        try {
          if(!selectedChild) throw Error("请搜索并从列表中选择子件号");
          const r = await api.post(`/bom/${encodeURIComponent(code)}/lines`, {
            json: { item_number: itemIn.value, child_object_code: selectedChild,
                    quantity: Number(qtyIn.value), unit_code: unitIn.value || null,
                    reference_designator: desigIn.value || null, applicability_rule_code: ruleSel.value || null },
          });
          if (r.warnings && r.warnings.length) r.warnings.forEach(w => toast(w, "error"));
          else toast("已添加");
          reload();
        } catch (e) { toastError(e); } } }, "新增子件"))),
    el("p", { class: "muted", style: "margin:8px 0 0" },
      "自引用和任意层级的循环会被系统拒绝——数量、项号、位号属于装配关系，不属于零件本身。"))) :
    el("div",{class:"note warn"},"当前账户只有查看权限，不能新增、编辑或删除 BOM 子件。请由系统管理员分配“设计工程师”或“构型管理员”角色。");

  if (ctx.can("draft_write")) addRow.classList.add("bom-add-panel");
  childIn.addEventListener("keydown", e => { if(e.key === "Escape") { ++searchSequence; clearTimeout(searchTimer); childResults.replaceChildren(); } });
  const issues = validation.errors.length || validation.warnings.length
    ? el("div", { class: validation.errors.length ? "note error" : "note warn" },
        validation.errors.length ? "以下问题会阻止生成快照：" : "以下内容请确认：",
        el("ul", {}, validation.errors.concat(validation.warnings).map(x => el("li", {}, x))))
    : null;

  return el("div", {},
    el("h1", {}, "BOM"),
    el("p", { class: "sub" }, el("span", { class: "mono" }, code)),
    el("div", { class: "note" },
      "BOM 中的删除只移除父子装配关系，不会删除或重新使用子件号主数据；件号停用需在对象状态流程中办理。"),
    el("div", { class: "actions" },
      link("BOM 管理", "#/bom", "btn"),
      link("返回对象", "#/object/" + encodeURIComponent(code), "btn"),
      link("装机关系", "#/where-used/" + encodeURIComponent(code), "btn"),
      ctx.can("baseline_release") ? el("button", { class: "btn primary", onclick: async () => {
        try { const s = await api.post(`/bom/${encodeURIComponent(code)}/snapshot`);
              toast(`已生成快照 ${s.snapshot_number}，内容自此冻结`); reload(); }
        catch (e) { toastError(e); } } }, "生成 BOM 快照") : null),
    issues, addRow,

    tablePanel("单层明细",
      table([{ label: "项号", mono: 1 }, { label: "子件号", mono: 1 }, { label: "名称" },
             { label: "数量" }, { label: "单位" }, { label: "位号" }, { label: "适用性" }, { label: "状态" },
             { label: "" }],
        data.lines, l => [
          el("td", { class: "mono" }, l.item_number),
          el("td", { class: "mono" }, link(l.child_object_code, "#/object/" + encodeURIComponent(l.child_object_code))),
          el("td", {}, l.child_name),
          el("td", { class: "num" }, l.quantity),
          el("td", { class: "muted" }, l.unit_code || "—"),
          el("td", { class: "mono muted" }, l.reference_designator || "—"),
          el("td", { class: "mono" }, l.applicability_rule_code || "ALL"),
          el("td", {}, status(l.child_status)),
          el("td", { class: "right" }, ctx.can("draft_write")
            ? el("div", { class: "row-actions" },
                el("button", { class: "btn small", onclick: () => editLine(l, appRules) }, "编辑"),
                reasonAction("删除子项", "/bom/lines/" + l.id, reload, "del")) : null)])
        || empty("BOM 为空", "从上方添加第一个子项。")),

    tablePanel("多层展开",
      table([{ label: "层" }, { label: "子件号", mono: 1 }, { label: "名称" },
             { label: "单层用量" }, { label: "累计用量" }, { label: "路径" }],
        expanded, x => [
          el("td", { class: "num" }, x.level),
          el("td", { class: "mono" },
            el("span", { class: "tree-indent" }, "· ".repeat(x.level - 1)),
            link(x.child_object_code, "#/object/" + encodeURIComponent(x.child_object_code))),
          el("td", {}, x.child_name),
          el("td", { class: "num" }, x.quantity),
          el("td", { class: "num" }, x.extended_quantity),
          el("td", { class: "mono muted" }, x.path)])),

    panel("按构型解析 BOM", resolvePanel(code, contexts, ctx)),
    snapshotTools(code),
    ctx.can("draft_write") ? panel("Applicability 规则", rulePanel(appRules)) : null,
    ctx.can("draft_write") ? panel("从表格导入", importForm(code)) : null);
}

function resolvePanel(code, contexts, ctx) {
  const ctxSel = select([{ value: "", label: "临时上下文" }].concat(
    contexts.map(c => ({ value: c.context_code, label: `${c.context_code} ${c.name_cn}` }))));
  const attrs = el("textarea", { rows: "4", placeholder: '{"model":"A320-214","msn":4567,"option":"O02"}',
    style: "width:100%;font-family:var(--mono,monospace)" });
  const out = el("div", {});
  const payload = () => {
    let a = {};
    if (attrs.value.trim()) a = JSON.parse(attrs.value);
    return { context_code: ctxSel.value || null, attributes: a, max_depth: 10 };
  };
  return el("div", {},
    el("p", { class: "muted" }, "选择已保存构型，或输入临时 JSON 上下文。系统只展开适用规则命中的 BOM 行；未绑定规则的行按 ALL 处理。"),
    field("构型上下文", ctxSel), el('details',{},el('summary',{},'临时构型属性（高级）'),field("临时/覆盖属性(JSON)", attrs)),
    el("div", { class: "actions" },
      el("button", { class: "btn primary", onclick: async () => {
        try { const r = await api.post(`/bom/${encodeURIComponent(code)}/resolve`, { json: payload() });
          out.replaceChildren(resolvedResult(r)); } catch(e) { toastError(e); } } }, "解析构型"),
      ctx.can("baseline_release") ? el("button", { class: "btn", onclick: async () => {
        try { const r = await api.post(`/bom/${encodeURIComponent(code)}/resolved-snapshot`, { json: payload() });
          toast(`已冻结 ${r.resolved_snapshot_number}`); reload(); } catch(e) { toastError(e); } } }, "冻结构型 BOM") : null),
    out);
}

function resolvedResult(r) {
  return el("div", {},
    r.overlaps && r.overlaps.length ? el("div", { class: "note error" },
      "适用性冲突，同一项号命中多个零件：", r.overlaps.map(x => `${x.item_number} → ${x.matches.join(" / ")}`).join("；")) :
      el("div", { class: "note" }, `解析通过，共 ${r.line_count} 行；排除 ${r.excluded?.length || 0} 行。`),
    table([{label:"层"},{label:"项号"},{label:"子件号",mono:1},{label:"数量"},{label:"累计"},{label:"规则",mono:1},{label:"路径"}],
      r.lines || [], x => [el("td",{},x.level),el("td",{class:"mono"},x.item_number),
        el("td",{class:"mono"},x.child_object_code),el("td",{class:"num"},x.quantity),
        el("td",{class:"num"},x.extended_quantity),el("td",{class:"mono"},x.rule_code || "ALL"),
        el("td",{class:"mono muted"},x.path)]));
}

function importForm(code) {
  const fileIn = el("input", { type: "file", accept: ".csv,.xlsx,.xlsm" });
  const out = el("div", {});
  return el("div", {},
    el("p", { class: "muted" },
      "上传后先预览，逐行显示校验结果。存在错误行时不会导入任何数据——部分导入会留下说不清完整性的 BOM。"),
    el("div", { class: "inline-form" },
      field("文件", fileIn),
      el("div", { style: "flex:0 0 auto" },
        el("button", { class: "btn", onclick: async () => {
          if (!fileIn.files[0]) return toast("请先选择文件", "error");
          try {
            const r = await api.upload("/import/bom/preview", { parent_object_code: code }, fileIn.files[0]);
            out.replaceChildren(previewResult(r));
          } catch (e) { toastError(e); } } }, "预览"),
        el("button", { class: "btn", onclick: () => api.download("/import/bom/template", "bom_template.csv").catch(toastError) }, "下载模板"))),
    out);
}

function previewResult(r) {
  return el("div", {},
    el("div", { class: r.error_rows ? "note error" : "note" },
      `共 ${r.total_rows} 行：${r.ok_rows} 行正常、${r.warning_rows} 行有提示、${r.error_rows} 行有错误。`,
      r.error_rows ? " 修正错误行后重新上传。" : ""),
    table([{ label: "行号" }, { label: "结果" }, { label: "内容" }, { label: "说明" }],
      r.rows, x => [
        el("td", { class: "num" }, x.row_number),
        el("td", {}, status(x.result)),
        el("td", { class: "mono muted" },
          [x.raw_data.item_number, x.raw_data.child_object_code, x.raw_data.quantity]
            .filter(Boolean).join(" / ")),
        el("td", {}, (x.messages || []).join("；") || "—")]),
    el("div", { class: "actions" },
      el("button", { class: "btn primary", disabled: !r.committable, onclick: async () => {
        try { const c = await api.post(`/import/batches/${r.batch_id}/commit`);
              toast(`已导入 ${c.lines_created} 行`); reload(); }
        catch (e) { toastError(e); } } },
        r.committable ? "确认导入" : "存在错误行，无法导入"),
      reasonAction("取消导入", `/import/batches/${r.batch_id}/abort`, reload)));
}

export async function whereUsed(ctx, params, code) {
  const r = await api.get("/where-used/" + encodeURIComponent(code));
  return el("div", {},
    el("h1", {}, "装机关系"),
    el("p", { class: "sub" }, el("span", { class: "mono" }, code),
      " 被哪些父项使用。工作 BOM 反映当前设计意图，快照反映已发布构型实际锁定的内容。"),
    r.used_in_released_baseline ? el("div", { class: "note warn" },
      "该对象已被已发布基线锁定。改动它之前，先确认对相关构型的影响。") : null,
    tablePanel("工作 BOM 中的父项",
      table([{ label: "层级" }, { label: "父项", mono: 1 }, { label: "项号" },
             { label: "数量" }, { label: "路径" }],
        r.working, w => [
          el("td", { class: "num" }, w.level),
          el("td", { class: "mono" }, link(w.parent_object_code, "#/object/" + encodeURIComponent(w.parent_object_code))),
          el("td", { class: "mono" }, w.item_number),
          el("td", { class: "num" }, w.quantity),
          el("td", { class: "mono muted" }, w.path)])
      || empty("没有父项引用该对象")),
    tablePanel("已冻结快照中的引用",
      table([{ label: "快照", mono: 1 }, { label: "父项", mono: 1 }, { label: "基线", mono: 1 },
             { label: "项号" }, { label: "数量" }],
        r.snapshots, s => [
          el("td", { class: "mono" }, s.snapshot_number),
          el("td", { class: "mono" }, s.parent_object_code),
          el("td", { class: "mono" }, s.baseline_code || "—"),
          el("td", { class: "mono" }, s.item_number),
          el("td", { class: "num" }, s.quantity)])));
}

/* ==================== 设计文件 ==================== */
export async function files(ctx, params) {
  const page = Number(params.get("page") || 1), q = params.get("q") || "";
  const result = await api.get("/files", { query: { page, page_size: 100, q } });
  const rows = result.items;
  const searchIn = input({ value: q, placeholder: "搜索文件号或名称" });
  const numIn = input({ class: "mono", placeholder: "UG-A10001M001" });
  const typeSel = select((await api.get("/dictionary/file-type"))
    .map(t => ({ value: t.code, label: `${t.code} ${t.name_cn}` })));
  const titleIn = input({ placeholder: "文件名称" });

  return el("div", {},
    el("h1", {}, "设计文件"),
    el("p", { class: "sub" }, "文件身份与版次分离。图号不变、内容改了，是新版次而不是新文件。"),
    panel("查询", el("div", { class: "inline-form" }, field("关键词", searchIn),
      el("button", { class: "btn", onclick: () => { location.hash = "#/files?" + new URLSearchParams({ q: searchIn.value.trim(), page: 1 }); } }, "查询"))),
    ctx.can("draft_write") ? panel("新建文件", el("div", { class: "inline-form" },
      field("文件号", numIn), field("文件类型", typeSel), field("名称", titleIn),
      el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary", onclick: async () => {
        try { const created = await api.post("/files", { json: { file_number: numIn.value,
                file_type_code: typeSel.value, title_cn: titleIn.value } });
              toast("文件已建立"); location.hash = "#/file/" + encodeURIComponent(created.file_number); }
        catch (e) { toastError(e); } } }, "建立"))))
      : el("div", { class: "note warn" }, "当前账户可查看设计文件；新建文件、版次和附件维护需要“设计工程师”或“构型管理员”角色。"),
    rows.length ? tablePanel("全部文件",
      table([{ label: "文件号", mono: 1 }, { label: "名称" }, { label: "类型" },
             { label: "当前发布版次" }, { label: "版次数" }],
        rows, f => [
          el("td", { class: "mono" }, link(f.file_number, "#/file/" + encodeURIComponent(f.file_number))),
          el("td", {}, f.title_cn),
          el("td", { class: "muted" }, codeText(f.file_type_code)),
          el("td", { class: "mono" }, f.current_released_revision || "—"),
          el("td", { class: "num" }, f.revision_count)]), pageControls(result, "/files", { q }))
      : empty("还没有设计文件"));
}

export async function fileDetail(ctx, params, num) {
  const f = await api.get("/files/" + encodeURIComponent(num));
  const open = f.revisions.find(r => ["WORKING", "IN_REVIEW"].includes(r.status));

  const acts = el("div", { class: "actions" });
  if (!open && ctx.can("draft_write"))
    acts.append(el("button", { class: "btn primary", onclick: async () => {
      const s = await askReason("新建版次", "本次修改内容是什么");
      if (!s) return;
      try { await api.post(`/files/${encodeURIComponent(num)}/revisions`,
              { json: { change_summary: s } }); toast("版次已建立"); reload(); }
      catch (e) { toastError(e); } } }, "新建版次"));

  const cell = (l, v, mono) => el("div", { class: "tb-cell" },
    el("b", {}, l), el("span", { class: mono ? "mono" : null }, v ?? "—"));

  return el("div", {},
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" },
        el("span", { class: "tb-code" }, f.file_number),
        el("span", { class: "tb-name" }, f.title_cn)),
      el("div", { class: "tb-grid" },
        cell("文件类型", codeText(f.file_type_code)),
        cell("状态", statusText(f.status)),
        cell("当前发布版次", f.current_released_revision, true),
        cell("建立时间", fmtDate(f.created_at)))),
    acts,
    el("div", { class: "note" },
      "已发布的版次内容冻结：不能替换附件，也不能改变更说明。内容要变就出新版次。"),
    tablePanel("版次",
      table([{ label: "版次", mono: 1 }, { label: "状态" }, { label: "变更说明" },
             { label: "附件数" }, { label: "发布时间" }, { label: "" }],
        f.revisions, r => [
          el("td", { class: "mono" }, "Rev." + r.revision_number),
          el("td", {}, status(r.status)),
          el("td", {}, r.change_summary || "—"),
          el("td", { class: "num" }, r.attachment_count),
          el("td", { class: "muted nowrap" }, fmtDate(r.released_at)),
          el("td", { class: "right" }, link("打开", "#/revision/" + r.id, "btn small"))])));
}

export async function revisionDetail(ctx, params, id) {
  const r = await api.get("/revisions/" + id);
  const editable = r.status === "WORKING";
  const fileIn = el("input", { type: "file" });
  const roleSel = select([
    { value: "RELEASED_PDF", label: "发布用 PDF" },
    { value: "PRIMARY_NATIVE", label: "原生设计文件" },
    { value: "DERIVED_STEP", label: "派生 STEP" },
    { value: "DERIVED_DXF", label: "派生 DXF" },
    { value: "REFERENCE", label: "参考资料" },
  ]);

  const acts = el("div", { class: "actions" });
  if (r.status === "WORKING" && ctx.can("submit"))
    acts.append(approvalSubmitButton("提交审核", `/revisions/${id}/submit`, reload));
  if (r.status === "IN_REVIEW" && ctx.can("approve") && String(r.approval_assignee_user_id||'')===String(ctx.user.id))
    acts.append(el("button", { class: "btn primary", onclick: async () => {
      try { await api.post(`/revisions/${id}/release`, { query: { comments: "同意发布" } });
            toast("已发布，内容自此冻结"); reload(); }
      catch (e) { toastError(e); } } }, "批准发布"));
  if (editable && ctx.can("draft_write")) acts.append(reasonAction("取消版次", `/revisions/${id}/cancel`, reload));
  if (ctx.can("read_audit"))
    acts.append(el("button", { class: "btn", onclick: async () => {
      try { const c = await api.post("/integrity/check", { query: { revision_id: id } });
            toast(c.passed ? `完整性检查通过（${c.checked} 个附件）`
                           : `发现 ${c.MISMATCH} 个被改动、${c.MISSING} 个丢失`,
                  c.passed ? "ok" : "error"); reload(); }
      catch (e) { toastError(e); } } }, "校验附件完整性"));

  return el("div", {},
    el("h1", {}, `${r.file_number} Rev.${r.revision_number}`),
    el("p", { class: "sub" }, r.title_cn, " · ", status(r.status)),
    el("p", {}, r.change_summary || ""),
    acts,
    editable && ctx.can("draft_write") ? panel("上传附件", el("div", {},
      el("div", { class: "inline-form" },
        field("用途", roleSel), field("文件", fileIn),
        el("div", { style: "flex:0 0 auto" },
          el("button", { class: "btn primary", onclick: async () => {
            if (!fileIn.files[0]) return toast("请先选择文件", "error");
            try { await api.upload(`/revisions/${id}/attachments`,
                    { role: roleSel.value }, fileIn.files[0]);
                  toast("附件已上传"); reload(); }
            catch (e) { toastError(e); } } }, "上传"))),
      el("p", { class: "muted", style: "margin:8px 0 0" },
        "上传时记录 SHA-256。发布之后附件不能替换或删除。"))) : null,
    tablePanel("附件",
      table([{ label: "用途" }, { label: "文件名" }, { label: "大小" },
             { label: "SHA-256", mono: 1 }, { label: "完整性" }, { label: "" }],
        r.attachments, a => [
          el("td", { class: "nowrap" }, codeText(a.attachment_role)),
          el("td", {}, a.filename),
          el("td", { class: "num" }, (a.size_bytes / 1024).toFixed(1) + " KB"),
          el("td", { class: "mono muted" }, a.sha256.slice(0, 16) + "…"),
          el("td", {}, status(a.integrity_status)),
          el("td", { class: "right" },
            el("button", { class: "btn small", onclick: () => api.download(`/attachments/${a.id}/download`, a.filename).catch(toastError) }, "下载"),
            editable && ctx.can("draft_write") ? reasonAction("删除附件", `/attachments/${a.id}`, reload, "del") : null)])
      || empty("还没有附件", "版次必须有附件才能提交审核。")));
}
