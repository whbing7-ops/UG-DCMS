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

export function status(v) {
  return v ? el("span", { class: "st st-" + v }, v) : el("span", { class: "muted" }, "—");
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
