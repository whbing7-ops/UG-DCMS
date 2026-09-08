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
  const match = select([{ value:'all', label:'同时满足全部条件（且）' }, { value:'any', label:'满足任一条件（或）' }], { 'aria-label':'条件组合方式' });
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
  return { node: el('div', {}, context ? null : field('条件组合方式', match), container, el('button', { type: 'button', class: 'btn small', onclick: add }, '＋ 添加条件')),
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
      return { [match.value]: entries };
    } };
}

export function createApplicability(context = false) {
  const code = input({ required: true, maxlength: 64 });
  const name = input({ required: true, maxlength: 128 });
  const conditions = conditionRows(context);
  editor(context ? '新建构型' : '新建适用性规则', el('div', {}, field('编号', code), field('名称', name),
    el('p', { class: 'muted' }, context ? '填写该构型的实际属性。' : '选择条件的“且／或”组合。序列号范围可用两个数字条件；A/C 构型共用可选择“或”。'), conditions.node), async () => {
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
  const a = select([{value:'',label:'选择旧快照'}], { required: true, 'aria-label': '旧快照编号' });
  const b = select([{value:'',label:'选择新快照'}], { required: true, 'aria-label': '新快照编号' });
  const number = select([{value:'',label:'选择构型快照'}], { required: true, 'aria-label': '构型快照编号' });
  const out = el('div');
  api.get('/bom/' + encodeURIComponent(code) + '/snapshots').then(r => {
    for (const item of r.bom) for (const sel of [a,b]) sel.append(el('option', {value:item.snapshot_number}, item.snapshot_number + ' · ' + item.line_count + ' 行'));
    for (const item of r.resolved) number.append(el('option', {value:item.resolved_snapshot_number}, item.resolved_snapshot_number + ' · ' + item.line_count + ' 行'));
    if (!r.bom.length && !r.resolved.length) out.replaceChildren(empty('尚未生成快照', '生成 BOM 快照或冻结构型后，可在这里选择查看。'));
  }).catch(e => out.replaceChildren(el('div', {class:'note error'}, e.message)));
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
  const labels = { from:'原值 / 旧快照',to:'新值 / 新快照',added:'新增项',removed:'移除项',changed:'变更项',identical:'内容相同',
    header:'快照信息',lines:'明细',item_number:'项号',child_object_code:'子件号',child_display_name:'名称',quantity:'数量',unit_code:'单位',reference_designator:'位号',
    extended_quantity:'累计用量',line_count:'行数',resolved_snapshot_number:'构型快照编号',snapshot_number:'BOM 快照编号',context_attributes:'构型属性',created_at:'建立时间',
    old_value:'变更前',new_value:'变更后',result:'结果',occupied:'已占用号码',next_sequence:'下一个可用号',allocated_number:'已分配号码',status:'状态',
    file_number:'文件号',filename:'文件名',revision_number:'版次',integrity_status:'完整性状态',occurred_at:'时间',username:'账户',reason:'原因',
    level:'层级',path:'装配路径',notes:'备注',effectivity:'适用性说明',applicability_rule_code:'适用性规则' };
  const title = key => labels[key] || key;
  if (Array.isArray(value)) {
    const cols = [...new Set(value.flatMap(v => v && typeof v === 'object' ? Object.keys(v) : []))];
    return cols.length ? el('div', { class: 'table-scroll' }, table(cols.map(key => ({ label:title(key) })), value, row => cols.map(c => el('td', {}, typeof row[c] === 'object' && row[c] !== null ? el('details',{},el('summary',{},'查看'),recordView(row[c])) : String(row[c] ?? '—'))))) : value.length ? el('ul',{},value.map(v=>el('li',{},String(v)))) : empty('暂无记录');
  }
  if (value && typeof value === 'object') return el('div', {}, Object.entries(value).map(([k, v]) => panel(title(k), typeof v === 'object' ? recordView(v) : el('p', {}, typeof v === 'boolean' ? (v?'是':'否') : String(v ?? '—')))));
  return el('p', {}, String(value ?? '—'));
}
