"""Full-scale import checks against disposable CI PostgreSQL, never production."""
import hashlib, importlib.util, io, json, sys, time, urllib.request, urllib.error, zipfile
from pathlib import Path
from unittest.mock import patch
from app import storage
from app.db import transaction, fetch_all, scalar
from app.services import data_backups, scale_demo, ima_demo, bom
spec=importlib.util.spec_from_file_location('smoke',Path(__file__).with_name('full-functional-smoke.py'))
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
creds=json.loads(Path(sys.argv[1]).read_text())
admin,_=smoke.login(creds['admin_username'],creds['admin_password'])
engineer,_=smoke.login(creds['username'],creds['password'])
uid=admin.get('/auth/me')['id']
with transaction() as conn:session_id=str(scalar(conn,'SELECT id FROM user_session WHERE user_id=%s ORDER BY issued_at DESC LIMIT 1',(uid,)))
actor=dict(user_id=uid,username=creds['admin_username'],session_id=session_id)
out=Path('scale-evidence');out.mkdir(exist_ok=True)
package=data_backups.create_scale_package();path=out/'UG-DCMS-10Projects-Data-Backup-v1.zip';path.write_bytes(package)
data=data_backups.validate_package(package)
assert [len(data[k]) for k in ['families','parts','externals','software','projects']]==[1000,10000,5000,500,10]
assert len({p[0] for p in data['parts']})==10000
assert data_backups.validate_package(data_backups.create_package())['dataset_code']==ima_demo.CODE

def upload(client=admin,content=package,expected=200):
    b='----ScaleBackup243'
    body=(f'--{b}\r\nContent-Disposition: form-data; name="confirmation"\r\n\r\n导入模拟数据\r\n--{b}\r\nContent-Disposition: form-data; name="file"; filename="scale.zip"\r\nContent-Type: application/zip\r\n\r\n').encode()+content+f'\r\n--{b}--\r\n'.encode()
    req=urllib.request.Request(smoke.BASE+'/system/data-backups/import',data=body,method='POST',headers={'Authorization':'Bearer '+client.token,'Content-Type':'multipart/form-data; boundary='+b})
    try:
        with urllib.request.urlopen(req,timeout=2700) as r:
            assert r.status==expected
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode();assert exc.code==expected,(exc.code,detail)
        return json.loads(detail)

def snapshot(conn,exclude=None):
    result={}
    for t in ['basic_drawing_family','part_number','design_object','design_file','file_revision','design_baseline','external_part','software_object']:
        where='WHERE created_by IS DISTINCT FROM %s::uuid' if exclude else ''
        result[t]=scalar(conn,f"SELECT md5(string_agg(row_to_json(t)::text,'' ORDER BY id)) FROM {t} t {where}",(exclude,) if exclude else None)
    return result

def disk():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in storage._root().rglob('*') if p.is_file()}
with transaction() as conn:before=snapshot(conn)
before_files=disk()
assert upload(engineer,expected=403)
with zipfile.ZipFile(io.BytesIO(package)) as z:entries={n:z.read(n) for n in z.namelist()}
tampered=json.loads(entries['dataset.json']);tampered['parts'].pop();entries['dataset.json']=json.dumps(tampered).encode()
mf=json.loads(entries['manifest.json']);mf['payload_sha256']=hashlib.sha256(entries['dataset.json']).hexdigest();entries['manifest.json']=json.dumps(mf).encode()
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
    for n,v in entries.items():z.writestr(n,v)
assert upload(content=buf.getvalue(),expected=400)
original=ima_demo._document
def injected(*a,**kw):
    original(*a,**kw)
    raise RuntimeError('injected scale rollback')
try:
    with transaction() as conn,patch.object(ima_demo,'_document',injected):scale_demo.import_dataset(conn,actor,data)
    raise AssertionError('injected failure did not fail')
except RuntimeError as exc:assert str(exc)=='injected scale rollback'
with transaction() as conn:
    assert snapshot(conn)==before
    assert not scale_demo.status(conn)['imported']
assert disk()==before_files
with transaction() as conn:
    conn.execute('SELECT pg_advisory_xact_lock(811038)')
    assert '正在导入' in upload(expected=400)['error']['message']
with transaction() as conn:
    with conn.transaction(force_rollback=True):
        conn.execute("INSERT INTO namespace(code,name_cn,name_en,kind) VALUES(%s,'existing','existing','OTHER')",(scale_demo.CODE,))
        try:scale_demo.import_dataset(conn,actor,data);raise AssertionError('collision accepted')
        except ValueError as exc:assert '前缀' in str(exc)
print('PASS: trusted payload, permissions, prefix collision, concurrency and rollback with file cleanup',flush=True)
started=time.monotonic();result=upload();elapsed=time.monotonic()-started
assert result['imported'];receipt=result['receipt'];m=receipt['manifest'];author=m['actors'][0]
with transaction() as conn:
    tables={'families':'basic_drawing_family','parts':'part_number','externals':'external_part','software':'software_object',
            'projects':'configuration_context','baselines':'design_baseline','documents':'design_file','bom_lines':'bom_line',
            'project_controls':'external_part_project_control','hardware_links':'software_hardware_compatibility'}
    counts={k:scalar(conn,f'SELECT count(*) FROM {t} WHERE created_by=%s',(author,)) for k,t in tables.items()}
    expected=dict(families=1000,parts=10000,externals=5000,software=500,projects=10,baselines=10000,documents=1010,bom_lines=15008,project_controls=5009,hardware_links=1010)
    assert counts==m['counts']==expected,(counts,m['counts'])
    assert snapshot(conn,author)==before
    assert scalar(conn,"SELECT count(*) FROM part_number WHERE created_by=%s AND (lifecycle_status!='RELEASED' OR current_baseline_id IS NULL)",(author,))==0
    assert scalar(conn,"SELECT count(*) FROM design_baseline WHERE created_by=%s AND (status!='RELEASED' OR NOT is_current)",(author,))==0
    assert scalar(conn,"SELECT count(*) FROM basic_drawing_family WHERE created_by=%s AND basic_drawing_number !~ '^UG1[0-9]{5}$'",(author,))==0
    assert not fetch_all(conn,'SELECT basic_drawing_family_id FROM part_number WHERE created_by=%s GROUP BY basic_drawing_family_id HAVING count(*)<>10',(author,))
    assert scalar(conn,'SELECT count(*) FROM app_user WHERE id=ANY(%s::uuid[]) AND is_active',(m['actors'],))==0
    assert scalar(conn,"SELECT count(*) FROM approval_request WHERE requester_id=%s AND (status!='APPROVED' OR title NOT LIKE '【模拟数据】%%')",(author,))==0
    roots=[]
    for project in m['projects']:
        oid=scalar(conn,'SELECT id FROM design_object WHERE object_code=%s',(project['root_part_number'],))
        lines=bom.expand(conn,str(oid));assert len(lines)==(1499 if project['index']==1 else 1501),(project,len(lines))
        assert max(x['level'] for x in lines)>=2
        roots.append(project['root_part_number'])
        assert scalar(conn,'SELECT count(*) FROM design_baseline WHERE project_code=%s',(project['code'],))==1000
        assert scalar(conn,"SELECT count(*) FROM external_part_project_control WHERE project_code=%s AND status='APPROVED'",(project['code'],))==(500 if project['index']==1 else 501)
        # All own-project leaves plus the cross-project common P/N are reachable.
        assigned={r['full_part_number'] for r in fetch_all(conn,'SELECT pn.full_part_number FROM part_number pn JOIN design_baseline b ON b.part_number_id=pn.id WHERE b.project_code=%s',(project['code'],))}
        assert assigned <= {x['child_object_code'] for x in lines}|{project['root_part_number']}
    assert len(set(roots))==10
    requests=scalar(conn,'SELECT count(*) FROM approval_request WHERE requester_id=%s',(author,));assert requests==22519,requests
    packages=fetch_all(conn,'SELECT package_storage_key,hash_sha256,status FROM software_version WHERE created_by=%s',(author,));assert len(packages)==500
    for row in packages:
        assert row['status']=='RELEASED'
        assert hashlib.sha256(storage.read(row['package_storage_key'])).hexdigest()==row['hash_sha256']
after_files=disk();assert len(after_files)-len(before_files)==1510
assert all(after_files.get(k)==v for k,v in before_files.items())
assert upload()['receipt']['imported_at']==receipt['imported_at']
with transaction() as conn:assert snapshot(conn,author)==before
assert disk()==after_files
print('PASS: exact persisted counts, all ten project BOMs, released baselines, attachment hashes, preservation and idempotency; seconds',round(elapsed,2),flush=True)
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1500,'height':1100})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.add_init_script("sessionStorage.setItem('dcms.token',"+json.dumps(admin.token)+")")
    page.goto('http://127.0.0.1:8080/#/backup')
    panel=page.locator('[data-scale-import]');expect(panel).to_be_visible()
    expect(panel.get_by_text('1000个设计族 · 10000个内部件号 · 500个软件 · 5000个外部件号 · 10个项目',exact=True)).to_be_visible()
    assert panel.get_by_role('link',name='查看 BOM',exact=True).count()==10
    page.screenshot(path=str(out/'ten-projects-import.png'),full_page=True)
    panel.get_by_role('link',name='查看基线',exact=True).first.click()
    expect(page.get_by_text('已发布',exact=True).first).to_be_visible()
    page.screenshot(path=str(out/'project-baseline.png'),full_page=True)
    page.goto('http://127.0.0.1:8080/#/backup')
    page.get_by_label('数据备份包',exact=True).set_input_files(str(path));page.get_by_label('数据导入确认文字',exact=True).fill('导入模拟数据')
    with page.expect_response(lambda r:r.url.endswith('/system/data-backups/import') and r.request.method=='POST') as response:page.get_by_role('button',name='校验并导入数据',exact=True).click()
    assert response.value.status==200
    expect(page.locator('[data-scale-import]')).to_be_visible()
    assert not errors,errors
    browser.close()
(out/'result.json').write_text(json.dumps(dict(passed=True,counts=counts,approvals=requests,import_seconds=elapsed,package_sha256=hashlib.sha256(package).hexdigest(),projects=m['projects']),ensure_ascii=False,indent=2))
print('PASS: real browser import entry, ten project links and baseline navigation',flush=True)
