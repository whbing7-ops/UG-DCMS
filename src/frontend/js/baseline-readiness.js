/* 基线发布前就绪清单: 把校验结果按类别分组, 阻止项在前, 每项给出直达处理位置。 */
import { el, panel, link } from "./ui.js";

const LEVEL = {
  BLOCK: { icon: "✕", label: "阻止发布", order: 0 },
  WARN:  { icon: "!", label: "请确认",   order: 1 },
  PASS:  { icon: "✓", label: "通过",     order: 2 },
};

export function readinessPanel(val) {
  const checks = val && val.checks;
  if (!checks || !checks.length) return null;

  const count = l => checks.filter(c => c.level === l).length;
  const blocked = count("BLOCK"), warned = count("WARN");

  const groups = new Map();
  for (const c of checks) {
    if (!groups.has(c.category)) groups.set(c.category, []);
    groups.get(c.category).push(c);
  }
  const worst = list => Math.min(...list.map(c => LEVEL[c.level].order));
  const ordered = [...groups.entries()].sort((a, b) => worst(a[1]) - worst(b[1]));

  const summary = el("div", { class: "ready-summary " + (blocked ? "is-block" : warned ? "is-warn" : "is-pass") },
    el("b", {}, blocked ? `还有 ${blocked} 项问题阻止发布`
      : warned ? "可以提交，但有需要确认的事项" : "全部检查通过，可以提交审批"),
    el("span", {}, `阻止 ${blocked} · 请确认 ${warned} · 通过 ${count("PASS")}`));

  const body = el("div", {}, summary, ordered.map(([cat, list]) =>
    el("section", { class: "ready-group" },
      el("h4", {}, cat),
      [...list].sort((a, b) => LEVEL[a.level].order - LEVEL[b.level].order).map(c =>
        el("div", { class: "ready-row lv-" + c.level },
          el("span", { class: "ready-ico", title: LEVEL[c.level].label, "aria-label": LEVEL[c.level].label }, LEVEL[c.level].icon),
          el("div", { class: "ready-text" }, el("div", {}, c.title),
            c.detail ? el("small", { class: "muted" }, c.detail) : null),
          c.href ? link(c.level === "PASS" ? "查看" : "去处理", c.href, "btn small") : null)))));
  return panel("发布前检查", body);
}
