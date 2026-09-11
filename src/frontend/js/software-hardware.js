/* 双向适装查询共用同一关系，不复制软件交付包。 */
import { api } from './api.js';
import { el, panel, table, empty, field, input, select, link, status, statusText, toast, toastError } from './ui.js';

export function softwarePackageButton(v) {
  if (!v.package_filename) return el('span', {class:'muted'}, '未上传附件');
  return el('button', {class:'btn small', type:'button', onclick:async (event)=>{
    const button=event.currentTarget; button.disabled=true;
    try { await api.download(`/software-versions/${v.software_version_id || v.id}/package/download`, v.package_filename); }
    catch(error) { toastError(error); }
    finally { button.disabled=false; }
  }}, `下载 ${v.package_filename}（${((v.package_size_bytes || 0)/1048576).toFixed(1)} MB）`);
}

export async function hardwareSoftwarePanel(code) {
  const rows=await api.get(`/hardware/${encodeURIComponent(code)}/software`);
  const version=select([{value:'',label:'全部已登记硬件版本'},
    ...[...new Set(rows.map(r=>r.hardware_version))].map(v=>({value:v,label:v}))]);
  const output=el('div', {});
  const render=()=>{
    const selected=rows.filter(r=>!version.value || r.hardware_version===version.value);
    const renderTable=items=>table([
      {label:'软件件号'}, {label:'软件名称'}, {label:'软件版本'}, {label:'构建号'},
      {label:'适用硬件版本'}, {label:'状态'}, {label:'适用说明'}, {label:'软件附件'}
    ], items, r=>[
      el('td',{class:'mono'},r.software_number),
      el('td',{},link(r.name_cn,`#/software/${encodeURIComponent(r.software_number)}?version_id=${encodeURIComponent(r.software_version_id)}`)),
      el('td',{class:'mono'},r.version), el('td',{class:'mono'},r.build || '—'),
      el('td',{class:'mono'},r.hardware_version), el('td',{},status(r.status)),
      el('td',{},r.applicability_note || '—'), el('td',{},softwarePackageButton(r))
    ]);
    const loadable=selected.filter(r=>r.loadable), other=selected.filter(r=>!r.loadable);
    output.replaceChildren(renderTable(loadable) || empty('暂无已发布的可加载软件', '未登记适装关系不代表兼容所有软件。'));
    if(other.length) output.append(el('details',{},
      el('summary',{},`其他关联软件（${other.length} 条，未批准或已失效，不可据此加载）`),renderTable(other)));
  };
  version.addEventListener('change',render); render();
  return panel('可加载软件',el('div',{},
    el('p',{class:'muted'},'按适用硬件版本核对。点击软件名称查看对应版本信息及附件；项目准入和基线要求仍需满足。'),
    field('筛选硬件版本',version),output));
}

export function softwareHardwarePanel(ctx, version, reload) {
  const rows=version.compatible_hardware || [];
  const editable=version.status==='DRAFT' && ctx.can('draft_write');
  const listing=table([{label:'硬件件号'},{label:'名称'},{label:'类型／来源'},
    {label:'硬件版本'},{label:'适用说明'},{label:'操作'}],rows,r=>[
    el('td',{},link(r.external_part_number || r.object_code,'#/object/'+encodeURIComponent(r.object_code))),
    el('td',{},r.display_name),el('td',{},r.object_type==='EXTERNAL_PART'?`外部件 · ${r.namespace_code}`:'内部件'),
    el('td',{class:'mono'},r.hardware_version),el('td',{},r.applicability_note || '—'),
    el('td',{},editable?el('button',{class:'btn small danger',onclick:async()=>{
      if(!window.confirm('确认移除这条适装关系？不会删除硬件件号或软件附件。'))return;
      try {await api.del(`/software-hardware/${r.id}`);toast('适装关系已移除');reload();}catch(e){toastError(e);}
    }},'移除'): '只读')]);
  const body=el('div',{},listing || empty('尚未登记适装硬件','提交审批前至少登记一个硬件件号及明确版本。'));
  if(editable){
    const search=input({placeholder:'搜索内部件号、外部件号或名称',autocomplete:'off','aria-label':'适装硬件搜索'});
    const results=el('div',{class:'part-picker-results'}), chosen=el('div',{class:'muted'},'尚未选择硬件');
    const hwVersion=input({placeholder:'明确硬件版本／基线标识，不可留空',maxlength:64});
    const note=input({placeholder:'加载条件、接口或适用范围',maxlength:500});
    let selection=null,timer,serial=0;
    search.addEventListener('input',()=>{
      selection=null;chosen.textContent='请从搜索结果中选择硬件';clearTimeout(timer);
      const request=++serial, q=search.value.trim();results.replaceChildren();
      if(!q)return;
      timer=setTimeout(async()=>{
        results.replaceChildren(el('div',{},'正在搜索…'));
        try{
          const matches=await api.get('/hardware-candidates',{query:{q,limit:30}});
          if(request!==serial)return;
          results.replaceChildren(...matches.map(r=>el('button',{type:'button',class:'part-picker-option',
            disabled:r.lifecycle_status==='OBSOLETE',onclick:()=>{
              ++serial;selection=r.object_code;search.value=r.external_part_number || r.object_code;
              chosen.textContent=`已选择：${r.display_name}（${r.object_code}）`;results.replaceChildren();
            }},el('b',{},r.external_part_number || r.object_code),el('span',{},r.display_name),
            el('small',{},`${r.object_type==='EXTERNAL_PART'?`外部件 · ${r.namespace_code}`:'内部件'} · ${statusText(r.lifecycle_status)}`))));
          if(!matches.length)results.append(el('div',{},'没有匹配件号'));
          if(matches.length===30)results.append(el('div',{class:'muted'},'显示前30条，请补充关键词缩小范围'));
        }catch(e){if(request===serial)results.replaceChildren(el('div',{},`搜索失败：${e.message}`));}
      },250);
    });
    body.append(el('div',{class:'inline-form'},
      field('硬件件号',el('div',{class:'part-picker'},search,results,chosen)),
      field('硬件版本',hwVersion),field('适用说明',note),
      el('button',{class:'btn primary',onclick:async(event)=>{
        if(!selection || !hwVersion.value.trim())return toast('请选择硬件并填写明确的硬件版本','error');
        const button=event.currentTarget;button.disabled=true;
        try{await api.post(`/software-versions/${version.id}/hardware`,{json:{hardware_object_code:selection,
          hardware_version:hwVersion.value.trim(),applicability_note:note.value.trim() || null}});
          toast('适装硬件已登记');reload();}catch(e){toastError(e);}finally{button.disabled=false;}
      }},'添加适装硬件')));
  }
  return panel(`适装硬件 · 软件版本 ${version.version}${version.build?' · 构建号 '+version.build:''}`,body);
}
