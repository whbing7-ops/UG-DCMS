"""不符合项与纠正措施（M7，UG-DAP-14），含工作日日历。

按判据 I-总 以反例为主。这个模块有两处最容易悄悄做错，用例围绕它们构造：

  其一，**两套计时口径**。M6 的 48 小时是日历小时，这里一类问题的 21 天是工作日，
  而且时限自局方**发布**记录表之日起算、不是收到之日。用例直接比出按收到日算会
  差几天，以及日历没加载时期限必须是 NULL 而不是一个估值。

  其二，**谁都不能单方面宣布关闭**。局方开具项要局方评估验证后方可关闭（步骤 8），
  所以内部关闭与局方关闭是两个端点，用例验内部关闭写进去之后 is_closed 仍为假。

其余覆盖：
  044   一类问题不得延期
  045   延期须在时限到期前提出；未获局方同意前时限不顺延
  046   没有 CAR 或没有根本原因分析不得关闭
  047   验证人不得是整改责任人
  048   根本原因只记"仅人为失误"不得关闭
  049   涉及独立监督职能自身须有责任经理指定记录（I6／I7）
  050   预防措施与纠正措施雷同视为未采取预防措施
  051   不符合项记录不得删除
  052   仅有"已整改"声明不得关闭；观察项走处理意见而不是关闭验证
  053   12 个月内同一依据条款重复发生，须先升级为系统性纠正措施

触发器违规归 409 并带 rule 字段，服务层 ValueError 归 400。只断言状态码不够：
这里有十条规则都返回 409，一条失效被另一条顶替，测试照样全绿。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-ncr.py <credentials.json>
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


def violates(rule, method, path, body=None):
    r = admin.call(method, path, body, expect=409)
    got = ((r or {}).get('error') or {}).get('rule')
    check(got == rule, '%s 拦下（实际报出 %s）' % (rule, got))
    return r


# ================= 工作日日历 =================
# 这是本模块的前置: 一类问题 21 个工作日, 没有日历就算不出来。

gaps = admin.call('GET', '/das/work-calendar/gaps', expect=200)
check(len(gaps) == 3, '日历缺口视图给出前后三年（%d 年）' % len(gaps))
print('ok   日历加载情况可查: %s'
      % '、'.join('%s=%s' % (g['calendar_year'], '已声明' if g['declared'] else '未声明')
                  for g in gaps))

year = TODAY.year
declared = {g['calendar_year'] for g in gaps if g['declared']}
if year not in declared:
    # 判据 L1 的反面: 日历未加载时, 工作日时限必须算不出来, 不能估一个出来。
    nc = admin.call('POST', '/das/ncr', {
        'source': 'CAAC', 'ncr_class': 'CLASS_1', 'ncr_no': 'NC-NOCAL-' + STAMP,
        'fact': '日历未加载时的一类问题', 'basis_clause': '21.900(a)-' + STAMP,
        'evidence': '证据', 'issued_on': d(-5)}, expect=201)
    check(nc['deadline']['deadline_on'] is None
          and '日历' in (nc['deadline']['deadline_note'] or ''),
          '日历未加载时一类问题的期限为 NULL, 并说明原因')
    print('ok   日历未加载 -> 21 个工作日算不出来, 期限为空且带原因说明')
    print('     （估一个出来比空着危险: 它看上去精确, 而错的是一条法规时限）')

admin.call('POST', '/das/work-calendar/years',
           {'year': year, 'source_ref': ''}, expect=422)
print('ok   声明日历年度须写依据来源')

if year not in declared:
    admin.call('POST', '/das/work-calendar/years',
               {'year': year, 'source_ref': 'CI 测试夹具, 非真实节假日安排'}, expect=201)
    print('ok   声明 %d 年日历已加载（本用例用的是夹具, 不是真实节假日安排）' % year)
admin.call('POST', '/das/work-calendar/years',
           {'year': year, 'source_ref': 'x'}, expect=400)
print('ok   同一年度不得重复声明')

# 未声明年份不得录例外日
future = year + 5
violates('DCMS-INV-043', 'POST', '/das/work-calendar/days',
         {'day': '%d-05-01' % future, 'kind': 'HOLIDAY', 'source_ref': 'x'})
print('ok   DCMS-INV-043 未声明加载的年份不得录入例外日（半成品日历比空日历更危险）')

# 调休上班日: 本该休息却上班, 必须能表达, 否则工作日一定算错
# 取当年 6 月的第一个周六: 用"下一个周六"在年末会跨到未声明日历的年份, 偶发失败。
sat = dt.date(year, 6, 1)
sat += dt.timedelta(days=(5 - sat.weekday()) % 7)
admin.call('POST', '/das/work-calendar/days',
           {'day': str(sat), 'kind': 'MAKEUP', 'name_cn': 'CI 夹具调休上班日',
            'source_ref': 'CI 测试夹具'}, expect=201)
wd = db_query("SELECT das_is_working_day(%s) AS w", (str(sat),))[0]['w']
check(wd is True, '调休上班日被判为工作日')
print('ok   调休上班日（%s 周六）被判为工作日——只按周一到周五算一定会错' % sat)

# ================= 不符合项登记 =================

admin.call('POST', '/das/ncr', {
    'source': 'CAAC', 'fact': '事实', 'basis_clause': '21.243', 'evidence': '证据',
    'issued_on': d(-10)}, expect=400)
print('ok   局方开具项不写分类被拒（定不出是 21 个工作日还是 3 个月）')

admin.call('POST', '/das/ncr', {
    'source': 'CAAC', 'ncr_class': 'CLASS_1', 'fact': '事实',
    'basis_clause': '21.243', 'evidence': '证据'}, expect=400)
print('ok   局方开具项不写发布日期被拒（时限自发布之日起算, 无从起算）')

admin.call('POST', '/das/ncr', {
    'source': 'INTERNAL_AUDIT', 'ncr_class': 'CLASS_1', 'fact': '事实',
    'basis_clause': '21.243', 'evidence': '证据'}, expect=400)
print('ok   内部来源不得套用局方的一类／二类分类')

admin.call('POST', '/das/ncr', {
    'source': 'CAAC', 'ncr_class': 'CLASS_1', 'fact': '事实', 'basis_clause': '21.243',
    'evidence': '证据', 'issued_on': d(-10), 'received_on': d(-12)}, expect=400)
print('ok   收到日期早于发布日期被拒')

BASIS1 = '21.243(c)-' + STAMP
c1 = admin.call('POST', '/das/ncr', {
    'source': 'CAAC', 'ncr_class': 'CLASS_1', 'ncr_no': 'NC-C1-' + STAMP,
    'fact': '符合性验证记录缺少独立核查签署', 'basis_clause': BASIS1,
    'evidence': '抽查 3 份验证报告均无核查人签署', 'issued_on': d(-10),
    'received_on': d(-8), 'source_ref': '审定信函 CAAC-' + STAMP}, expect=201)
check(c1['deadline']['deadline_on'] is not None, '一类问题的 21 个工作日已算出')
print('ok   一类问题已登记, 期限 %s（发布日 %s + 21 个工作日）'
      % (c1['deadline']['deadline_on'], d(-10)))

# 起算点: 发布日 vs 收到日
by_issue = db_query("SELECT das_add_working_days(%s::date, 21) AS x", (d(-10),))[0]['x']
by_recv = db_query("SELECT das_add_working_days(%s::date, 21) AS x", (d(-8),))[0]['x']
check(str(by_issue) == c1['deadline']['deadline_on'], '期限确实按发布日算出')
check(by_recv != by_issue, '按收到日算会得到不同的日期')
print('ok   时限自发布之日起算: 按发布日 %s, 按收到日会是 %s（差 %d 天）'
      % (by_issue, by_recv, (by_recv - by_issue).days))

# ================= 判据 044: 一类不得延期 =================
violates('DCMS-INV-044', 'POST', '/das/ncr/%d/extensions' % c1['id'],
         {'requested_to': d(60), 'reason': '需要更多时间'})
print('ok   DCMS-INV-044 一类问题的 21 个工作日不得延期（第 7 章: 局方可暂停体系权利）')

# ================= 判据 050: 三栏不得雷同 =================
eng = make_user(admin, 'ENGINEER', 'ncr')
SAME = '修订 UG-DAP-07 增加核查签署栏'
CAR = {'correction_owner': eng.id, 'corrective_owner': eng.id, 'preventive_owner': eng.id,
       'correction_due': d(10), 'corrective_due': d(20), 'preventive_due': d(25)}
violates('DCMS-INV-050', 'POST', '/das/ncr/%d/car' % c1['id'],
         {**CAR, 'correction': '补签 3 份报告', 'corrective_action': SAME,
          'preventive_action': SAME})
print('ok   DCMS-INV-050 预防措施与纠正措施完全雷同, 视为未采取预防措施')

violates('DCMS-INV-050', 'POST', '/das/ncr/%d/car' % c1['id'],
         {**CAR, 'correction': '补签 3 份报告',
          'corrective_action': SAME + '并培训', 'preventive_action': SAME})
print('ok   DCMS-INV-050 预防措施是纠正措施的子串也算雷同')

admin.call('POST', '/das/ncr/%d/car' % c1['id'], {
    **CAR, 'correction': '补签 3 份已出具验证报告的核查签署',
    'corrective_action': '修订 UG-DAP-07 第 6 章, 验证报告模板增加核查人签署栏并作废旧模板',
    'preventive_action': '排查 2025 年以来全部项目的验证报告, '
                         '并检查 UG-DAP-04、UG-DAP-11 是否有同类缺签'}, expect=201)
print('ok   三栏各写各的可登记（纠正消除本身／措施针对根因／预防举一反三）')

admin.call('POST', '/das/ncr/%d/car' % c1['id'], {
    **CAR, 'correction': 'a', 'corrective_action': 'b', 'preventive_action': 'c'},
    expect=400)
print('ok   同一不符合项不得重复登记 UG-DAF-05C')

pr = admin.call('GET', '/das/ncr/preventive-review', expect=200)
mine = [x for x in pr if x['ncr_id'] == c1['id']]
check(mine and mine[0]['same_owner'] is True,
      '三个负责人同一人时在待复核清单中被标出（不违规, 但要被看见）')
print('ok   语义雷同系统不判, 只给线索供独立验证人判断（本例标出了负责人同一人）')

# ================= 判据 046/048: 关闭的前置 =================
violates('DCMS-INV-046', 'POST', '/das/ncr/%d/verification' % c1['id'],
         {'evidence': '已见补签的报告', 'effectiveness': '抽查无缺签'})
print('ok   DCMS-INV-046 没做根本原因分析不得关闭（步骤 6）')

admin.call('POST', '/das/ncr/%d/root-causes' % c1['id'],
           {'level': 'HUMAN_ERROR_ONLY', 'analysis': '编制人忘记送核查'}, expect=201)
violates('DCMS-INV-048', 'POST', '/das/ncr/%d/verification' % c1['id'],
         {'evidence': '已见补签的报告', 'effectiveness': '抽查无缺签'})
print('ok   DCMS-INV-048 根本原因只记"仅人为失误"不得关闭')
print('     （人为失误可以是事实的一部分, 不能是唯一答案——步骤 6 要求追到管理或程序层面）')

admin.call('POST', '/das/ncr/%d/root-causes' % c1['id'],
           {'level': 'PROCEDURE',
            'analysis': 'UG-DAP-07 的验证报告模板本身没有核查签署栏, 不签也符合模板'},
           expect=201)
print('ok   补上程序层面的原因后可继续')

# ================= 判据 047: 验证人不得是整改责任人 =================
# 当前登录人(admin)不是三栏负责人, 先把 eng 设为责任部门负责人试一次反例。
db_execute("UPDATE das_ncr SET responsible_user=%s WHERE id=%s", (admin.id, c1['id']))
violates('DCMS-INV-047', 'POST', '/das/ncr/%d/verification' % c1['id'],
         {'evidence': '已见补签的报告', 'effectiveness': '抽查无缺签'})
print('ok   DCMS-INV-047 验证人不得是整改责任人（本例: 操作人正是责任部门负责人）')
db_execute("UPDATE das_ncr SET responsible_user=%s WHERE id=%s", (eng.id, c1['id']))

# 空白串长度不为 0, 过得了 Pydantic 的 min_length, 由服务层 strip 后拒绝 -> 400
admin.call('POST', '/das/ncr/%d/verification' % c1['id'],
           {'evidence': '   ', 'effectiveness': '已整改'}, expect=400)
print('ok   M7-2 只写"已整改"不行: 实施证据与有效性结论都不得为空')

admin.call('POST', '/das/ncr/%d/verification' % c1['id'],
           {'evidence': '核对了补签的 3 份报告原件与新模板的发布记录',
            'effectiveness': '上线后抽查 5 份新报告均有核查人签署, 措施有效'}, expect=201)
print('ok   独立于整改责任人的验证人可登记关闭验证')

# ================= 判据 M7-1: 两支笔 =================
r = admin.call('POST', '/das/ncr/%d/internal-closure' % c1['id'], expect=200)
check(r['internal_closed_at'] is not None, '内部关闭已登记')
check(r['is_closed'] is False, 'M7-1 局方开具项不因内部关闭而闭环')
check('局方' in (r.get('note') or ''), '返回里说明了还缺局方确认')
print('ok   M7-1 内部关闭写进去了, is_closed 仍为假——局方开具项须局方确认后方可关闭')

admin.call('POST', '/das/ncr/%d/caac-closure' % c1['id'], {'closed_ref': ''}, expect=422)
print('ok   局方关闭确认须写依据（没有依据的"局方已关闭"等于内部自己宣布关闭）')

r = admin.call('POST', '/das/ncr/%d/caac-closure' % c1['id'],
               {'closed_ref': '监督检查关闭通知 CAAC-' + STAMP}, expect=200)
check(r['is_closed'] is True, '局方确认后才算闭环')
print('ok   M7-1 局方确认关闭后 is_closed 才为真——两条独立记录, 谁也推不动对方')

check(rejected("UPDATE das_ncr SET caac_closed_at=now(), caac_closed_ref=NULL WHERE id=%s",
               (c1['id'],)), '局方关闭不得清空依据')
print('ok   局方关闭的依据不可被清空')

# ================= 二类: 3 个月与延期链 =================
c2 = admin.call('POST', '/das/ncr', {
    'source': 'CAAC', 'ncr_class': 'CLASS_2', 'ncr_no': 'NC-C2-' + STAMP,
    'fact': '供应商评价记录不完整', 'basis_clause': '21.243(b)-' + STAMP,
    'evidence': '抽查 2 家无年度评价', 'issued_on': d(-10)}, expect=201)
base_due = c2['deadline']['deadline_on']
# 原来这条写成 "...== 一串字符串拼接 or base_due is not None", 后半截让它近乎恒真。
# 真正要验的是两件事: 期限等于发布日 + 3 个日历月, 且**不是**一类的 21 个工作日。
exp_cal = db_query("SELECT ((%s::date) + interval '3 months')::date AS x", (d(-10),))[0]['x']
exp_wd = db_query("SELECT das_add_working_days(%s::date, 21) AS x", (d(-10),))[0]['x']
check(base_due == str(exp_cal), '二类期限 = 发布日 + 3 个日历月（算得 %s）' % base_due)
check(base_due != str(exp_wd), '二类不套用一类的 21 个工作日口径（那会是 %s）' % exp_wd)
print('ok   二类问题期限 %s = 发布日 + 3 个日历月；一类口径会是 %s, 两套不混用'
      % (base_due, exp_wd))

violates('DCMS-INV-045', 'POST', '/das/ncr/%d/extensions' % c2['id'],
         {'requested_to': base_due, 'reason': '排期冲突'})
print('ok   DCMS-INV-045 延期后的日期不晚于原时限被拒')

ext = admin.call('POST', '/das/ncr/%d/extensions' % c2['id'],
                 {'requested_to': d(150), 'reason': '两家供应商现场审核须等其生产周期'},
                 expect=201)
after = [x for x in admin.call('GET', '/das/ncr/deadlines', expect=200)
         if x['id'] == c2['id']][0]
check(after['deadline_on'] == base_due, 'M7: 延期申请本身不顺延时限')
print('ok   延期申请已登记但时限未动——第 7 章要求延期计划**事先**得到局方同意')

admin.call('POST', '/das/ncr/extensions/%d/agreement' % ext['id'],
           {'agreed_on': d(0), 'agreed_ref': ''}, expect=422)
print('ok   局方同意须写依据')
admin.call('POST', '/das/ncr/extensions/%d/agreement' % ext['id'],
           {'agreed_on': d(0), 'agreed_ref': '主管监察员邮件同意 ' + str(TODAY)}, expect=200)
after = [x for x in admin.call('GET', '/das/ncr/deadlines', expect=200)
         if x['id'] == c2['id']][0]
check(after['deadline_on'] == d(150) and '顺延' in (after['deadline_note'] or ''),
      '局方同意后时限顺延到申请的日期')
print('ok   登记局方同意后时限才顺延到 %s' % after['deadline_on'])

admin.call('POST', '/das/ncr/extensions/%d/agreement' % ext['id'],
           {'agreed_on': d(0), 'agreed_ref': 'x'}, expect=400)
print('ok   同一延期申请不得重复登记局方同意')

risk = admin.call('GET', '/das/ncr/escalation-risk', expect=200)
print('ok   二类上升风险清单可查（%d 条临近时限且缺 CAR 或缺答复）' % len(risk))

# 表-21-166 只对局方开具项
internal = admin.call('POST', '/das/ncr', {
    'source': 'SUPERVISION', 'ncr_no': 'NC-IN-' + STAMP,
    'fact': '内部监督发现的记录缺失', 'basis_clause': '21.247-' + STAMP,
    'evidence': '监督记录'}, expect=201)
check(internal['deadline']['deadline_on'] is None, '内部不符合项无局方法规时限')
admin.call('POST', '/das/ncr/%d/caac-reply' % internal['id'],
           {'form_ref': 'x', 'correction_completed_on': d(5),
            'corrective_completed_on': d(10), 'confirmed_by': admin.id}, expect=400)
print('ok   表-21-166 只用于局方开具项, 内部不符合项不向局方提交答复')

admin.call('POST', '/das/ncr/%d/caac-reply' % c2['id'],
           {'form_ref': 'F166-' + STAMP, 'correction_completed_on': d(5),
            'corrective_completed_on': d(10), 'confirmed_by': admin.id}, expect=201)
print('ok   表-21-166 已提交, 两个完成时间都载明（缺一即答复不完整）')

# ================= 判据 053: 12 个月内重复发生 =================
rec = admin.call('POST', '/das/ncr', {
    'source': 'SUPERVISION', 'ncr_no': 'NC-R1-' + STAMP,
    'fact': '又一次缺核查签署', 'basis_clause': BASIS1,
    'evidence': '监督抽查', 'responsible_user': eng.id}, expect=201)
check(rec['recurrence_within_12m'] >= 1, '登记时即提示 12 个月内已有同条款记录')
print('ok   登记当场提示同一依据条款在 12 个月内已出现 %d 次'
      % rec['recurrence_within_12m'])

admin.call('POST', '/das/ncr/%d/car' % rec['id'], {
    **CAR, 'correction': '补签本次涉及的报告',
    'corrective_action': '把核查签署做成系统强制步骤, 未签不得提交',
    'preventive_action': '核查其他依赖"人记得"的环节, 逐个改为系统强制'}, expect=201)
admin.call('POST', '/das/ncr/%d/root-causes' % rec['id'],
           {'level': 'MANAGEMENT',
            'analysis': '上一轮纠正措施只改了模板, 没有改掉"靠人记得"这个机制'}, expect=201)
violates('DCMS-INV-053', 'POST', '/das/ncr/%d/verification' % rec['id'],
         {'evidence': '已见补签记录', 'effectiveness': '抽查无缺签'})
print('ok   DCMS-INV-053 12 个月内重复发生却未升级为系统性纠正措施, 不得关闭')

admin.call('POST', '/das/ncr/%d/systemic' % rec['id'], expect=200)
admin.call('POST', '/das/ncr/%d/verification' % rec['id'],
           {'evidence': '已见补签记录与系统强制步骤的上线记录',
            'effectiveness': '连续 2 个月无新增缺签'}, expect=201)
r = admin.call('POST', '/das/ncr/%d/internal-closure' % rec['id'], expect=200)
check(r['is_closed'] is True, '内部来源的不符合项内部关闭即闭环')
print('ok   升级为系统性后可关闭; 内部来源不需局方确认')

recs = admin.call('GET', '/das/ncr/recurrence', expect=200)
check(any(x['basis_clause'] == BASIS1 and x['occurrences'] >= 2 for x in recs),
      '重复发生清单按依据条款汇总')
print('ok   重复发生清单可查（按依据条款汇总, 供季度趋势分析用）')

# ================= 判据 049: 涉及独立监督职能自身 =================
ism = admin.call('POST', '/das/ncr', {
    'source': 'SUPERVISION', 'ncr_no': 'NC-ISM-' + STAMP,
    'fact': '独立监督年度审核计划未覆盖符合性检查单全部适用项',
    'basis_clause': '21.243(d)-' + STAMP, 'evidence': '对照检查单缺 4 项',
    'concerns_ism': True, 'responsible_user': eng.id}, expect=201)
admin.call('POST', '/das/ncr/%d/car' % ism['id'], {
    **CAR, 'correction': '补做缺的 4 项审核',
    'corrective_action': '审核计划改为按符合性检查单逐项生成, 不再手工列',
    'preventive_action': '核查其他按清单驱动的工作是否也在手工列'}, expect=201)
admin.call('POST', '/das/ncr/%d/root-causes' % ism['id'],
           {'level': 'PROCEDURE', 'analysis': '审核计划的生成没有与符合性检查单绑定'},
           expect=201)
violates('DCMS-INV-049', 'POST', '/das/ncr/%d/verification' % ism['id'],
         {'evidence': '已见补做的 4 项审核记录', 'effectiveness': '新计划已覆盖全部适用项'})
print('ok   DCMS-INV-049 涉及独立监督职能自身, 缺责任经理指定记录不得关闭（I6／I7）')

mgr = make_user(admin, 'APPROVER', 'am')
admin.call('POST', '/das/ncr/%d/verification' % ism['id'],
           {'evidence': '已见补做的 4 项审核记录', 'effectiveness': '新计划已覆盖全部适用项',
            'designated_by': mgr.id,
            'designation_basis': '责任经理 %s 书面指定（UG-DAP-14 步骤 9）' % TODAY,
            'verifier_qualification': '审核员资格, 已完成 UG-DAW-013 培训并考核合格',
            'independence_note': '未参与本项整改, 不在独立监督职能内任职'}, expect=201)
print('ok   带指定依据、验证人资格与独立性说明后可关闭')

# ================= 观察项 =================
ob = admin.call('POST', '/das/ncr', {
    'source': 'CAAC', 'ncr_class': 'OBSERVATION', 'ncr_no': 'NC-OB-' + STAMP,
    'fact': '建议把表单填写说明做成在线提示', 'basis_clause': '21.243-' + STAMP,
    'evidence': '审定信函观察项', 'issued_on': d(-10)}, expect=201)
check(ob['deadline']['deadline_on'] is None, '观察项无法规时限')
print('ok   观察项无法规整改时限（不构成一类或二类问题）')

oo = admin.call('GET', '/das/ncr/observations-open', expect=200)
check(any(x['id'] == ob['id'] for x in oo), '未给处理意见的观察项可查')
r = violates('DCMS-INV-052', 'POST', '/das/ncr/%d/internal-closure' % ob['id'])
check('处理意见' in str(r) and '关闭验证' not in str(r),
      '观察项报的是"缺处理意见", 不是"缺关闭验证"')
print('ok   DCMS-INV-052 观察项未给处理意见不得关闭, 且理由指到步骤 3 而不是步骤 9')
print('     （观察项不构成不符合, 没有整改实施证据与措施有效性可验）')

admin.call('POST', '/das/ncr/%d/observation-response' % ob['id'],
           {'assessment': '确属改进建议, 不构成不符合',
            'disposition': '纳入下季度系统改进计划, 提交管理评审'}, expect=201)
r = admin.call('POST', '/das/ncr/%d/internal-closure' % ob['id'], expect=200)
check(r['is_closed'] is True, '观察项登记处理意见后内部关闭即闭环')
print('ok   观察项登记评估与处理意见后可关闭, 不需局方确认')

admin.call('POST', '/das/ncr/%d/observation-response' % c2['id'],
           {'assessment': 'x', 'disposition': 'y'}, expect=400)
print('ok   不符合项不得走观察项的处理意见路径')

# ================= 判据 051: 记录不得删除 =================
check(rejected("DELETE FROM das_ncr WHERE id=%s", (c1['id'],)),
      'DCMS-INV-051 不符合项不得删除')
check(rejected("DELETE FROM das_car WHERE ncr_id=%s", (c1['id'],)), 'CAR 不得删除')
check(rejected("UPDATE das_ncr_closure SET effectiveness='改掉' WHERE ncr_id=%s",
               (c1['id'],)), '关闭验证不可改写')
check(rejected("UPDATE das_ncr_root_cause SET analysis='改掉' WHERE ncr_id=%s",
               (c1['id'],)), '原因分析不可改写')
print('ok   DCMS-INV-051 台账完整: 不符合项、CAR、关闭验证、原因分析均不可删除或改写')

q = admin.call('GET', '/das/ncr/quarterly', expect=200)
check(q and all('quarter' in x for x in q), '季度趋势可查（步骤 10）')
print('ok   季度趋势可查（%d 组, 供管理评审识别重复性问题）' % len(q))

print()
print('全部通过: 不符合项与纠正措施')
