// Browser interaction regression with a stateful HTTP contract fixture.
// Windows CI additionally runs the same UI against the installed PostgreSQL backend.
const { chromium } = require(process.env.DCMS_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
(async () => {
 const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
 const frontend=path.resolve(__dirname,'frontend');
 const server=http.createServer((req,res)=>{
  const file=path.resolve(frontend,'.'+new URL(req.url,'http://localhost').pathname.replace(/\/$/,'/index.html'));
  if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file));
 });
 await new Promise(resolve=>server.listen(8765,'127.0.0.1',resolve));
 const browser = await chromium.launch({headless:true, ...(process.env.DCMS_BROWSER_PATH ? {executablePath:process.env.DCMS_BROWSER_PATH} : process.platform === 'win32' ? {channel:'msedge'} : {})});
 const page = await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 const requests=[];
 let users=[{id:'admin',username:'admin',full_name:'系统管理员',roles:['SYSTEM_ADMIN','DATA_ADMIN'],is_active:true,must_change_password:true}];
 const rules=[], contexts=[];
 let lines=[{id:'line1',item_number:'010',child_object_code:'UG-CHILD-001',child_name:'演示零件',quantity:1,unit_code:'EA',child_status:'ACTIVE'}];
 const perms=['user_manage','session_manage','draft_write','read_audit','dictionary_write','baseline_release','submit','approve'];
 await page.route('**/api/v1/**',async route=>{
  const req=route.request(), url=new URL(req.url()), p=url.pathname.replace('/api/v1',''), method=req.method();
  const data=req.postData()?JSON.parse(req.postData()):{};
  requests.push({p,method,data}); let body=[];
  if(p==='/auth/login') body={access_token:'test',user:users[0]};
  else if(p==='/auth/password') {users[0].must_change_password=false;body={};}
  else if(p==='/auth/me') body={...users[0],permissions:perms};
  else if(p==='/reports/dashboard') body={objects:{},quality:{},integrity:{},families:{},baselines:{}};
  else if(p==='/admin/users' && method==='GET') body=users;
  else if(p==='/admin/users' && method==='POST') { body={...data,id:'new',is_active:true}; users.push(body); }
  else if(p==='/admin/users/new' && method==='PATCH') { Object.assign(users[1],data); body=users[1]; }
  else if(p==='/admin/users/admin' && method==='PATCH' && data.is_active===false) return route.fulfill({status:400,json:{error:{message:'这是最后一个启用状态的系统管理员，不得停用'}}});
  else if(p==='/admin/license') body={limit:10,active_accounts:1,available:9};
  else if(p==='/search') body={total:1,results:[{object_code:'UG-DEMO-001',display_name:'演示成品',kind:'PART_NUMBER'}]};
  else if(p==='/bom/UG-DEMO-001') body={lines};
  else if(p==='/bom/UG-DEMO-001/snapshots') body={bom:[],resolved:[]};
  else if(p.endsWith('/validate')) body={errors:[],warnings:[]};
  else if(p==='/applicability/rules') { if(method==='POST') rules.push({...data,status:'ACTIVE'}); body=rules; }
  else if(p==='/configuration/contexts') { if(method==='POST') contexts.push({...data,status:'ACTIVE'}); body=contexts; }
  else if(p==='/bom/lines/line1' && method==='PATCH') { Object.assign(lines[0],data); body=lines[0]; }
  else if(p==='/bom/lines/line1' && method==='DELETE') {lines=[]; body={};}
  else if(p.endsWith('/resolve')) body={line_count:lines.length,lines,excluded:[],overlaps:[]};
  else if(p==='/dictionary') body=['unit'];
  else if(p==='/dictionary/unit') body=[{code:'EA',name_cn:'件',status:'ACTIVE'}];
  else if(p==='/import/bom/template') {assert.equal(req.headers().authorization,'Bearer test'); return route.fulfill({body:'item_number,child_object_code,quantity\n',contentType:'text/csv'});}
  await route.fulfill({json:body});
 });
 await page.goto('http://127.0.0.1:8765/');
 if(process.env.DCMS_TEST_FONT){
  const font=fs.readFileSync(process.env.DCMS_TEST_FONT).toString('base64');
  await page.addStyleTag({content:`@font-face{font-family:UITestChinese;src:url(data:font/woff2;base64,${font}) format('woff2')} :root{--sans:UITestChinese,system-ui;--mono:Consolas,UITestChinese,monospace}`});
  await page.evaluate(()=>document.fonts.ready);
 }
 await page.getByRole('textbox',{name:'账户',exact:true}).fill('admin');
 await page.getByLabel('当前口令').fill('TestPassword!26');
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('heading',{name:'先修改初始口令'}).waitFor();
 await page.getByLabel('当前口令').fill('TestPassword!26');
 await page.getByLabel('新口令',{exact:true}).fill('ChangedPassword!26');
 await page.getByLabel('再次输入新口令').fill('ChangedPassword!26');
 await page.getByRole('button',{name:'修改口令',exact:true}).click();
 await page.locator('.nav a[href="#/admin"]').click();
 await page.getByRole('button',{name:'＋ 新建账户'}).click();
 let d=page.getByRole('dialog');
 await d.getByLabel('账户名（字母开头）').fill('designer01');
 await d.getByLabel('姓名',{exact:true}).fill('设计工程师');
 await d.getByLabel('初始密码').fill('Start@2026Demo');
 await d.getByLabel('设计工程师',{exact:true}).check();
 await d.getByRole('button',{name:'保存',exact:true}).click();
 await page.getByText('designer01',{exact:true}).waitFor();
 let row=page.getByRole('row').filter({hasText:'designer01'});
 await row.getByRole('button',{name:'编辑',exact:true}).click();
 await d.getByLabel('姓名',{exact:true}).fill('设计工程师甲');
 await d.getByLabel('变更原因').fill('补全姓名');
 await d.getByRole('button',{name:'保存',exact:true}).click();
 await page.getByText('设计工程师甲',{exact:true}).waitFor();
 await row.getByRole('button',{name:'停用',exact:true}).click();
 await d.getByLabel('操作原因').fill('测试停用');
 await d.getByRole('button',{name:'确认停用账户'}).click();
 await row.getByRole('button',{name:'启用',exact:true}).waitFor();
 await row.getByRole('button',{name:'启用',exact:true}).click();
 await d.getByLabel('操作原因').fill('测试启用');
 await d.getByRole('button',{name:'确认启用账户'}).click();
 await row.getByRole('button',{name:'重置密码'}).click();
 await d.getByLabel('新临时密码').fill('Fresh@2026Demo');
 await d.getByLabel('再次输入密码').fill('Wrong');
 await d.getByLabel('操作原因').fill('测试重置');
 await d.getByRole('button',{name:'确认重置密码'}).click();
 await d.getByText('两次密码不一致').waitFor();
 await d.getByLabel('再次输入密码').fill('Fresh@2026Demo');
 await d.getByRole('button',{name:'确认重置密码'}).click();
 await d.waitFor({state:'hidden'});
 await row.getByRole('button',{name:'解锁',exact:true}).click();
 await d.getByRole('button',{name:'确认解除登录锁定'}).click();
 await d.waitFor({state:'hidden'});
 await page.getByRole('row').filter({hasText:'admin'}).getByRole('button',{name:'停用',exact:true}).click();
 await d.getByLabel('操作原因').fill('验证最后管理员保护');
 await d.getByRole('button',{name:'确认停用账户'}).click();
 await d.getByText('这是最后一个启用状态的系统管理员，不得停用').waitFor();
 await d.getByRole('button',{name:'取消',exact:true}).click();
 await page.getByLabel('搜索账户').fill('工程师甲');
 assert.equal(await page.getByRole('row').filter({hasText:'admin'}).count(),0);
 await page.getByLabel('搜索账户').fill('');
 await page.screenshot({path:'accounts-ui.png',fullPage:true});
 await page.locator('.nav a[href="#/bom"]').click();
 await page.getByLabel('父件号或名称').fill('演示');
 await page.getByRole('button',{name:'查找件号'}).click();
 await page.getByRole('link',{name:'打开 BOM'}).click();
 await page.getByRole('button',{name:'新建适用性规则',exact:true}).click();
 await d.getByLabel('编号',{exact:true}).fill('MODEL-A');
 await d.getByLabel('名称',{exact:true}).fill('A 构型');
 await d.getByLabel('属性名',{exact:true}).fill('model');
 await d.getByLabel('属性值',{exact:true}).fill('A');
 await d.getByRole('button',{name:'保存',exact:true}).click();
 await d.waitFor({state:'hidden'});
 await page.getByRole('button',{name:'新建构型',exact:true}).click();
 await d.getByLabel('编号',{exact:true}).fill('CTX-A');
 await d.getByLabel('名称',{exact:true}).fill('A 构型定义');
 await d.getByLabel('属性名',{exact:true}).fill('model');
 await d.getByLabel('属性值',{exact:true}).fill('A');
 await d.getByRole('button',{name:'保存',exact:true}).click();
 await d.waitFor({state:'hidden'});
 await page.getByRole('button',{name:'编辑',exact:true}).click();
 await d.getByLabel('数量',{exact:true}).fill('3');
 await d.getByLabel('适用性规则').selectOption('MODEL-A');
 await d.getByRole('button',{name:'保存',exact:true}).click();
 await d.waitFor({state:'hidden'});
 assert.equal(lines[0].quantity,3); assert.equal(lines[0].applicability_rule_code,'MODEL-A');
 await page.getByLabel('构型上下文').selectOption('CTX-A');
 await page.getByRole('button',{name:'解析构型',exact:true}).click();
 await page.getByText('解析通过，共 1 行；排除 0 行。').waitFor();
 const downloadPromise=page.waitForEvent('download');
 await page.getByRole('button',{name:'下载模板'}).click();
 assert.equal((await downloadPromise).suggestedFilename(),'bom_template.csv');
 await page.screenshot({path:'bom-ui.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile page overflows horizontally');
 await page.getByRole('button',{name:'删除子项'}).click();
 await d.getByLabel('原因（记入审计）').fill('删除测试');
 await d.getByRole('button',{name:'保存',exact:true}).click();
 await d.waitFor({state:'hidden'}); assert.equal(lines.length,0);
 // Exercise approval target navigation and baseline state/permission rendering.
 const reviewChecks=await page.evaluate(async()=>{
  const {api}=await import('/js/api.js');
  const {approvals,approvalDetail}=await import('/js/views5.js');
  const {baselines,baselineDetail}=await import('/js/views4.js');
  const original=api.get;
  const records=[
   {id:'cancel',status:'CANCELLED',baseline_sequence:5},
   {id:'old',status:'SUPERSEDED',baseline_sequence:1},
   {id:'review',status:'IN_REVIEW',baseline_sequence:4},
   {id:'new',status:'RELEASED',baseline_sequence:3},
   {id:'middle',status:'SUPERSEDED',baseline_sequence:2},
  ];
  const ctx={can:()=>false};
  try {
   api.get=async path=>path.endsWith('/validate')?{errors:[],warnings:[]}:path.startsWith('/parts/')?records:{status:'CANCELLED',items:[]};
   const list=await baselines(ctx,{},'PN');
   const comparison=[...list.querySelectorAll('a')].find(a=>a.textContent==='比较最近两版');
   if(comparison?.getAttribute('href')!=='#/baseline-compare/middle/new') throw Error('Baseline comparison selected unpublished or wrong revisions');
   records.splice(0,records.length,{id:'only',status:'RELEASED',baseline_sequence:1},{id:'cancel',status:'CANCELLED',baseline_sequence:2});
   if((await baselines(ctx,{},'PN')).textContent.includes('比较最近两版')) throw Error('Comparison should require two published baselines');
   const cancelled=await baselineDetail(ctx,{},'cancel');
   if(!cancelled.textContent.includes('该基线已取消')||cancelled.textContent.includes('该基线已发布')) throw Error('Cancelled state misrepresented');
   for(const [type,target] of [['BASIC_DRAWING_FAMILY','family'],['FILE_REVISION','revision'],['DESIGN_BASELINE','baseline']]) {
    const r={id:'request',object_type:type,object_id:'target',steps:[],status:'PENDING'};
    api.get=async path=>path==='/approvals/inbox'?[r]:path==='/approvals/mine'?[]:r;
    for(const view of [await approvals(ctx),await approvalDetail(ctx,{},'request')]) {
     const a=[...view.querySelectorAll('a')].find(a=>a.textContent==='打开对象办理');
     if(a?.getAttribute('href')!=='#/'+target+'/target') throw Error('Approval target missing: '+type);
    }
   }
   for(const allowed of [false,true]) {
    api.get=async path=>path.endsWith('/validate')?{errors:[],warnings:[]}:{status:'IN_REVIEW',items:[]};
    const view=await baselineDetail({can:()=>allowed},{},'bl');
    if(view.textContent.includes('批准发布')!==allowed) throw Error('Baseline approval permission rendering');
   }
   return true;
  } finally {api.get=original;}
 });
 assert(reviewChecks);
 console.log('PASS: approval target links, published baseline selection, cancellation message, approval permission rendering.');
 assert.deepEqual(errors,[]);
 assert(requests.some(r=>r.p==='/admin/users/new/password-reset'));
 console.log('PASS: login, account create/edit/filter/disable/enable/reset/unlock/error, BOM search/rule/context/edit/resolve/delete, authenticated download, mobile layout.');
 await browser.close();
 await new Promise(resolve=>server.close(resolve));
})().catch(e=>{console.error(e);process.exit(1)});
