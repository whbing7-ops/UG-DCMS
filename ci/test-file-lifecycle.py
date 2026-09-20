"""设计文件生命周期集成测试(真实 HTTP + PostgreSQL): 修改名称、作废/恢复、对比版次、
反查引用、解除关联、发布资料库过滤。只依赖标准库。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-file-lifecycle.py <credentials.json>
账户: 管理员(工程师+构型管理)，另现造审批人(APPROVER)与只读(VIEWER)账号。
"""
import time
import uuid
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dcms_http import (Client, FINAL_PASSWORD, STAMP, check, db_execute,  # noqa: E402,F401
                       login_admin, make_user, top_part_number)



eng = login_admin()
approver = make_user(eng, 'APPROVER')
viewer = make_user(eng, 'VIEWER')
num = 'LC-' + str(time.time_ns())[-10:]


def release(file_number, filename, content, summary, role='RELEASED_PDF'):
    rev = eng.call('POST', f'/files/{file_number}/revisions', {'change_summary': summary}, expect=201)
    eng.upload(f'/revisions/{rev["id"]}/attachments', filename, content, role)
    eng.call('POST', f'/revisions/{rev["id"]}/submit', {'approver_user_id': approver.id}, expect=200)
    approver.call('POST', f'/revisions/{rev["id"]}/release?comments=ok', expect=200)
    return rev['id']


# --- 建立文件并发布 Rev.00 ---
eng.call('POST', '/files', {'file_number': num, 'file_type_code': 'DWG', 'title_cn': '【测试】生命周期图纸'}, expect=201)
rev0 = release(num, 'a.pdf', b'v0 content', '初版')
f = eng.call('GET', f'/files/{num}')
check(f['current_released_revision'] == '00', '发布后当前版次为 00')

# --- 修改名称 ---
r = eng.call('PATCH', f'/files/{num}', {'title_cn': '【测试】改名后', 'title_en': None}, expect=200)
check(r['title_cn'] == '【测试】改名后', '文件名称可修改')
viewer.call('PATCH', f'/files/{num}', {'title_cn': 'x'}, expect=403)
print('ok   只读账户不能改名(403)')

# --- 出 Rev.01 并对比 ---
rev1 = release(num, 'a.pdf', b'v1 content changed', '改内容')
cmp_ = eng.call('GET', f'/files/{num}/compare?a={rev0}&b={rev1}', expect=200)
check(cmp_['changed'] == 1 and cmp_['rows'][0]['state'] == 'CHANGED', '版次对比识别到 PDF 内容变化')
cmp_same = eng.call('GET', f'/files/{num}/compare?a={rev0}&b={rev0}', expect=200)
check(cmp_same['changed'] == 0, '同一版次对比无差异')
eng.call('GET', f'/files/{num}/compare?a={rev0}&b={uuid.uuid4()}', expect=404)
print('ok   对比不存在的版次返回 404')

# --- 引用关系: 关联到一个设计对象并反查 ---
obj = eng.call('POST', '/external-parts', dict(namespace_code='AIRBUS', external_part_number=num,
                                               name_cn='【测试】关联用外部件', external_class_code='T2'),
               expect=201)['object_code']
link = eng.call('POST', '/definitions', {'object_code': obj, 'file_number': num,
                                          'relation_type': 'SUPPORTING_DEFINITION'}, expect=201)
usage = eng.call('GET', f'/files/{num}/where-used', expect=200)
check(any(o['object_code'] == obj and o['is_active'] for o in usage['objects']), '反查到关联对象')
eng.call('POST', '/definitions', {'object_code': obj, 'file_number': num,
                                   'relation_type': 'SUPPORTING_DEFINITION'}, expect=409)
print('ok   重复关联返回 409')
eng.call('DELETE', f'/definitions/{link["id"]}?reason=test', expect=200)
usage = eng.call('GET', f'/files/{num}/where-used')
check(any(o['object_code'] == obj and not o['is_active'] for o in usage['objects']), '解除后保留历史且标记为停用')
eng.call('DELETE', f'/definitions/{link["id"]}?reason=test', expect=400)
print('ok   重复解除返回 400')
again = eng.call('POST', '/definitions', {'object_code': obj, 'file_number': num,
                                           'relation_type': 'SUPPORTING_DEFINITION'}, expect=201)
check(again['is_active'], '解除后可重新关联(不触发唯一约束)')

# --- 发布资料库过滤: 当前有效 + 仅 PDF ---
lib = viewer.call('GET', f'/design-materials?q={num}&current_only=true&revision_status=RELEASED&attachment_role=RELEASED_PDF')
check(lib['total'] == 1 and lib['items'][0]['revision_number'] == '01', '发布资料库只给出当前有效版次(Rev.01)')

# --- 作废: 有未完成版次时被拒绝; 只读/工程师无权; 构型管理员可作废与恢复 ---
open_rev = eng.call('POST', f'/files/{num}/revisions', {'change_summary': '进行中'}, expect=201)
eng.call('POST', f'/files/{num}/obsolete', {'reason': 'x'}, expect=400)
print('ok   有未完成版次时不可作废(400)')
eng.call('POST', f'/revisions/{open_rev["id"]}/cancel?reason=cancel', expect=200)
viewer.call('POST', f'/files/{num}/obsolete', {'reason': 'x'}, expect=403)
print('ok   只读账户不能作废(403)')
eng.call('POST', f'/files/{num}/obsolete', {'reason': '不再使用'}, expect=200)
check(eng.call('GET', f'/files/{num}')['status'] == 'OBSOLETE', '文件已作废')
eng.call('POST', f'/files/{num}/revisions', {'change_summary': 'no'}, expect=400)
print('ok   作废后不可新建版次(400)')
lib = viewer.call('GET', f'/design-materials?q={num}&current_only=true&revision_status=RELEASED')
check(lib['total'] == 0, '作废文件不再出现在发布资料库')
listing = eng.call('GET', f'/files?q={num}&status=OBSOLETE&page=1')
check(listing['total'] == 1, '文件列表可按状态过滤')
eng.call('POST', f'/files/{num}/reactivate', {'reason': '恢复使用'}, expect=200)
check(eng.call('GET', f'/files?q={num}&stage=RELEASED&page=1')['total'] == 1, '恢复后按“已有发布版次”过滤可见')
print('ALL OK')
