/* 三级签署(编制-审核-批准)与电子签名: 提交对话框、签署对话框、签署栏、有权签署人清单页。
   签署一律要求本人重新输入口令; 口令只随这次请求发送, 不保存、不写入页面状态。 */
import { api } from "./api.js";
import { editor } from "./manage.js";
import { el, field, input, select, panel, table, tablePanel, empty, status, statusText, fmtDate, link, toast, toastError, askReason, codeText } from "./ui.js";

const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
const who = x => `${x.full_name}（${x.username}${x.employee_no ? " · " + x.employee_no : ""}）`;

const passwordField = () => {
  const pw = input({ type: "password", required: true, autocomplete: "current-password", "aria-label": "签署口令" });
  return { pw, node: field("签署口令（重新输入您本人的口令）", pw) };
};

/* 提交审核 = "编制"签署: 选审核人、批准人, 再用口令确认 */
export function revisionSubmitButton(label, id, path, after, beforeSubmit = null) {
  return el("button", { class: "btn", onclick: async () => {
    try {
      const [reviewers, approvers] = await Promise.all([
        api.get(`/revisions/${id}/signers`, { query: { level: "REVIEW" } }),
        api.get(`/revisions/${id}/signers`, { query: { level: "APPROVE" } }),
      ]);
      if (!reviewers.length || !approvers.length)
        throw Error("没有可选的有权签署人：需要至少一名有权审核人和一名有权批准人，且与编制人各不相同。请联系构型管理员在“有权签署人”中登记。");
      const reviewer = select(reviewers.map(x => ({ value: x.id, label: who(x) })));
      const approver = select([]);
      const refill = () => {
        const list = approvers.filter(x => x.id !== reviewer.value);
        approver.replaceChildren(...list.map(x => el("option", { value: x.id }, who(x))));
      };
      reviewer.addEventListener("change", refill); refill();
      const { pw, node } = passwordField();
      editor(label, el("div", {},
        el("p", { class: "muted" }, "编制、审核、批准必须是三个不同的人；审核人和批准人须在有权签署人清单内。"),
        field("审核人（第 1 级）", reviewer), field("批准人（第 2 级）", approver), node,
        el("p", { class: "muted" }, "提交即表示您以口令确认对本版次内容的“编制”签署。")),
      async () => {
        if (!approver.value) throw Error("没有与所选审核人不同的有权批准人，请更换审核人或联系构型管理员");
        if (beforeSubmit) await beforeSubmit();
        await api.post(path, { json: { reviewer_user_id: reviewer.value, approver_user_id: approver.value, password: pw.value } });
        toast("已提交，编制签署完成，等待审核"); await after();
      }, { submit: "签署并提交" });
    } catch (e) { toast(e.message || "无法取得有权签署人", "error"); }
  } }, label);
}

/* 审核/批准签署: 填意见 + 口令 */
export function signButton(label, meaning, path, after, primary = true) {
  return el("button", { class: "btn" + (primary ? " primary" : ""), onclick: () => {
    const comments = input({ maxlength: 500, value: "同意" });
    const { pw, node } = passwordField();
    editor(label, el("div", {}, field("意见", comments), node,
      el("p", { class: "muted" }, `提交即表示您以口令确认对本版次内容的“${meaning}”签署，记录不可更改。`)),
    async () => {
      await api.post(path, { json: { comments: comments.value.trim() || "同意", password: pw.value } });
      toast(`已签署“${meaning}”`); await after();
    }, { submit: "签署" });
  } }, label);
}

const STATE_TEXT = { SIGNED: "已签署", PENDING: "待签署", NONE: "—", NOT_APPLICABLE: "无此级（旧流程）", RETURNED: "已退回", REJECTED: "已驳回", APPROVED: "已通过" };

/* 版次页的签署栏: 编制、审核、批准各是谁、何时、什么状态 */
export function signoffPanel(signoff) {
  if (!signoff || !signoff.length) return null;
  return panel("签署记录", el("div", { class: "signoff" }, signoff.map((s, i) =>
    el("div", { class: "signoff-cell st-" + s.state },
      el("div", { class: "signoff-step" }, `${i + 1}`),
      el("b", {}, s.meaning),
      el("div", { class: "signoff-name" }, s.signer_name || "—"),
      el("small", { class: "muted" }, s.state === "SIGNED"
        ? [fmtDate(s.signed_at), s.auth_method === "SIMULATION" ? " · 模拟身份，非真人签署" : ""]
        : STATE_TEXT[s.state] || s.state),
      s.comments ? el("small", { class: "muted" }, "意见：" + s.comments) : null,
      s.content_sha256 ? el("small", { class: "mono muted", title: "被签署内容的摘要(版次+变更说明+全部附件)" }, "内容摘要 " + s.content_sha256.slice(0, 12) + "…") : null))));
}

/* ---------------- 有权签署人清单页 ---------------- */
export async function signersPage(ctx, params) {
  const showRevoked = params.get("revoked") === "1";
  const canManage = ctx.can("signer_manage");
  const [rows, types, eligible] = await Promise.all([
    api.get("/signers", { query: { include_revoked: showRevoked ? "true" : "false" } }),
    api.get("/dictionary/file-type"),
    canManage ? api.get("/signers/eligible-users") : [],
  ]);
  const LEVEL = { REVIEW: "审核", APPROVE: "批准" };
  const stateOf = r => r.revoked_at ? "OBSOLETE" : r.in_force ? "ACTIVE" : "SUPERSEDED";
  const stateText = r => r.revoked_at ? "已撤销" : r.in_force ? "有效" : "未生效/已过期";

  let grant = null;
  if (canManage) {
    const user = select(eligible.map(x => ({ value: x.id, label: who(x) })));
    const level = select([{ value: "REVIEW", label: "审核（第 1 级）" }, { value: "APPROVE", label: "批准（第 2 级）" }]);
    const type = select([{ value: "", label: "所有文件类型" }, ...types.map(t => ({ value: t.code, label: `${t.code} ${t.name_cn}` }))]);
    const from = input({ type: "date" }), to = input({ type: "date" });
    const note = input({ maxlength: 500, placeholder: "授权依据，例如任命文号、资质" });
    grant = panel("授权", el("div", {},
      el("div", { class: "inline-form" }, field("人员", user), field("签署级别", level), field("文件类型", type)),
      el("div", { class: "inline-form", style: "margin-top:10px" }, field("起始日（默认今天）", from), field("截止日（空=长期）", to), field("依据/说明", note),
        el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary", onclick: async () => {
          try {
            await api.post("/signers", { json: { user_id: user.value, level: level.value, file_type_code: type.value || null,
              valid_from: from.value || null, valid_to: to.value || null, note: note.value.trim() || null } });
            toast("授权已登记"); reload();
          } catch (e) { toastError(e); } } }, "授权"))),
      el("p", { class: "muted", style: "margin:10px 0 0" }, "不能给自己授权；授权记录只能撤销，不能修改或删除，历史授权永久可查。")));
  }

  return el("div", {},
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, "有权签署人"),
        el("p", { class: "sub" }, "设计文件版次的“审核”和“批准”只能由清单内的人签署：哪个人、哪一级、哪类文件、什么时候到什么时候。")),
      el("div", { class: "page-head-actions" }, link(showRevoked ? "只看未撤销" : "包含已撤销", showRevoked ? "#/signers" : "#/signers?revoked=1", "btn"))),
    grant,
    rows.length ? tablePanel(`授权清单 · ${rows.length} 条`,
      table([{ label: "人员" }, { label: "级别" }, { label: "文件类型" }, { label: "有效期" }, { label: "状态" },
             { label: "授权人" }, { label: "说明" }, { label: "" }], rows, r => [
        el("td", {}, `${r.full_name}（${r.username}）`), el("td", {}, LEVEL[r.level]),
        el("td", {}, r.file_type_code ? `${r.file_type_code} ${r.file_type_name || ""}` : "所有类型"),
        el("td", { class: "nowrap" }, `${r.valid_from} ~ ${r.valid_to || "长期"}`),
        el("td", {}, status(stateOf(r)), r.revoked_at ? el("div", { class: "muted small" }, `${fmtDate(r.revoked_at)} · ${r.revoked_by_name || ""}：${r.revoke_reason || ""}`) : null),
        el("td", { class: "muted" }, r.granted_by_name), el("td", { class: "muted" }, r.note || "—"),
        el("td", { class: "right" }, canManage && !r.revoked_at ? el("button", { class: "btn small danger", onclick: async () => {
          const reason = await askReason("撤销授权", `撤销 ${r.full_name} 的${LEVEL[r.level]}授权`);
          if (!reason) return;
          try { await api.post(`/signers/${r.id}/revoke`, { json: { reason } }); toast("授权已撤销"); reload(); }
          catch (e) { toastError(e); } } }, "撤销") : null)]))
      : empty("清单为空", canManage ? "在上方登记第一位有权审核人和有权批准人后，才能提交设计文件版次审核。" : "尚未登记有权签署人，请联系构型管理员。"));
}
