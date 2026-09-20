"""审批超期提醒集成测试(真实 HTTP + PostgreSQL)。超期阈值 3 天。

用法: python ci/test-approval-overdue.py <base_url> <password> <postgres_dsn>
需要直接连库把申请时间调早, 因为 HTTP 接口无法改写 requested_at。
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import psycopg

BASE = sys.argv[1].rstrip('/') + '/api/v1'
PASSWORD, DSN = sys.argv[2], sys.argv[3]


class Client:
    def __init__(self, username):
        r = self.call('POST', '/auth/login', {'username': username, 'password': PASSWORD}, auth=False)
        self.token = r['access_token']
        self.id = self.call('GET', '/auth/me')['id']

    def call(self, method, path, body=None, auth=True, expect=None):
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(BASE + urllib.parse.quote(path, safe='/?=&:%,.'), method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data, status = res.read(), res.status
        except urllib.error.HTTPError as e:
            data, status = e.read(), e.code
        if expect is not None and status != expect:
            raise AssertionError(f'{method} {path}: expected {expect}, got {status}: {data[:300]!r}')
        return json.loads(data) if data else None

    def upload(self, path, filename, content, role):
        boundary = uuid.uuid4().hex
        body = b''.join([f'--{boundary}\r\nContent-Disposition: form-data; name="role"\r\n\r\n{role}\r\n'.encode(),
                         (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                          'Content-Type: application/octet-stream\r\n\r\n').encode(), content,
                         f'\r\n--{boundary}--\r\n'.encode()])
        req = urllib.request.Request(BASE + path, method='POST', data=body, headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.loads(res.read())


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print('ok  ', msg)


def backdate(request_id, interval):
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE approval_request SET requested_at = now() - %s::interval WHERE id = %s", (interval, request_id))
        c.commit()


eng, approver = Client('admin'), Client('demo_approver1')
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
