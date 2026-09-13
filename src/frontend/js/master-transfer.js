import {api} from './api.js';
import {el,field,input,table,toast,toastError} from './ui.js';
import {editor} from './manage.js';

export function transferActions(ctx,kind,familyId='') {
  const names={families:'设计族',parts:'Dash 件号',externals:'外部件'};
  const title=names[kind];
  const root=el('div',{class:'actions'},
    el('button',{class:'btn',onclick:()=>api.download(`/master-data/${kind}/export${familyId?'?family_id='+encodeURIComponent(familyId):''}`,title+'导出.csv').catch(toastError)},
      familyId?'导出本族件号':'导出全部'+title));
  if(!ctx.can('draft_write')) return root;
  root.append(el('button',{class:'btn',onclick:()=>api.download(`/master-data/${kind}/template`,title+'导入模板.csv').catch(toastError)},'下载'+title+'导入模板'));
  root.append(el('button',{class:'btn',onclick:()=>{
    const file=input({type:'file',accept:'.csv,.xlsx',required:true});
    const results=el('div',{}); let batch=null;
    const confirm=el('button',{class:'btn primary',type:'button',disabled:true,onclick:async()=>{
      confirm.disabled=true;
      try {
        const r=await api.post(`/master-data/batches/${batch.batch_id}/commit`);
        toast(`已导入 ${r.created} 条${title}${kind==='families'?'，请逐项提交审批发号':''}`);
        confirm.closest('dialog').close(); window.dispatchEvent(new HashChangeEvent('hashchange'));
      } catch(e) { toastError(e); batch=null; }
    }},'确认整批导入');
    file.addEventListener('change',()=>{batch=null;confirm.disabled=true;results.replaceChildren();});
    const preview=el('button',{class:'btn',type:'button',onclick:async()=>{
      batch=null;confirm.disabled=true;results.replaceChildren();
      if(!file.files[0]) {toast('请选择文件','error');return;}
      preview.disabled=true;file.disabled=true;
      try {
        const form=new FormData();form.append('file',file.files[0]);
        batch=await api.post(`/master-data/${kind}/preview`,{form});
        results.replaceChildren(el('p',{},`共 ${batch.total_rows} 行，通过 ${batch.ok_rows} 行，错误 ${batch.error_rows} 行。`),
          table([{label:'行号'},{label:'结果'},{label:'错误原因'}],batch.rows,r=>[
            el('td',{},r.row_number),el('td',{},r.result==='OK'?'通过':'错误'),el('td',{},r.messages.join('；'))]));
        confirm.disabled=!batch.committable;
      } catch(e) {toastError(e);}
      finally {preview.disabled=false;file.disabled=false;}
    }},'校验并预览');
    const note=kind==='families'?'按词典代码填写；基本图号、名称和状态列留空。导入建立待审批设计族，由审批人发号。':
      kind==='parts'?'填写已批准的基本图号。Dash 号留空可自动分配，填写时必须未被占用；完整件号和状态列留空。':
      '外部件分类只填写 T1、T2 或 T3。外部件号不能与已有记录或本批其他行重复，即使名称或来源不同。状态列留空。';
    editor('批量导入'+title,el('div',{},el('p',{class:'note'},note),
      el('p',{class:'muted'},'支持 CSV、XLSX，每批最多 2000 行。先下载模板；全部校验通过后才能整批导入。导出包含已有编号，直接重新导入会被防重规则拦截。'),
      field('导入文件',file),el('div',{class:'actions'},preview,confirm),results),async()=>{}, {submit:'关闭'});
  }},'批量导入'+title));
  return root;
}
