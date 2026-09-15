"""Real PostgreSQL + HTTP + browser coverage of every applicant workflow type."""
import hashlib, importlib.util, io, json, os, sys, time, zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from app.db import transaction,fetch_one,scalar
from app.services import drafts
from playwright.sync_api import sync_playwright,expect

spec=importlib.util.spec_from_file_location('smoke',Path(__file__).with_name('full-functional-smoke.py'))
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
original_call=smoke.Client.call
smoke.Client.call=lambda self,method,path,*a,**kw:original_call(self,method,quote(path,safe='/?=&:%'),*a,**kw)
creds=json.loads(Path(sys.argv[1]).read_text())
admin,_=smoke.login(creds['admin_username'],creds['admin_password'])
engineer,_=smoke.login(creds['username'],creds['password'])
admin_id=admin.get('/auth/me')['id']; engineer_id=engineer.get('/auth/me')['id']
stamp=str(time.time_ns())[-12:];serial=0

def unique():
    global serial
    serial+=1
    return 'APPLICANT-'+stamp+'-'+str(serial)

def software_bytes(label):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:z.writestr('模拟说明.txt',label)
    return buffer.getvalue()

with transaction() as conn:
    ima=fetch_one(conn,"SELECT manifest FROM simulation_dataset WHERE dataset_code='SIM-IMA-V1'")['manifest']
root_pn=ima['top_part_number']
pcs=engineer.get('/dictionary/physical-class'); terms=engineer.get('/dictionary/core-term');functions=engineer.get('/dictionary/function-item')
pc=next(x for x in pcs if x['primary_class_code']=='T1'); term=next(x for x in terms if x['primary_class_code']=='T1')
external=engineer.post('/external-parts',dict(namespace_code='AIRBUS',external_part_number=unique(),name_cn='【模拟数据】审批测试外部件',external_class_code='T2'))
ext_code=external['object_code']
ts=engineer.post('/external-parts/'+ext_code+'/states',{'supplier_revision':'INITIAL'})
engineer.post('/external-states/'+ts['id']+'/submit',{'approver_user_id':admin_id});admin.post('/external-states/'+ts['id']+'/accept')

def create(kind):
    label=unique()
    if kind=='BASIC_DRAWING_FAMILY':
        return engineer.post('/families',dict(primary_class_code='T1',physical_class_id=pc['id'],core_term_id=term['id'],object_level_code='PART',primary_function_id=functions[0]['id'],family_definition='【模拟数据】'+label,allowed_variation='尺寸',excluded_variation='原理',new_family_reason='独立审批测试'))['id']
    if kind=='FILE_REVISION':
        engineer.post('/files',dict(file_number=label,file_type_code='DWG',title_cn='【模拟数据】审批图纸'))
        rev=engineer.post('/files/'+label+'/revisions',{'change_summary':'初稿'})
        engineer.upload('/revisions/'+rev['id']+'/attachments','drawing.txt','【模拟数据】图纸'.encode(),role='PRIMARY_NATIVE')
        return rev['id']
    if kind=='DESIGN_BASELINE':
        return engineer.post('/parts/'+root_pn+'/baselines',dict(reason='【模拟数据】审批基线',scope_note='测试范围',project_code='SIM-IMA-V1',copy_from_current=True))['id']
    if kind=='EXTERNAL_TECHNICAL_STATE':
        return engineer.post('/external-parts/'+ext_code+'/states',{'supplier_revision':label})['id']
    if kind=='EXTERNAL_PROJECT_CONTROL':
        return engineer.post('/external-parts/'+ext_code+'/project-controls',dict(project_code=label,applicability='【模拟数据】测试范围',evaluation_basis='供应规格与试验记录'))['id']
    engineer.post('/software',dict(software_number=label,name_cn='【模拟数据】审批软件',software_type='FIRMWARE'))
    v=engineer.upload_software('/software/'+label+'/versions/package','initial.zip',software_bytes('初始模拟包'),'1.0.0','00')
    engineer.post('/software-versions/'+v['id']+'/hardware',dict(hardware_object_code=root_pn,hardware_version='R00'))
    return v['id']

fields={'BASIC_DRAWING_FAMILY':('family_definition','设计族定义'),'FILE_REVISION':('change_summary','更改说明'),
 'DESIGN_BASELINE':('reason','建立理由'),'EXTERNAL_TECHNICAL_STATE':('notes','说明'),
 'EXTERNAL_PROJECT_CONTROL':('evaluation_basis','评价依据'),'SOFTWARE_VERSION':('notes','说明')}
release_paths={'BASIC_DRAWING_FAMILY':('/families/','/approve'),'FILE_REVISION':('/revisions/','/release'),
 'DESIGN_BASELINE':('/baselines/','/release'),'EXTERNAL_TECHNICAL_STATE':('/external-states/','/accept'),
 'EXTERNAL_PROJECT_CONTROL':('/external-project-controls/','/approve'),'SOFTWARE_VERSION':('/software-versions/','/release')}

def submit(kind,oid):return engineer.post('/drafts/'+kind+'/'+oid+'/submit',{'approver_user_id':admin_id})
def decide(kind,oid,client=admin):
    a,b=release_paths[kind];return client.post(a+oid+b)

out=Path('applicant-evidence');out.mkdir(exist_ok=True)
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1500,'height':1100});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('dialog',lambda d:d.accept('【模拟数据】撤回或取消测试') if d.type=='prompt' else d.accept())
    page.add_init_script("sessionStorage.setItem('dcms.token',"+json.dumps(engineer.token)+")")
    for kind in drafts.TYPES:
        field,label=fields[kind]
        # Delete a never-submitted draft through the actual UI.
        oid=create(kind);path='/drafts/'+kind+'/'+oid
        admin.call('PATCH',path,{'values':{field:'越权修改'}},expected=(403,))
        admin.call('DELETE',path,expected=(403,))
        admin.call('POST',path+'/submit',{'approver_user_id':engineer_id},expected=(403,))
        page.goto('http://127.0.0.1:8080/#/draft/'+kind+'/'+oid)
        expect(page.get_by_role('button',name='删除草稿',exact=True)).to_be_visible()
        with page.expect_response(lambda r:r.request.method=='DELETE' and r.url.endswith(path)) as deletion:
            page.get_by_role('button',name='删除草稿',exact=True).click()
        assert deletion.value.status==200,deletion.value.text()
        engineer.call('GET',path,expected=(404,))
        oid=create(kind);path='/drafts/'+kind+'/'+oid
        page.goto('http://127.0.0.1:8080/#/draft/'+kind+'/'+oid)
        page.get_by_label(label,exact=True).fill('【模拟数据】浏览器编辑-'+kind)
        with page.expect_response(lambda r:r.request.method=='PATCH' and r.url.endswith(path)) as edit:
            page.get_by_role('button',name='保存修改',exact=True).click()
        assert edit.value.status==200,edit.value.text()
        # Submit directly after another edit; the button must save that edit too.
        page.get_by_label(label,exact=True).fill('【模拟数据】提交前最后修改-'+kind)
        page.get_by_role('button',name='保存并提交审批',exact=True).click()
        dialog=page.get_by_role('dialog');dialog.locator('select').select_option(admin_id)
        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith(path+'/submit')) as response:
            dialog.get_by_role('button',name='确认提交',exact=True).click()
        assert response.value.status==200,response.value.text()
        req=response.value.json();rid=req['id'];number=req['request_number']
        assert engineer.get(path)['values'][field]=='【模拟数据】提交前最后修改-'+kind
        engineer.call('PATCH',path,{'values':{field:'审批中修改'}},expected=(400,))
        engineer.call('DELETE',path,expected=(400,))
        engineer.call('POST',path+'/submit',{'approver_user_id':admin_id},expected=(400,))
        admin.call('POST','/approvals/'+rid+'/withdraw?reason=foreign',expected=(403,))
        page.goto('http://127.0.0.1:8080/#/approval/'+rid)
        with page.expect_response(lambda r:r.url.split('?')[0].endswith('/withdraw')) as withdrawn:
            page.get_by_role('button',name='撤回修改',exact=True).click()
        assert withdrawn.value.status==200,withdrawn.value.text()
        assert engineer.get(path)['can_edit']
        a,b=release_paths[kind];admin.call('POST',a+oid+b,expected=(400,))
        req=submit(kind,oid);assert req['id']==rid and req['request_number']==number and req['submission_round']==2
        admin.post('/approvals/'+rid+'/reject?reason=本轮需修订')
        page.goto('http://127.0.0.1:8080/#/approval/'+rid)
        page.get_by_role('link',name='编辑后重新提交',exact=True).click()
        page.get_by_label(label,exact=True).fill('【模拟数据】驳回后修改-'+kind)
        with page.expect_response(lambda r:r.request.method=='PATCH' and r.url.endswith(path)):
            page.get_by_role('button',name='保存修改',exact=True).click()
        req=submit(kind,oid);assert req['id']==rid and req['submission_round']==3
        engineer.post('/approvals/'+rid+'/cancel?reason=取消后修订')
        req=submit(kind,oid);assert req['id']==rid and req['submission_round']==4
        admin.post('/approvals/'+rid+'/return?reason=退回补充资料')
        assert engineer.get(path)['can_edit']
        req=submit(kind,oid);assert req['id']==rid and req['submission_round']==5
        detail=engineer.get('/approvals/'+rid)
        assert [h['snapshot']['status'] for h in detail['history']]==['CANCELLED','REJECTED','CANCELLED','RETURNED']
        assert detail['history'][1]['snapshot']['steps'][0]['comments']=='本轮需修订'
        assert detail['history'][0]['snapshot']['payload']['submitted_object']['object'][field]=='【模拟数据】提交前最后修改-'+kind
        assert detail['payload']['submitted_object']['object'][field]=='【模拟数据】驳回后修改-'+kind
        with transaction() as conn:
            assert scalar(conn,'SELECT count(*) FROM approval_step WHERE approval_request_id=%s',(rid,))==5
        # A final decision and withdrawal race must never both succeed.
        if kind=='SOFTWARE_VERSION':
            def race(client,url):
                return client.call('POST',url,expected=(200,400))
            with ThreadPoolExecutor(max_workers=2) as pool:
                x=pool.submit(race,engineer,'/approvals/'+rid+'/withdraw?reason=race')
                y=pool.submit(race,admin,a+oid+b)
                outcomes=[x.result(),y.result()]
            assert sum('error' not in x for x in outcomes)==1,outcomes
            if engineer.get('/approvals/'+rid)['status']=='CANCELLED':submit(kind,oid);decide(kind,oid)
        else:decide(kind,oid)
        assert engineer.get('/approvals/'+rid)['status']=='APPROVED'
        engineer.call('POST','/approvals/'+rid+'/withdraw?reason=too-late',expected=(400,))
        engineer.call('POST','/approvals/'+rid+'/cancel?reason=too-late',expected=(400,))
        engineer.call('PATCH',path,{'values':{field:'批准后修改'}},expected=(400,))
        engineer.call('DELETE',path,expected=(400,))
        page.goto('http://127.0.0.1:8080/#/approval/'+rid)
        expect(page.get_by_text('已批准',exact=True).first).to_be_visible()
        expect(page.get_by_role('heading',name='历次提交与审批',exact=True)).to_be_visible()
        page.screenshot(path=str(out/(kind+'.png')),full_page=True)
        print('PASS applicant edit/delete/withdraw/reject/cancel/resubmit/history/release/ownership: '+kind,flush=True)
    # Software package replacement and deletion with existing hardware children.
    oid=create('SOFTWARE_VERSION');path='/drafts/SOFTWARE_VERSION/'+oid
    payload=software_bytes('【模拟数据】替换软件内容')
    engineer.upload(path+'/package','replacement.zip',payload)
    v=engineer.get(path)['values'];assert v['hash_sha256']==hashlib.sha256(payload).hexdigest()
    assert engineer.download('/software-versions/'+oid+'/package/download')==payload
    retained=submit('SOFTWARE_VERSION',oid)
    engineer.post('/approvals/'+retained['id']+'/withdraw?reason=删除前撤回')
    engineer.delete(path)
    assert engineer.get('/approvals/'+retained['id'])['object_deleted']
    page.goto('http://127.0.0.1:8080/#/approvals')
    expect(page.get_by_role('heading',name='我的待提交草稿',exact=True)).to_be_visible()
    page.screenshot(path=str(out/'my-applications.png'),full_page=True)
    assert not errors,errors
    browser.close()
with transaction() as conn:
    conn.execute('SAVEPOINT step_guard')
    try:conn.execute("DELETE FROM approval_step WHERE submission_round=1")
    except Exception:conn.execute('ROLLBACK TO SAVEPOINT step_guard')
    else:raise AssertionError('original approval steps can be deleted')
    conn.execute('SAVEPOINT history_guard')
    try:conn.execute("UPDATE approval_round_history SET snapshot='{}'::jsonb")
    except Exception:conn.execute('ROLLBACK TO SAVEPOINT history_guard')
    else:raise AssertionError('approval history is mutable')
print('PASS applicant approval/withdraw concurrency, software replacement and immutable round history',flush=True)
(out/'result.json').write_text(json.dumps({'passed':True,'types':list(drafts.TYPES)},ensure_ascii=False,indent=2))
