"""PostgreSQL events, actual HTTP device pairing/delivery and login-home browser tests."""
import importlib.util,json,sys,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from app.db import transaction,scalar,fetch_all
from playwright.sync_api import sync_playwright,expect
spec=importlib.util.spec_from_file_location('smoke',Path(__file__).with_name('full-functional-smoke.py'))
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
creds=json.loads(Path(sys.argv[1]).read_text())
admin,_=smoke.login(creds['admin_username'],creds['admin_password'])
engineer,_=smoke.login(creds['username'],creds['password'])
aid=admin.get('/auth/me')['id'];eid=engineer.get('/auth/me')['id']
anon=smoke.Client()
# Old messages stay in history; mark read for focused delivery assertions.
for c in (admin,engineer):
    with transaction() as conn:conn.execute('UPDATE notification SET read_at=now() WHERE user_id=%s',(c.get('/auth/me')['id'],))

def bind(c):
    code=c.post('/notifications/pair')['code']
    result=anon.post('/notifications/desktop/redeem',{'code':code})
    anon.call('POST','/notifications/desktop/redeem',{'code':code},expected=(401,))
    return smoke.Client(result['token'])

before=0
with transaction() as conn:before=scalar(conn,'SELECT count(*) FROM user_session')
ad=bind(admin);ed=bind(engineer)
with transaction() as conn:assert scalar(conn,'SELECT count(*) FROM user_session')==before
# Device tokens cannot use general business APIs; web tokens cannot impersonate devices.
ad.call('GET','/auth/me',expected=(401,));admin.call('GET','/notifications/desktop/poll',expected=(401,))
pcs=engineer.get('/dictionary/physical-class');terms=engineer.get('/dictionary/core-term');funcs=engineer.get('/dictionary/function-item')
pc=next(x for x in pcs if x['primary_class_code']=='T1');term=next(x for x in terms if x['primary_class_code']=='T1')
f=engineer.post('/families',dict(primary_class_code='T1',physical_class_id=pc['id'],core_term_id=term['id'],object_level_code='PART',primary_function_id=funcs[0]['id'],family_definition='【模拟数据】Windows通知-'+str(time.time_ns()),allowed_variation='尺寸',excluded_variation='原理',new_family_reason='验证通知'))
req=engineer.post('/families/'+f['id']+'/submit',{'approver_user_id':aid});rid=req['id']
a=admin.get('/notifications/overview');assert any(x['id']==rid for x in a['inbox'])
assert not any(x['request_id']==rid and x['kind']=='PENDING' for x in engineer.get('/notifications/overview')['messages'])
# Two devices must not both deliver the same outstanding event.
ad2=bind(admin)
with ThreadPoolExecutor(max_workers=2) as p:
    one=p.submit(ad.get,'/notifications/desktop/poll');two=p.submit(ad2.get,'/notifications/desktop/poll')
    results=[one.result(),two.result()]
items=[x for r in results for x in r['items']];assert len(items)==1 and items[0]['kind']=='PENDING',items
winner=ad if results[0]['items'] else ad2
other=ad2 if winner is ad else ad
nid=items[0]['id']
# A foreign user cannot consume or mark another recipient's event.
ed.post('/notifications/desktop/ack',{'ids':[nid]});engineer.post('/notifications/read',{'ids':[nid]})
assert winner.get('/notifications/desktop/poll')['items'][0]['id']==nid
winner.post('/notifications/desktop/ack',{'ids':[nid]})
assert not winner.get('/notifications/desktop/poll')['items']
assert not other.get('/notifications/desktop/poll')['items']
assert admin.get('/notifications/overview')['unread']==1
# Withdrawal invalidates pending messages; resubmission generates a new round event.
engineer.post('/approvals/'+rid+'/withdraw?reason=notification-test')
assert not any(x['request_id']==rid for x in admin.get('/notifications/overview')['messages'])
req2=engineer.post('/families/'+f['id']+'/submit',{'approver_user_id':aid});assert req2['id']==rid
assert ad.get('/notifications/desktop/poll')['items'][0]['id']!=nid
admin.post('/families/'+f['id']+'/approve')
assert not ad.get('/notifications/desktop/poll')['items']
result=ed.get('/notifications/desktop/poll')['items'];assert len(result)==1 and result[0]['kind']=='APPROVED'
ed.post('/notifications/desktop/ack',{'ids':[x['id'] for x in result]})
assert not ed.get('/notifications/desktop/poll')['items']
# Login must land on the live personal queue and provide working message navigation.
out=Path('notification-evidence');out.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1500,'height':1100})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8080/#/login')
    page.get_by_label('账户',exact=True).fill(creds['username']);page.get_by_label('当前口令',exact=True).fill(creds['password'])
    page.get_by_role('button',name='登录',exact=True).click()
    expect(page.get_by_role('heading',name='我的待办事项',exact=True)).to_be_visible()
    expect(page.get_by_role('heading',name='审批消息',exact=True)).to_be_visible()
    expect(page.get_by_text('申请审批通过',exact=True).first).to_be_visible()
    page.get_by_role('button',name='生成绑定信息',exact=True).click()
    pairing=page.get_by_label('通知助手绑定信息');expect(pairing).to_be_visible()
    assert pairing.input_value().startswith('http://127.0.0.1:8080|')
    page.screenshot(path=str(out/'login-todos.png'),full_page=True)
    row=page.locator('tr').filter(has_text=req['request_number']).filter(has_text='申请审批通过').first
    row.get_by_role('link',name='查看申请',exact=True).click()
    expect(page.get_by_text(req['request_number'],exact=True)).to_be_visible()
    assert engineer.get('/notifications/overview')['unread']==0
    assert not errors,errors
    browser.close()
# Expiry, logout, administrative revocation/disable and single-use pairing.
code=admin.post('/notifications/pair')['code']
with transaction() as conn:conn.execute("UPDATE notification_device SET pair_expires_at=now()-interval '1 second' WHERE token_hash IS NULL")
anon.call('POST','/notifications/desktop/redeem',{'code':code},expected=(401,))
admin.post('/notifications/disconnect');ad.call('GET','/notifications/desktop/poll',expected=(401,))
engineer.post('/auth/logout');ed.call('GET','/notifications/desktop/poll',expected=(401,))
# Event creation rolls back with its business transaction.
with transaction() as conn:
    before=scalar(conn,'SELECT count(*) FROM notification')
    conn.execute('SAVEPOINT rollback_event')
    conn.execute("UPDATE approval_request SET status='PENDING' WHERE id=%s",(rid,))
    conn.execute("UPDATE approval_request SET status='RETURNED' WHERE id=%s",(rid,))
    assert scalar(conn,'SELECT count(*) FROM notification')==before+1
    conn.execute('ROLLBACK TO SAVEPOINT rollback_event')
    assert scalar(conn,'SELECT count(*) FROM notification')==before
    types=fetch_all(conn,"SELECT DISTINCT r.object_type FROM notification n JOIN approval_request r ON r.id=n.request_id WHERE n.kind='APPROVED'")
    assert len(types)==6,types
(out/'result.json').write_text(json.dumps({'passed':True,'types':len(types),'checks':['login-home','recipient-isolation','all-six-approval-results','same-round-deduplication','withdraw-resubmit','concurrent-desktops','restricted-device-token','single-use-expiring-pairing','logout-and-revocation','rollback']},indent=2))
print('PASS notifications: personal login tasks, six-type event delivery, ownership, concurrency, retry, lifecycle and rollback',flush=True)
