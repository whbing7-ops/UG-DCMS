import { api } from './api.js';
import { el, field, input, select, panel, table, tablePanel, toast, empty, fmtDate, codeText } from './ui.js';
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

export function approvalSubmitButton(label, path, after, requiredRole = 'APPROVER') {
  return el('button', { class: 'btn', onclick: async () => {
    try {
      const candidates = await api.get('/approvals/candidates', { query: { required_role: requiredRole } });
      if (!candidates.length) throw Error('当前没有可用审批人，请管理员启用审批员账户');
      const approver = select(candidates.map(x => ({ value: x.id,
        label: `${x.full_name}（${x.username}${x.employee_no ? ' · '+x.employee_no : ''}）` })));
      editor(label, el('div', {}, field('审批人', approver),
        el('p', { class: 'muted' }, '仅显示启用、具备审批权限且不是发起人本人的账户。')),
      async () => {
        await api.post(path, { json: { approver_user_id: approver.value } });
        toast('已提交给所选审批人'); await after();
      }, { submit: '确认提交' });
    } catch (e) { toast(e.message || '无法取得审批人', 'error'); }
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

export async function backupPage(ctx) {
  if (!ctx.can('system_setting')) return empty('无系统备份权限', '仅系统管理员可执行备份和恢复。');
  const root=el('div');
  const draw=async()=>{
    const data=await api.get('/system/backups'); const s=data.schedule;
    const enabled=input({type:'checkbox',checked:s.enabled});
    const freq=select([{value:'DAILY',label:'每天'},{value:'WEEKLY',label:'每周'}].map(x=>({...x,selected:x.value===s.frequency})));
    const hour=input({type:'number',min:0,max:23,value:s.hour});
    const weekday=select(['周一','周二','周三','周四','周五','周六','周日'].map((label,i)=>({value:i,label,selected:i===s.weekday})));
    const retention=input({type:'number',min:1,max:365,value:s.retention});
    const restoreFile=input({type:'file',accept:'.zip'}); const confirm=input({placeholder:'输入：恢复UG-DCMS'});
    root.replaceChildren(el('h1',{},'系统备份与恢复'),
      el('p',{class:'sub'},'备份集同时包含PostgreSQL数据库和全部附件；恢复前自动生成恢复前备份。'),
      el('div',{class:'actions'},el('button',{class:'btn primary',onclick:async()=>{try{await api.post('/system/backups');toast('全量备份已完成');await draw();}catch(e){toast(e.message,'error')}}},'立即备份')),
      panel('定时备份',el('div',{class:'inline-form'},field('启用',enabled),field('频率',freq),field('执行小时',hour),field('星期',weekday),field('保留份数',retention),
        el('button',{class:'btn',onclick:async()=>{try{await api.put('/system/backup-schedule',{json:{enabled:enabled.checked,frequency:freq.value,hour:Number(hour.value),weekday:Number(weekday.value),retention:Number(retention.value)}});toast('定时任务已保存');await draw();}catch(e){toast(e.message,'error')}}},'保存设置'))),
      tablePanel('备份清单',table([{label:'文件'},{label:'时间'},{label:'原因'},{label:'附件数'},{label:'大小'},{label:''}],data.backups,b=>[
        el('td',{class:'mono'},b.filename),el('td',{},fmtDate(b.created_at)),el('td',{},codeText(b.reason)),el('td',{class:'num'},b.file_count??'—'),el('td',{class:'num'},((b.size_bytes||0)/1048576).toFixed(1)+' MB'),
        el('td',{},el('button',{class:'btn small',onclick:()=>api.download('/system/backups/'+encodeURIComponent(b.filename)+'/download',b.filename)},'下载'))])||empty('暂无备份')),
      panel('恢复系统',el('div',{},
        el('p',{class:'note warn'},'恢复将替换当前数据库和附件；执行前系统会自动备份当前状态。'),
        el('h4',{},'第1步：上传并校验备份包'),
        el('div',{class:'inline-form'},field('备份文件',restoreFile),field('确认文字',confirm),el('button',{class:'btn danger',onclick:async()=>{if(!restoreFile.files[0])return toast('请选择备份文件','error');try{const r=await api.upload('/system/restore',{confirmation:confirm.value},restoreFile.files[0]);toast('备份包校验通过，已进入待恢复状态');await draw();}catch(e){toast(e.message,'error')}}},'上传并校验')),
        data.restore_pending ? el('div',{class:'note warn'},
          el('h4',{},'第2步：执行恢复'),
          el('p',{},`待恢复文件：${data.restore_pending.filename||'备份包'}；排队时间：${fmtDate(data.restore_pending.queued_at)}`),
          el('div',{class:'actions'},
            el('button',{class:'btn danger',onclick:async()=>{if(!window.confirm('确认立即重启UG-DCMS并恢复数据库和全部附件？'))return;try{const r=await api.post('/system/restore/apply');toast(r.message);setTimeout(()=>location.reload(),65000);}catch(e){toast(e.message,'error')}}},'立即重启并执行恢复'),
            el('button',{class:'btn',onclick:async()=>{try{const r=await api.del('/system/restore');toast(r.message);await draw();}catch(e){toast(e.message,'error')}}},'取消待恢复任务'))) :
          el('p',{class:'muted'},'备份包校验通过后，此处将出现“立即重启并执行恢复”按钮。'))));
  }; await draw(); return root;
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
        el('td', {}, new Date(r.occurred_at).toLocaleString()), el('td', {}, r.username), el('td', {}, codeText(r.action)), el('td', {}, r.object_code || r.object_id), el('td', {}, r.reason || '—'),
        el('td', {}, el('details', {}, el('summary', {}, '查看变更'), recordView({ old_value: r.old_value, new_value: r.new_value, result: r.result }))),
      ]) || empty('暂无匹配记录')));
    } catch (e) { out.replaceChildren(el('div', { class: 'note error' }, e.message)); }
  };
  await load();
  return el('div', {}, el('h1', {}, '审计记录'), el('p', { class: 'sub' }, '按动作或对象筛选；记录不可修改或删除。'),
    el('form', { class: 'toolbar', onsubmit: load }, action, type, id, el('button', { class: 'btn primary' }, '查询')), out);
}
