import { api } from './api.js';
import { el, input, field, select, panel, table, empty, toast } from './ui.js';
import { editor } from './manage.js';
const refresh = () => window.dispatchEvent(new HashChangeEvent('hashchange'));

export function editLine(line, rules) {
  const item = input({ required: true, maxlength: 32, value: line.item_number });
  const quantity = input({ required: true, type: 'number', min: '0.001', step: 'any', value: line.quantity });
  const unit = input({ value: line.unit_code || '' });
  const reference = input({ maxlength: 256, value: line.reference_designator || '' });
  const rule = select([{ value: '', label: 'ALL · 全部构型' }, ...rules.filter(r => r.status === 'ACTIVE').map(r => ({ value: r.rule_code, label: r.rule_code + ' · ' + r.name_cn }))]
    .map(r => ({ ...r, selected: r.value === (line.applicability_rule_code || '') })));
  // Do not silently clear an existing deprecated rule when editing quantity.
  if (line.applicability_rule_code && ![...rule.options].some(o => o.value === line.applicability_rule_code))
    rule.append(el('option', { value: line.applicability_rule_code, selected: true }, line.applicability_rule_code + '（已废止，请重新选择）'));
  editor('编辑 BOM 子项 · ' + line.child_object_code, el('div', {},
    el('div', { class: 'grid2' }, field('项号', item), field('数量', quantity), field('单位', unit), field('位号', reference)), field('适用性规则', rule)), async () => {
    if (!item.value.trim()) throw Error('项号不能为空');
    const json = { item_number: item.value.trim(), quantity: Number(quantity.value), unit_code: unit.value,
      reference_designator: reference.value };
    if (rule.value !== (line.applicability_rule_code || '')) json.applicability_rule_code = rule.value || null;
    await api.patch('/bom/lines/' + line.id, { json }); toast('BOM 子项已保存'); refresh();
  });
}

function conditionRows(context = false) {
  const rows = [];
  const container = el('div');
  const add = () => {
    const key = input({ required: true, placeholder: '例如 model、msn、option', 'aria-label': '属性名' });
    const type = select([{ value: 'text', label: '文本' }, { value: 'number', label: '数字' }, { value: 'boolean', label: '布尔值' }], { 'aria-label': '值类型' });
    const value = input({ required: true, placeholder: '例如 A320-214', 'aria-label': '属性值' });
    const op = select([{ value: 'eq', label: '等于' }, { value: 'ne', label: '不等于' }, { value: 'gte', label: '大于等于' }, { value: 'lte', label: '小于等于' }], { 'aria-label': '比较方式' });
    const row = { key, type, value, op };
    row.node = el('div', { class: 'condition-row' }, key, context ? null : op, type, value,
      el('button', { type: 'button', class: 'btn small', onclick: () => { rows.splice(rows.indexOf(row), 1); row.node.remove(); } }, '移除'));
    rows.push(row); container.append(row.node);
  };
  add();
  return { node: el('div', {}, container, el('button', { type: 'button', class: 'btn small', onclick: add }, '＋ 添加条件')),
    value: () => {
      if (!rows.length) throw Error('请至少填写一个属性条件');
      const entries = rows.map(r => {
        const key = r.key.value.trim(), raw = r.value.value.trim();
        if (!key || !raw) throw Error('请完整填写属性名和值');
        let value = raw;
        if (r.type.value === 'number') { value = Number(raw); if (!Number.isFinite(value)) throw Error('请输入有效数字'); }
        if (r.type.value === 'boolean') { if (!['true', 'false'].includes(raw)) throw Error('布尔值请输入 true 或 false'); value = raw === 'true'; }
        return { field: key, op: r.op.value, value };
      });
      if (context) {
        if (new Set(entries.map(e => e.field)).size !== entries.length) throw Error('构型属性名不能重复');
        return Object.fromEntries(entries.map(e => [e.field, e.value]));
      }
      return { all: entries };
    } };
}

export function createApplicability(context = false) {
  const code = input({ required: true, maxlength: 64 });
  const name = input({ required: true, maxlength: 128 });
  const conditions = conditionRows(context);
  editor(context ? '新建构型' : '新建适用性规则', el('div', {}, field('编号', code), field('名称', name),
    el('p', { class: 'muted' }, context ? '填写该构型的实际属性。' : '所有条件同时满足时，该 BOM 行生效。序列号范围可用两个数字条件。'), conditions.node), async () => {
    if (!code.value.trim() || !name.value.trim()) throw Error('编号和名称不能为空');
    const json = context ? { context_code: code.value.trim(), name_cn: name.value.trim(), attributes: conditions.value() } :
      { rule_code: code.value.trim(), name_cn: name.value.trim(), expression: conditions.value() };
    await api.post(context ? '/configuration/contexts' : '/applicability/rules', { json });
    toast(context ? '构型已创建' : '适用性规则已创建'); refresh();
  });
}

export function rulePanel(rules) {
  return el('div', {}, el('p', { class: 'muted' }, '同一父件号共用 BOM；按行绑定规则决定各构型实际装配哪些零件。'),
    el('div', { class: 'actions' }, el('button', { class: 'btn primary', onclick: () => createApplicability() }, '新建适用性规则'),
      el('button', { class: 'btn', onclick: () => createApplicability(true) }, '新建构型')),
    table([{ label: '规则' }, { label: '名称' }, { label: '条件' }], rules, r => [el('td', {}, r.rule_code), el('td', {}, r.name_cn), el('td', { class: 'mono' }, JSON.stringify(r.expression))]) || empty('尚无规则', '未绑定规则的 BOM 行适用于全部构型。'));
}

export function snapshotTools(code) {
  const a = input({ required: true, placeholder: 'SNAP-000001', 'aria-label': '旧快照编号' });
  const b = input({ required: true, placeholder: 'SNAP-000002', 'aria-label': '新快照编号' });
  const number = input({ required: true, placeholder: 'RSNAP-000001', 'aria-label': '构型快照编号' });
  const out = el('div');
  const show = async (path, query) => {
    try { const r = await api.get(path, { query }); out.replaceChildren(recordView(r)); }
    catch (e) { out.replaceChildren(el('div', { class: 'note error' }, e.message)); }
  };
  return panel('快照查询与比较', el('div', {},
    el('form', { class: 'toolbar', onsubmit: e => { e.preventDefault(); show('/bom/snapshots/compare', { a: a.value.trim(), b: b.value.trim() }); } }, a, b, el('button', { class: 'btn' }, '比较 BOM 快照')),
    el('form', { class: 'toolbar', onsubmit: e => { e.preventDefault(); show('/resolved-bom/' + encodeURIComponent(number.value.trim())); } }, number, el('button', { class: 'btn' }, '查看构型快照')),
    el('button', { class: 'btn', onclick: () => show('/bom/' + encodeURIComponent(code) + '/summary') }, '汇总零件用量'), out));
}

export function recordView(value) {
  if (Array.isArray(value)) {
    const cols = [...new Set(value.flatMap(v => v && typeof v === 'object' ? Object.keys(v) : []))];
    return cols.length ? el('div', { class: 'table-scroll' }, table(cols.map(label => ({ label })), value, row => cols.map(c => el('td', {}, typeof row[c] === 'object' ? JSON.stringify(row[c]) : String(row[c] ?? '—'))))) : empty('暂无记录');
  }
  if (value && typeof value === 'object') return el('div', {}, Object.entries(value).map(([k, v]) => panel(k, typeof v === 'object' ? recordView(v) : el('p', {}, String(v ?? '—')))));
  return el('p', {}, String(value ?? '—'));
}
