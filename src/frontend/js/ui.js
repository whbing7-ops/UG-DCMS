/* DOM 与呈现辅助。刻意用原生 DOM 而非模板字符串拼 innerHTML:
   件号、名称、备注都是用户输入, 拼字符串迟早会把 < 当标签解析。
   el() 走 textContent, 从源头上不存在这个问题。 */

export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2).toLowerCase(), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  const appendKid = (k) => {
    if (Array.isArray(k)) { k.forEach(appendKid); return; }
    if (k === null || k === undefined || k === false) return;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  };
  kids.forEach(appendKid);
  return n;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

export function toast(msg, kind = "ok", detail) {
  const box = document.getElementById("toast");
  const t = el("div", { class: "toast" + (kind === "error" ? " error" : "") }, msg,
                detail ? el("small", {}, detail) : null);
  box.append(t);
  setTimeout(() => t.remove(), kind === "error" ? 8000 : 3500);
}

/** 错误提示带上不变量编号 —— 使用者需要知道是哪条规则拦下的, 才能判断该怎么办。 */
export function toastError(e) {
  toast(e.message || "操作失败", "error", e.rule ? `规则 ${e.rule}` : null);
}

const STATUS_CN = Object.freeze({
  ACTIVE:'有效', INACTIVE:'停用', DEPRECATED:'已废止', HISTORICAL:'历史',
  DRAFT:'草稿', WORKING:'编制中', PENDING:'待处理', IN_REVIEW:'审核中',
  APPROVED:'已批准', REJECTED:'已拒绝', RELEASED:'已发布',
  SUPERSEDED:'已被取代', OBSOLETE:'已作废', CANCELLED:'已取消', SUSPENDED:'已暂停',
  RESERVED:'已预留', ALLOCATED:'已分配', ACCEPTED:'已接受',
  OPEN:'待处置', CLOSED:'已关闭', WAIVED:'已豁免',
  OK:'正常', WARNING:'警告', ERROR:'错误', MISSING:'缺失', INFO:'信息',
  UNKNOWN:'未校验', MISMATCH:'不一致', CURRENT:'当前', PASS:'通过', FAIL:'失败',
  FUNCTIONAL:'功能基线', ALLOCATED_BASELINE:'分配基线', DESIGN:'设计基线',
  PRODUCT:'产品基线', AS_BUILT:'实造基线',
  INTERNAL_PART:'内部件', EXTERNAL_PART:'外部件', SOFTWARE:'软件对象',
  BASIC_DRAWING_FAMILY:'设计族', DESIGN_FILE:'设计文件', REVISION_ATTACHMENT:'附件',
  PRIMARY:'主功能', AUXILIARY:'辅助功能',
  PRIMARY_NATIVE:'主源文件', RELEASED_PDF:'发布版PDF', DERIVED_STEP:'派生STEP文件',
  DERIVED_DXF:'派生DXF文件', REFERENCE:'参考附件',
  PRIMARY_DEFINITION:'主设计定义', SUPPORTING_DEFINITION:'支持性定义',
  INTERFACE_DEFINITION:'接口定义', QUALIFICATION_EVIDENCE:'鉴定证据',
  FILE_REVISION:'文件版次', BOM_SNAPSHOT:'BOM快照',
  EXTERNAL_TECHNICAL_STATE:'外部件技术状态', SOFTWARE_VERSION:'软件版本',
  OEM_PN:'原制造商件号', CUSTOMER_PN:'客户件号', LEGACY_PN:'历史件号',
  SUPPLIER_PN:'供应商件号', ALTERNATE_IDENTIFIER:'替代标识',
  NATIVE:'系统内建立', LEGACY:'历史迁移',
  L0:'待整理', L1:'已识别', L2:'已校验', L3:'已批准', L4:'已纳入基线',
  MANUAL:'手动备份', SCHEDULED:'定时备份', PRE_RESTORE:'恢复前备份',
  FIRMWARE:'固件', CONFIG_DATA:'配置数据', LOADABLE:'可加载软件',
  DWG:'零件图／装配图', PSCD:'产品规范与构型定义', SPEC:'技术规范',
  WD:'电气原理图', BOMDOC:'BOM文件', ICD:'接口控制文件',
  QTP:'试验大纲', QTR:'试验报告', ANLS:'分析报告', SWRD:'软件版本说明', REF:'参考资料'
});

export function statusText(v) { return v ? (STATUS_CN[v] || v) : '—'; }
export function codeText(v) {
  if (v === null || v === undefined || v === '') return '—';
  const raw=String(v);
  if (STATUS_CN[raw]) return STATUS_CN[raw];
  return raw.replace(/[A-Z][A-Z0-9_]{2,}/g, token => STATUS_CN[token] || token);
}

export function status(v) {
  return v ? el("span", { class: "st st-" + v, title: v }, statusText(v)) : el("span", { class: "muted" }, "—");
}

export function table(cols, rows, render) {
  if (!rows || !rows.length) return null;
  return el("table", {},
    el("thead", {}, el("tr", {}, cols.map(c =>
      el("th", { class: c.mono ? "mono" : null }, c.label)))),
    el("tbody", {}, rows.map((r, i) => el("tr", {}, render(r, i)))));
}

export function empty(title, hint) {
  return el("div", { class: "empty" }, el("b", {}, title), hint || "");
}

export function panel(title, bodyNodes, headActions) {
  return el("section", { class: "panel" },
    el("header", {}, el("h3", {}, title), headActions || null),
    el("div", { class: "body" }, bodyNodes));
}

/** 表格类面板不加内边距, 让表头与面板边框对齐 —— 明细表在图纸上就是贴边的。 */
export function tablePanel(title, node, headActions) {
  return el("section", { class: "panel" },
    el("header", {}, el("h3", {}, title), headActions || null),
    el("div", { class: "table-scroll" }, node || empty("暂无数据")));
}

let fieldId = 0;
export function field(label, control) {
  if (!control.id) control.id = "field-" + (++fieldId);
  return el("div", {}, el("label", { for: control.id }, label), control);
}

export function input(attrs = {}) { return el("input", attrs); }

export function select(options, attrs = {}) {
  return el("select", attrs, options.map(o =>
    el("option", { value: o.value, selected: o.selected }, o.label)));
}

export function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s);
  return isNaN(d) ? s : d.toLocaleString("zh-CN", { hour12: false });
}

export function link(text, hash, cls) {
  return el("a", { href: hash, class: cls }, text);
}

/** 需要理由的动作一律弹窗要求填写, 不提供跳过路径 ——
    作废号码、豁免质量问题这类操作, 事后没人能凭空回忆当初为什么这么做。 */
export function askReason(title, hint) {
  return new Promise(resolve => {
    const v = window.prompt(`${title}\n${hint || "请填写理由（将记入审计）"}`, "");
    resolve(v && v.trim() ? v.trim() : null);
  });
}
