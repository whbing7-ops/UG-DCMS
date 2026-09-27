"""Failure/interruption status persists independently of rolled-back business rows."""
import importlib.util,json,sys,faulthandler
faulthandler.dump_traceback_later(90,repeat=True)
from pathlib import Path
from unittest.mock import patch
from app.db import transaction,scalar
from app import storage
from app.services import import_jobs,data_backups,scale_demo,ima_demo
spec=importlib.util.spec_from_file_location('smoke',Path(__file__).with_name('full-functional-smoke.py'))
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
c=json.loads(Path(sys.argv[1]).read_text())
admin,_=smoke.login(c['admin_username'],c['admin_password'])
engineer,_=smoke.login(c['username'],c['password'])
uid=admin.get('/auth/me')['id']
with transaction() as conn:sid=scalar(conn,'SELECT id FROM user_session WHERE user_id=%s ORDER BY issued_at DESC LIMIT 1',(uid,))
actor=dict(user_id=uid,username=c['admin_username'],session_id=str(sid))
engineer.call('GET','/system/data-backups/status',expected=(403,))
package=data_backups.create_scale_package()
with transaction() as conn:
    job,dataset=import_jobs.enqueue(conn,actor,package,data_backups.CONFIRMATION)
    try:import_jobs.enqueue(conn,actor,package,data_backups.CONFIRMATION);raise AssertionError('Queued duplicate accepted')
    except ValueError as exc:assert '排队' in str(exc)
before=set(storage._root().rglob('*'))
original=ima_demo._document
def fail(*a,**kw):
    original(*a,**kw)
    raise ValueError('测试注入：模拟附件处理失败')
with patch.object(ima_demo,'_document',fail):import_jobs.run(job,dataset,actor)
s=admin.get('/system/data-backups/status');assert s['state']=='FAILED' and '已回滚' in s['message'],s
with transaction() as conn:assert not scale_demo.status(conn)['imported']
assert {p for p in storage._root().rglob('*') if p.is_file()}=={p for p in before if p.is_file()}
# Reopening the real page retains failure details and enables a retry.
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1200,'height':900})
    page.add_init_script("sessionStorage.setItem('dcms.token',"+json.dumps(admin.token)+")")
    page.goto('http://127.0.0.1:8080/#/backup')
    expect(page.locator('[data-import-progress]').get_by_text('导入失败',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='校验并导入数据',exact=True)).to_be_enabled()
    page.reload();expect(page.locator('[data-import-progress]').get_by_text('导入失败',exact=True)).to_be_visible()
    Path('scale-evidence').mkdir(exist_ok=True);page.screenshot(path='scale-evidence/import-failed.png',full_page=True)
    browser.close()
# Model a service death: persisted RUNNING but no live advisory lock or receipt.
with transaction() as conn:
    job,dataset=import_jobs.enqueue(conn,actor,package,data_backups.CONFIRMATION)
    conn.execute("UPDATE data_import_job SET state='RUNNING',progress=43 WHERE id=%s",(job,))
s=admin.get('/system/data-backups/status');assert s['state']=='INTERRUPTED' and s['progress']==43,s
# The migration may be replayed during cross-version restore without data loss.
with transaction() as conn:
    conn.execute(Path('src/db/migrations/0025_data_import_progress.sql').read_text())
    assert str(scalar(conn,'SELECT id FROM data_import_job WHERE slot=1'))==job
print('PASS: progress permissions, duplicate queue, failure rollback, durable failure UI, interruption and migration replay')
