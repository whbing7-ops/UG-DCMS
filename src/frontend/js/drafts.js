import {api} from './api.js';
import {el,link,panel,table,tablePanel,input,select,field,toast,toastError,askReason,statusText,fmtDate,empty} from './ui.js';
import {approvalSubmitButton} from './extras.js';

export async function workflowState(kind,id){return api.get(`/drafts/${kind}/${id}`);}
export async function removeDraft(kind,id){
  if(!window.confirm('确认删除这份草稿？已提交过的审批历史仍会保留。'))return;
  try{const r=await api.del(`/drafts/${kind}/${id}`);toast('草稿已删除');location.hash=r.parent_url;window.dispatchEvent(new HashChangeEvent('hashchange'));}catch(e){toastError(e);}
}
export function requestActions(r,after=()=>window.dispatchEvent(new HashChangeEvent('hashchange'))){
  const act=async(cancel)=>{
    const reason=await askReason(cancel?'取消申请':'撤回修改',cancel?'请填写取消原因':'请填写撤回原因，撤回后可以编辑并再次提交');
    if(!reason)return;
    try{await api.post(`/approvals/${r.id}/${cancel?'cancel':'withdraw'}`,{query:{reason}});toast(cancel?'申请已取消':'已撤回，可重新编辑');await after();}catch(e){toastError(e);}
  };
  return el('span',{class:'actions'},el('button',{class:'btn small',onclick:()=>act(false)},'撤回修改'),el('button',{class:'btn small danger',onclick:()=>act(true)},'取消申请'));
}
export function applicantActions(ctx,w){
  const actions=el('span',{class:'actions'});
  if(w.can_edit&&ctx.can('draft_write'))actions.append(link('编辑草稿',`#/draft/${w.object_type}/${w.id}`,'btn small'),
    el('button',{class:'btn small danger',onclick:()=>removeDraft(w.object_type,w.id)},'删除草稿'));
  if(w.request)actions.append(link('审批状态',`#/approval/${w.request.id}`,'btn small'));
  if(w.can_withdraw)actions.append(requestActions(w.request));
  return actions;
}
export function draftListing(rows){
  return tablePanel('我的待提交草稿',table([{label:'类型'},{label:'对象'},{label:'建立时间'},{label:'操作'}],rows,r=>[
    el('td',{},r.label),el('td',{},link(r.object_code,r.object_url)),el('td',{},fmtDate(r.created_at)),
    el('td',{},link('编辑后提交',`#/draft/${r.object_type}/${r.id}`,'btn small'), ' ',el('button',{class:'btn small danger',onclick:()=>removeDraft(r.object_type,r.id)},'删除草稿'))])||empty('暂无待提交草稿'));
}
export async function draftEditor(ctx,params,kind,id){
  const w=await workflowState(kind,id),controls={},body=el('div'),feedback=el('p',{role:'status'});
  let dictionaries={};
  if(kind==='BASIC_DRAWING_FAMILY'){
    const names=['physical-class','core-term','qualifier','function-item'];
    const lists=await Promise.all(names.map(n=>api.get('/dictionary/'+n)));
    dictionaries=Object.fromEntries(names.map((n,i)=>[n,lists[i]]));
  }
  const choices=f=>{
    if(f.name==='primary_class_code')return ['T1','T2','T3'].map(value=>({value,label:value}));
    if(f.name==='object_level_code')return [{value:'PART',label:'零件'},{value:'ASSEMBLY',label:'组件'}];
    const dict={physical_class_id:'physical-class',core_term_id:'core-term',qualifier_1_id:'qualifier',qualifier_2_id:'qualifier',primary_function_id:'function-item'}[f.name];
    if(!dict)return null;
    const cls=controls.primary_class_code?.value||w.values.primary_class_code;
    return dictionaries[dict].filter(x=>(!x.status||x.status==='ACTIVE')&&(!x.primary_class_code||x.primary_class_code===cls)).map(x=>({value:x.id,label:x.code+' '+x.name_cn}));
  };
  for(const f of w.fields){
    const options=choices(f),value=w.values[f.name]??'';
    const c=options?select([...(f.required?[]:[{value:'',label:'（无）'}]),...options].map(x=>({...x,selected:String(x.value)===String(value)}))):
      f.max_length>500?el('textarea',{rows:3,maxlength:f.max_length},value):input({type:f.type,value,maxlength:f.max_length});
    c.setAttribute('aria-label',f.label);c.required=f.required;c.disabled=!w.can_edit||!ctx.can('draft_write');controls[f.name]=c;body.append(field(f.label,c));
  }
  controls.primary_class_code?.addEventListener('change',()=>{
    for(const f of w.fields.filter(x=>['physical_class_id','core_term_id','qualifier_1_id','qualifier_2_id'].includes(x.name))){
      const c=controls[f.name],options=choices(f);c.replaceChildren(...[...(f.required?[]:[{value:'',label:'（无）'}]),...options].map(x=>el('option',{value:x.value},x.label)));
    }
  });
  const actions=el('div',{class:'actions'});
  if(w.can_edit&&ctx.can('draft_write')){
    const save=el('button',{class:'btn primary',type:'submit'},'保存修改');
    const form=el('form',{onsubmit:async e=>{e.preventDefault();save.disabled=true;try{
      await api.patch(`/drafts/${kind}/${id}`,{json:{values:Object.fromEntries(Object.entries(controls).map(([k,c])=>[k,c.value||null]))}});
      toast('草稿修改已保存');feedback.textContent='已保存，可以提交审批。';
    }catch(e){feedback.textContent=e.message;toastError(e);}finally{save.disabled=false;}}},body,save,feedback);
    actions.append(approvalSubmitButton('保存并提交审批',`/drafts/${kind}/${id}/submit`,()=>{location.hash=w.object_url;window.dispatchEvent(new HashChangeEvent('hashchange'));},kind==='DESIGN_BASELINE'?'CONFIGURATION_MANAGER':'APPROVER',async()=>{
      if(!form.reportValidity())throw Error('请填写完整的申请内容');
      await api.patch(`/drafts/${kind}/${id}`,{json:{values:Object.fromEntries(Object.entries(controls).map(([k,c])=>[k,c.value||null]))}});
    }));
    w.form=form;
    actions.append(el('button',{class:'btn danger',onclick:()=>removeDraft(kind,id)},'删除草稿'));
  }
  let upload=null;
  if(kind==='SOFTWARE_VERSION'&&w.can_edit&&ctx.can('draft_write')){
    const file=input({type:'file','aria-label':'替换软件包'});
    upload=panel('替换软件内容压缩包',el('div',{},el('p',{},'保存版本信息后，可替换本草稿的软件包，系统会重新计算 SHA-256。'),file,
      el('button',{class:'btn',onclick:async()=>{if(!file.files[0])return toast('请选择软件包','error');try{await api.upload(`/drafts/SOFTWARE_VERSION/${id}/package`,{},file.files[0]);toast('软件包已替换');}catch(e){toastError(e);}}},'上传替换')));
  }
  return el('div',{},el('h1',{},w.label+' · 编辑与审批'),el('p',{class:'mono'},w.object_code),
    el('p',{},w.can_edit?'编辑后先保存，再提交审批。附件、适装硬件或基线明细可通过“打开业务对象”维护。':'当前为只读状态，审核中可撤回后修改。'),
    panel('申请内容',w.form||body),upload,actions,
    w.can_withdraw?requestActions(w.request):null,
    el('div',{class:'actions'},link('打开业务对象',w.object_url,'btn'),w.request?link('查看审批状态与历史','#/approval/'+w.request.id,'btn'):null,link('返回审批中心','#/approvals','btn')));
}
