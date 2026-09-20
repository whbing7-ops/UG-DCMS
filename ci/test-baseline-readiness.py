"""基线发布前就绪检查集成测试(真实 HTTP + PostgreSQL, 需已装载 SIM-IMA-V1 演示数据)。

用法: python ci/test-baseline-readiness.py <base_url> <password> <top_part_number>
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip('/') + '/api/v1'
PASSWORD, TOP = sys.argv[2], sys.argv[3]


class Client:
    def __init__(self, username):
        self.token = self.call('POST', '/auth/login', {'username': username, 'password': PASSWORD}, auth=False)['access_token']

    def call(self, method, path, body=None, auth=True, expect=None):
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(BASE + urllib.parse.quote(path, safe='/?=&:%,'), method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data, status = res.read(), res.status
        except urllib.error.HTTPError as e:
            data, status = e.read(), e.code
        if expect is not None and status != expect:
            raise AssertionError(f'{method} {path}: expected {expect}, got {status}: {data[:300]!r}')
        return json.loads(data) if data else None


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print('ok  ', msg)


eng = Client('admin')
bl = eng.call('POST', f'/parts/{TOP}/baselines', dict(
    reason='【测试】就绪检查', scope_note='测试范围', project_code='SIM-IMA-V1', copy_from_current=True), expect=201)
bid = bl['id']
try:
    val = eng.call('GET', f'/baselines/{bid}/validate', expect=200)
    checks = val['checks']
    check(all({'category', 'level', 'title', 'detail', 'href'} <= set(c) for c in checks), '每项检查都带类别/级别/说明/直达链接字段')
    check(all(c['level'] in ('BLOCK', 'WARN', 'PASS') for c in checks), '级别只有 BLOCK/WARN/PASS')
    check(val['errors'] == [c['title'] for c in checks if c['level'] == 'BLOCK'], 'errors 与 BLOCK 项一致(向后兼容)')
    check(val['warnings'] == [c['title'] for c in checks if c['level'] == 'WARN'], 'warnings 与 WARN 项一致(向后兼容)')
    check(any(c['category'] == '明细完整性' and c['level'] == 'PASS' and '主设计定义' in c['title'] for c in checks),
          '复制自当前基线时主设计定义检查通过')
    same = [c for c in checks if c['category'] == '与当前基线的差异']
    check(len(same) == 1 and same[0]['level'] == 'WARN' and '完全相同' in same[0]['title'] and same[0]['href'],
          '与当前基线内容相同时给出警告和对比链接')

    # 移除主设计定义 -> 阻止发布, 并指出原因
    full = eng.call('GET', f'/baselines/{bid}', expect=200)
    primary = next(i for i in full['items'] if i['item_type'] == 'FILE_REVISION' and i['item_role'] == 'PRIMARY_DEFINITION')
    check(primary.get('file_revision_id'), '明细带有版次 id, 便于界面直达')
    eng.call('DELETE', f'/baselines/items/{primary["id"]}', expect=200)
    val = eng.call('GET', f'/baselines/{bid}/validate', expect=200)
    check(val['passed'] is False, '缺少主设计定义时校验不通过')
    check(any('INV-022' in c['title'] and c['level'] == 'BLOCK' for c in val['checks']), '阻止项指出 INV-022')
    check(val['checks'][0]['level'] in ('BLOCK', 'WARN', 'PASS'), '检查结果可被排序展示')
finally:
    eng.call('POST', f'/baselines/{bid}/cancel?reason=cleanup')
print('ALL OK')
