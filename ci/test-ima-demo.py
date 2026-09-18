"""IMA domain, rollback, concurrency, preservation and real-browser checks.

Runs only against the disposable CI database after full-functional-smoke.
Never runs automatically on an installed user's database.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

from app import storage
from app.db import transaction, fetch_all, fetch_one, scalar
from app.services import ima_demo, applicability, data_backups
import io, zipfile, urllib.request, urllib.error

spec=importlib.util.spec_from_file_location('smoke',Path(__file__).with_name('full-functional-smoke.py'))
smoke=importlib.util.module_from_spec(spec); spec.loader.exec_module(smoke)
creds=json.loads(Path(sys.argv[1]).read_text())
admin,_=smoke.login(creds['admin_username'],creds['admin_password'])
engineer,_=smoke.login(creds['username'],creds['password'])
uid=admin.get('/auth/me')['id']
with transaction() as conn:
    session_id=str(scalar(conn,'SELECT id FROM user_session WHERE user_id=%s ORDER BY issued_at DESC LIMIT 1',(uid,)))
actor={'user_id':uid,'username':creds['admin_username'],'session_id':session_id}

def snapshot(conn):
    out={}
    for r in fetch_all(conn,"SELECT tablename FROM pg_tables WHERE schemaname='public'"):
        name=r['tablename']
        if name in ('user_session','recent_access'): continue
        out[name]={json.dumps(row,sort_keys=True,default=str) for row in fetch_all(conn,f'SELECT * FROM "{name}"')}
    return out

def disk():
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in storage._root().rglob('*') if p.is_file()}

package=data_backups.create_package()
package_path=Path('ima-evidence/UG-DCMS-IMA-Data-Backup-v1.zip')
package_path.parent.mkdir(exist_ok=True);package_path.write_bytes(package)

def upload(client=admin, content=package, confirmation='导入模拟数据', expected=200):
    boundary='----DataBackupTest239'
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="confirmation"\r\n\r\n{confirmation}\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="ima.zip"\r\n'
          'Content-Type: application/zip\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
    headers={'Content-Type':f'multipart/form-data; boundary={boundary}'}
    if client.token: headers['Authorization']='Bearer '+client.token
    req=urllib.request.Request(smoke.BASE+'/system/data-backups/import',data=body,headers=headers,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=120) as response:
            assert response.status==expected
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode()
        assert exc.code==expected,(exc.code,detail)
        return json.loads(detail)

assert not admin.get('/system/backups')['data_import']['imported']
smoke.Client().call('GET','/simulation/ima',expected=(404,))
assert upload(engineer,expected=403)
assert upload(smoke.Client(),expected=401)
assert upload(confirmation='恢复UG-DCMS',expected=400)
assert upload(content=b'broken zip',expected=400)
assert upload(content=b'x'*(data_backups.MAX_BYTES+1),expected=400)
# Even a package with a matching, attacker-recalculated manifest is rejected.
with zipfile.ZipFile(io.BytesIO(package)) as z: entries={n:z.read(n) for n in z.namelist()}
manifest=json.loads(entries['manifest.json']);payload=json.loads(entries['dataset.json'])
payload['parts'][0][1]='未经支持的修改'
entries['dataset.json']=json.dumps(payload).encode()
manifest['payload_sha256']=hashlib.sha256(entries['dataset.json']).hexdigest()
entries['manifest.json']=json.dumps(manifest).encode()
buffer=io.BytesIO()
with zipfile.ZipFile(buffer,'w') as z:
    for name,value in entries.items():z.writestr(name,value)
assert upload(content=buffer.getvalue(),expected=400)
# Full restore format and path-bearing archives cannot reach data import.
for entries in ({'manifest.json':b'{}','database.dump':b'PGDMP'},
                {'manifest.json':b'{}','dataset.json':b'{}','../file':b'x'}):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for name,value in entries.items():z.writestr(name,value)
    assert upload(content=buffer.getvalue(),expected=400)
with transaction() as conn:
    before=snapshot(conn)
before_files=disk()
real_save=storage.save
saved=0
def fail_save(key,content):
    global saved
    saved+=1
    if saved==4: raise OSError('injected disk failure')
    return real_save(key,content)
try:
    with transaction() as conn, patch.object(storage,'save',side_effect=fail_save):
        ima_demo.import_dataset(conn,actor)
except OSError as exc:
    assert str(exc)=='injected disk failure'
else: raise AssertionError('failure injection did not fire')
with transaction() as conn:
    assert snapshot(conn)==before, 'partial DB writes after rollback'
assert disk()==before_files, 'partial attachment writes after rollback'
print('PASS IMA rollback restores database and attachment bytes',flush=True)

# Collision fails without adopting or updating an existing namespace.
with transaction() as conn:
    conn.execute('SAVEPOINT fixture_collision')
    conn.execute("INSERT INTO namespace(code,name_cn,kind) VALUES(%s,'真实来源保持不变','OTHER')",(ima_demo.CODE,))
    collision=snapshot(conn)
    try: ima_demo.import_dataset(conn,actor)
    except ValueError as exc: assert '保护已有数据' in str(exc)
    else: raise AssertionError('identity collision accepted')
    assert snapshot(conn)==collision
    conn.execute('ROLLBACK TO SAVEPOINT fixture_collision')

# An in-progress import is reported promptly from a second connection.
with transaction() as conn:
    conn.execute('SELECT pg_advisory_xact_lock(811038)')
    conflict=upload(expected=400)
    assert '正在导入' in conflict['error']['message']
print('PASS IMA permissions, fixed-identity collision and concurrency',flush=True)

from playwright.sync_api import sync_playwright, expect
evidence=Path('ima-evidence'); evidence.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'],
        **({'executable_path':os.environ['DCMS_BROWSER_PATH']} if os.environ.get('DCMS_BROWSER_PATH') else {}))
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    # Inject the session before app boot to avoid an aborted /auth/me clearing it.
    page.add_init_script("sessionStorage.setItem('dcms.token',"+json.dumps(admin.token)+")")
    page.goto('http://127.0.0.1:8080/#/backup')
    try:
        expect(page.get_by_role('heading',name='系统备份与恢复',exact=True)).to_be_visible()
    except BaseException:
        page.screenshot(path=str(evidence/'ima-open-failure.png'),full_page=True)
        print({'page_errors':errors,'body':page.locator('body').inner_text()},flush=True)
        raise
    assert page.get_by_role('link',name='IMA模拟数据',exact=True).count()==0
    page.get_by_label('数据备份包',exact=True).set_input_files(str(package_path))
    page.get_by_label('数据导入确认文字',exact=True).fill('导入模拟数据')
    with page.expect_response(lambda r:r.url.endswith('/system/data-backups/import') and r.request.method=='POST',timeout=120000) as response:
        page.get_by_role('button',name='校验并导入数据',exact=True).click()
    assert response.value.status==202,response.value.text()
    try:
        expect(page.get_by_role('link',name='打开共用 BOM',exact=True)).to_be_visible(timeout=10000)
    except BaseException:
        page.screenshot(path=str(evidence/'ima-failure.png'),full_page=True)
        print(page.locator('main').inner_text(),flush=True)
        raise
    page.screenshot(path=str(evidence/'ima-import.png'),full_page=True)
    result=admin.get('/system/backups')['data_import'];m=result['receipt']['manifest']
    page.get_by_role('link',name='打开共用 BOM',exact=True).click()
    for variant in ('A','B'):
        page.get_by_label('构型上下文',exact=True).select_option('SIM-IMA-V1-'+variant)
        with page.expect_response(lambda r:r.url.endswith('/resolve') and r.request.method=='POST') as resolved_response:
            page.get_by_role('button',name='解析构型',exact=True).click()
        resolved=resolved_response.value.json()
        assert resolved['passed']
        expect(page.get_by_text('解析通过，共 '+str(resolved['line_count'])+' 行；排除 '+str(len(resolved['excluded']))+' 行。',exact=True)).to_be_visible()
    page.screenshot(path=str(evidence/'ima-bom-config-b.png'),full_page=True)
    page.goto('http://127.0.0.1:8080/#/backup')
    page.get_by_role('link',name='查看顶层设计基线',exact=True).click()
    expect(page.get_by_text('【模拟数据】',exact=False).first).to_be_visible()
    page.screenshot(path=str(evidence/'ima-baseline.png'),full_page=True)
    page.goto('http://127.0.0.1:8080/#/family/'+m['parts']['IMA']['family_id'])
    expect(page.locator('.tb-name')).to_contain_text('【模拟数据】')
    page.goto('http://127.0.0.1:8080/#/backup')
    page.get_by_role('link',name='查看模拟设计资料',exact=True).click()
    expect(page.get_by_role('heading',name='设计资料清单',exact=True)).to_be_visible()
    expect(page.get_by_text('SIM-IMA-V1-MANUAL',exact=True).first).to_be_visible()
    page.screenshot(path=str(evidence/'ima-design-materials.png'),full_page=True)
    # Engineering users view imported records through the normal business pages.
    page.close()
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.add_init_script("sessionStorage.setItem('dcms.token',"+json.dumps(engineer.token)+")")
    page.goto('http://127.0.0.1:8080/#/object/'+m['top_part_number'])
    expect(page.get_by_text('【模拟数据】IMA综合模块化航电设备',exact=False).first).to_be_visible()
    page.goto('http://127.0.0.1:8080/#/backup')
    expect(page.get_by_text('无系统备份权限',exact=True)).to_be_visible()
    assert not errors,errors
    browser.close()
print('PASS IMA real browser import, navigation, download and read-only access',flush=True)

assert m['counts']=={'parts':19,'externals':9,'bom_lines':45,'software':3,'documents':23,'baselines':19,'configurations':2},m['counts']
with transaction() as conn:
    after=snapshot(conn)
    for table,rows in before.items():
        assert rows<=after[table],f'existing rows changed: {table}'
    for path,digest in before_files.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
    assert scalar(conn,'SELECT count(*) FROM app_user WHERE id=ANY(%s::uuid[]) AND is_active',(m['actors'],))==0
    assert scalar(conn,'SELECT count(*) FROM user_session WHERE user_id=ANY(%s::uuid[])',(m['actors'],))==0
    requests=fetch_all(conn,'SELECT * FROM approval_request WHERE requester_id=ANY(%s::uuid[])',(m['actors'],))
    assert len(requests)==82,len(requests)
    assert all(r['status']=='APPROVED' and r['title'].startswith('【模拟数据】') for r in requests)
    assert not scalar(conn,"""SELECT count(*) FROM approval_step s JOIN approval_request r ON r.id=s.approval_request_id
        WHERE r.requester_id=ANY(%s::uuid[]) AND (s.decided_by=r.requester_id OR s.decision<>'APPROVED')""",(m['actors'],))
    for part in m['parts'].values():
        assert re.fullmatch(r'UG[123]\d{5}-001',part['part_number'])
        pn=fetch_one(conn,'SELECT lifecycle_status,current_baseline_id FROM part_number WHERE id=%s',(part['id'],))
        assert pn['lifecycle_status']=='RELEASED' and pn['current_baseline_id']
    config_lines={}
    for variant in ('A','B'):
        resolved=applicability.resolve_with_validation(conn,m['parts']['IMA']['object_id'],m['configurations'][variant]['attributes'])
        assert resolved['passed'] and max(l['level'] for l in resolved['lines'])==3
        config_lines[variant]=resolved['lines']
        codes={l['child_object_code'] for l in resolved['lines']}
        assert m['parts']['CPU_'+variant]['part_number'] in codes
        assert m['parts']['CPU_'+('B' if variant=='A' else 'A')]['part_number'] not in codes
        assert (m['parts']['EXT_IO']['part_number'] in codes)==(variant=='B')
        heatsinks=[l for l in resolved['lines'] if l['child_object_code']==m['parts']['HEATSINK']['part_number']]
        assert sum(float(l['extended_quantity']) for l in heatsinks)==4
    for doc in m['documents']:
        raw=admin.download('/attachments/'+doc['attachment_id']+'/download')
        assert hashlib.sha256(raw).hexdigest()==doc['sha256']
        assert '模拟' in raw.decode()
    for sw in m['software'].values():
        assert scalar(conn,"SELECT status FROM software_version WHERE id=%s",(sw['version_id'],))=='RELEASED'
        assert scalar(conn,'SELECT count(*) FROM software_hardware_compatibility WHERE software_version_id=%s',(sw['version_id'],))==len(sw['hardware'])
    frozen=fetch_all(conn,'SELECT * FROM resolved_bom_snapshot_line')
    conn.execute('SAVEPOINT modify_draft')
    conn.execute("UPDATE bom_line SET quantity=quantity+1 WHERE bom_header_id IN (SELECT id FROM bom_header WHERE parent_design_object_id=%s)",(m['parts']['CPU']['object_id'],))
    assert fetch_all(conn,'SELECT * FROM resolved_bom_snapshot_line')==frozen
    conn.execute('ROLLBACK TO SAVEPOINT modify_draft')
    after=snapshot(conn)
    after_files=disk()
assert upload()==result
with transaction() as conn:
    assert snapshot(conn)==after,'repeat import mutated records'
assert disk()==after_files
print('PASS IMA original data preservation, 82 approvals, both configurations, shared quantities, fixed baselines and idempotency',flush=True)
Path('ima-evidence/result.json').write_text(json.dumps({'passed':True,'counts':m['counts'],'top_part_number':m['top_part_number'],'package_sha256':hashlib.sha256(package).hexdigest()},ensure_ascii=False,indent=2))
