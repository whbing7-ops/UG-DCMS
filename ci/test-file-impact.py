"""变更影响分析集成测试(真实 HTTP + PostgreSQL, 需已装载 SIM-IMA-V1 演示数据)。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-file-impact.py <credentials.json>
"""
import uuid
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dcms_http import (Client, FINAL_PASSWORD, STAMP, check, db_execute,  # noqa: E402,F401
                       login_admin, make_user, top_part_number)

TOP = top_part_number()


eng = login_admin()
approver = make_user(eng, 'APPROVER')
viewer = make_user(eng, 'VIEWER')

links = eng.call('GET', f'/definitions/{TOP}', expect=200)
primary = next(x for x in links if x['relation_type'] == 'PRIMARY_DEFINITION' and x['is_active'])
num = primary['file_number']

before = viewer.call('GET', f'/files/{num}/impact', expect=200)
check(TOP in [o['object_code'] for o in before['objects']], '影响分析包含关联的设计对象')
check({'objects', 'upstream_parents', 'baselines', 'current_baselines_behind'} <= set(before['summary']), '摘要字段齐全(只读账户可查看)')
check(all('upstream' in o for o in before['objects']), '每个对象带上层组件列表')
behind_before = before['summary']['current_baselines_behind']   # 在已跑过本用例的库上可能不为 0
check(before['summary']['baselines'] >= 1, '演示数据中该文件被基线锁定')
viewer.call('GET', f'/files/{"NO-SUCH-" + str(uuid.uuid4())[:6]}/impact', expect=404)
print('ok   不存在的文件返回 404')

# 出并发布新版次 -> 当前基线仍锁定旧版次(INV-014), 应被标为"落后"
rev = eng.call('POST', f'/files/{num}/revisions', {'change_summary': '【测试】影响分析'}, expect=201)
eng.upload(f'/revisions/{rev["id"]}/attachments', 'impact.pdf', b'impact test ' + uuid.uuid4().bytes, 'RELEASED_PDF')
eng.call('POST', f'/revisions/{rev["id"]}/submit', {'approver_user_id': approver.id}, expect=200)
approver.call('POST', f'/revisions/{rev["id"]}/release?comments=ok', expect=200)
after = viewer.call('GET', f'/files/{num}/impact', expect=200)
check(after['latest_released_revision'] == rev['revision_number'], '最新发布版次已更新')
check(after['summary']['current_baselines_behind'] >= max(1, behind_before), '发布新版次后，锁定旧版次的当前基线被标为落后')
check(any(b['behind'] and b['is_current'] for b in after['baselines']), '落后标记落在当前基线上')
check('INV-014' in after['note'], '说明中指出 INV-014')
print('ALL OK')
