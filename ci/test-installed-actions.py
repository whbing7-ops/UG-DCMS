"""Click the installed UI, then verify persisted data and denied operations.

Runs only against the disposable Windows CI installation. No mocked API responses.
"""
import json
import sys
import time
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

credentials = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
output = Path(sys.argv[2]); output.mkdir(parents=True, exist_ok=True)
stamp = str(int(time.time()))
username = 'ui_' + stamp
initial = 'Start!Demo' + stamp
changed = 'Changed!Demo' + stamp
reset = 'Reset!Demo' + stamp
base = 'http://127.0.0.1:8080'
passed = []

def mark(name):
    passed.append(name); print('PASS', name, flush=True)

def login(page, user, password):
    page.goto(base)
    page.get_by_role('textbox', name='账户', exact=True).fill(user)
    page.get_by_label('当前口令').fill(password)
    page.get_by_role('button', name='登录', exact=True).click()

def nav(page, path):
    page.locator('.nav a[href="#' + path + '"]').click()

def call(page, method, path, data=None, status=200):
    token = page.evaluate("sessionStorage.getItem('dcms.token')")
    response = page.request.fetch(base + '/api/v1' + path, method=method,
        headers={'Authorization': 'Bearer ' + token}, data=data)
    assert response.status == status, (method, path, response.status, response.text())
    return response.json()

def save(page, name='保存'):
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name=name, exact=True).click()
    expect(dialog).not_to_be_visible()

def reason(page, label='原因（记入审计）'):
    page.get_by_role('dialog').get_by_label(label).fill('CI UI operation verification')

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **({'executable_path': os.environ['DCMS_BROWSER_PATH']} if os.environ.get('DCMS_BROWSER_PATH') else {'channel':'msedge'}))
    context = browser.new_context(viewport={'width':1440, 'height':1000}, accept_downloads=True)
    admin = context.new_page()
    errors=[]
    admin.on('pageerror', lambda e: errors.append(str(e)))
    try:
        login(admin, credentials['admin_username'], credentials['admin_password'])
        nav(admin, '/admin')
        admin.get_by_role('button', name='＋ 新建账户').click()
        dialog = admin.get_by_role('dialog')
        dialog.get_by_label('账户名（字母开头）').fill(username)
        dialog.get_by_label('姓名', exact=True).fill('UI 测试工程师')
        dialog.get_by_label('初始密码').fill(initial)
        dialog.get_by_label('设计工程师', exact=True).check()
        dialog.get_by_label('构型管理员', exact=True).check()
        save(admin)
        row = admin.get_by_role('row').filter(has=admin.get_by_text(username,exact=True))
        expect(row).to_be_visible()
        row.get_by_role('button', name='编辑', exact=True).click()
        dialog.get_by_label('姓名', exact=True).fill('UI 工程师甲')
        dialog.get_by_label('变更原因').fill('更新测试姓名')
        save(admin)
        expect(row).to_contain_text('UI 工程师甲')
        admin.get_by_label('搜索账户').fill('UI 工程师甲')
        expect(admin.locator('tbody tr')).to_have_count(1 + len(call(admin,'GET','/admin/sessions')))
        admin.get_by_label('搜索账户').fill('')
        user = next(u for u in call(admin,'GET','/admin/users') if u['username']==username)
        assert user['full_name']=='UI 工程师甲' and 'ENGINEER' in user['roles']
        mark('account/create-edit-role-filter-persisted')
        assert user['email'] is None
        admin.get_by_role('button',name='＋ 新建账户').click()
        dialog.get_by_label('账户名（字母开头）').fill(username+'_view')
        dialog.get_by_label('姓名',exact=True).fill('UI 只读验证账户')
        dialog.get_by_label('初始密码').fill(initial); save(admin)
        row.get_by_role('button',name='编辑',exact=True).click()
        dialog.get_by_label('邮箱（可选）').fill(username+'@example.test')
        dialog.get_by_label('变更原因').fill('测试邮箱'); save(admin)
        row.get_by_role('button',name='编辑',exact=True).click()
        dialog.get_by_label('邮箱（可选）').fill('')
        dialog.get_by_label('变更原因').fill('清空邮箱'); save(admin)
        assert next(u for u in call(admin,'GET','/admin/users') if u['username']==username)['email'] is None
        mark('account/multiple-empty-emails-and-clear-email')

        user_context = browser.new_context(viewport={'width':1440,'height':1000}, accept_downloads=True)
        page = user_context.new_page(); page.on('pageerror', lambda e: errors.append(str(e)))
        login(page, username, initial)
        expect(page.get_by_role('heading',name='先修改初始口令')).to_be_visible()
        page.get_by_label('当前口令').fill(initial)
        page.get_by_label('新口令',exact=True).fill(changed)
        page.get_by_label('再次输入新口令').fill(changed)
        page.get_by_role('button', name='修改口令',exact=True).click()
        expect(page.locator('.nav')).to_be_visible()
        old_token=page.evaluate("sessionStorage.getItem('dcms.token')")
        row.get_by_role('button',name='停用',exact=True).click(); reason(admin,'操作原因'); save(admin,'确认停用账户')
        expect(row.get_by_role('button',name='启用',exact=True)).to_be_visible()
        response=page.request.get(base+'/api/v1/auth/me', headers={'Authorization':'Bearer '+old_token})
        assert response.status == 401
        denied=page.request.post(base+'/api/v1/auth/login',data={'username':username,'password':changed})
        assert denied.status in (401,403)
        row.get_by_role('button',name='启用',exact=True).click(); reason(admin,'操作原因'); save(admin,'确认启用账户')
        row.get_by_role('button',name='重置密码',exact=True).click()
        dialog.get_by_label('新临时密码').fill(reset); dialog.get_by_label('再次输入密码').fill('mismatch')
        reason(admin,'操作原因'); dialog.get_by_role('button',name='确认重置密码').click()
        expect(dialog.get_by_text('两次密码不一致')).to_be_visible()
        dialog.get_by_label('再次输入密码').fill(reset); save(admin,'确认重置密码')
        row.get_by_role('button',name='解锁',exact=True).click(); save(admin,'确认解除登录锁定')
        # The full demo seed intentionally has more than one system administrator,
        # so the disposable UI run must not assume the logged-in account is the last one.
        mark('account/first-login-disable-revoke-enable-reset-unlock')
        admin.screenshot(path=str(output/'accounts-ui.png'),full_page=True)

        user_context.close()
        user_context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
        page=user_context.new_page(); page.on('pageerror',lambda e: errors.append(str(e)))
        login(page,username,reset)
        expect(page.get_by_role('heading',name='先修改初始口令')).to_be_visible()
        page.get_by_label('当前口令').fill(reset)
        page.get_by_label('新口令',exact=True).fill(changed)
        page.get_by_label('再次输入新口令').fill(changed)
        page.get_by_role('button',name='修改口令',exact=True).click()
        expect(page.locator('.nav')).to_be_visible()

        nav(page,'/external-parts')
        expect(page.get_by_role('heading',name='外部件',exact=True)).to_be_visible()
        expect(page.get_by_text('第 1 /',exact=False)).to_be_visible()
        assert page.locator('tbody tr').count() <= 100
        # 业务库清空后不再依赖 0015 的固定演示件；使用前序功能冒烟
        # 自建的唯一外部件验证分页筛选。
        page.get_by_label('关键词').fill('CI-EXT-')
        page.get_by_role('button',name='查询',exact=True).click()
        expect(page.locator('tbody tr')).to_have_count(1)
        mark('large-list/external-parts-paged-filtered-responsive')

        nav(admin,'/dictionary')
        admin.get_by_label('字典',exact=True).select_option('manufacturer')
        dictionary_row=admin.locator('tbody tr').first
        dictionary_row.get_by_role('button',name='废止',exact=True).click(); reason(admin); save(admin)
        dictionary_row.get_by_role('button',name='启用',exact=True).click(); reason(admin); save(admin)
        mark('dictionary/deprecate-reactivate')

        nav(page,'/bom')
        page.get_by_label('父件号或名称').fill('CI演示成品')
        page.get_by_role('button',name='查找件号').click()
        page.get_by_role('link',name='打开 BOM').first.click()
        expect(page.get_by_role('heading',name='BOM',exact=True)).to_be_visible()
        pn=page.url.split('/bom/')[1]
        children=call(page,'GET','/search?q=CI演示子件B&kinds=PART_NUMBER&limit=100')['results']
        child=children[0]['object_code']
        rule='UI-A-'+stamp; ctx='UI-CTX-'+stamp
        for is_context in (False,True):
            page.get_by_role('button',name='新建构型' if is_context else '新建适用性规则',exact=True).click()
            d=page.get_by_role('dialog')
            d.get_by_label('编号',exact=True).fill(ctx if is_context else rule)
            d.get_by_label('名称',exact=True).fill('UI A 构型')
            d.get_by_label('属性名',exact=True).fill('model')
            d.get_by_label('属性值',exact=True).fill('UI-A')
            save(page)
        page.get_by_label('项号',exact=True).fill('990')
        page.get_by_label('子件号',exact=True).fill(child)
        page.get_by_label('数量',exact=True).fill('2')
        page.get_by_label('适用性',exact=True).select_option(rule)
        page.get_by_role('button',name='新增子件',exact=True).click()
        bom_row=page.get_by_role('row').filter(has_text='990').first
        expect(bom_row).to_be_visible()
        bom_row.get_by_role('button',name='编辑',exact=True).click()
        page.get_by_role('dialog').get_by_label('数量',exact=True).fill('3'); save(page)
        stored=call(page,'GET','/bom/'+pn)['lines']
        line=next(x for x in stored if x['item_number']=='990')
        assert float(line['quantity'])==3 and line['applicability_rule_code']==rule
        # Both mutation paths must roll back if applicability binding fails.
        call(page,'POST','/bom/'+pn+'/lines', {'item_number':'991','child_object_code':child,'quantity':1,'applicability_rule_code':'NOT-FOUND'},404)
        call(page,'PATCH','/bom/lines/'+line['id'], {'quantity':99,'applicability_rule_code':'NOT-FOUND'},404)
        after=call(page,'GET','/bom/'+pn)['lines']
        assert not any(x['item_number']=='991' for x in after)
        assert float(next(x for x in after if x['id']==line['id'])['quantity'])==3
        page.get_by_label('构型上下文').select_option(ctx)
        page.get_by_role('button',name='解析构型',exact=True).click()
        expect(page.get_by_text('解析通过',exact=False)).to_be_visible()
        assert call(page,'POST','/bom/'+pn+'/resolve',{'context_code':ctx})['line_count']==1
        with page.expect_download() as download:
            page.get_by_role('button',name='下载模板').click()
        assert download.value.suggested_filename=='bom_template.csv'
        page.get_by_role('button',name='冻结构型 BOM').click()
        expect(page.locator('#toast')).to_contain_text('已冻结')
        snapshots=call(page,'GET','/bom/'+pn+'/snapshots')
        resolved_number=snapshots['resolved'][0]['resolved_snapshot_number']
        page.get_by_label('构型快照编号').select_option(resolved_number)
        page.get_by_role('button',name='查看构型快照').click()
        expect(page.locator('main')).to_contain_text(resolved_number)
        page.get_by_role('button',name='生成 BOM 快照').click()
        expect(page.locator('#toast')).to_contain_text('已生成快照')
        snapshots=call(page,'GET','/bom/'+pn+'/snapshots')
        snap=snapshots['bom'][0]['snapshot_number']
        page.get_by_label('旧快照编号').select_option(snap)
        page.get_by_label('新快照编号').select_option(snap)
        page.get_by_role('button',name='比较 BOM 快照').click()
        expect(page.locator('main')).to_contain_text('内容相同')
        call(page,'GET','/bom/snapshots/compare?a=NONEXISTENT&b=NONEXISTENT',status=404)
        page.screenshot(path=str(output/'bom-ui.png'),full_page=True)
        bom_row.get_by_role('button',name='删除子项').click(); reason(page); save(page)
        assert not any(x['item_number']=='990' for x in call(page,'GET','/bom/'+pn)['lines'])
        mark('bom/search-create-rule-context-add-edit-atomic-rollback-resolve-freeze-download-delete')

        nav(page,'/files')
        file_number='UI-FILE-'+stamp
        page.get_by_label('文件号',exact=True).fill(file_number)
        page.get_by_label('名称',exact=True).fill('UI 文件验证')
        page.get_by_role('button',name='建立',exact=True).click()
        expect(page.locator('.tb-code')).to_have_text(file_number)
        page.once('dialog',lambda d:d.accept('UI upload test'))
        page.get_by_role('button',name='新建版次').click()
        page.get_by_role('link',name='打开',exact=True).click()
        page.get_by_label('用途',exact=True).select_option('REFERENCE')
        content='中文附件下载与删除测试'.encode('utf-8')
        page.get_by_label('文件',exact=True).set_input_files({'name':'测试附件.txt','mimeType':'text/plain','buffer':content})
        page.get_by_role('button',name='上传',exact=True).click()
        expect(page.get_by_role('cell',name='测试附件.txt',exact=True)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('button',name='下载',exact=True).click()
        assert Path(download.value.path()).read_bytes()==content
        page.get_by_role('button',name='删除附件').click(); reason(page); save(page)
        expect(page.get_by_text('还没有附件',exact=True)).to_be_visible()
        page.get_by_role('button',name='取消版次').click(); reason(page); save(page)
        expect(page.locator('.st-CANCELLED')).to_be_visible()
        mark('files/create-revision-upload-Chinese-download-delete-cancel')

        page.goto(base+'/#/object/'+pn)
        page.get_by_role('button',name='关联设计文件').click()
        page.get_by_role('dialog').get_by_label('文件编号').fill(file_number); save(page)
        expect(page.get_by_role('link',name=file_number,exact=True)).to_be_visible()
        page.get_by_role('link',name='设计基线',exact=True).click()
        page.get_by_role('button',name='新建基线',exact=True).click()
        dialog=page.get_by_role('dialog')
        dialog.get_by_label('项目编号').fill('CI-UI-PROJECT')
        dialog.get_by_label('建立原因').fill('UI first baseline test')
        dialog.get_by_label('范围说明').fill('UI 全产品设计定义与BOM快照')
        dialog.get_by_role('button',name='建立基线',exact=True).click()
        page.get_by_role('button',name='取消基线',exact=True).click(); reason(page); save(page)
        expect(page.locator('.tb-grid')).to_contain_text('已取消')
        mark('definitions/link-and-display-first-baseline-create-cancel')

        nav(admin,'/audit')
        admin.get_by_label('动作',exact=True).fill('USER_CREATE')
        admin.get_by_role('button',name='查询',exact=True).click()
        audit_row=admin.get_by_role('row').filter(has=admin.get_by_role('cell',name='新建账户',exact=True)).filter(has=admin.get_by_role('cell',name=username,exact=True))
        expect(audit_row).to_be_visible()
        audit_row.get_by_text('查看变更',exact=True).click()
        mark('audit/filter-view-change')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        mark('mobile/layout-no-JavaScript-errors')
    except Exception:
        admin.screenshot(path=str(output/'failure-admin.png'),full_page=True)
        if 'page' in locals() and not page.is_closed():
            page.screenshot(path=str(output/'failure-page.png'),full_page=True)
        raise
    finally:
        (output/'UI-ACTIONS-RESULTS.json').write_text(json.dumps({'passed':passed,'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
