import { api } from './api.js';
import { el, field, input, select, panel, table, toast, empty } from './ui.js';
import { editor } from './manage.js';
import { recordView } from './bom-tools.js';

export function reasonAction(label, path, after, method = 'post') {
  return el('button', { class: 'btn small danger', onclick: () => {
    const reason = input({ required: true, maxlength: 256 });
    editor(label, field('原因（记入审计）', reason), async () => {
      if (!reason.value.trim()) throw Error('请填写原因');
      await api[method](path, { query: { reason: reason.value.trim() } });
      toast(label + '成功'); await after();
    });
  } }, label);
}

export function definitionButton(code) {
  return el('button', { class: 'btn', onclick: async () => {
    const file = input({ required: true, placeholder: '输入已有设计文件编号' });
    const type = select(['PRIMARY_DEFINITION', 'SUPPORTING_DEFINITION', 'INTERFACE_DEFINITION', 'QUALIFICATION_EVIDENCE'].map((value, i) => ({ value, label: ['主设计定义', '支持性定义', '接口定义', '鉴定证据'][i] })));
    const note = input({ maxlength: 256 });
    editor('关联设计文件 · ' + code, el('div', {}, field('文件编号', file), field('关联角色', type), field('适用性说明', note)), async () => {
      await api.post('/definitions', { json: { object_code: code, file_number: file.value.trim(), relation_type: type.value, applicability_note: note.value || null } });
      toast('设计文件已关联'); window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
  } }, '关联设计文件');
}

export async function diagnostics() {
  const out = el('div');
  const load = async path => {
    try { out.replaceChildren(recordView(await api.get(path))); }
    catch (e) { out.replaceChildren(el('div', { class: 'note error' }, e.message)); }
  };
  return el('div', {}, el('h1', {}, '系统自检'), el('p', { class: 'sub' }, '查看数据库迁移、字典装载及系统约束'),
    el('div', { class: 'actions' }, ...[['数据库迁移', '/system/migrations'], ['字典装载', '/system/dictionary-status'], ['系统约束', '/system/invariants']].map(([label, path]) => el('button', { class: 'btn', onclick: () => load(path) }, label))), out);
}

export async function auditPage(ctx) {
  if (!ctx.can('read_audit')) return empty('无审计查看权限');
  const action = input({ placeholder: '如 USER_CREATE', 'aria-label': '动作' });
  const type = input({ placeholder: '如 APP_USER', 'aria-label': '对象类型' });
  const id = input({ placeholder: '可选对象 ID', 'aria-label': '对象 ID' });
  const out = el('div');
  const load = async e => {
    e?.preventDefault();
    try {
      const rows = await api.get('/admin/audit', { query: { action: action.value.trim(), object_type: type.value.trim(), object_id: id.value.trim(), limit: 1000 } });
      out.replaceChildren(panel(`审计记录 · ${rows.length}（最多 1000 条）`, table(['时间', '账户', '动作', '对象', '原因', '详情'].map(label => ({ label })), rows, r => [
        el('td', {}, new Date(r.occurred_at).toLocaleString()), el('td', {}, r.username), el('td', {}, r.action), el('td', {}, r.object_code || r.object_id), el('td', {}, r.reason || '—'),
        el('td', {}, el('details', {}, el('summary', {}, '查看变更'), recordView({ old_value: r.old_value, new_value: r.new_value, result: r.result }))),
      ]) || empty('暂无匹配记录')));
    } catch (e) { out.replaceChildren(el('div', { class: 'note error' }, e.message)); }
  };
  await load();
  return el('div', {}, el('h1', {}, '审计记录'), el('p', { class: 'sub' }, '按动作或对象筛选；记录不可修改或删除。'),
    el('form', { class: 'toolbar', onsubmit: load }, action, type, id, el('button', { class: 'btn primary' }, '查询')), out);
}
