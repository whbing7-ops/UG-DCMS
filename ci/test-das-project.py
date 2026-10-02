"""M3 项目实体与三条审定路径（第二批，判据 M3-1）。

按判据 I-总 以反例为主。这块最容易做错的是**把三条路径做成两条**——第三类
（设计供应商受托）沿着 STC 的分支走下去，迟早会走到"签符合性声明"，而那一签就是对
委托方的产品作了设计批准：这是越权，不是流程瑕疵（UG-DAP-07 第 12 步 d、
AP-21-18 7.1(5)）。所以用例的重点是证明第三类走不到那几步。

覆盖：
  M3-1 三类的步骤**集合**必须不同（条数不行：STC 与 PMA 都是 30 步）
  091  项目编号前缀由类型决定，格式按 UG-DAW-002 第 5 章
  092  不适用于本类型的步骤不得登记；"首次申请 DOA"不属于任何产品项目
  093  PMA 未确认生产质量系统不得提交申请；四项有一项为否不算确认具备；
       前置步骤登记了而实质记录不存在同样不放行——拦的是"没确认"不是"没登记"
  094  第三类不得签符合性声明；非在任责任经理签署须写明纸面授权依据
  095  第三类不得产生设计批准；批准类别与项目类型须一致
  096  没有符合性声明不得登记设计批准（取证顺序）
  097  转段三项未齐不得结项
  098  步骤记录、声明、设计批准、转段记录均不得抹改
  099  立项未经责任经理批准不得登记后续步骤

【每个反例只能因为它自己那条规则失败】
写这个用例时踩过一次：测"声明编号不含 -SM-"时没写授权依据，结果被 094 先拦下，
编号校验根本没跑到，而断言照样通过——这条用例就只是在重测 094。所以下面每个反例
前都先把别的前置条件补齐，让待测的那条成为唯一的失败点。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-project.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
TODAY = dt.date.today()
YY = TODAY.strftime('%y')


def free_no(prefix):
    """挑一个本库里还没用过的项目编号。

    编号格式被 UG-DAW-002 限成"两位序号＋版本字母＋两位年份", 可选空间不大, 而项目
    记录不能删。靠 STAMP 取模有碰撞概率, 碰上了就是莫名其妙的唯一约束失败, 所以直接查。
    """
    used = {r['project_no'] for r in db_query(
        "SELECT project_no FROM das_project WHERE project_no LIKE %s", (prefix + '%',))}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        for seq in range(1, 100):
            no = '%s%02d%s%s' % (prefix, seq, letter, YY)
            if no not in used:
                return no
    raise AssertionError('项目编号用尽: ' + prefix)


STC_NO = free_no('UG-STC')
PMA_NO = free_no('UG-PMA')
SUP_NO = free_no('UG-SUP')


def reason(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error as e:
        return str(e)
    return ''


# ================= 判据 M3-1：三条路径 =================
types = {t['code']: t for t in admin.call('GET', '/das/projects/types', expect=200)}
check(set(types) == {'STC', 'PMA', 'SUP'}, '三条审定路径都在（不是两条）')
check(types['SUP']['yields_design_approval'] is False
      and types['SUP']['signs_compliance_statement'] is False,
      '第三类不出符合性声明、不产生设计批准（UG-DAP-07 第 12 步 d）')
check(all(types[c]['yields_design_approval'] for c in ('STC', 'PMA')),
      '前两类产出设计批准')
print('ok   三条审定路径: STC / PMA / 设计供应商受托（后者不出声明、不出批准）')

cov = admin.call('GET', '/das/projects/step-coverage', expect=200)
counts = {c['code']: c for c in cov['counts']}
check(sum(1 for c in counts.values() if c['steps']) == 3, '三类都有适用步骤')
# 【条数比不出来】STC 与 PMA 都是 30 步，各自独有一条，条数正好抵平
check(counts['STC']['steps'] == counts['PMA']['steps'],
      'STC 与 PMA 步骤数相同（%d）——所以不能拿条数证明参数化起作用'
      % counts['STC']['steps'])
exc = {e['code']: e for e in cov['exclusive']}
check(exc['STC']['exclusive_steps'] and exc['PMA']['exclusive_steps']
      and exc['SUP']['exclusive_steps'],
      '三类各有独有步骤——按集合比才看得出差别（判据 M3-1）')
check('DAP09.4' in exc['STC']['exclusive_steps'], 'STC 独有「STC 要点核对」（原文明定）')
check('DAP09.3' in exc['PMA']['exclusive_steps'],
      'PMA 独有「生产质量系统确认」（原文明定仅 PMA 项目）')
check('DAP07.12' in exc['SUP']['exclusive_steps'],
      '第三类独有「作为设计供应商承接的验证工作」')
check('DAP07.11' in exc['SUP']['excluded_steps'],
      '**第三类不含符合性声明那一步**——判据 M3-1 的核心')
print('ok   M3-1 三类步骤集互不相同:')
for c in ('STC', 'PMA', 'SUP'):
    print('       %-4s 独有 %s' % (c, exc[c]['exclusive_steps'][:46]))

una = cov['unassigned']
check(len(una) == 1 and una[0]['code'] == 'DAP09.2',
      '只有「首次申请 DOA」不属于任何产品项目（它是申请许可证本身，归 M10）')
check('M10' in (una[0]['note'] or ''), '不属于产品项目的那一步写明了归属，不是挂着没人认领')
inf = {i['code']: i for i in cov['inferred']}
check(inf and all(i['note'] for i in inf.values()),
      '适用性属推断的步骤都写明了理由——自评要分清原文与推断（%s）' % '、'.join(inf))
all_steps = admin.call('GET', '/das/projects/steps', expect=200)
check(len(all_steps) == 33,
      'UG-DAP-04 的 11 ＋ UG-DAP-07 的 13 ＋ UG-DAP-09 的 9 ＝ 33 步（实际 %d）'
      % len(all_steps))
print('ok   33 步齐备; 1 步不属于产品项目并写明归 M10; %d 步的适用性属推断且有理由'
      % len(inf))

# ================= 闸门按岗位任命（三份不同的文件）=================
plain = make_user(admin, 'CONFIGURATION_MANAGER', 'pjplain')
r = plain.call('POST', '/das/projects',
               {'project_no': STC_NO, 'type_code': 'STC', 'name_cn': '编的'}, expect=403)
check('资料管理负责人' in str(r), '立项与编号归资料管理负责人（UG-DAW-002 第 5 章）')

dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'pjdcm')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'pjam')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'pjawm')
pm = make_user(admin, 'CONFIGURATION_MANAGER', 'pjpm')
for u, tag in ((dcm, 'PJDCM'), (am, 'PJAM'), (awm, 'PJAWM'), (pm, 'PJPM')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, pos in ((dcm, 'DCM'), (am, 'AM'), (awm, 'AWM'), (pm, 'PM')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': pos, 'kind': 'FORMAL'}, expect=201)
print('ok   三道门槛来自三份文件: 立项编号→DCM、批准立项→AM、质量确认与取证→AWM')

# ================= 反例 091：编号前缀与格式 =================
r = dcm.call('POST', '/das/projects',
             {'project_no': 'UG-STC' + STC_NO[6:], 'type_code': 'PMA',
              'name_cn': '编的'}, expect=409)
check('UG-PMA' in str(r), 'PMA 项目用 STC 前缀被拒, 并说明该用哪个前缀')
check('归错类' in str(r), '拒绝理由说明了后果: 前缀对不上的项目在台账里会被归错类')
r = dcm.call('POST', '/das/projects',
             {'project_no': 'UG-PMA9A26', 'type_code': 'PMA', 'name_cn': '编的'},
             expect=409)
check('UG-DAW-002' in str(r), '编号格式不合 UG-DAW-002 第 5 章被拒')
print('ok   091 编号前缀是类型的一部分, 格式按 UG-DAW-002 第 5 章')

# ================= 立项 =================
stc = dcm.call('POST', '/das/projects',
               {'project_no': STC_NO, 'type_code': 'STC', 'name_cn': '座椅电源改装 ' + STAMP,
                'aircraft_type': 'B737-800', 'certification_basis': 'CCAR-25 部'},
               expect=201)
pma = dcm.call('POST', '/das/projects',
               {'project_no': PMA_NO, 'type_code': 'PMA', 'name_cn': '手持电话 PMA ' + STAMP,
                'aircraft_type': 'A320', 'caac_project_no': 'NAPMA-2025-125-XN'},
               expect=201)
sup = dcm.call('POST', '/das/projects',
               {'project_no': SUP_NO, 'type_code': 'SUP', 'name_cn': '受托验证 ' + STAMP},
               expect=201)
p = admin.call('GET', '/das/projects/%s' % PMA_NO, expect=200)
check(p['caac_project_no'] == 'NAPMA-2025-125-XN',
      '局方受理编号另行记录、与本单位编号并列（UG-DAW-002 第 5 章）')
check(p['project_no'] == PMA_NO, '局方编号不替代本单位项目编号')
print('ok   三个项目已立项; 局方受理编号与本单位编号并列登记')

# ================= 反例 099：立项未批准 =================
r = pm.call('POST', '/das/projects/%s/steps' % STC_NO,
            {'step_code': 'DAP04.1', 'record_ref': 'REC-001'}, expect=409)
check('责任经理批准立项' in str(r), '立项未批准不得登记后续步骤（UG-DAP-09 第 1 步）')
r = dcm.call('POST', '/das/projects/%s/approval' % STC_NO,
             {'approval_ref': '立项单 ' + STAMP}, expect=403)
check('责任经理' in str(r), '批准立项须责任经理, 资料管理负责人不行')
for no in (STC_NO, PMA_NO, SUP_NO):
    am.call('POST', '/das/projects/%s/approval' % no,
            {'approval_ref': '立项单 %s-%s' % (no, STAMP)}, expect=201)
r = am.call('POST', '/das/projects/%s/approval' % STC_NO,
            {'approval_ref': '再批一次'}, expect=400)
check('不重复批准' in str(r), '已批准的不重复批准')
print('ok   099 立项未批准不得登记后续步骤; 批准立项与立项是两个人两份依据')

# ================= 反例 092：走错分支 =================
r = pm.call('POST', '/das/projects/%s/steps' % SUP_NO,
            {'step_code': 'DAP09.4', 'record_ref': 'REC-002'}, expect=400)
check('不适用' in str(r) and 'STC' in str(r), '受托项目不得登记「STC 要点核对」')
check('M3-1' in str(r) and '越权' not in str(r) or 'M3-1' in str(r),
      '拒绝理由指到判据 M3-1')
r = pm.call('POST', '/das/projects/%s/steps' % SUP_NO,
            {'step_code': 'DAP07.11', 'record_ref': 'REC-003'}, expect=400)
check('符合性声明' in str(r), '**受托项目不得登记「符合性声明」步骤**（M3-1 的核心）')
r = pm.call('POST', '/das/projects/%s/steps' % STC_NO,
            {'step_code': 'DAP09.3', 'record_ref': 'REC-004'}, expect=400)
check('PMA' in str(r), 'STC 项目不得登记「PMA 生产质量系统确认」')
r = pm.call('POST', '/das/projects/%s/steps' % STC_NO,
            {'step_code': 'DAP09.2', 'record_ref': 'REC-005'}, expect=400)
check('M10' in str(r) and '完不成' in str(r),
      '「首次申请 DOA」不属于任何产品项目, 并说明登记进去的后果')
print('ok   092 三类各走各的分支; 不属于产品项目的那一步谁都登不上')

# ================= 反例 093：PMA 的质量系统闸门 =================
r = awm.call('POST', '/das/projects/%s/pma-quality' % STC_NO,
             {'manual_ref': 'QM-2026 R3', 'established': True, 'covers_project': True,
              'materials_submitted': True, 'interface_ref': 'UG-DAP-10 协议'},
             expect=400)
check('仅 PMA' in str(r), '生产质量系统确认只适用于 PMA 项目')
r = awm.call('POST', '/das/projects/%s/pma-quality' % PMA_NO,
             {'manual_ref': 'QM-2026 R3', 'established': True, 'covers_project': False,
              'materials_submitted': True, 'interface_ref': 'UG-DAP-10 协议'},
             expect=400)
check('覆盖本项目的零部件类别' in str(r) and '已确认' in str(r),
      '四项有一项为否不算确认具备, 并说明后果: 下一步就是提交 PMA 申请')
# 这个反例要的是"前置步骤没登记", 所以此刻确认记录也不存在 —— 两条都会拦。
# 先验"连步骤都没登记"这一条, 再单独验"登记了但实质记录不存在"那一条。
r = pm.call('POST', '/das/projects/%s/steps' % PMA_NO,
            {'step_code': 'DAP09.5', 'record_ref': '局方受理记录'}, expect=409)
check('不得提交 PMA 申请' in str(r), '未确认生产质量系统不得提交申请（UG-DAP-09 第 3 步）')
r = pm.call('POST', '/das/projects/%s/steps' % PMA_NO,
            {'step_code': 'DAP09.3', 'record_ref': 'UG-DAF-PMA-QC-001'}, expect=409)
check('实质记录' in str(r) and 'das_pma_quality_confirm' in str(r),
      '只在步骤台账上记个勾、没有确认记录, 不算完成')
print('ok   093 "没确认"与"没登记"分别拦住 —— 只记勾不算确认')

awm.call('POST', '/das/projects/%s/pma-quality' % PMA_NO,
         {'manual_ref': 'QM-2026 R3', 'established': True, 'covers_project': True,
          'materials_submitted': True,
          'interface_ref': 'UG-DAP-10 接口协议 ' + STAMP}, expect=201)
pm.call('POST', '/das/projects/%s/steps' % PMA_NO,
        {'step_code': 'DAP09.3', 'record_ref': 'UG-DAF-PMA-QC-' + STAMP}, expect=201)
pm.call('POST', '/das/projects/%s/steps' % PMA_NO,
        {'step_code': 'DAP09.5', 'record_ref': '局方受理记录 ' + STAMP}, expect=201)
print('ok   四项齐备 → 第 3 步可登记 → 第 5 步（申请）才放行')

# ================= 反例 094：第三类签声明 =================
stmt = {'statement_no': 'UG-DOA-SM-2026-' + STAMP[-4:],
        'completion_confirm_ref': 'UG-DAF-17-' + STAMP[-4:],
        'verification_docs_ref': 'AR-01 R2、QTP-02 R1'}
r = pm.call('POST', '/das/projects/%s/statements' % SUP_NO, stmt, expect=400)
check('不得签符合性声明' in str(r) and 'AP-21-18' in str(r),
      '**受托项目不得签符合性声明**, 理由指到 UG-DAP-07 第 12 步 d 与 AP-21-18 7.1(5)')
check('越权' in str(r), '拒绝理由说明了这是越权作了设计批准, 不是流程瑕疵')
# 非在任责任经理签署且不写授权依据 → 403。此处其它条件都已齐备, 失败点唯一。
r = awm.call('POST', '/das/projects/%s/statements' % STC_NO, stmt, expect=403)
check('授权依据' in str(r) and '待澄清项' in str(r),
      '非在任责任经理签署须写明纸面授权依据, 并说明系统为什么判不了授权范围')
# 编号不含 -SM- → 400。授权依据写上, 让编号校验成为唯一失败点。
r = awm.call('POST', '/das/projects/%s/statements' % STC_NO,
             dict(stmt, statement_no='UG-DOA-2026-999',
                  signed_under_authority_ref='授权书 2026-02'), expect=400)
check('UG-DAW-002' in str(r) and '不校验' in str(r),
      '编号规则按 UG-DAW-002; 许可证编号部分不校验并说明理由（DOA 尚在申请中）')
print('ok   094 第三类签不了声明; 授权人员签署要说得出依据, 系统不冒充校验授权范围')

s_stc = awm.call('POST', '/das/projects/%s/statements' % STC_NO,
                 dict(stmt, signed_under_authority_ref='UG-DAM-01-附2 授权书 ' + STAMP),
                 expect=201)
s_pma = am.call('POST', '/das/projects/%s/statements' % PMA_NO,
                dict(stmt, statement_no='UG-DOA-SM-2026-P' + STAMP[-4:]), expect=201)
ov = admin.call('GET', '/das/projects/oversight', expect=200)
check([d for d in ov['delegated'] if d['statement_no'] == s_stc['statement_no']],
      '由授权人员签署的声明进可见性清单, 交独立监督核对签署事项范围')
check(not [d for d in ov['delegated'] if d['statement_no'] == s_pma['statement_no']],
      '在任责任经理本人签的不进该清单')
print('ok   由"授权人员"签的与责任经理本人签的分开, 前者交独立监督核对')

# ================= 反例 095／096：设计批准 =================
appr = {'certificate_no': 'STC-CAAC-2026-' + STAMP[-4:], 'issued_on': str(TODAY),
        'product_scope_ref': 'B737-800 B-1415 4～28 排'}
r = awm.call('POST', '/das/projects/%s/design-approval' % SUP_NO, appr, expect=400)
check('不产出设计批准' in str(r) and '证件属于委托方' in str(r),
      '**受托项目不产生设计批准**, 证件属于委托方')
# 没有声明不得登记批准: 另起一个没签声明的 STC 项目, 让这一条成为唯一失败点
STC2 = free_no('UG-STC')
dcm.call('POST', '/das/projects',
         {'project_no': STC2, 'type_code': 'STC', 'name_cn': '另一个 STC ' + STAMP},
         expect=201)
am.call('POST', '/das/projects/%s/approval' % STC2, {'approval_ref': '立项单 2'},
        expect=201)
r = awm.call('POST', '/das/projects/%s/design-approval' % STC2,
             dict(appr, certificate_no='STC-X-' + STAMP[-4:]), expect=400)
check('取证顺序是倒的' in str(r), '没有符合性声明不得登记设计批准（UG-DAP-09 第 8 步）')
# PMA 不写延续日期: 声明已签, 延续校验成为唯一失败点
r = awm.call('POST', '/das/projects/%s/design-approval' % PMA_NO,
             {'certificate_no': 'PMA-2026-' + STAMP[-4:], 'issued_on': str(TODAY),
              'product_scope_ref': 'N40 手持电话'}, expect=400)
check('2 年延续' in str(r) and 'M10' in str(r),
      'PMA 项目单须写明延续到期日, 否则等于把两年后失效的东西记成长期有效')
print('ok   095／096 第三类无设计批准; 没声明不得取证; PMA 必须写延续日期')

a_stc = awm.call('POST', '/das/projects/%s/design-approval' % STC_NO, appr, expect=201)
check(a_stc['approval_kind'] == 'STC', '批准类别由项目类型决定, 不由调用方给')
check(a_stc['statement_id'], '设计批准自动关联到本项目已签的符合性声明')
a_pma = awm.call('POST', '/das/projects/%s/design-approval' % PMA_NO,
                 {'certificate_no': 'PMA-2026-' + STAMP[-4:], 'issued_on': str(TODAY),
                  'product_scope_ref': 'N40 手持电话 P/N UG-N40-1A/1B/1C',
                  'renew_due_on': str(TODAY + dt.timedelta(days=730))}, expect=201)
rn = [x for x in admin.call('GET', '/das/projects/oversight',
                            expect=200)['renewal']
      if x['certificate_no'] == a_pma['certificate_no']]
check(rn and rn[0]['days_left'] == 730, 'PMA 项目单的延续到期进到期清单（归 M10）')
print('ok   设计批准是独立对象: 证后活动挂证件不挂项目（设计输入第 8.3 节）')

# ================= 反例 097：转段三项 =================
r = pm.call('POST', '/das/projects/%s/closure' % STC_NO, {}, expect=400)
for cn in ('持续适航', '生产协调', '归档'):
    check(cn in str(r), '结项前报出还差「%s」' % cn)
pm.call('POST', '/das/projects/%s/handovers' % STC_NO,
        {'item_code': 'AIRWORTHINESS', 'target_ref': 'UG-DAP-11 移交 ' + STAMP,
         'received_by': awm.id}, expect=201)
pm.call('POST', '/das/projects/%s/handovers' % STC_NO,
        {'item_code': 'PRODUCTION', 'target_ref': 'UG-DAP-10 协调 ' + STAMP,
         'received_by': awm.id}, expect=201)
r = pm.call('POST', '/das/projects/%s/closure' % STC_NO, {}, expect=400)
# 只看"还差"那一句。后面引 UG-DAP-09 第 9 步原文时三项都会提到,
# 拿整条消息做"不含持续适航"的断言, 等于在断言那句引文不存在。
missing_clause = str(r).split('。')[0]
check('归档' in missing_clause and '持续适航' not in missing_clause,
      '只把还差的那一项列进"还差"里（实际: %s）' % missing_clause[-28:])
hp = [x for x in admin.call('GET', '/das/projects/oversight',
                            expect=200)['handover_pending']
      if x['project_no'] == STC_NO]
check(hp and hp[0]['pending'] == 1, '已取证而转段未齐的项目进待办清单')
pm.call('POST', '/das/projects/%s/handovers' % STC_NO,
        {'item_code': 'ARCHIVE', 'target_ref': 'UG-DAP-01 归档 ' + STAMP,
         'received_by': dcm.id}, expect=201)
c = pm.call('POST', '/das/projects/%s/closure' % STC_NO, {}, expect=201)
check(c['closed_on'], '三项齐备后方可结项')
print('ok   097 转段三项齐备才结项 —— 项目做到转段为止, 证后活动要有承接人')

# ================= 反例 098：取证材料不得抹改 =================
check('DCMS-INV-098' in reason(
    "UPDATE das_compliance_statement SET verification_docs_ref='改一下' WHERE id=%s",
    (s_stc['id'],)), '098 符合性声明不得改写 —— 能改写的签署不是签署')
check('DCMS-INV-098' in reason(
    "DELETE FROM das_compliance_statement WHERE id=%s", (s_stc['id'],)),
    '098 符合性声明不得删除')
check('DCMS-INV-098' in reason(
    "DELETE FROM das_design_approval WHERE id=%s", (a_stc['id'],)),
    '098 设计批准不得删除')
# 【要打在真有行的地方】
# 零行的 UPDATE 不会触发任何触发器, 于是 reason() 返回空字符串, 断言只会说"没报错",
# 而它本来想证明的是"报了 098"。STC 项目没登记过步骤, 打在它上面就是在测空气。
n = db_query("SELECT count(*) AS n FROM das_project_step_done WHERE project_id=%s",
             (pma['id'],))[0]['n']
check(n > 0, 'PMA 项目确实有步骤完成记录可打（%d 条）' % n)
check('DCMS-INV-098' in reason(
    "UPDATE das_project_step_done SET record_ref='改一下' WHERE project_id=%s",
    (pma['id'],)), '098 步骤完成记录不得改写')
check('DCMS-INV-098' in reason(
    "DELETE FROM das_project_handover WHERE project_id=%s", (stc['id'],)),
    '098 转段记录不得删除')
print('ok   098 声明、设计批准、步骤记录、转段记录都是取证材料, 一律不得抹改')

# ================= 受托项目走完它自己那条路 =================
for code in ('DAP04.1', 'DAP04.2', 'DAP04.3', 'DAP07.12'):
    pm.call('POST', '/das/projects/%s/steps' % SUP_NO,
            {'step_code': code, 'record_ref': 'SUP-%s-%s' % (code, STAMP)}, expect=201)
sp = admin.call('GET', '/das/projects/%s' % SUP_NO, expect=200)
applicable = {x['step_code'] for x in sp['progress']}
check('DAP07.12' in applicable, '受托项目有它自己独有的那一步')
check('DAP07.11' not in applicable and 'DAP09.4' not in applicable,
      '受托项目的进展表里根本看不到符合性声明与 STC 要点核对')
check(len(applicable) == 23, '受托项目适用 23 步（11＋11＋1），不是 30 步（%d）'
      % len(applicable))
check(not sp['approvals'] and not sp['statements'],
      '受托项目既无声明也无设计批准')
print('ok   M3-1 第三类走的是它自己那条路: 23 步, 无声明无批准')

print('\nPASS M3 项目实体与三条审定路径: M3-1 按类型参数化且步骤集互不相同、'
      '091 编号前缀、092 分支隔离、093 PMA 质量闸门、094／095 第三类不出声明不出批准、'
      '096 取证顺序、097 转段三项、098 取证材料不得抹改、099 立项批准')
