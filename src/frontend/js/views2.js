/* 设计族向导、发号、BOM、文件、基线、质量、管理。 */
import { api } from "./api.js";
import { editor } from "./manage.js";
import { approvalSubmitButton } from "./extras.js";
import {
  el, table, tablePanel, panel, empty, status, statusText, field, input, select,
  toast, toastError, fmtDate, link, askReason, pageControls,
} from "./ui.js";

/* ==================== 设计族列表与向导 ==================== */
export async function families(ctx, params) {
  const page = Number(params.get("page") || 1), q = params.get("q") || "";
  const result = await api.get("/families", { query: { page, page_size: 100, q } });
  const rows = result.items;
  const searchIn = input({ value: q, placeholder: "搜索基本图号或名称" });
  return el("div", {},
    el("h1", {}, "设计族"),
    el("p", { class: "sub" }, "基本图号对应一个设计族。族内用 Dash 号区分具体规格。"),
    panel("查询", el("div", { class: "inline-form" }, field("关键词", searchIn),
      el("button", { class: "btn", onclick: () => { location.hash = "#/families?" + new URLSearchParams({ q: searchIn.value.trim(), page: 1 }); } }, "查询"))),
    el("div", { class: "actions" },
      el("a", { class: "btn primary", href: "#/family-new" }, "新建设计族")),
    rows.length ? tablePanel("全部设计族",
      table([{ label: "基本图号", mono: 1 }, { label: "名称" }, { label: "一级类别" },
             { label: "二级分类" }, { label: "Dash 数" }, { label: "状态" }],
        rows, r => [
          el("td", { class: "mono" }, link(r.basic_drawing_number.startsWith("PENDING-")
            ? "（待发号）" : r.basic_drawing_number, "#/family/" + r.id)),
          el("td", {}, r.family_name_cn),
          el("td", { class: "mono" }, r.primary_class_code),
          el("td", { class: "mono muted" }, r.physical_class_code),
          el("td", { class: "num" }, r.dash_count),
          el("td", {}, status(r.status))]), pageControls(result, "/families", { q }))
      : empty("还没有设计族", "新建设计族前，系统会先带你检索是否已有可复用的族。"));
}

/** 新建设计族向导。相似族检索是必经步骤，不提供跳过入口——
    基本图号一经分配即永不复用，建错族的代价远高于多花几分钟检索。 */
export async function familyNew() {
  const [pcs, terms, quals, fns, levels] = await Promise.all([
    api.get("/dictionary/physical-class"),
    api.get("/dictionary/core-term"),
    api.get("/dictionary/qualifier"),
    api.get("/dictionary/function-item"),
    api.get("/dictionary/object-level"),
  ]);

  const state = { cls: "T1", searched: false };
  const box = el("div", {});
  const stepsEl = el("div", { class: "steps" });
  const formEl = el("div", {});
  const previewEl = el("div", {});
  const similarEl = el("div", {});

  const clsSel = select(["T1", "T2", "T3"].map(c => ({ value: c, label: c + " 类" })));
  const pcSel = select([]);
  const lvSel = select(levels.map(l => ({ value: l.code, label: l.name_cn })));
  const ctSel = select([]);
  const q1Sel = select([]);
  const q2Sel = select([]);
  const fnSel = select(fns.map(f => ({ value: f.id, label: `${f.code} ${f.name_cn}` })));
  const defIn = el("textarea", { placeholder: "这一族对象共同的技术特征是什么" });
  const allowIn = el("textarea", { placeholder: "族内允许出现哪些变化，例如尺寸、材料" });
  const exclIn = el("textarea", { placeholder: "出现哪些变化就该另建新族，例如改变工作原理" });
  const reasonIn = el("textarea", { placeholder: "为什么现有设计族无法覆盖" });
  const noteIn = el("textarea", { placeholder: "选用“其他”类时必填：为什么无法归入现有分类" });

  function refreshClassOptions() {
    const c = clsSel.value;
    fill(pcSel, pcs.filter(p => p.primary_class_code === c)
      .map(p => ({ value: p.id, label: `${p.code} ${p.name_cn}` })));
    fill(ctSel, terms.filter(t => t.primary_class_code === c)
      .map(t => ({ value: t.id, label: `${t.code} ${t.name_cn}` })));
    const qOpts = [{ value: "", label: "（不使用）" }].concat(
      quals.map(x => ({ value: x.id, label: `${x.name_cn}（${x.qualifier_type}）` })));
    fill(q1Sel, qOpts); fill(q2Sel, qOpts);
    preview();
  }
  function fill(sel, opts) {
    sel.replaceChildren(...opts.map(o => el("option", { value: o.value }, o.label)));
  }

  async function preview() {
    if (!ctSel.value) return;
    try {
      const p = await api.post("/naming/preview", {
        json: { core_term_id: ctSel.value, qualifier_1_id: q1Sel.value || null,
                qualifier_2_id: q2Sel.value || null },
      });
      previewEl.replaceChildren(el("div", { class: "note" },
        "系统将生成的名称：",
        el("div", { class: "mono", style: "font-size:16px;margin-top:4px" }, p.family_name_cn),
        el("div", { class: "mono muted" }, p.family_name_en),
        el("div", { class: "muted", style: "margin-top:6px" },
          "名称由受控词典组合生成，不能手工改写。要改名称，就改核心词或限定词。")));
    } catch (e) { previewEl.replaceChildren(); }
  }

  async function runSimilar() {
    const hits = await api.post("/families/similar-search", {
      json: { primary_class_code: clsSel.value, physical_class_id: pcSel.value,
              core_term_id: ctSel.value,
              qualifier_ids: [q1Sel.value, q2Sel.value].filter(Boolean) },
    });
    state.searched = true;
    similarEl.replaceChildren(
      hits.length
        ? tablePanel(`找到 ${hits.length} 个相似设计族——请先确认是否可以复用`,
            table([{ label: "基本图号", mono: 1 }, { label: "名称" }, { label: "族定义" },
                   { label: "Dash 数" }, { label: "匹配度" }],
              hits, h => [
                el("td", { class: "mono" }, link(h.basic_drawing_number, "#/family/" + h.id)),
                el("td", {}, h.family_name_cn),
                el("td", { class: "muted" }, h.family_definition),
                el("td", { class: "num" }, h.dash_count),
                el("td", { class: "num" }, h.match_score)]))
        : el("div", { class: "note" }, "没有找到相似设计族，可以新建。"));
    renderSteps();
    submitBtn.disabled = false;
  }

  const submitBtn = el("button", { class: "btn primary", disabled: true, onclick: submit }, "建立设计族");

  async function submit() {
    try {
      const r = await api.post("/families", {
        json: {
          primary_class_code: clsSel.value, physical_class_id: pcSel.value,
          object_level_code: lvSel.value, core_term_id: ctSel.value,
          qualifier_1_id: q1Sel.value || null, qualifier_2_id: q2Sel.value || null,
          primary_function_id: fnSel.value,
          family_definition: defIn.value, allowed_variation: allowIn.value,
          excluded_variation: exclIn.value, new_family_reason: reasonIn.value,
          classification_note: noteIn.value || null,
        },
      });
      toast("设计族已建立，等待提交审批");
      location.hash = "#/family/" + r.id;
    } catch (e) { toastError(e); }
  }

  function renderSteps() {
    stepsEl.replaceChildren(
      el("div", { class: "step done" }, el("i", {}, "1"), "选择分类与命名"),
      el("div", { class: "step " + (state.searched ? "done" : "on") }, el("i", {}, "2"), "检索相似设计族"),
      el("div", { class: "step" }, el("i", {}, "3"), "填写族定义并提交"));
  }

  clsSel.addEventListener("change", refreshClassOptions);
  [ctSel, q1Sel, q2Sel].forEach(s => s.addEventListener("change", preview));
  refreshClassOptions();
  renderSteps();

  formEl.append(
    panel("分类与命名", el("div", {},
      el("div", { class: "grid2" },
        field("一级技术类别", clsSel),
        field("二级物理分类", pcSel),
        field("对象层级", lvSel),
        field("核心实体词", ctSel),
        field("稳定限定词 1", q1Sel),
        field("稳定限定词 2", q2Sel),
        field("主功能", fnSel)),
      previewEl)),
    panel("相似设计族检索", el("div", {},
      el("p", { class: "muted" },
        "基本图号一经分配即永不复用。先确认没有可复用的族，再建新的。"),
      el("button", { class: "btn", onclick: () => runSimilar().catch(toastError) },
         "检索相似设计族"),
      similarEl)),
    panel("族定义", el("div", {},
      field("族定义", defIn),
      field("允许的变化范围", allowIn),
      field("排除的变化范围", exclIn),
      field("新建理由", reasonIn),
      field("分类说明（选用“其他”类时必填）", noteIn),
      el("div", { class: "actions" }, submitBtn,
         el("span", { class: "muted", style: "align-self:center" },
            "完成相似检索后才能提交")))));

  box.append(el("h1", {}, "新建设计族"),
    el("p", { class: "sub" }, "名称由受控词典组合生成；提交后由批准人分配基本图号。"),
    stepsEl, formEl);
  return box;
}

/* ==================== 设计族详情 ==================== */
export async function familyDetail(ctx, params, id) {
  const fam = await api.get("/families/" + id);
  const [dashes, numbers] = await Promise.all([
    api.get(`/families/${id}/dashes`),
    api.get(`/families/${id}/numbers`),
  ]);

  const cell = (l, v, mono) => el("div", { class: "tb-cell" },
    el("b", {}, l), el("span", { class: mono ? "mono" : null }, v ?? "—"));

  const acts = el("div", { class: "actions" });
  if (fam.status === "PENDING") {
    if (ctx.can("submit") && !fam.approval_request_id)
      acts.append(approvalSubmitButton("提交审批", `/families/${id}/submit`, reload));
    if (ctx.can("approve") && String(fam.approval_assignee_user_id||'')===String(ctx.user.id))
      acts.append(el("button", { class: "btn primary", onclick: async () => {
        try { await api.post(`/families/${id}/approve`, { query: { comments: "同意新建" } });
              toast("已批准，基本图号已分配"); reload(); }
        catch (e) { toastError(e); } } }, "批准并分配图号"));
  }
  if (fam.status === "ACTIVE" && ctx.can("draft_write"))
    acts.append(el("a", { class: "btn primary", href: "#/dash-new/" + id }, "新增 Dash 件号"));

  if (fam.status === "ACTIVE" && ctx.can("number_allocate")) acts.append(el("button", { class: "btn", onclick: () => {
    const dash = input({ type: "number", min: 1, max: 999, required: true, value: numbers.next_available || "" });
    const reason = input({ required: true, maxlength: 256 });
    editor("登记跳号", el("div", {}, field("Dash 数字", dash), field("跳号原因", reason)), async () => {
      if (!reason.value.trim()) throw Error("请填写原因");
      await api.post(`/families/${id}/numbers/${Number(dash.value)}/skip`, { query: { reason: reason.value.trim() } }); toast("已登记跳号"); reload();
    });
  } }, "登记跳号"));
  const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

  return el("div", {},
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" },
        el("span", { class: "tb-code" }, fam.basic_drawing_number.startsWith("PENDING-")
          ? "（待发号）" : fam.basic_drawing_number),
        el("span", { class: "tb-name" }, fam.family_name_cn)),
      el("div", { class: "tb-grid" },
        cell("状态", statusText(fam.status)),
        cell("英文名称", fam.family_name_en, true),
        cell("一级类别", fam.primary_class_code, true),
        cell("二级分类", fam.physical_class_code, true),
        cell("核心实体词", fam.core_term_code, true),
        cell("主功能", fam.primary_function_code, true),
        cell("对象层级", fam.object_level_code),
        cell("批准时间", fmtDate(fam.approved_at)))),
    acts,

    panel("族定义", el("div", {},
      el("p", {}, fam.family_definition),
      el("p", {}, el("b", {}, "允许的变化："), " ", fam.allowed_variation || "—"),
      el("p", {}, el("b", {}, "排除的变化："), " ", fam.excluded_variation || "—"),
      fam.new_family_reason ? el("p", { class: "muted" }, "新建理由：" + fam.new_family_reason) : null)),

    tablePanel("Dash 件号",
      table([{ label: "件号", mono: 1 }, { label: "名称" }, { label: "差异说明" },
             { label: "状态" }],
        dashes, d => [
          el("td", { class: "mono" }, link(d.full_part_number, "#/object/" + encodeURIComponent(d.full_part_number))),
          el("td", {}, d.formal_name_cn),
          el("td", { class: "muted" }, d.difference_summary || "—"),
          el("td", {}, status(d.lifecycle_status))])),

    tablePanel(`号码占用（下一个可用：${numbers.next_available ?? "已用尽"}）`,
      table([{ label: "Dash", mono: 1 }, { label: "状态" }, { label: "说明" },
             { label: "操作" }],
        numbers.occupied, n => [
          el("td", { class: "mono" }, n.allocated_number),
          el("td", {}, status(n.status)),
          el("td", { class: "muted" }, n.cancel_reason || n.skip_reason || n.reserve_reason || "—"),
          el("td", {}, n.status === "ALLOCATED" && ctx.can("number_allocate")
            ? el("button", { class: "btn small danger", onclick: async () => {
                const reason = await askReason(`作废 Dash -${n.allocated_number}`,
                  "该号码作废后永不复用，请说明原因（将记入审计）");
                if (!reason) return;
                try { await api.post(`/families/${id}/numbers/${n.numeric_sequence}/cancel`,
                        { query: { reason } });
                      toast("号码已作废，该号永不复用"); reload(); }
                catch (e) { toastError(e); } } }, "作废")
            : null)])),
  );
}

/* ==================== 新增 Dash ==================== */
export async function dashNew(ctx, params, familyId) {
  const fam = await api.get("/families/" + familyId);
  const levels = await api.get("/dictionary/object-level");
  const nameIn = input({ placeholder: "例如：P形抱箍 内径12" });
  const enIn = input({ placeholder: "P-SHAPED CLAMP ID12", class: "mono" });
  const diffIn = el("textarea", { placeholder: "与同族其它 Dash 的差异是什么" });
  const lvSel = select(levels.map(l => ({ value: l.code, label: l.name_cn,
                                          selected: l.code === fam.object_level_code })));
  const check = el("div", {});

  nameIn.addEventListener("blur", async () => {
    if (!nameIn.value.trim()) return check.replaceChildren();
    const r = await api.post("/naming/check", { json: { text: nameIn.value, scope: "DASH" } });
    const bad = r.blocked, warn = r.warnings.concat(r.needs_approval);
    check.replaceChildren(
      bad.length ? el("div", { class: "note error" }, "名称中有不允许出现的内容：",
        el("ul", {}, bad.map(b => el("li", {}, b.message)))) :
      warn.length ? el("div", { class: "note warn" }, "请确认以下内容是否必要：",
        el("ul", {}, warn.map(b => el("li", {}, b.message)))) :
      el("div", { class: "note" }, "名称检查通过。"));
  });

  return el("div", {},
    el("h1", {}, "新增 Dash 件号"),
    el("p", { class: "sub" }, `设计族 ${fam.basic_drawing_number} · ${fam.family_name_cn}`),
    panel("件号信息", el("div", {},
      field("中文名称", nameIn), check,
      field("英文名称", enIn),
      field("对象层级", lvSel),
      field("与同族其它 Dash 的差异", diffIn),
      el("div", { class: "note" }, "Dash 号由系统按占用记录分配。已分配和已作废的号码都不会再使用。"),
      el("div", { class: "actions" },
        el("button", { class: "btn primary", onclick: async () => {
          try {
            const r = await api.post(`/families/${familyId}/dashes`, {
              json: { formal_name_cn: nameIn.value, formal_name_en: enIn.value,
                      object_level_code: lvSel.value, difference_summary: diffIn.value },
            });
            toast("件号已建立：" + r.full_part_number);
            location.hash = "#/object/" + encodeURIComponent(r.full_part_number);
          } catch (e) { toastError(e); } } }, "建立件号"),
        el("a", { class: "btn", href: "#/family/" + familyId }, "返回")))));
}
