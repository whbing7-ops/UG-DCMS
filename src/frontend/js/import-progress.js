import { api } from './api.js';
import { el, fmtDate } from './ui.js';

export function importProgress(initial, onComplete, setBusy) {
  let current=initial, timer=null, polling=false, pending=false, previousId=null;
  const title=el('strong'), bar=el('progress',{max:100,'aria-label':'数据导入进度'}),
    detail=el('p'), times=el('p',{class:'muted'}), connection=el('p',{role:'status'}),
    host=el('div',{'data-import-progress':'true',class:'note'},title,bar,detail,times,connection,
      el('p',{class:'muted'},'进度按处理阶段和数量计算。全部处理完成后统一提交；可刷新或离开本页，请保持服务运行。'));
  const busy=()=>pending||['QUEUED','RUNNING'].includes(current?.state);
  const render=()=>{
    host.hidden=!current&&!pending;
    const labels={QUEUED:'等待开始',RUNNING:'正在导入',COMPLETED:'导入完成',FAILED:'导入失败',INTERRUPTED:'导入中断'};
    title.textContent=pending?'正在上传并校验数据包':labels[current?.state]||'等待导入';
    host.className='note '+(['FAILED','INTERRUPTED'].includes(current?.state)?'error':current?.state==='COMPLETED'?'ok':'');
    if(pending)bar.removeAttribute('value'); else bar.value=current?.progress||0;
    detail.textContent=pending?'数据包校验通过后自动开始，正在等待服务器确认。':current?
      `${current.progress||0}% · ${current.stage}${current.total?` · ${current.completed} / ${current.total}`:''} · ${current.message}`:'';
    const end=current?.finished_at?Date.parse(current.finished_at):Date.now();
    const seconds=current?.started_at?Math.max(0,Math.floor((end-Date.parse(current.started_at))/1000)):0;
    times.textContent=current?.started_at?`已耗时 ${Math.floor(seconds/60)} 分 ${seconds%60} 秒 · 最近更新 ${fmtDate(current.updated_at)}${current.finished_at?' · 结束 '+fmtDate(current.finished_at):''}`:'';
    setBusy(busy());
  };
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(poll,1500);};
  const poll=async()=>{
    if(!host.isConnected||polling)return;
    polling=true;
    try{
      const next=await api.get('/system/data-backups/status');
      if(!host.isConnected)return;
      const wasBusy=busy();
      if(pending&&(!next||next.id===previousId)){
        current={state:'FAILED',stage:'提交未确认',progress:0,message:'未找到新受理的导入任务，请重新选择数据包提交。'};pending=false;
      }else if(next){current=next;pending=false;}
      connection.textContent='';render();
      if(wasBusy&&current?.state==='COMPLETED'){await onComplete();return;}
    }catch(e){
      if(!host.isConnected)return;
      connection.textContent=e.status===401?'登录已失效，重新登录后可查看导入结果。':'暂时无法读取导入状态，正在重试；这不代表导入失败，请勿重复提交。';
    }finally{polling=false;}
    if(busy())schedule();
  };
  render();if(busy())schedule();
  return {host,
    async submit(file, confirmation){
      if(busy())return;
      previousId=current?.id;pending=true;current=null;connection.textContent='';render();
      try{
        const accepted=await api.upload('/system/data-backups/import',{confirmation,background:'true'},file);
        current={id:accepted.job_id,state:'QUEUED',stage:'等待开始',message:'数据包已校验，任务已受理',progress:0,started_at:new Date().toISOString()};
        pending=false;render();schedule();
      }catch(e){
        pending=false;
        if(e.status&&e.status<500){
          current={state:'FAILED',stage:'校验或提交',message:e.message,progress:0};render();
          // A competing request may already be running; show that durable job.
          try{const s=await api.get('/system/data-backups/status');if(['QUEUED','RUNNING'].includes(s?.state)){current=s;render();schedule();}}catch(_){}
        }else{
          pending=true;render();connection.textContent='连接中断，正在查询任务是否已受理，请勿重复提交。';schedule();
        }
      }
    }
  };
}
