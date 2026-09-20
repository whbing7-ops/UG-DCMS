"""件号资料包集成测试(真实 HTTP + PostgreSQL, 需已装载 SIM-IMA-V1 演示数据)。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-part-package.py <credentials.json>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dcms_http import (Client, FINAL_PASSWORD, STAMP, check, db_execute,  # noqa: E402,F401
                       login_admin, make_user, top_part_number)

TOP = top_part_number()


viewer = make_user(login_admin(), 'VIEWER')          # 只读账户; 生产/采购的过滤见 test-consumer-roles.py
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
_, data = viewer.call('GET', f'/attachments/{att["id"]}/download', expect=200, raw=True)
check(len(data) == att['size_bytes'], '只读账户可下载附件且大小一致')

# CSV 导出
_, raw = viewer.call('GET', f'/parts/{TOP}/release-package.csv', expect=200, raw=True)
text = raw.decode('utf-8')
check(text.startswith('﻿'), 'CSV 带 UTF-8 BOM(Excel 直接打开不乱码)')
check('类别,编号,名称/说明' in text and '设计文件' in text and 'BOM' in text, 'CSV 含表头与各类行')
check(TOP in text.splitlines()[0], 'CSV 首行标明件号')

viewer.call('GET', '/parts/NO-SUCH-PN/release-package', expect=404)
print('ok   不存在的件号返回 404')
print('ALL OK')
