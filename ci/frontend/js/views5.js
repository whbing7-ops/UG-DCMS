/* 外部件、软件对象、审批中心。 */
import { api } from "./api.js";
import {
  el, table, tablePanel, panel, empty, status, field, input, select,
  toast, toastError, fmtDate, link, askReason,
} from "./ui.js";

const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

const REQ_CN = {
  BASIC_DRAWING_NUMBER: "新建设计族", DASH_NUMBER: "申请 Dash 号",
  DESIGN_FILE_NUMBER: "申请文件号", FILE_REVISION_RELEASE: "发布文件版次",
  BASELINE_RELEASE: "发布设计基线", EXTERNAL_TS_ACCEPT: "接受外部件技术状态",
  NUMBER_CANCELLATION: "作废号码", DICTIONARY_CHANGE: "变更受控字典",
  CHANGE_PACKAGE: "变更包", FAMILY_CLOSE: "关闭设计族", OBJECT_OBSOLETE: "废止对象",
};

/* ==================== 审批中心 ==================== */
export async function approvals(ctx) {
  const [inbox, mine] = await Promise.all([
    api.get("/approvals/inbox"),
    api.get("/approvals/mine", { query: { include_closed: true } }),
  ]);

  // 动作路径写成显式常量而非拼接: 前后端契约测试会扫描前端源码里的 API 路径,
  // 拼接出来的路径它核验不了 —— 而这类路径打错要到运行时点那个按钮才发现。
  const ACTIONS = {
    return:   (id, q) => api.post(`/approvals/${id}/return`, { query: q }),
    reject:   (id, q) => api.post(`/approvals/${id}/reject`, { query: q }),
    withdraw: (id, q) => api.post(`/approvals/${id}/withdraw`, { query: q }),
  };
  const act = async (id, kind, title, hint) => {
    const reason = await askReason(title, hint);
    if (!reason) return;
    try {
      const r = await ACTIONS[kind](id, { reason });
      toast(`已${title}：${r.request_number}`);
      reload();
    } catch (e) { toastError(e); }
  };

  return el("div", {},
    el("h1", {}, "审批"),
    el("p", { class: "sub" },
      "待办中不含你自己发起的申请——同一个人不能既申请又批准。"),

    inbox.length ? tablePanel(`待我处理 ${inbox.length} 项`,
      table([{ label: "申请单", mono: 1 }, { label: "类型" }, { label: "对象", mono: 1 },
             { label: "申请人" }, { label: "提交时间" }, { label: "" }],
        inbox, r => [
          el("td", { class: "mono" }, link(r.request_number, "#/approval/" + r.id)),
          el("td", {}, REQ_CN[r.request_type] || r.request_type),
          el("td", { class: "mono" }, r.object_code || "—"),
          el("td", {}, r.requester_name),
          el("td", { class: "muted nowrap" }, fmtDate(r.requested_at)),
          el("td", { class: "right nowrap" },
            el("button", { class: "btn small", onclick: () =>
              act(r.id, "return", "退回补充", "需要补充什么？申请人改完可再次提交") }, "退回"),
            " ",
            el("button", { class: "btn small danger", onclick: () =>
              act(r.id, "reject", "拒绝", "为什么这件事不该做？拒绝后需另起新申请") }, "拒绝"))]))
      : empty("没有待你处理的审批"),

    tablePanel("我发起的申请",
      table([{ label: "申请单", mono: 1 }, { label: "类型" }, { label: "对象", mono: 1 },
             { label: "状态" }, { label: "提交时间" }, { label: "" }],
        mine, r => [
          el("td", { class: "mono" }, link(r.request_number, "#/approval/" + r.id)),
          el("td", {}, REQ_CN[r.request_type] || r.request_type),
          el("td", { class: "mono" }, r.object_code || "—"),
          el("td", {}, status(r.status)),
          el("td", { class: "muted nowrap" }, fmtDate(r.requested_at)),
          el("td", { class: "right" }, r.status === "PENDING"
            ? el("button", { class: "btn small", onclick: () =>
                act(r.id, "withdraw", "撤回", "为什么撤回？") }, "撤回") : null)])
      || empty("你还没有发起过申请")));
}

export async function approvalDetail(ctx, params, id) {
  const r = await api.get("/approvals/" + id);
  const cell = (l, v, mono) => el("div", { class: "tb-cell" },
    el("b", {}, l), el("span", { class: mono ? "mono" : null }, v ?? "—"));
  return el("div", {},
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" },
        el("span", { class: "tb-code" }, r.request_number),
        el("span", { class: "tb-name" }, r.title)),
      el("div", { class: "tb-grid" },
        cell("类型", REQ_CN[r.request_type] || r.request_type),
        cell("状态", r.status),
        cell("对象", r.object_code, true),
        cell("申请人", r.requester_name),
        cell("提交时间", fmtDate(r.requested_at)),
        cell("结束时间", fmtDate(r.closed_at)))),
    tablePanel("审批步骤",
      table([{ label: "步骤" }, { label: "名称" }, { label: "要求角色" },
             { label: "最终步骤" }, { label: "结论" }, { label: "处理人" },
             { label: "意见" }, { label: "时间" }],
        r.steps, s => [
          el("td", { class: "num" }, s.step_order),
          el("td", {}, s.step_name),
          el("td", { class: "muted" }, s.required_role_code || "—"),
          el("td", {}, s.is_final ? "是" : ""),
          el("td", {}, status(s.decision)),
          el("td", { class: "mono" }, s.decided_by_username || "—"),
          el("td", {}, s.comments || "—"),
          el("td", { class: "muted nowrap" }, fmtDate(s.acted_at))])),
    el("div", { class: "actions" }, link("返回审批中心", "#/approvals", "btn")));
}

/* ==================== 外部件 ==================== */
export async function externalParts(ctx) {
  const [rows, namespaces, manufacturers] = await Promise.all([
    api.get("/external-parts"),
    api.get("/dictionary/namespace"),
    api.get("/dictionary/manufacturer"),
  ]);
  const nsSel = select(namespaces.map(n => ({ value: n.code, label: `${n.code} ${n.name_cn}` })));
  const pnIn = input({ class: "mono", placeholder: "43025-0400" });
  const nameIn = input({ placeholder: "Micro-Fit 连接器壳体" });
  const manufacturerSel = select([{ value: "", label: "未指定" }, ...manufacturers.map(m => ({ value: m.code, label: m.code + " · " + m.name_cn }))]);

  return el("div", {},
    el("h1", {}, "外部件"),
    el("p", { class: "sub" },
      "同一件号在不同来源下是不同对象。供应商改版不改件号——改版登记为新的技术状态。"),
    ctx.can("draft_write") ? panel("登记外部件", el("div", {},
      el("div", { class: "inline-form" },
        field("来源", nsSel), field("外部件号", pnIn), field("名称", nameIn),
        field("制造商", manufacturerSel),
        el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary",
          onclick: async () => {
            try { const r = await api.post("/external-parts", { json: {
                    namespace_code: nsSel.value, external_part_number: pnIn.value,
                    name_cn: nameIn.value, manufacturer_code: manufacturerSel.value || null } });
                  toast("已登记：" + r.object_code);
                  location.hash = "#/external/" + encodeURIComponent(r.object_code); }
            catch (e) { toastError(e); } } }, "登记"))))) : null,
    rows.length ? tablePanel("全部外部件",
      table([{ label: "对象编码", mono: 1 }, { label: "外部件号", mono: 1 },
             { label: "名称" }, { label: "来源" }, { label: "技术状态数" },
             { label: "已接受版" }, { label: "状态" }],
        rows, r => [
          el("td", { class: "mono" }, link(r.object_code, "#/external/" + encodeURIComponent(r.object_code))),
          el("td", { class: "mono" }, r.external_part_number),
          el("td", {}, r.name_cn),
          el("td", { class: "muted" }, r.namespace_code),
          el("td", { class: "num" }, r.state_count),
          el("td", { class: "num" }, r.accepted_state ?? "—"),
          el("td", {}, status(r.lifecycle_status))]))
      : empty("还没有登记外部件"));
}

export async function externalDetail(ctx, params, code) {
  const ep = await api.get("/external-parts/" + encodeURIComponent(code));
  const revIn = input({ class: "mono", placeholder: "供应商版本，如 C" });
  const docIn = input({ placeholder: "供应商文件号" });
  const dateIn = input({ type: "date" });

  const cell = (l, v, mono) => el("div", { class: "tb-cell" },
    el("b", {}, l), el("span", { class: mono ? "mono" : null }, v ?? "—"));

  return el("div", {},
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" },
        el("span", { class: "tb-code" }, ep.object_code),
        el("span", { class: "tb-name" }, ep.name_cn)),
      el("div", { class: "tb-grid" },
        cell("来源", `${ep.namespace_code} ${ep.namespace_name}`),
        cell("外部件号", ep.external_part_number, true),
        cell("制造商", ep.manufacturer_name),
        cell("状态", ep.object_status))),
    el("div", { class: "note" },
      "只有已接受的技术状态才能进入设计基线。收到供应商文件不等于认可它——中间需要一次明确确认。"),
    ctx.can("draft_write") ? panel("登记新技术状态", el("div", { class: "inline-form" },
      field("供应商版本", revIn), field("供应商文件号", docIn), field("文件日期", dateIn),
      el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary",
        onclick: async () => {
          try { await api.post(`/external-parts/${encodeURIComponent(code)}/states`, {
                  json: { supplier_revision: revIn.value,
                          supplier_document: docIn.value || null,
                          supplier_document_date: dateIn.value || null } });
                toast("已登记，状态为草稿，需经接受后方可用于基线"); reload(); }
          catch (e) { toastError(e); } } }, "登记")))) : null,
    tablePanel("技术状态",
      table([{ label: "序号" }, { label: "供应商版本", mono: 1 }, { label: "供应商文件" },
             { label: "文件日期" }, { label: "状态" }, { label: "接受人" },
             { label: "基线引用" }, { label: "" }],
        ep.technical_states, s => [
          el("td", { class: "num" }, s.state_sequence),
          el("td", { class: "mono" }, s.supplier_revision || "—"),
          el("td", {}, s.supplier_document || "—"),
          el("td", { class: "muted" }, s.supplier_document_date || "—"),
          el("td", {}, status(s.status)),
          el("td", { class: "mono" }, s.accepted_by_username || "—"),
          el("td", { class: "num" }, s.baseline_refs),
          el("td", { class: "right nowrap" }, s.status === "DRAFT" && ctx.can("approve")
            ? [el("button", { class: "btn small primary", onclick: async () => {
                  const c = await askReason("接受技术状态", "确认这一版供应商规格满足我方设计要求");
                  if (c === null) return;
                  try { await api.post(`/external-states/${s.id}/accept`, { query: { comments: c } });
                        toast("已接受，可用于基线"); reload(); }
                  catch (e) { toastError(e); } } }, "接受"),
               " ",
               el("button", { class: "btn small danger", onclick: async () => {
                  const r = await askReason("拒绝技术状态", "为什么不接受这一版？");
                  if (!r) return;
                  try { await api.post(`/external-states/${s.id}/reject`, { query: { reason: r } });
                        toast("已拒绝"); reload(); }
                  catch (e) { toastError(e); } } }, "拒绝")]
            : null)])
      || empty("还没有技术状态", "登记供应商给出的规格版本。")));
}

/* ==================== 软件对象 ==================== */
export async function software(ctx) {
  const rows = await api.get("/software");
  const numIn = input({ class: "mono", placeholder: "UG-SW0001" });
  const nameIn = input({ placeholder: "照明控制固件" });
  const typeSel = select([
    { value: "SOFTWARE", label: "软件" }, { value: "FIRMWARE", label: "固件" },
    { value: "CONFIG_DATA", label: "配置数据" }, { value: "LOADABLE", label: "可加载件" },
  ]);
  return el("div", {},
    el("h1", {}, "软件对象"),
    el("p", { class: "sub" }, "软件编号是身份，版本是技术状态。改版不改编号。"),
    ctx.can("draft_write") ? panel("登记软件对象", el("div", { class: "inline-form" },
      field("软件编号", numIn), field("名称", nameIn), field("类型", typeSel),
      el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary",
        onclick: async () => {
          try { await api.post("/software", { json: { software_number: numIn.value,
                  name_cn: nameIn.value, software_type: typeSel.value } });
                toast("已登记"); reload(); }
          catch (e) { toastError(e); } } }, "登记")))) : null,
    rows.length ? tablePanel("全部软件对象",
      table([{ label: "软件编号", mono: 1 }, { label: "名称" }, { label: "类型" },
             { label: "当前版本", mono: 1 }, { label: "版本数" }, { label: "状态" }],
        rows, r => [
          el("td", { class: "mono" }, link(r.software_number, "#/software/" + encodeURIComponent(r.software_number))),
          el("td", {}, r.name_cn),
          el("td", { class: "muted" }, r.software_type),
          el("td", { class: "mono" }, r.current_version || "—"),
          el("td", { class: "num" }, r.version_count),
          el("td", {}, status(r.lifecycle_status))]))
      : empty("还没有软件对象"));
}

export async function softwareDetail(ctx, params, num) {
  const so = await api.get("/software/" + encodeURIComponent(num));
  const vIn = input({ class: "mono", placeholder: "1.0.0" });
  const bIn = input({ class: "mono", placeholder: "build 号，可空" });
  const hIn = input({ class: "mono", placeholder: "软件包 SHA-256（64 位十六进制）" });
  const cell = (l, v, mono) => el("div", { class: "tb-cell" },
    el("b", {}, l), el("span", { class: mono ? "mono" : null }, v ?? "—"));

  return el("div", {},
    el("div", { class: "titleblock" },
      el("div", { class: "tb-head" },
        el("span", { class: "tb-code" }, so.software_number),
        el("span", { class: "tb-name" }, so.name_cn)),
      el("div", { class: "tb-grid" },
        cell("类型", so.software_type),
        cell("状态", so.lifecycle_status),
        cell("版本数", so.versions.length),
        cell("建立时间", fmtDate(so.created_at)))),
    el("div", { class: "note" },
      "发布前必须登记软件包的 SHA-256，否则日后无法核对交付物是否被替换。"),
    ctx.can("draft_write") ? panel("登记新版本", el("div", { class: "inline-form" },
      field("版本号", vIn), field("Build", bIn), field("SHA-256", hIn),
      el("div", { style: "flex:0 0 auto" }, el("button", { class: "btn primary",
        onclick: async () => {
          try { await api.post(`/software/${encodeURIComponent(num)}/versions`, {
                  json: { version: vIn.value, build: bIn.value || "",
                          hash_sha256: hIn.value || null } });
                toast("已登记"); reload(); }
          catch (e) { toastError(e); } } }, "登记")))) : null,
    tablePanel("版本",
      table([{ label: "版本", mono: 1 }, { label: "Build", mono: 1 },
             { label: "SHA-256", mono: 1 }, { label: "状态" },
             { label: "发布时间" }, { label: "基线引用" }, { label: "" }],
        so.versions, v => [
          el("td", { class: "mono" }, v.version),
          el("td", { class: "mono muted" }, v.build || "—"),
          el("td", { class: "mono muted" }, v.hash_sha256 ? v.hash_sha256.slice(0, 16) + "…" : "未登记"),
          el("td", {}, status(v.status)),
          el("td", { class: "muted nowrap" }, fmtDate(v.released_at)),
          el("td", { class: "num" }, v.baseline_refs),
          el("td", { class: "right" }, v.status === "DRAFT" && ctx.can("approve")
            ? el("button", { class: "btn small primary", onclick: async () => {
                try { await api.post(`/software-versions/${v.id}/release`,
                        { query: { comments: "同意发布" } });
                      toast("已发布"); reload(); }
                catch (e) { toastError(e); } } }, "发布") : null)])
      || empty("还没有版本")));
}
