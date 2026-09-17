import {api,token} from './api.js';
import {el,link,panel,table,tablePanel,empty,statusText,fmtDate,toastError,toast} from './ui.js';
let runningToken=null,timer=null,busy=false;

function workQueue(data){
  const items=[...data.inbox.map(r=>({title:r.title,number:r.request_number,state:'待我审批',action:'查看申请',url:'#/approval/'+r.id})),
    ...data.returned.map(r=>({title:r.title,number:r.request_number,state:statusText(r.status)+'，待修改',action:'编辑申请',url:`#/draft/${r.object_type}/${r.object_id}`}))];
  return el('div',{},
    el('div',{class:'actions'},link(`待我审批 ${data.inbox_count} 项`,'#/approvals','btn primary'),
      link(`退回待修改 ${data.returned.length} 项`,'#/approvals','btn'),
      link(`待提交草稿 ${data.draft_count} 项`,'#/approvals','btn'),link('打开审批中心','#/approvals','btn')),
    table([{label:'申请单'},{label:'事项'},{label:'状态'},{label:'操作'}],items.slice(0,10),r=>[
      el('td',{},r.number),el('td',{},r.title),el('td',{},r.state),el('td',{},link(r.action,r.url,'btn small'))])||empty('当前没有待审批或退回待修改的事项'),
    items.length>10?el('p',{class:'muted'},'首页显示前10项，进入审批中心查看全部。'):null,
    data.drafts.length?el('details',{},el('summary',{},'我的待提交草稿（最近20项）'),
      data.drafts.map(r=>el('p',{},link(r.label+' · '+r.object_code,`#/draft/${r.object_type}/${r.id}`)))):null);
}
function messageList(data){
  return table([{label:'消息'},{label:'内容'},{label:'时间'},{label:'操作'}],data.messages,r=>[
    el('td',{},r.read_at?r.title:el('b',{},r.title)),el('td',{},r.body),el('td',{},fmtDate(r.created_at)),
    el('td',{},link('查看申请',r.url,'btn small'),!r.read_at?el('button',{class:'btn small',onclick:async()=>{
      try{await api.post('/notifications/read',{json:{ids:[r.id]}});await refresh();}catch(e){toastError(e);}
    }},'标为已读'):null)])||empty('暂无通知');
}
export async function personalDashboard(){
  const d=await api.get('/notifications/overview');
  return el('div',{},panel('我的待办事项',el('div',{'data-work-queue':'true'},workQueue(d))),
    notificationSettings(d),
    panel('审批消息',el('div',{'data-notification-list':'true',style:'max-height:440px;overflow:auto'},messageList(d))));
}
function notificationSettings(d){
  const code=el('input',{'aria-label':'通知助手绑定信息',readonly:true,style:'width:100%;display:none'});
  const connection=el('p',{'data-desktop-status':'true'},d.desktop_connected?'Windows 通知助手已连接':'Windows 通知助手未连接');
  return panel('Windows 系统通知',el('div',{},connection,
    el('p',{},'在需要接收提醒的电脑运行通知助手。点击“生成绑定信息”，复制到助手后连接；有待审批申请、审批通过、驳回或退回时会弹出 Windows 消息，点击可打开申请。'),
    el('p',{class:'muted'},'助手须保持运行；退出网页登录、会话到期或停用账户后会停止接收。Windows 勿扰模式或关闭系统通知可能隐藏弹窗，消息仍保留在本页。'),
    el('div',{class:'actions'},link('下载 Windows 通知助手','https://github.com/whbing7-ops/UG-DCMS/releases/download/rc2.41/UG-DCMS-Notify-rc2.41.zip','btn'),
      el('button',{class:'btn',onclick:async()=>{try{
        const r=await api.post('/notifications/pair');code.style.display='block';code.value=location.origin+'|'+r.code;code.focus();code.select();toast('已生成，有效期5分钟；复制后粘贴到通知助手。');
      }catch(e){toastError(e);}}},'生成绑定信息'),
      el('button',{class:'btn',onclick:async()=>{try{await api.post('/notifications/disconnect');toast('已停用本账户的通知助手绑定');await refresh();}catch(e){toastError(e);}}},'停用通知助手')),
    code));
}
async function refresh(){
  if(busy||!token()||token()!==runningToken)return;
  busy=true;
  const expected=token();
  try{
    const d=await api.get('/notifications/overview');
    if(expected!==token())return;
    document.querySelectorAll('[data-notification-badge]').forEach(n=>n.textContent=d.unread?`消息 ${d.unread}`:'待办与消息');
    document.querySelector('[data-work-queue]')?.replaceChildren(workQueue(d));
    document.querySelector('[data-notification-list]')?.replaceChildren(messageList(d));
    const state=document.querySelector('[data-desktop-status]');if(state)state.textContent=d.desktop_connected?'Windows 通知助手已连接':'Windows 通知助手未连接';
  }catch(e){if(e.status===401)stopNotifications();}finally{busy=false;}
}
export function startNotifications(user){
  if(user.must_change_password)return stopNotifications();
  if(runningToken===token())return;
  stopNotifications();runningToken=token();refresh();timer=setInterval(refresh,30000);
}
export function stopNotifications(){clearInterval(timer);timer=null;runningToken=null;}
export function notificationBadge(){return link('待办与消息','#/','btn small');}
