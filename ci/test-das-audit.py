"""独立监督与内部审核（M2，UG-DAP-13）。

按判据 I-总 以反例为主。本模块有两条与别的模块不同的要害：

  **判据 M2-2：独立权限域，admin 角色也写不进去。**别的模块把写权限挂在
  `DAS_*_MANAGE` 上；这里不能——挂在角色上，持有该角色的人（包括 admin）就能写
  监督记录，I6／I7 就落空了。所以第一组用例就是：带全部角色的 admin 在没有岗位
  任命时，一条监督记录都写不了。

  **判据 M2-1：两类活动不能互相充当。**独立监督覆盖符合性检查单的适用项，质量
  系统内部审核覆盖部门与过程。用例验类别与计划不一致被拒、检查单覆盖只能记在
  独立监督上、以及两类的周期状态分别算。

其余覆盖：
  054  计划周期不得超过 12 个月（AP-21-18 的 24 个月是局方周期，填不进来）
  055  计划未经责任经理批准不得实施
  057  准则须覆盖 CCAR-21；独立监督还须含符合性检查单
  058  报告须有发现记录、不符合须当场确认、收件人须是在任责任经理
  059  审核员不得审核其本人负责的活动
  060  独立监督职能不能自我审核；须责任经理组织
  061  主审须有有效的审核员资格
  062  监督记录 append-only

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-audit.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
TODAY = dt.date.today()


def d(days):
    return str(TODAY + dt.timedelta(days=days))


def rejected(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False


def violates(rule, client, method, path, body=None):
    """构造一次违规并确认拦下它的是**哪一条**规则。

    client 必须传: 本模块的写入闸门是岗位任命, 用没有任命的账号去试, 会先拿到 403,
    根本到不了要验的那条规则 —— 这正是判据 M2-2 的效果, 但它不该把别的用例也一起挡掉。
    """
    r = client.call(method, path, body, expect=409)
    got = ((r or {}).get('error') or {}).get('rule')
    check(got == rule, '%s 拦下（实际报出 %s）' % (rule, got))
    return r


def appoint(user, code):
    admin.call('POST', '/das/appointments',
               {'user_id': user.id, 'position_code': code, 'kind': 'FORMAL'}, expect=201)


# ================= 判据 M2-2: admin 写不进监督记录 =================
# admin 持 SYSTEM_ADMIN + CONFIGURATION_MANAGER, 在别的模块里什么都能写。
# 这里它没有岗位任命, 所以一条都写不了 —— 这正是 I6／I7 要的。
PLAN = {'kind': 'DAS_SUPERVISION', 'period_from': d(-30), 'period_to': d(300),
        'scope_note': 'DOA 符合性检查单全部适用项'}
r = admin.call('POST', '/das/audit/plans', PLAN, expect=403)
check('岗位任命' in str(r) and '角色' in str(r),
      'M2-2 拒绝理由说明写入授权来自岗位任命而非账号角色')
print('ok   M2-2 admin（SYSTEM_ADMIN + CONFIGURATION_MANAGER）无岗位任命时写不了监督计划')
print('     （这是独立权限域的实质: 被监督对象无写权限, 系统管理员角色也不例外）')

admin.call('POST', '/das/audit/auditors', {
    'user_id': admin.id, 'method_training_ref': 'a', 'ccar21_training_ref': 'b',
    'manual_training_ref': 'c'}, expect=403)
print('ok   M2-2 无适航管理负责人任命者不得确认审核员资格（步骤 4）')

# 读是开放的: 谁在监督、覆盖率多少不是内部机密, 遮起来反而让人猜。
cyc = admin.call('GET', '/das/audit/cycle-status', expect=200)
check(len(cyc) == 2, '两类活动的周期状态分别列出（%d 条）' % len(cyc))
check({c['kind'] for c in cyc} == {'DAS_SUPERVISION', 'QMS_AUDIT'}, 'M2-1 两类各一条')
print('ok   M2-1 周期状态分别算: %s'
      % '；'.join('%s=%s' % (c['kind'], c['cycle_state']) for c in cyc))

# ================= 造岗位 =================
ism = make_user(admin, 'CONFIGURATION_MANAGER', 'ism')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'am')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'awm')
aud = make_user(admin, 'ENGINEER', 'aud')
own = make_user(admin, 'ENGINEER', 'own')
ind = make_user(admin, 'ENGINEER', 'ind')
for u, tag in ((ism, 'ISM'), (am, 'AM'), (awm, 'AWM'), (aud, 'AUD'),
               (own, 'OWN'), (ind, 'IND')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, code in ((ism, 'ISM'), (am, 'AM'), (awm, 'AWM')):
    appoint(u, code)
print('ok   已任命独立监督负责人、责任经理、适航管理负责人（三个不同自然人）')

# ================= 判据 054: 周期上限 12 个月 =================
r = violates('DCMS-INV-054', ism, 'POST', '/das/audit/plans',
             {**PLAN, 'period_from': d(-30), 'period_to': d(700)})
check('24 个月' in str(r) and '局方' in str(r),
      '054 的理由点明 24 个月是局方周期, 不是本单位的')
print('ok   DCMS-INV-054 计划周期超过 12 个月被拒, 且说明 24 个月是局方的监督周期')
print('     （AP-21-18 4.2 的 24 个月一旦混用, 内部监督就晚一年——判据 L10）')

# ================= 判据 M2-1: 两类计划分别编制 =================
DOC = 'DAF05A-' + STAMP
p1 = ism.call('POST', '/das/audit/plans',
                  {**PLAN, 'document_ref': DOC}, expect=201)
p2 = ism.call('POST', '/das/audit/plans',
                  {'kind': 'QMS_AUDIT', 'period_from': d(-30), 'period_to': d(300),
                   'document_ref': DOC,
                   'scope_note': '质量系统全部过程、活动和部门, 并覆盖 CCAR-21 适用要求'},
                  expect=201)
ps = [x for x in admin.call('GET', '/das/audit/plans', expect=200)
      if x['document_ref'] == DOC]
check(len(ps) == 2 and {x['kind'] for x in ps} == {'DAS_SUPERVISION', 'QMS_AUDIT'},
      'M2-1 合并编制为两份计划共用文件号, 周期与范围分别标明')
check(all(x['scope_note'] for x in ps), 'M2-1 两类的覆盖范围各自写明')
print('ok   M2-1 两类计划共用一份文件号, 但各自的覆盖范围分别标明')

ism.call('POST', '/das/audit/plans',
             {**PLAN, 'scope_note': '', 'document_ref': DOC}, expect=422)
print('ok   不写覆盖范围被拒（不写就等于把两类的区分合并掉了）')

# ================= 审核员资格（步骤 4）=================
awm.call('POST', '/das/audit/auditors',
             {'user_id': aud.id, 'method_training_ref': '审核方法培训',
              'ccar21_training_ref': '', 'manual_training_ref': '体系文件培训'}, expect=422)
print('ok   三项培训证据缺一不可（只会审核方法而未学 CCAR-21, 结论就是"依据不全"）')

awm.call('POST', '/das/audit/auditors',
             {'external_name': '外部甲', 'external_org': '某机构',
              'method_training_ref': 'a', 'ccar21_training_ref': 'b',
              'manual_training_ref': 'c'}, expect=400)
print('ok   外单位审核员须保存资格证明（步骤 4 末句）')

a_aud = awm.call('POST', '/das/audit/auditors', {
    'user_id': aud.id, 'method_training_ref': '审核方法培训 考核合格',
    'ccar21_training_ref': 'CCAR-21 培训 考核合格',
    'manual_training_ref': 'UG-DAM-01 及全部程序培训', 'valid_from': d(-60)}, expect=201)
a_own = awm.call('POST', '/das/audit/auditors', {
    'user_id': own.id, 'method_training_ref': '审核方法培训',
    'ccar21_training_ref': 'CCAR-21 培训', 'manual_training_ref': '体系文件培训',
    'valid_from': d(-60)}, expect=201)
a_ext = awm.call('POST', '/das/audit/auditors', {
    'external_name': '外部甲 ' + STAMP, 'external_org': '某适航咨询机构',
    'method_training_ref': 'a', 'ccar21_training_ref': 'b', 'manual_training_ref': 'c',
    'external_evidence_ref': '资格证书扫描件 EXT-' + STAMP}, expect=201)
print('ok   审核员资格已确认（本单位 2 名、外单位 1 名, 均由适航管理负责人确认）')

# ================= 判据 055: 计划未批准不得实施 =================
item = ism.call('POST', '/das/audit/plans/%d/items' % p1['id'],
                    {'seq': 1, 'scope_kind': 'CHECKLIST',
                     'scope_ref': 'CCAR-21.5 事件报告与 UG-DAP-12',
                     'scope_owner': own.id}, expect=201)
AUD = {'kind': 'DAS_SUPERVISION', 'plan_item_id': item['id'],
       'scope_ref': 'CCAR-21.5 事件报告与 UG-DAP-12', 'scope_owner': own.id,
       'criteria_ccar21': True, 'criteria_ap2118_d': True, 'criteria_checklist': True,
       'criteria_manual': True, 'lead_auditor': a_aud['id'],
       'conducted_from': d(-5), 'conducted_to': d(-3)}
violates('DCMS-INV-055', ism, 'POST', '/das/audit', {**AUD, 'audit_ref': 'A-X-' + STAMP})
print('ok   DCMS-INV-055 计划未经责任经理批准不得实施（步骤 3）')

ism.call('POST', '/das/audit/plans/%d/approve' % p1['id'], expect=403)
print('ok   独立监督负责人不能自己批准自己编的计划（批准须在任责任经理）')
am.call('POST', '/das/audit/plans/%d/approve' % p1['id'], expect=200)
am.call('POST', '/das/audit/plans/%d/approve' % p2['id'], expect=200)
am.call('POST', '/das/audit/plans/%d/approve' % p1['id'], expect=400)
print('ok   责任经理批准两份计划, 重复批准被拒')

# ================= 判据 057: 审核准则 =================
violates('DCMS-INV-057', ism, 'POST', '/das/audit',
         {**AUD, 'audit_ref': 'A-ISO-' + STAMP, 'criteria_ccar21': False,
          'criteria_other': 'ISO9001:2015 + AS9100D'})
print('ok   DCMS-INV-057 准则只写 ISO9001/AS9100 被拒（第 7 章: 视为审核依据不全）')

violates('DCMS-INV-057', ism, 'POST', '/das/audit',
         {**AUD, 'audit_ref': 'A-NOCL-' + STAMP, 'criteria_checklist': False})
print('ok   DCMS-INV-057 独立监督的准则不含符合性检查单被拒（AC-21-48 3.6(6)）')

# ================= 判据 056: 两类不得互相充当 =================
violates('DCMS-INV-056', ism, 'POST', '/das/audit',
         {**AUD, 'audit_ref': 'A-MIX-' + STAMP, 'kind': 'QMS_AUDIT'})
print('ok   DCMS-INV-056 审核类别与所属计划不一致被拒（判据 M2-1）')

# ================= 判据 061: 主审资格 =================
violates('DCMS-INV-061', ism, 'POST', '/das/audit',
         {**AUD, 'audit_ref': 'A-EARLY-' + STAMP,
          'conducted_from': d(-200), 'conducted_to': d(-198)})
print('ok   DCMS-INV-061 主审在审核期间尚无有效资格被拒（步骤 4）')

a1 = ism.call('POST', '/das/audit',
                  {**AUD, 'audit_ref': 'A-1-' + STAMP,
                   'criteria_other': 'ISO9001:2015（附带, 不作为唯一依据）'}, expect=201)
print('ok   审核已登记, 准则四项齐备')

ism.call('POST', '/das/audit',
             {**AUD, 'audit_ref': 'A-ORPHAN-' + STAMP, 'plan_item_id': None,
              'trigger_id': None}, expect=400)
print('ok   既不在计划内也无触发依据的审核被拒（否则覆盖率与执行率都统计不进去）')

# ================= 判据 059: 审核员不得自审 =================
violates('DCMS-INV-059', ism, 'POST', '/das/audit/%d/auditors' % a1['id'],
         {'auditor_id': a_own['id']})
print('ok   DCMS-INV-059 审核员不得审核其本人负责的范围（第 7 章: 该次审核结果无效）')

ism.call('POST', '/das/audit/%d/auditors' % a1['id'],
             {'auditor_id': a_aud['id']}, expect=201)
ism.call('POST', '/das/audit/%d/auditors' % a1['id'],
             {'auditor_id': a_ext['id']}, expect=201)
print('ok   独立的审核员（含外单位）可加入——外单位审核员不在本单位任职, 无自审问题')

awm.call('POST', '/das/audit/auditors/%d/revoke' % a_ext['id'],
             {'reason': 'CI: 测试资格失效路径'}, expect=200)
ism.call('POST', '/das/audit/%d/auditors' % a1['id'],
             {'auditor_id': a_ext['id']}, expect=400)
print('ok   资格已撤销的审核员不得再加入审核')

# ================= 判据 058: 报告的三个前提 =================
RPT = {'report_ref': 'RPT-' + STAMP, 'conclusion': '准则覆盖 CCAR-21.5 与 UG-DAP-12',
       'issued_to_am': am.id, 'issued_to_action_owner': own.id}
violates('DCMS-INV-058', ism, 'POST', '/das/audit/%d/report' % a1['id'], RPT)
print('ok   DCMS-INV-058 一条发现都没有就出报告被拒（步骤 6: 须如实记载, 含符合项）')

ism.call('POST', '/das/audit/%d/findings' % a1['id'],
             {'clause_ref': 'CCAR-21.5(六)', 'verdict': 'NONCONFORM',
              'fact': '两起事件未在 48 小时内报送', 'evidence': '抽查 2 份 UG-DAF-08'},
             expect=400)
print('ok   不符合发现未当场确认被拒（事后补确认, 受审部门已无从核对当时的证据）')

ism.call('POST', '/das/audit/%d/findings' % a1['id'],
             {'clause_ref': 'CCAR-21.5(二)', 'verdict': 'CONFORM',
              'fact': '13 种情形逐项判断记录完整', 'evidence': '抽查 5 份均有 13 项判断'},
             expect=201)
f2 = ism.call('POST', '/das/audit/%d/findings' % a1['id'],
                  {'clause_ref': 'CCAR-21.5(六)', 'verdict': 'NONCONFORM',
                   'fact': '两起事件未在 48 小时内报送',
                   'evidence': '抽查 OC-003、OC-007 的报送时间',
                   'confirmed_with_auditee': True, 'auditee_rep': '被审部门负责人 ' + STAMP},
                  expect=201)
det = admin.call('GET', '/das/audit/%d' % a1['id'], expect=200)
check(det['findings'] == 2 and det['nonconformities'] == 1,
      '发现 2 条（1 符合 1 不符合）——符合项也记, 否则看不出审了哪些')
check(len(det['findings_detail']) == 2
      and {x['verdict'] for x in det['findings_detail']} == {'CONFORM', 'NONCONFORM'},
      '明细里两种结论都在（计数与明细是两个字段, 不互相覆盖）')
print('ok   审核发现已记录: 符合项与不符合项都在（共 %d 条）' % det['findings'])

violates('DCMS-INV-058', ism, 'POST', '/das/audit/%d/report' % a1['id'],
         {**RPT, 'issued_to_am': own.id})
print('ok   DCMS-INV-058 报告收件人不是在任责任经理被拒')
print('     （第 7 章: 审核报告须直接送责任经理, 不经被审核部门转交）')

ism.call('POST', '/das/audit/%d/report' % a1['id'], RPT, expect=201)
ism.call('POST', '/das/audit/%d/report' % a1['id'], RPT, expect=400)
print('ok   报告已出具并直送责任经理; 重复出具被拒')

# ================= 覆盖率（步骤 9）=================
items = db_query("""SELECT id FROM das_checklist_item
                     WHERE applicable_stc <> '否' OR applicable_pma <> '否'
                     ORDER BY seq LIMIT 6""")
cov = ism.call('POST', '/das/audit/%d/checklist-coverage' % a1['id'],
                   {'checklist_item_ids': [x['id'] for x in items]}, expect=201)
check(cov['coverage']['covered_items'] >= 6, '覆盖条目已登记')
check(cov['coverage']['coverage_pct'] is not None, '覆盖率可算出')
print('ok   独立监督覆盖 %d 条检查单适用项, 覆盖率 %s%%（分母为适用项, "部分"仍计入）'
      % (cov['added'], cov['coverage']['coverage_pct']))

cc = admin.call('GET', '/das/audit/checklist-coverage', expect=200)
check(cc['uncovered'] and len(cc['uncovered']) == cc['stat']['uncovered_items'],
      '未覆盖的条目逐条列出, 与统计数一致')
print('ok   未覆盖的是哪几条能列出来（%d 条）——覆盖率是个数字, 缺的条目才能动作'
      % len(cc['uncovered']))

# 质量系统内部审核不得登记检查单覆盖（判据 M2-1）
item2 = ism.call('POST', '/das/audit/plans/%d/items' % p2['id'],
                     {'seq': 1, 'scope_kind': 'DEPARTMENT', 'scope_ref': '研发部',
                      'scope_owner': own.id}, expect=201)
a2 = ism.call('POST', '/das/audit',
                  {'kind': 'QMS_AUDIT', 'plan_item_id': item2['id'],
                   'scope_ref': '研发部', 'scope_owner': own.id,
                   'criteria_ccar21': True, 'criteria_manual': True,
                   'lead_auditor': a_aud['id'], 'conducted_from': d(-2),
                   'conducted_to': d(-1), 'audit_ref': 'A-2-' + STAMP}, expect=201)
ism.call('POST', '/das/audit/%d/checklist-coverage' % a2['id'],
             {'checklist_item_ids': [items[0]['id']]}, expect=400)
print('ok   M2-1 质量系统内部审核不得登记检查单覆盖——两类覆盖对象不同, 不得互相充当')

cyc = {c['kind']: c for c in admin.call('GET', '/das/audit/cycle-status', expect=200)}
check(cyc['DAS_SUPERVISION']['cycle_state'] == 'WITHIN_CYCLE', '独立监督在周期内')
check(cyc['QMS_AUDIT']['cycle_state'] == 'WITHIN_CYCLE', '质量系统内审在周期内')
check(cyc['DAS_SUPERVISION']['plan_approved'] and cyc['QMS_AUDIT']['plan_approved'],
      '两类计划都已批准')
print('ok   两类活动的周期状态分别算出, 各有各的计划与上次审核日')

# ================= 专项审核（步骤 2）=================
trg = ism.call('POST', '/das/audit/triggers',
                   {'trigger_kind': 'KEY_PERSONNEL_CHANGE', 'occurred_on': d(-20),
                    'description': '适航管理负责人变更 ' + STAMP}, expect=201)
due = admin.call('GET', '/das/audit/triggers-due', expect=200)
check(any(x['id'] == trg['id'] for x in due), '触发了未启动的专项审核可查')
print('ok   专项审核触发事件单独登记（第 7 章: 触发情形发生而未启动的为不符合项）')
print('     （只在计划里排审核查不出这件事——没排的那次本来就不在计划里）')

a3 = ism.call('POST', '/das/audit',
                  {'kind': 'QMS_AUDIT', 'trigger_id': trg['id'],
                   'scope_ref': '关键人员变更后的专项审核', 'scope_owner': own.id,
                   'criteria_ccar21': True, 'criteria_manual': True,
                   'lead_auditor': a_aud['id'], 'conducted_from': d(-1),
                   'conducted_to': d(0), 'audit_ref': 'A-SP-' + STAMP}, expect=201)
due2 = admin.call('GET', '/das/audit/triggers-due', expect=200)
check(not any(x['id'] == trg['id'] for x in due2), '启动专项审核后不再列为待启动')
print('ok   启动专项审核后该触发事件出清')

# ================= 判据 060: 独立监督职能不能自我审核 =================
ISMA = {'report_ref': 'ISMA-' + STAMP, 'period_from': d(-300), 'period_to': d(-1),
        'is_external': False,
        'finding_plan_completeness': '计划覆盖适用项的 85%, 缺供应商环节',
        'finding_execution_rate': '计划执行率 92%',
        'finding_adequacy': '发现问题充分, 但对供应商环节抽样偏少',
        'finding_car_closure': 'NCR 闭环质量良好, 2 项超期',
        'finding_auditor_qual': '审核员资格均在有效期内',
        'finding_independence': '独立性实际保持, 未见自审情况'}
ism.call('POST', '/das/audit/ism-function', ISMA, expect=403)
print('ok   DCMS-INV-060 独立监督负责人不得组织对本职能的审核（须责任经理组织）')

am.call('POST', '/das/audit/ism-function',
        {**ISMA, 'auditor_user_id': ism.id, 'designation_basis': '指定',
         'chaired_by_am': True, 'auditor_capability_note': '具备能力'}, expect=409)
print('ok   DCMS-INV-060 实施人持独立监督负责人任命被拒')
print('     （第 7 章: 由该职能自行出具的自查结论不作为符合性证据）')

am.call('POST', '/das/audit/ism-function',
        {**ISMA, 'auditor_user_id': ind.id}, expect=400)
print('ok   非外部审核员却不写指定依据与能力说明被拒')

am.call('POST', '/das/audit/ism-function',
        {**ISMA, 'auditor_user_id': ind.id, 'designation_basis': '责任经理书面指定',
         'auditor_capability_note': '曾任质量工程师, 未参与独立监督活动',
         'chaired_by_am': False}, expect=400)
print('ok   由本单位指定人员实施时须责任经理亲自主持')
print('     （不主持就把这次审核交回给了被审对象所在的管理链条）')

am.call('POST', '/das/audit/ism-function',
        {**ISMA, 'auditor_user_id': ind.id,
         'designation_basis': '责任经理 %s 书面指定（步骤 10, 确无外部资源）' % TODAY,
         'auditor_capability_note': '曾任质量工程师, 完成 UG-DAW-013 培训, 未参与独立监督活动',
         'chaired_by_am': True}, expect=201)
print('ok   责任经理组织、指定独立人员、亲自主持, 六项内容逐项留结论 -> 可登记')

ismd = admin.call('GET', '/das/audit/ism-function/due', expect=200)
check(ismd['state'] in ('WITHIN_CYCLE', 'OVERDUE'), '对独立监督职能的审核已有记录')
check(ismd['designated_audits'] >= 1 and ismd['external_audits'] == 0,
      '区分外部实施与指定实施（本例为指定）')
print('ok   步骤 10 到期状态可查: %s, 外部 %d 次 / 指定 %d 次'
      % (ismd['state'], ismd['external_audits'], ismd['designated_audits']))

# ================= 判据 062: append-only =================
check(rejected("DELETE FROM das_audit WHERE id=%s", (a1['id'],)),
      'DCMS-INV-062 审核记录不得删除')
check(rejected("UPDATE das_audit_finding SET verdict='CONFORM' WHERE id=%s", (f2['id'],)),
      '审核发现不得改写')
check(rejected("UPDATE das_audit_report SET conclusion='改掉' WHERE audit_id=%s",
               (a1['id'],)), '审核报告不得改写')
check(rejected("DELETE FROM das_audit_plan WHERE id=%s", (p1['id'],)), '计划不得删除')
check(rejected("DELETE FROM das_ism_function_audit WHERE report_ref=%s",
               ('ISMA-' + STAMP,)), '对独立监督职能的审核记录不得删除')
print('ok   DCMS-INV-062 监督记录 append-only: 审核、发现、报告、计划、职能审核均不可删改')

reg = admin.call('GET', '/das/audit', expect=200)
mine = [x for x in reg if x['id'] in (a1['id'], a2['id'], a3['id'])]
check(len(mine) == 3, '台账含本次三次审核')
print('ok   审核台账可查（本次 3 次审核: 1 次独立监督、1 次内审、1 次专项）')

print()
print('全部通过: 独立监督与内部审核')
