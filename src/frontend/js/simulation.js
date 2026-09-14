import {api} from './api.js';
import {el, link, panel, table, tablePanel, fmtDate, toastError} from './ui.js';

export async function imaSimulation(ctx) {
  const root=el('div');
  async function refresh() {
    const data=await api.get('/simulation/ima');
    const result=el('div');
    root.replaceChildren(el('h1',{},'IMA 模拟业务数据'),
      el('div',{class:'note warn'},data.notice),
      el('p',{},'19个内部件、9个外部件、45条多层BOM记录、两种构型、3套软件及其适装关系。包含模拟图纸、图纸手册、审批发布和设计基线。'),
      el('p',{},'基本图号按系统规则自动分配；仅新增模拟记录，保留已有业务数据。重复导入返回原批次。安装升级不会自动导入。'),result);
    if (!data.imported) {
      const feedback=el('p',{role:'status'});
      const button=el('button',{class:'btn primary',onclick:async()=>{
        button.disabled=true; feedback.textContent='正在建立模拟业务数据，请等待；请勿关闭服务。';
        try {
          await api.post('/simulation/ima',{dataset_code:'SIM-IMA-V1'});
          await refresh();
        } catch(e) {
          feedback.textContent=e.message+'。可刷新页面核对导入结果后重试。';
          button.disabled=false;
        }
      }},'建立 IMA 模拟数据');
      result.append(panel('导入到当前系统',
        el('p',{},'模拟审批使用两个专用模拟身份，导入完成即停用。全部数据统一标识“模拟数据”，不代表真实人员批准。'),
        ctx.can('system_setting')?button:el('p',{},'请由系统管理员执行导入。'),feedback));
      return;
    }
    const m=data.receipt.manifest;
    result.append(el('p',{class:'note',role:'status'},'导入已完成 · '+fmtDate(data.receipt.imported_at)),
      panel('查看模拟产品',link(m.top_part_number+' · IMA设备','#/object/'+encodeURIComponent(m.top_part_number),'btn'),
        ' ',link('打开共用 BOM','#/bom/'+encodeURIComponent(m.top_part_number),'btn primary'),
        ' ',link('查看顶层设计基线','#/baseline/'+m.baselines.IMA.id,'btn'),
        ' ',link('查看模拟设计资料','#/design-materials?q=SIM-IMA-V1','btn')),
      tablePanel('两种构型的冻结结果',table([{label:'构型'},{label:'说明'},{label:'冻结快照'},{label:'操作'}],
        Object.entries(m.configurations),([key,c])=>[
          el('td',{},'【模拟数据】IMA构型'+key),el('td',{},key==='A'?'基础计算配置':'增强计算及扩展接口'),
          el('td',{},c.snapshot.resolved_snapshot_number+' · '+c.snapshot.line_count+'行'),
          el('td',{},el('button',{class:'btn small',onclick:()=>api.download('/attachments/'+c.document.attachment_id+'/download',c.document.filename).catch(toastError)},'下载冻结清单'))])),
      tablePanel('内部件与设计基线',table([{label:'件号'},{label:'名称'},{label:'操作'}],Object.entries(m.parts),([key,p])=>[
        el('td',{},link(p.part_number,'#/object/'+encodeURIComponent(p.part_number))),el('td',{},p.name),
        el('td',{},link('设计族','#/family/'+p.family_id),' · ',link('设计基线','#/baseline/'+m.baselines[key].id))])),
      tablePanel('软件适装关系',table([{label:'软件'},{label:'适装硬件 R00'}],Object.values(m.software),s=>[
        el('td',{},link(s.software_number+' · SIM-1.0.0','#/software/'+s.software_number)),
        el('td',{},s.hardware.map(h=>m.parts[h].part_number).join('、'))])),
      tablePanel('模拟附件',table([{label:'文件'},{label:'操作'}],m.documents,d=>[
        el('td',{},d.filename),el('td',{},link('查看版次','#/revision/'+d.revision_id),' ',
          el('button',{class:'btn small',onclick:()=>api.download('/attachments/'+d.attachment_id+'/download',d.filename).catch(toastError)},'下载'))])));
  }
  await refresh(); return root;
}
