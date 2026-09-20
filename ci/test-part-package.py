"""件号资料包集成测试(真实 HTTP + PostgreSQL, 需已装载 SIM-IMA-V1 演示数据)。

用法: python ci/test-part-package.py <base_url> <password> <top_part_number>
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

    def call(self, method, path, body=None, auth=True, expect=None, raw=False):
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(BASE + urllib.parse.quote(path, safe='/?=&:%,.'), method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data, status, ctype = res.read(), res.status, res.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            data, status, ctype = e.read(), e.code, ''
        if expect is not None and status != expect:
            raise AssertionError(f'{method} {path}: expected {expect}, got {status}: {data[:300]!r}')
        if raw:
            return data, ctype
        return json.loads(data) if data else None


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print('ok  ', msg)


viewer = Client('demo_auditor')          # 只读账户, 代表生产/采购
pkg = viewer.call('GET', f'/parts/{TOP}/release-package', expect=200)
cb = pkg['current_baseline']
check(cb and cb['baseline_code'], '返回当前基线')
check(pkg['documents'], '包含基线锁定的设计文件')
check(all(d['attachments'] for d in pkg['documents'][:1]), '文件带可下载的附件')
check(all(d['newer_available'] == (bool(d['latest_revision']) and d['latest_revision'] != d['revision_number'])
          for d in pkg['documents']), '“有更新版次”标记与锁定版次/最新版次一致')
check(pkg['bom'] and {'item_number', 'child_object_code', 'quantity'} <= set(pkg['bom'][0]), '包含 BOM 快照明细')
check(pkg['change_from_previous'] is None or 'from_baseline' in pkg['change_from_previous'], '相对上一基线的变化结构正确')

# 附件可被只读账户下载
att = pkg['documents'][0]['attachments'][0]
data, _ = viewer.call('GET', f'/attachments/{att["id"]}/download', expect=200, raw=True)
check(len(data) == att['size_bytes'], '只读账户可下载附件且大小一致')

# CSV 导出
raw, ctype = viewer.call('GET', f'/parts/{TOP}/release-package.csv', expect=200, raw=True)
text = raw.decode('utf-8')
check(text.startswith('﻿'), 'CSV 带 UTF-8 BOM(Excel 直接打开不乱码)')
check('类别,编号,名称/说明' in text and '设计文件' in text and 'BOM' in text, 'CSV 含表头与各类行')
check(TOP in text.splitlines()[0], 'CSV 首行标明件号')

viewer.call('GET', '/parts/NO-SUCH-PN/release-package', expect=404)
print('ok   不存在的件号返回 404')
print('ALL OK')
