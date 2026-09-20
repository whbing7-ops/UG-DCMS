"""rc2.45 集成测试共用的 HTTP 客户端与账号工具(仅标准库)。

用法(CI 与本地一致):
    DCMS_BASE_URL=http://127.0.0.1:8080  PYTHONPATH=src/backend  python ci/test-xxx.py <credentials.json>
credentials.json 与 full-functional-smoke.py 写出的相同，至少含 admin_username/admin_password。
需要顶层件号或直连数据库的用例，通过 app.db 读取(与其它 ci/test-*.py 相同，依赖 DCMS_PG_* 环境变量)。
"""
import atexit
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get('DCMS_BASE_URL', 'http://127.0.0.1:8080').rstrip('/') + '/api/v1'
CREDS = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')) if len(sys.argv) > 1 else {}
STAMP = str(time.time_ns())[-9:]
TMP_PASSWORD = 'Ci-Tmp-Pass-2026!a'
FINAL_PASSWORD = 'Ci-Final-Pass-2026!b'

_clients = []


@atexit.register
def _logout_all():
    # 并发会话数受许可限制: 结束(含失败)时释放自己占用的会话, 以免影响后续用例
    for c in _clients:
        try:
            c.call('POST', '/auth/logout')
        except Exception:
            pass


class Client:
    def __init__(self, username, password):
        self.username, self.password = username, password    # 电子签名要用本人口令
        r = self.call('POST', '/auth/login', {'username': username, 'password': password}, auth=False)
        self.token = r['access_token']
        self.id = self.call('GET', '/auth/me')['id']
        _clients.append(self)

    def call(self, method, path, body=None, auth=True, expect=None, raw=False):
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(BASE + urllib.parse.quote(path, safe='/?=&:%,.'), method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                data, status = res.read(), res.status
        except urllib.error.HTTPError as e:
            data, status = e.read(), e.code
        if expect is not None and status != expect:
            raise AssertionError(f'{method} {path}: expected {expect}, got {status}: {data[:300]!r}')
        if raw:
            return status, data
        return json.loads(data) if data else None

    def upload(self, path, filename, content, role):
        boundary = uuid.uuid4().hex
        body = b''.join([f'--{boundary}\r\nContent-Disposition: form-data; name="role"\r\n\r\n{role}\r\n'.encode(),
                         (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                          'Content-Type: application/octet-stream\r\n\r\n').encode(), content,
                         f'\r\n--{boundary}--\r\n'.encode()])
        req = urllib.request.Request(BASE + path, method='POST', data=body, headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read())


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print('ok  ', msg)


def _release_stale_sessions():
    """并发账号数有许可上限(默认 10)。前面的用例(尤其不自动登出的旧脚本)留下的会话会占位,
    这里在本脚本开始时把它们全部撤销 —— 用例是串行运行的, 此时不会有别人在用。"""
    try:
        db_execute("UPDATE user_session SET revoked_at = now() WHERE revoked_at IS NULL")
    except Exception:
        pass    # 没有数据库环境变量(纯 HTTP 运行)时跳过


def login_admin():
    _release_stale_sessions()
    return Client(CREDS['admin_username'], CREDS['admin_password'])


def make_user(admin, role, tag=''):
    """用管理员现造一个只带指定角色的账号(避免依赖预置的演示账号及其口令)，并完成首次改密。"""
    name = f'ci245_{role.lower()}{tag}_{STAMP}'
    admin.call('POST', '/admin/users', {'username': name, 'full_name': f'CI {role}', 'password': TMP_PASSWORD,
                                        'roles': [role]}, expect=201)
    Client(name, TMP_PASSWORD).call('POST', '/auth/password',
                                    {'old_password': TMP_PASSWORD, 'new_password': FINAL_PASSWORD}, expect=200)
    return Client(name, FINAL_PASSWORD)


def top_part_number():
    """SIM-IMA-V1 演示数据的顶层件号(先由 test-ima-demo.py 装载)。DCMS_TOP_PN 可直接指定。"""
    if os.environ.get('DCMS_TOP_PN'):
        return os.environ['DCMS_TOP_PN']
    from app.db import fetch_one, transaction
    with transaction() as conn:
        row = fetch_one(conn, "SELECT manifest FROM simulation_dataset WHERE dataset_code='SIM-IMA-V1'")
    if row is None:
        raise SystemExit('未找到 SIM-IMA-V1 演示数据, 请先运行 ci/test-ima-demo.py')
    return row['manifest']['top_part_number']


def db_execute(sql, params=()):
    from app.db import execute, transaction
    with transaction() as conn:
        execute(conn, sql, params)


def db_query(sql, params=()):
    from app.db import fetch_all, transaction
    with transaction() as conn:
        return fetch_all(conn, sql, params)


def make_signers(admin, tag=''):
    """现造并授权一名审核人(ENGINEER)和一名批准人(APPROVER), 均对所有文件类型有效。

    三级签署要求编制、审核、批准是三个不同的人, 且审核/批准必须在有权签署人清单内。
    管理员不能给自己授权, 所以这里授权的是另外两个账号。
    """
    reviewer = make_user(admin, 'ENGINEER', 'rv' + tag)
    approver = make_user(admin, 'APPROVER', 'ap' + tag)
    admin.call('POST', '/signers', {'user_id': reviewer.id, 'level': 'REVIEW', 'note': 'CI'}, expect=201)
    admin.call('POST', '/signers', {'user_id': approver.id, 'level': 'APPROVE', 'note': 'CI'}, expect=201)
    return reviewer, approver


def submit_for_signoff(preparer, revision_id, reviewer, approver, expect=200):
    return preparer.call('POST', f'/revisions/{revision_id}/submit', {
        'reviewer_user_id': reviewer.id, 'approver_user_id': approver.id, 'password': preparer.password}, expect=expect)


def review(reviewer, revision_id, expect=200, password=None):
    return reviewer.call('POST', f'/revisions/{revision_id}/review',
                         {'password': reviewer.password if password is None else password, 'comments': 'ok'}, expect=expect)


def approve(approver, revision_id, expect=200, password=None):
    return approver.call('POST', f'/revisions/{revision_id}/release',
                         {'password': approver.password if password is None else password, 'comments': 'ok'}, expect=expect)


def release_flow(preparer, revision_id, reviewer, approver):
    """编制提交 → 审核 → 批准发布, 三级都用各自口令签署。返回提交时的审批申请。"""
    req = submit_for_signoff(preparer, revision_id, reviewer, approver)
    review(reviewer, revision_id)
    approve(approver, revision_id)
    return req
