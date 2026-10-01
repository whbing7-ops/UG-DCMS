"""DOA 符合性检查单主数据集成测试（真实 HTTP + PostgreSQL）。

按判据 I-总 的做法写: 每条约束配一个"尝试违反并确认被拒绝"的用例, 而不是只验
正常路径能走通。正常路径能走通几乎不说明什么——约束是不是真的拦得住, 只有反例
能证明。

覆盖 UG-RPT-2026-003 G 版第十一章界定范围内的判据:
  EV1  条款级引用, 版本必填
  EV2  文件改版反查受影响条目
  EV3  自评说明可引运行证据(索引)
  EV4-2 提交口径只含前 5 列
  EV5  不适用项须写理由
  EV9-1 失效只标自评, 不回退覆盖记录
  EV9-2 自评 append-only, 改判不覆盖历史
  C5   有引用不等于符合; 结论由人给
  S1   体系级与项目级检查单是不同实体
  S2   变更评估反查
  S3   导出带日期、操作人

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-checklist.py <credentials.json>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import (check, db_execute, db_query, login_admin, make_user)  # noqa: E402


def rejected(sql, params=()):
    """数据库是否拒绝了这条写入。约束要靠反例证明, 不是靠正常路径能走通证明。"""
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False

admin = login_admin()
eng = make_user(admin, 'ENGINEER', 'ck')
viewer = make_user(admin, 'VIEWER', 'cv')

# 种一条条目直接入库: 66 条正式内容属数据导入, 不在本测试范围。
db_execute("""INSERT INTO das_checklist_item (seq, req_code, req_name, req_text, req_source)
              VALUES (9001, 'CCAR-21.TEST', '【测试】条目', '测试用要求原文', 'CCAR-21')
              ON CONFLICT (seq) DO NOTHING""")
ITEM = db_query("SELECT id FROM das_checklist_item WHERE seq=9001")[0]['id']

# ---------------- 权限 ----------------
viewer.call('GET', '/das/checklist', expect=200)
print('ok   任何登录用户可查看检查单')
eng.call('POST', f'/das/checklist/{ITEM}/assess',
         {'conclusion': '符合', 'statement': '测试'}, expect=403)
print('ok   工程师不能写自评(403)')
eng.call('GET', '/das/checklist/export/submission', expect=403)
print('ok   工程师不能导出提交件(403)')

# ---------------- EV1: 条款级引用, 版本必填 ----------------
admin.call('POST', f'/das/checklist/{ITEM}/doc-refs',
           {'doc_code': 'UG-DAP-12', 'doc_name': '事件报告程序', 'doc_version': '', 'clause': '第6章'},
           expect=422)
print('ok   EV1 引用缺版本被拒(422)')
ref = admin.call('POST', f'/das/checklist/{ITEM}/doc-refs',
                 {'doc_code': 'UG-DAP-12', 'doc_name': '事件报告程序',
                  'doc_version': '00', 'clause': '第6章第4步'}, expect=201)
print('ok   EV1 完整引用可建立')

# ---------------- C5: 有引用不等于符合 ----------------
detail = admin.call('GET', f'/das/checklist/{ITEM}', expect=200)
check(not detail['assessments'], 'C5 挂了引用之后自评仍为空, 系统没有自动改判为符合')
print('ok   C5 有引用不自动改判')

# ---------------- 自评的三条拒绝 ----------------
admin.call('POST', f'/das/checklist/{ITEM}/assess',
           {'conclusion': '符合', 'statement': ''}, expect=422)
print('ok   自评说明为空被拒(422)')
admin.call('POST', f'/das/checklist/{ITEM}/assess',
           {'conclusion': '部分符合', 'statement': '部分做到'}, expect=400)
print('ok   结论非"符合"而无完善计划被拒(400)')
admin.call('POST', f'/das/checklist/{ITEM}/assess',
           {'conclusion': '基本符合', 'statement': '测试'}, expect=422)
print('ok   结论取值不在三选一内被拒(422)')

# ---------------- EV3: 自评可引运行证据 ----------------
a1 = admin.call('POST', f'/das/checklist/{ITEM}/assess', {
    'conclusion': '部分符合', 'statement': '文件规定已到位, 实施待验证',
    'improvement_plan': '首次监督后补证据',
    'evidence': [{'kind': 'record', 'ref': 'REC-TEST-001', 'note': '测试记录'}],
}, expect=201)
print('ok   EV3 自评可挂运行证据索引')
check(db_query("SELECT count(*) c FROM das_checklist_evidence WHERE assessment_id=%s",
               (a1['id'],))[0]['c'] == 1, 'EV3 证据索引已落库')

# ---------------- EV9-2: 自评 append-only ----------------
check(rejected("UPDATE das_checklist_assessment SET conclusion='符合' WHERE id=%s", (a1['id'],)),
      'EV9-2 数据库拒绝改写自评')
print('ok   EV9-2 自评不可改写')
check(rejected("DELETE FROM das_checklist_assessment WHERE id=%s", (a1['id'],)),
      'EV9-2 数据库拒绝删除自评')
print('ok   EV9-2 自评不可删除')

a2 = admin.call('POST', f'/das/checklist/{ITEM}/assess',
                {'conclusion': '符合', 'statement': '实施已验证'}, expect=201)
detail = admin.call('GET', f'/das/checklist/{ITEM}', expect=200)
check(len(detail['assessments']) == 2, 'EV9-2 改判是新增一行, 原评价仍在')
print('ok   EV9-2 改判留痕, 历史评价可回溯')

# ---------------- EV9-1: 失效只标自评, 不回退覆盖 ----------------
db_execute("""INSERT INTO das_checklist_coverage
                     (item_id, audit_ref, audit_kind, cycle_start, covered_at, result)
              VALUES (%s, 'AUD-TEST-001', 'internal', current_date, current_date, '符合项')""",
           (ITEM,))
cov_before = db_query("SELECT count(*) c FROM das_checklist_coverage WHERE item_id=%s", (ITEM,))[0]['c']
flag = admin.call('POST', f'/das/checklist/{ITEM}/flags',
                  {'reason': 'authorization_revoked', 'source_ref': 'AUTH-TEST-001'}, expect=201)
cov_after = db_query("SELECT count(*) c FROM das_checklist_coverage WHERE item_id=%s", (ITEM,))[0]['c']
check(cov_before == cov_after, 'EV9-1 标记待复核不回退监督覆盖记录')
print('ok   EV9-1 覆盖是历史事实, 不因证据失效而回退')

admin.call('POST', f'/das/checklist/{ITEM}/flags',
           {'reason': '随便写', 'source_ref': 'X'}, expect=422)
print('ok   待复核原因不在枚举内被拒(422)')
admin.call('POST', f'/das/checklist/flags/{flag["id"]}/clear', {'note': ''}, expect=422)
print('ok   关闭待复核标记不写复核结论被拒(422)')
admin.call('POST', f'/das/checklist/flags/{flag["id"]}/clear', {'note': '已复核, 签署有效'}, expect=200)
admin.call('POST', f'/das/checklist/flags/{flag["id"]}/clear', {'note': '重复关闭'}, expect=400)
print('ok   重复关闭同一标记被拒(400)')

# ---------------- EV2 + S2: 文件改版的反查与传播 ----------------
impact = admin.call('GET', '/das/checklist/impact/UG-DAP-12?doc_version=00', expect=200)
check(any(r['seq'] == 9001 for r in impact), 'S2 按文件+版本反查到受影响条目')
print('ok   S2 变更评估可反查受影响条目')

res = admin.call('POST', '/das/checklist/doc-revision',
                 {'doc_code': 'UG-DAP-12', 'old_version': '00', 'new_version': '01'}, expect=200)
check(res['affected_items'] >= 1, 'EV2 改版识别出受影响条目')
sup = db_query("""SELECT superseded_at FROM das_checklist_doc_ref WHERE id=%s""", (ref['id'],))[0]
check(sup['superseded_at'] is not None, 'EV2 旧版本引用置失效日期')
check(db_query("SELECT count(*) c FROM das_checklist_doc_ref WHERE id=%s", (ref['id'],))[0]['c'] == 1,
      'EV2 旧引用留行不删, 历史评价的依据还在')
print('ok   EV2 改版: 旧引用置失效不删行, 受影响条目挂待复核')
open_flags = db_query("""SELECT count(*) c FROM das_checklist_review_flag
                          WHERE item_id=%s AND reason='doc_revised' AND cleared_at IS NULL""", (ITEM,))
check(open_flags[0]['c'] == 1, 'EV2 受影响条目已挂 doc_revised 标记')

# 自评未被系统擅自改写
detail = admin.call('GET', f'/das/checklist/{ITEM}', expect=200)
check(len(detail['assessments']) == 2, 'EV9-2 传播不擅自改写自评, 仍是两条')
print('ok   EV9-2 传播只提示复核, 不代人改自评')

# ---------------- EV5: 适用性三值, 不适用项须写理由 ----------------
check(rejected("""INSERT INTO das_checklist_item
                         (seq, req_code, req_name, req_source, applicable_stc, applicable_pma)
                  VALUES (9002, 'X', 'Y', 'CCAR-21', '否', '否')"""),
      'EV5 两个适用性均为否而无理由被拒')
print('ok   EV5 不适用项必须写理由, 否则分母会被悄悄缩小')
check(rejected("""INSERT INTO das_checklist_item
                         (seq, req_code, req_name, req_source, applicable_stc)
                  VALUES (9003, 'X', 'Y', 'CCAR-21', '待定')"""),
      'EV5 适用性取值不在 是/否/部分 内被拒')
print('ok   EV5 适用性只能是 是/否/部分')
db_execute("""INSERT INTO das_checklist_item
                     (seq, req_code, req_name, req_source, applicable_stc, applicable_pma)
              VALUES (9004, 'P', '【测试】部分适用', 'CCAR-21', '部分', '否')
              ON CONFLICT (seq) DO NOTHING""")
den = db_query("""SELECT count(*) c FROM das_checklist_item
                   WHERE seq IN (9001, 9004) AND (applicable_stc <> '否' OR applicable_pma <> '否')""")
check(den[0]['c'] == 2, 'EV5 "部分"计入覆盖率分母, 不被当作不适用排除')
print('ok   EV5 "部分适用"仍计入分母——部分适用也要覆盖')

# ---------------- EV4-2 / S3: 提交口径只含前 5 列 ----------------
sub = admin.call('GET', '/das/checklist/export/submission', expect=200)
check(sub['exported_by'] and sub['exported_at'], 'S3 导出件带操作人和导出日期')
row = next(r for r in sub['rows'] if r['序号'] == 9001)
check(set(row) == {'序号', '要求编号、名称', '要求内容',
                   '设计保证系统文件编号、名称、版本', '符合性自评说明'},
      'EV4-2 提交口径只有前 5 列, 内部辅助列不外流')
check('独立监督覆盖' not in row and '自评结论' not in row, 'EV4-2 内部辅助列确实不在导出件里')
print('ok   EV4-2/S3 导出只含前 5 列, 带操作人与日期')

# ---------------- S1: 体系级与项目级是不同实体 ----------------
# 模式必须走参数: psycopg 在传了 params 时会解析 SQL 里的 %, 字面量 '…%' 会被
# 当成坏占位符而抛 ProgrammingError。而模式要用 %checklist% 而不是 das_checklist%,
# 否则下一条断言是空的——项目级表会叫 das_project_checklist_item, 压根落不进
# das_checklist 开头的范围里, 那句检查就永远不可能失败。
tables = {r['table_name'] for r in db_query(
    "SELECT table_name FROM information_schema.tables"
    " WHERE table_schema='public' AND table_name LIKE %s",
    ('%checklist%',))}
check('das_checklist_item' in tables, 'S1 体系级检查单表存在')
check(not any('project' in t for t in tables),
      'S1 本迁移不建项目级检查单表, 它属 M3 的另一个实体')
print('ok   S1 体系级与项目级检查单未被合并')

print()
print('全部通过: DOA 符合性检查单主数据')
