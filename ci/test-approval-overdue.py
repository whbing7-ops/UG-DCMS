"""审批超期提醒集成测试(真实 HTTP + PostgreSQL)。超期阈值 3 天。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-approval-overdue.py <credentials.json>
需要直接连库把申请时间调早, 因为 HTTP 接口无法改写 requested_at。
"""
import time
import uuid
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dcms_http import (Client, FINAL_PASSWORD, STAMP, check, db_execute,  # noqa: E402,F401
                       login_admin, make_user, top_part_number)



def backdate(request_id, interval):
    db_execute("UPDATE approval_request SET requested_at = now() - %s::interval WHERE id = %s", (interval, request_id))


eng = login_admin()
approver = make_user(eng, 'APPROVER')
num = 'OD-' + str(time.time_ns())[-10:]
eng.call('POST', '/files', {'file_number': num, 'file_type_code': 'DWG', 'title_cn': '【测试】超期提醒'}, expect=201)
rev = eng.call('POST', f'/files/{num}/revisions', {'change_summary': '超期测试'}, expect=201)
eng.upload(f'/revisions/{rev["id"]}/attachments', 'od.pdf', b'overdue ' + uuid.uuid4().bytes, 'RELEASED_PDF')
req = eng.call('POST', f'/revisions/{rev["id"]}/submit', {'approver_user_id': approver.id}, expect=200)
rid = req['id']


def mine():
    return next(r for r in approver.call('GET', '/approvals/inbox') if str(r['object_id']) == rev['id'])


base_overdue = approver.call('GET', '/notifications/overview')['overdue_inbox_count']
fresh = mine()
check(fresh['is_overdue'] is False and fresh['waiting_days'] == 0, '刚提交的申请不算超期，等待 0 天')

backdate(rid, '2 days 23 hours')
check(mine()['is_overdue'] is False and mine()['waiting_days'] == 2, '未满 3 天不算超期')

backdate(rid, '3 days 1 minute')
item = mine()
check(item['is_overdue'] is True and item['waiting_days'] == 3, '超过 3 天算超期')

backdate(rid, '5 days')
ov = approver.call('GET', '/notifications/overview')
check(ov['overdue_days'] == 3, '概览返回超期阈值 3 天')
check(ov['overdue_inbox_count'] == base_overdue + 1, '审批人的超期计数增加 1')
check(approver.call('GET', '/approvals/summary')['inbox_overdue'] == ov['overdue_inbox_count'], '汇总接口与概览一致')
check(mine()['waiting_days'] == 5, '等待天数按整天计算')

# 申请人视角: 自己提交的申请卡住了, 能看到卡在谁那里
ov_eng = eng.call('GET', '/notifications/overview')
stuck = [r for r in ov_eng['stuck'] if r['id'] == rid]
check(len(stuck) == 1 and stuck[0]['assignee_name'], '申请人概览列出超期申请及当前处理人')
check(eng.call('GET', '/approvals/summary')['my_overdue'] >= 1, '申请人汇总含 my_overdue')
check(rid not in [r['id'] for r in approver.call('GET', '/notifications/overview')['stuck']], '审批人的“我提交的超期”不含别人的申请')

# 处理后不再计入超期
approver.call('POST', f'/revisions/{rev["id"]}/release?comments=ok', expect=200)
check(approver.call('GET', '/notifications/overview')['overdue_inbox_count'] == base_overdue, '批准后不再计入超期')
check(all(r['id'] != rid for r in eng.call('GET', '/notifications/overview')['stuck']), '批准后从申请人的超期列表消失')
print('ALL OK')
