"""生产/采购角色与访问范围、版次 ZIP 下载集成测试(真实 HTTP + PostgreSQL)。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-consumer-roles.py <credentials.json>

"""
import hashlib
import io
import uuid
import zipfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dcms_http import (Client, FINAL_PASSWORD, STAMP, check, db_execute,  # noqa: E402,F401
                       login_admin, make_signers, make_user, release_flow, top_part_number)

TOP = top_part_number()


admin = login_admin()
reviewer, approver = make_signers(admin)
auditor = make_user(admin, 'VIEWER')          # VIEWER: 行为必须保持不变
stamp = STAMP


production, procurement = make_user(admin, 'PRODUCTION'), make_user(admin, 'PROCUREMENT')
check('download_native' not in production.call('GET', '/auth/me')['permissions'], '生产角色不含原生文件下载权限')
check('read_unreleased' not in procurement.call('GET', '/auth/me')['permissions'], '采购角色不含未发布内容权限')
check('read' in production.call('GET', '/auth/me')['permissions'], '生产角色可读')

# ---- 数据: Rev.00 已发布(原生+PDF+STEP), Rev.01 编制中 ----
num = 'CR-' + stamp
admin.call('POST', '/files', {'file_number': num, 'file_type_code': 'DWG', 'title_cn': '【测试】访问范围'}, expect=201)
r0 = admin.call('POST', f'/files/{num}/revisions', {'change_summary': '初版'}, expect=201)
contents = {'PRIMARY_NATIVE': (f'native-{stamp}.dwg', b'native bytes ' + uuid.uuid4().bytes),
            'RELEASED_PDF': (f'sheet-{stamp}.pdf', b'pdf bytes ' + uuid.uuid4().bytes),
            'DERIVED_STEP': (f'model-{stamp}.step', b'step bytes ' + uuid.uuid4().bytes)}
att = {role: admin.upload(f'/revisions/{r0["id"]}/attachments', n, c, role) for role, (n, c) in contents.items()}
release_flow(admin, r0['id'], reviewer, approver)
r1 = admin.call('POST', f'/files/{num}/revisions', {'change_summary': '编制中的新版'}, expect=201)
admin.upload(f'/revisions/{r1["id"]}/attachments', f'draft-{stamp}.pdf', b'draft bytes', 'RELEASED_PDF')

# ---- 现有角色行为不变 ----
check(len(admin.call('GET', f'/revisions/{r0["id"]}')['attachments']) == 3, '管理员看到全部 3 个附件')
check(len(auditor.call('GET', f'/revisions/{r0["id"]}')['attachments']) == 3, '只读账户(VIEWER)行为不变：仍可见原生文件')
auditor.call('GET', f'/revisions/{r1["id"]}', expect=200)
print('ok   只读账户(VIEWER)仍可查看编制中版次')
status, _ = auditor.call('GET', f'/attachments/{att["PRIMARY_NATIVE"]["id"]}/download', raw=True)
check(status == 200, '只读账户仍可下载原生文件')

for who, label in ((production, '生产'), (procurement, '采购')):
    roles_seen = sorted(a['attachment_role'] for a in who.call('GET', f'/revisions/{r0["id"]}')['attachments'])
    check(roles_seen == ['DERIVED_STEP', 'RELEASED_PDF'], f'{label}：已发布版次只看到 PDF 和 STEP，看不到原生文件')
    check(who.call('GET', f'/attachments/{att["PRIMARY_NATIVE"]["id"]}/download', raw=True)[0] == 404, f'{label}：直接下载原生文件返回 404')
    check(who.call('GET', f'/attachments/{att["RELEASED_PDF"]["id"]}/download', raw=True)[0] == 200, f'{label}：可下载发布版 PDF')
    who.call('GET', f'/revisions/{r1["id"]}', expect=404)
    print(f'ok   {label}：查看编制中版次返回 404')
    check([r['status'] for r in who.call('GET', f'/files/{num}')['revisions']] == ['RELEASED'], f'{label}：文件详情只列已发布版次')

    lib = who.call('GET', f'/design-materials?q={num}&page_size=50')
    check(sorted(i['attachment_role'] for i in lib['items']) == ['DERIVED_STEP', 'RELEASED_PDF'], f'{label}：资料清单不含原生文件和草稿附件')

    # 模糊匹配可能带出允许查看的 PDF/STEP, 这里只要求原生文件和草稿附件绝不出现
    for term in (f'native-{stamp}', f'draft-{stamp}', stamp):
        hits = [x for x in who.call('GET', f'/search?q={term}')['results'] if x.get('kind') == 'ATTACHMENT']
        leaked = [x['display_name'] for x in hits
                  if x['display_name'].startswith(('native-', 'draft-')) or 'PRIMARY_NATIVE' in x['matched_via']]
        check(leaked == [], f'{label}：搜索“{term}”不出现原生文件/草稿附件')
    for path in ('/bom/NO-SUCH', '/families', '/drafts/mine', '/parts/NO-SUCH/baselines', '/applicability/rules'):
        who.call('GET', path, expect=403)
    print(f'ok   {label}：BOM/设计族/草稿/基线历史/适用性接口返回 403')
    who.call('GET', f'/files/{num}/impact', expect=403)
    print(f'ok   {label}：变更影响分析(含工作 BOM)返回 403')
    who.call('GET', f'/files/{num}/compare?a={r0["id"]}&b={r1["id"]}', expect=404)
    print(f'ok   {label}：对比含编制中版次返回 404')

# ---- 管理员搜索得到原生文件(证明上面的“搜不到”是权限所致) ----
check(any(x.get('kind') == 'ATTACHMENT' for x in admin.call('GET', f'/search?q=native-{stamp}')['results']), '管理员能搜到原生文件附件')

# ---- ZIP ----
def zip_of(client, rev_id):
    status, data = client.call('GET', f'/revisions/{rev_id}/download-all', raw=True)
    return status, (zipfile.ZipFile(io.BytesIO(data)) if status == 200 else None)

st, z = zip_of(admin, r0['id'])
check(st == 200 and sorted(z.namelist()) == sorted([contents[r][0] for r in contents] + ['SHA256SUMS.txt']), '管理员 ZIP 含全部附件和 SHA256SUMS.txt')
sums = dict(line.split('  ', 1)[::-1] for line in z.read('SHA256SUMS.txt').decode().splitlines())
check(all(hashlib.sha256(z.read(n)).hexdigest() == h for n, h in sums.items()), 'SHA256SUMS.txt 与文件内容一致')
st, z = zip_of(production, r0['id'])
check(st == 200 and contents['PRIMARY_NATIVE'][0] not in z.namelist() and contents['RELEASED_PDF'][0] in z.namelist(), '生产 ZIP 不含原生文件')
check(zip_of(procurement, r1['id'])[0] == 404, '采购下载编制中版次的 ZIP 返回 404')
check(zip_of(admin, str(uuid.uuid4()))[0] == 404, '不存在的版次 ZIP 返回 404')

# ---- 件号资料包 ----
if TOP:
    pkg_admin = admin.call('GET', f'/parts/{TOP}/release-package', expect=200)
    pkg_prod = production.call('GET', f'/parts/{TOP}/release-package', expect=200)
    roles_prod = {a['attachment_role'] for d in pkg_prod['documents'] for a in d['attachments']}
    roles_admin = {a['attachment_role'] for d in pkg_admin['documents'] for a in d['attachments']}
    check('PRIMARY_NATIVE' not in roles_prod and 'PRIMARY_NATIVE' in roles_admin, '件号资料包对生产隐藏原生文件、对管理员保留')
    check(procurement.call('GET', f'/parts/{TOP}/release-package.csv', raw=True)[0] == 200, '采购可导出件号资料包 CSV')
print('ALL OK')
