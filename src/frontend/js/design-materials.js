import {api} from './api.js';
import {el, field, input, select, table, tablePanel, panel, empty, status, statusText,
  codeText, fmtDate, link, pageControls, toastError} from './ui.js';

export async function designMaterials(ctx, params) {
  const query={q:params.get('q')||'',file_type_code:params.get('file_type_code')||'',
    revision_status:params.get('revision_status')||'',current_only:params.get('current_only')||'false'};
  const [result,types]=await Promise.all([
    api.get('/design-materials',{query:{...query,page:Number(params.get('page')||1),page_size:50}}),
    api.get('/dictionary/file-type'),
  ]);
  const q=input({value:query.q,placeholder:'附件名称、文件编号或设计文件名称'});
  const type=select([{value:'',label:'全部类型'},...types.map(t=>({value:t.code,label:t.name_cn}))]);
  const state=select([{value:'',label:'全部状态'},...['WORKING','IN_REVIEW','RELEASED','SUPERSEDED','CANCELLED'].map(s=>({value:s,label:statusText(s)}))]);
  const scope=select([{value:'false',label:'全部版次附件'},{value:'true',label:'仅当前已发布版次'}]);
  type.value=query.file_type_code;state.value=query.revision_status;scope.value=query.current_only;
  const search=e=>{e.preventDefault();location.hash='#/design-materials?'+new URLSearchParams({q:q.value.trim(),file_type_code:type.value,revision_status:state.value,current_only:scope.value,page:1});};
  return el('div',{},el('h1',{},'设计资料清单'),
    el('p',{class:'sub'},'汇总图纸、图纸手册等设计文件已上传的附件，每份附件一行。新增或删除附件后，清单自动更新。'),
    panel('查询',el('form',{class:'inline-form',onsubmit:search},field('关键词',q),field('文件类型',type),field('版次状态',state),field('范围',scope),
      el('button',{class:'btn primary',type:'submit'},'查询'),link('重置','#/design-materials','btn'))),
    result.items.length ? tablePanel(`设计资料附件 · 共 ${result.total} 份`,
      table([{label:'附件名称'},{label:'文件编号'},{label:'设计文件名称'},{label:'类型'},{label:'版次'},{label:'状态'},
        {label:'附件用途'},{label:'大小'},{label:'上传时间'},{label:'操作'}],result.items,r=>[
        el('td',{},r.filename),el('td',{class:'mono'},link(r.file_number,'#/file/'+encodeURIComponent(r.file_number))),
        el('td',{},r.title_cn),el('td',{},r.file_type_name),el('td',{class:'mono'},r.revision_number),
        el('td',{},status(r.revision_status),r.is_current?' · 当前发布':'',r.file_status==='OBSOLETE'?' · 文件已废止':''),
        el('td',{},codeText(r.attachment_role)),el('td',{},r.size_bytes<1024?`${r.size_bytes} B`:`${(r.size_bytes/1024).toFixed(1)} KB`),
        el('td',{},fmtDate(r.uploaded_at)),el('td',{class:'nowrap'},
          link('查看版次','#/revision/'+r.revision_id,'btn small'),' ',
          el('button',{class:'btn small',onclick:()=>api.download(`/attachments/${r.id}/download`,r.filename).catch(toastError)},'下载'))]),
      pageControls(result,'/design-materials',query)) : empty('没有符合条件的设计资料附件','请调整查询条件，或在设计文件的版次页面上传附件。'));
}
