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


def login_admin():
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
