import { api } from './api.js';
import { el, field, input, select, panel, table, tablePanel, empty, toast, fmtDate, link } from './ui.js';

export const roles = {
  SYSTEM_ADMIN: '系统管理员', DATA_ADMIN: '数据管理员', ENGINEER: '设计工程师',
  APPROVER: '审批员', CONFIGURATION_MANAGER: '构型管理员', VIEWER: '只读用户',
};

// Native dialog supplies focus trapping and Escape support. Errors remain beside the form.
export function editor(title, controls, save, options = {}) {
  const previous = document.activeElement;
  const error = el('div', { role: 'alert' });
  const submit = el('button', { type: 'submit', class: 'btn primary' }, options.submit || '保存');
  const cancel = el('button', { type: 'button', class: 'btn', onclick: () => dialog.close() }, '取消');
  let busy = false;
  const form = el('form', { onsubmit: async e => {
    e.preventDefault();
    if (busy) return;
    busy = true; submit.disabled = true; cancel.disabled = true; error.replaceChildren();
    try { await save(); dialog.close(); }
    catch (e) { error.replaceChildren(el('div', { class: 'note error' }, e.message || '保存失败，请重试')); }
    finally { busy = false; submit.disabled = false; cancel.disabled = false; }
  } }, el('h2', { id: 'editor-title' }, title), controls, error,
  el('div', { class: 'actions dialog-actions' }, cancel, submit));
  const dialog = el('dialog', { class: 'editor', 'aria-labelledby': 'editor-title',
    oncancel: e => { if (busy) e.preventDefault(); },
    onclose: () => { dialog.remove(); if (previous?.isConnected) previous.focus(); },
  }, form);
  document.body.append(dialog); dialog.showModal();
}

export async function accounts(ctx) {
  if (!ctx.can('user_manage')) return empty('无账户管理权限', '请联系系统管理员。');
  const root = el('div');
  let users = [], sessions = [], license = null;
  let term = '', state = '';
  const refresh = async () => {
    [users, sessions, license] = await Promise.all([
      api.get('/admin/users'), ctx.can('session_manage') ? api.get('/admin/sessions') : [],
      ctx.can('session_manage') ? api.get('/admin/license') : null,
    ]);
    render();
  };
  const done = async message => { toast(message); await refresh(); };
  function edit(user) {
    const username = input({ required: true, minlength: 3, maxlength: 64,
      pattern: '[A-Za-z][A-Za-z0-9._-]*', value: user?.username || '', disabled: !!user, autocomplete: 'off' });
    const name = input({ required: true, maxlength: 64, value: user?.full_name || '' });
    const email = input({ type: 'email', maxlength: 128, value: user?.email || '' });
    const password = input({ type: 'password', required: !user, minlength: 10, maxlength: 72, autocomplete: 'new-password' });
    const reason = input({ required: true, maxlength: 256, placeholder: '填写变更原因' });
    const checks = Object.entries(roles).map(([code, label]) => {
      const box = input({ type: 'checkbox', value: code, checked: (user?.roles || ['VIEWER']).includes(code) });
      return { code, box, node: el('label', { class: 'role-option' }, box, label) };
    });
    editor(user ? '编辑账户 · ' + user.username : '新建账户', el('div', {},
      el('div', { class: 'grid2' }, field('账户名（字母开头）', username), field('姓名', name)),
      field('邮箱（可选）', email), user ? null : field('初始密码', password),
      user ? null : el('p', { class: 'muted' }, '至少 10 位，包含大小写字母、数字、符号中的三类，不含账户名；首次登录须改密。'),
      el('fieldset', {}, el('legend', {}, '角色权限（可多选）'), el('div', { class: 'role-grid' }, checks.map(c => c.node))),
      user ? field('变更原因', reason) : null), async () => {
      if (!name.value.trim()) throw Error('请填写姓名');
      const json = { full_name: name.value.trim(), email: email.value.trim(), roles: checks.filter(c => c.box.checked).map(c => c.code) };
      if (!json.roles.length) throw Error('请至少选择一个角色');
      if (user) await api.patch('/admin/users/' + user.id, { json: { ...json, reason: reason.value.trim() } });
      else await api.post('/admin/users', { json: { ...json, username: username.value.trim(), password: password.value } });
      await done(user ? '账户已更新' : '账户已创建，首次登录须修改密码');
      if (user?.username === ctx.user.username) window.dispatchEvent(new Event('dcms:refresh-session'));
    });
  }
  function action(user, kind) {
    const reason = input({ required: true, maxlength: 128 });
    const password = input({ type: 'password', required: true, minlength: 10, maxlength: 72, autocomplete: 'new-password' });
    const confirm = input({ type: 'password', required: true, autocomplete: 'new-password' });
    const title = kind === 'reset' ? '重置密码' : kind === 'unlock' ? '解除登录锁定' : user.is_active ? '停用账户' : '启用账户';
    editor(title + ' · ' + user.username, el('div', {},
      el('p', { class: 'note' }, kind === 'reset' ? '该账户全部会话将失效，下次登录必须修改密码。' :
        kind === 'unlock' ? '解除密码输错导致的登录锁定，不改变账户启用状态。' : user.is_active ? '停用后立即撤销全部会话，历史记录保留。' : '启用后可再次登录，原角色保留。'),
      kind === 'reset' ? [field('新临时密码', password), field('再次输入密码', confirm)] : null,
      kind !== 'unlock' ? field('操作原因', reason) : null), async () => {
      if (kind !== 'unlock' && !reason.value.trim()) throw Error('请填写操作原因');
      if (kind === 'reset') {
        if (password.value !== confirm.value) throw Error('两次密码不一致');
        await api.post('/admin/users/' + user.id + '/password-reset', { json: { new_password: password.value, reason: reason.value.trim() } });
      } else if (kind === 'unlock') await api.post('/admin/users/' + user.id + '/unlock');
      else await api.patch('/admin/users/' + user.id, { json: { is_active: !user.is_active, reason: reason.value.trim() } });
      await done(title + '成功');
    }, { submit: '确认' + title });
  }
  function render() {
    const search = input({ value: term, placeholder: '搜索账户、姓名或角色', 'aria-label': '搜索账户' });
    const filter = select([{ value: '', label: '全部状态' }, { value: 'active', label: '已启用' }, { value: 'disabled', label: '已停用' }].map(x => ({ ...x, selected: x.value === state })), { 'aria-label': '账户状态' });
    const results = el('div');
    const draw = () => {
      const rows = users.filter(u => (!state || u.is_active === (state === 'active')) &&
        [u.username, u.full_name, ...(u.roles || []).map(r => roles[r] || r)].join(' ').toLowerCase().includes(term.toLowerCase()));
      results.replaceChildren(tablePanel(`账户 · ${rows.length}`, table(
        ['账户 / 姓名', '角色', '状态', '最近登录', '操作'].map(label => ({ label })), rows, u => [
          el('td', {}, el('b', { class: 'mono' }, u.username), el('div', { class: 'muted' }, u.full_name)),
          el('td', {}, el('div', { class: 'badges' }, (u.roles || []).map(r => el('span', { class: 'badge' }, roles[r] || r)))),
          el('td', {}, el('span', { class: 'st ' + (u.is_active ? 'st-ACTIVE' : 'st-CANCELLED') }, u.is_active ? '已启用' : '已停用'), u.is_online ? el('div', { class: 'st-current' }, '在线') : null),
          el('td', {}, fmtDate(u.last_login_at)),
          el('td', {}, el('div', { class: 'row-actions' },
            el('button', { class: 'btn small', onclick: () => edit(u) }, '编辑'),
            el('button', { class: 'btn small', onclick: () => action(u, 'reset') }, '重置密码'),
            el('button', { class: 'btn small', onclick: () => action(u, 'unlock') }, '解锁'),
            el('button', { class: 'btn small' + (u.is_active ? ' danger' : ''), onclick: () => action(u, 'toggle') }, u.is_active ? '停用' : '启用'))),
        ]) || empty('没有匹配的账户', '调整关键词或状态筛选。')));
    };
    search.addEventListener('input', () => { term = search.value; draw(); });
    filter.addEventListener('change', () => { state = filter.value; draw(); });
    root.replaceChildren(el('div', { class: 'page-heading' }, el('div', {}, el('h1', {}, '系统管理'), el('p', { class: 'sub' }, '账户、权限与在线会话')),
      el('button', { class: 'btn primary', onclick: () => edit(null) }, '＋ 新建账户')),
      el('div', { class: 'grid4' }, ...[[users.length, '账户总数'], [users.filter(u => u.is_active).length, '启用账户'],
        [license ? `${license.active_accounts} / ${license.limit}` : '—', '同时在线账户']].map(([n, s]) => el('div', { class: 'stat' }, el('b', {}, n), el('span', {}, s)))),
      el('div', { class: 'toolbar' }, search, filter), results,
      ctx.can('session_manage') ? tablePanel('在线会话', table(['账户', '登录时间', '最近活动', '操作'].map(label => ({ label })), sessions, s => [
        el('td', {}, s.username), el('td', {}, fmtDate(s.issued_at)), el('td', {}, fmtDate(s.last_seen_at)),
        el('td', {}, el('button', { class: 'btn small danger', onclick: () => {
          const reason = input({ required: true, maxlength: 128 });
          editor('强制下线 · ' + s.username, field('操作原因', reason), async () => {
            if (!reason.value.trim()) throw Error('请填写原因');
            await api.del('/admin/sessions/' + s.id, { query: { reason: reason.value.trim() } }); await done('会话已撤销');
          });
        } }, '强制下线')),
      ])) : null);
    draw();
  }
  await refresh(); return root;
}

export async function bomHub(ctx, params) {
  const keyword = input({ value: params.get('q') || '', required: true, placeholder: '输入父件号或中文名称', 'aria-label': '父件号或名称' });
  const results = el('div', {}, empty('选择父件号开始', '同一 P/N 共用一套 BOM，通过行适用性控制不同构型。'));
  const button = el('button', { class: 'btn primary', type: 'submit' }, '查找件号');
  const search = async e => {
    e?.preventDefault();
    if (!keyword.value.trim()) return;
    button.disabled = true;
    try {
      const r = await api.get('/search', { query: { q: keyword.value.trim(), kinds: 'PART_NUMBER', limit: 100 } });
      results.replaceChildren(tablePanel('选择父件号 · ' + r.total, table(['件号', '名称', '操作'].map(label => ({ label })), r.results, p => [
        el('td', { class: 'mono' }, p.object_code), el('td', {}, p.display_name || p.name_cn || '—'),
        el('td', {}, link('打开 BOM', '#/bom/' + encodeURIComponent(p.object_code), 'btn primary small')),
      ]) || empty('没有匹配的件号', '请先在设计族中创建件号，或换一个关键词。')));
    } catch (e) { results.replaceChildren(el('div', { class: 'note error', role: 'alert' }, e.message)); }
    finally { button.disabled = false; }
  };
  const root = el('div', {}, el('h1', {}, 'BOM 管理'), el('p', { class: 'sub' }, '共用件号 · 装配结构 · 构型适用性'),
    el('div', { class: 'note' }, '先选择父件号，再维护下级零件、数量与适用性；可查看多层展开、反查装机关系并解析具体构型。'),
    !ctx.can('draft_write') ? el('div', { class: 'note warn' }, '当前角色可查看 BOM；编辑需要设计工程师或构型管理员角色，可由系统管理员在账户管理中分配。') : null,
    panel('查找父件号', el('form', { class: 'toolbar', onsubmit: search }, keyword, button)), results);
  if (keyword.value.trim()) await search();
  return root;
}
