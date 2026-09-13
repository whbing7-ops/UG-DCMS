/* 应用入口: 会话、导航、哈希路由。
   不引入前端框架, 也不用打包工具 —— 部署环境是内网工作站, 很可能不通外网,
   任何 CDN 依赖都会让页面在现场打不开。原生 ES 模块由浏览器直接加载。 */
import { api, setToken, token } from "./api.js";
import { el, clear, toast, toastError } from "./ui.js";
import * as V1 from "./views.js";
import * as V2 from "./views2.js";
import * as V3 from "./views3.js";
import * as V4 from "./views4.js";
import * as V5 from "./views5.js";
import { accounts, bomHub, roles } from "./manage.js";
import { diagnostics, auditPage, backupPage } from "./extras.js";

const root = document.getElementById("root");
let ctx = null;
let renderVersion = 0;

/* 路由表。顺序即匹配顺序, 静态路径在前。 */
const ROUTES = [
  ["/",                    V1.home],
  ["/search",              V1.search],
  ["/families",            V2.families],
  ["/family-new",          V2.familyNew],
  ["/files",               V3.files],
  ["/quality",             V4.quality],
  ["/reports",             V4.reports],
  ["/approvals",           V5.approvals],
  ["/external-parts",      V5.externalParts],
  ["/software",            V5.software],
  ["/admin",               accounts],
  ["/bom", bomHub],
  ["/system-check", diagnostics],
  ["/audit",               auditPage],
  ["/backup",              backupPage],
  ["/dictionary",          V4.dictionary],
  ["/object/:code",        V1.objectDetail],
  ["/family/:id",          V2.familyDetail],
  ["/dash-new/:id",        V2.dashNew],
  ["/bom/:code",           V3.bom],
  ["/where-used/:code",    V3.whereUsed],
  ["/file/:num",           V3.fileDetail],
  ["/revision/:id",        V3.revisionDetail],
  ["/baselines/:pn",       V4.baselines],
  ["/baseline/:id",        V4.baselineDetail],
  ["/baseline-compare/:a/:b", V4.baselineCompare],
  ["/approval/:id",        V5.approvalDetail],
  ["/external/:code",      V5.externalDetail],
  ["/software/:num",       V5.softwareDetail],
];

const NAV = [
  { group: "设计数据", items: [
    ["#/", "概览", "⌂"], ["#/search", "查找", "⌕"], ["#/bom", "BOM 管理", "≡"], ["#/families", "设计族", "◫"], ["#/files", "设计文件", "▤"],
    ["#/external-parts", "外部件", "◇"], ["#/software", "软件对象", "⬡"],
  ]},
  { group: "流程", items: [
    ["#/approvals", "审批", "✓"],
  ]},
  { group: "质量与统计", items: [
    ["#/quality", "数据质量", "◉"], ["#/reports", "统计", "▥"],
  ]},
  { group: "系统", items: [
    ["#/system-check", "系统自检", "⌁"], ["#/dictionary", "受控字典", "▦"], ["#/audit", "审计记录", "◷"], ["#/backup", "备份恢复", "↻"], ["#/admin", "系统管理", "⚙"],
  ]},
];

function match(path) {
  for (const [pattern, view] of ROUTES) {
    const p = pattern.split("/"), a = path.split("/");
    if (p.length !== a.length) continue;
    const args = [];
    let ok = true;
    for (let i = 0; i < p.length; i++) {
      if (p[i].startsWith(":")) args.push(decodeURIComponent(a[i]));
      else if (p[i] !== a[i]) { ok = false; break; }
    }
    if (ok) return { view, args };
  }
  return null;
}

/* ---------------- 登录 ---------------- */
function loginView(msg) {
  const u = el("input", { autofocus: true, autocomplete: "username", "aria-label": "账户" });
  const p = el("input", { type: "password", autocomplete: "current-password", "aria-label": "当前口令" });
  const err = el("div", {});
  const submit = async () => {
    err.replaceChildren();
    try {
      const r = await api.login(u.value.trim(), p.value);
      setToken(r.access_token);
      if (!(await boot())) return;
      if (r.user.must_change_password) { location.hash = "#/change-password"; }
      else { location.hash = "#/"; }
      await render();
    } catch (e) {
      err.replaceChildren(el("div", { class: "note error" }, e.message,
        e.rule ? el("div", { class: "mono muted" }, "规则 " + e.rule) : null));
    }
  };
  [u, p].forEach(i => i.addEventListener("keydown", e => { if (e.key === "Enter") submit(); }));

  clear(root).append(el("div", { class: "login-wrap" },
    el("div", { class: "login" },
      el("div", { class: "head" },
        el("h1", {}, "UG-DCMS"),
        el("p", {}, "设计构型管理系统")),
      el("div", { class: "body" },
        msg ? el("div", { class: "note" }, msg) : null,
        el("label", {}, "账户"), u,
        el("label", {}, "口令"), p,
        err,
        el("div", { class: "actions" },
          el("button", { class: "btn primary", onclick: submit }, "登录"))))));
}

/* 首次登录强制改密。系统在这一步之前不放行任何业务操作。 */
function changePasswordView() {
  const oldP = el("input", { type: "password", autocomplete: "current-password", "aria-label": "当前口令" });
  const newP = el("input", { type: "password", autocomplete: "new-password", "aria-label": "新口令" });
  const newP2 = el("input", { type: "password", autocomplete: "new-password", "aria-label": "再次输入新口令" });
  const err = el("div", {});
  const submit = async () => {
    err.replaceChildren();
    if (newP.value !== newP2.value)
      return err.replaceChildren(el("div", { class: "note error" }, "两次输入的新口令不一致。"));
    try {
      await api.changePassword(oldP.value, newP.value);
      toast("口令已修改，其他会话已失效");
      if (!(await boot())) return;
      location.hash = "#/";
      await render();
    } catch (e) {
      err.replaceChildren(el("div", { class: "note error" }, e.message));
    }
  };
  clear(root).append(el("div", { class: "login-wrap" },
    el("div", { class: "login" },
      el("div", { class: "head" },
        el("h1", {}, ctx?.user.must_change_password ? "先修改初始口令" : "修改密码"),
        el("p", {}, "口令至少 10 位，不得包含账户名")),
      el("div", { class: "body" },
        el("div", { class: "note" },
          "口令至少 10 位，需包含大小写字母、数字、符号中的三类，且不能含账户名。"),
        el("label", {}, "当前口令"), oldP,
        el("label", {}, "新口令"), newP,
        el("label", {}, "再次输入新口令"), newP2,
        err,
        el("div", { class: "actions" },
          !ctx?.user.must_change_password ? el('a', {class:'btn',href:'#/'},'取消') : null,
          el("button", { class: "btn primary", onclick: submit }, "修改口令"))))));
}

/* ---------------- 主框架 ---------------- */
function shell(content) {
  const path = (location.hash.slice(1).split("?")[0]) || "/";
  const rail = el("aside", { class: "rail" },
    el("div", { class: "brand" }, "UG-DCMS", el("small", {}, "设计构型管理 · rc2.36")),
    el("nav", { class: "nav" }, NAV.map(g => [
      el("h4", {}, g.group),
      g.items.map(([href, label, icon]) =>
        el("a", { href, class: (href.slice(1) === path || (href === "#/bom" && path.startsWith("/bom/"))) ? "on" : null },
          el("span",{class:"nav-icon","aria-hidden":"true"},icon),el("span",{},label))),
    ])),
    el("div", { class: "whoami" },
      el("div", {}, ctx.user.full_name),
      el("div", { class: "muted" }, (ctx.user.roles || []).map(r => roles[r] || r).join("、")),
      el("a", { href: "#/change-password" }, "修改密码"),
      el("a", { href: "#", onclick: async e => {
        e.preventDefault();
        try { await api.logout(); } catch {}
        setToken(null); ctx = null; location.hash = "#/login"; loginView("已退出登录。");
      } }, "退出登录")));
  clear(root).append(el("div", { class: "shell" }, rail, el("main", { class: "main" }, content)));
}

async function render() {
  const version = ++renderVersion;
  if (!token()) return loginView();
  const hash = location.hash.slice(1) || "/";
  if (hash === "/change-password") return changePasswordView();
  if (!ctx) { if (!(await boot())) return; }
  if (ctx.user.must_change_password) return changePasswordView();

  const [path, qs] = hash.split("?");
  const required = { "/admin": "user_manage", "/audit": "read_audit", "/family-new": "draft_write" }[path];
  if (required && !ctx.can(required)) return shell(el("div", {}, el("h1", {}, "无操作权限"), el("p", {}, "请联系系统管理员分配所需角色。")));
  const m = match(path || "/");
  if (!m) return shell(el("div", {},
    el("h1", {}, "页面不存在"),
    el("p", { class: "sub" }, "地址可能已失效。"),
    el("a", { class: "btn", href: "#/" }, "回到概览")));

  const loading = el("p", { class: "muted" }, "读取中…");
  shell(loading);
  try {
    const node = await m.view(ctx, new URLSearchParams(qs || ""), ...m.args);
    if (version === renderVersion) shell(node);
  } catch (e) {
    if (version !== renderVersion) return;
    if (e.status === 401) return;
    shell(el("div", {},
      el("h1", {}, "无法打开这个页面"),
      el("div", { class: "note error" }, e.message,
        e.rule ? el("div", { class: "mono muted" }, "规则 " + e.rule) : null),
      el("a", { class: "btn", href: "#/" }, "回到概览")));
  }
}

async function boot() {
  try {
    const me = await api.me();
    ctx = {
      user: me,
      perms: new Set(me.permissions || []),
      can(p) { return this.perms.has(p); },
    };
    return true;
  } catch {
    setToken(null);
    loginView();
    return false;
  }
}

window.addEventListener("hashchange", () => render().catch(e => toastError(e)));
window.addEventListener('dcms:refresh-session', async () => { if (await boot()) await render(); });
render().catch(e => toastError(e));
