"""事件报告的 48 小时链条（M6，UG-DAP-12）。

按判据 I-总 以反例为主。这个模块的全部要害是**计时起算点**，所以用例围绕
"能不能把 48 小时绕过去"来构造，而不是围绕"正常路径能不能走通"：

  M6-1   48 小时从确认存在时间起算；晚登记不得重置计时
  M6-1   不填确认存在时间 = 计时不开始，这类记录必须单独可见
  M6-1   确认存在时间往后挪须留痕（DCMS-INV-039）
  M6-2   报告内容缺项不得阻止报送——拦下来就是直接造成超期
  M6-3   免于报告须有理由、证据和**在任的适航管理负责人**签署
  040    初判须对 13 种情形逐项判完
  041    有疑问时不得结论为"不属 21.5 范围"
  038    所有报告（含判定不需上报的）均登记，不得删除
  步骤9  匿名件没有报告人可反馈；保密件有报告人但台账不显示

触发器违规归 409 并带 rule 字段，服务层 ValueError 归 400。只断言状态码不够：
三四条规则都返回 409 时，一条失效被另一条顶替，测试照样全绿。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-occurrence.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
NOW = dt.datetime.now().astimezone()


def iso(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def rejected(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False


def violates(rule, method, path, body=None):
    """确认拦下请求的是**哪一条**规则, 不只是"被拒了"。"""
    r = admin.call(method, path, body, expect=409)
    got = ((r or {}).get('error') or {}).get('rule')
    check(got == rule, '%s 拦下（实际报出 %s）' % (rule, got))
    return r


db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s AND (employee_no IS NULL OR employee_no='')",
           ('OC' + STAMP, admin.id))

# ---------------- 13 种情形是配置 ----------------
cats = admin.call('GET', '/das/occurrence-categories', expect=200)
check(len(cats) == 13, 'CCAR-21.5（二）13 种情形已种入（%d 种）' % len(cats))
check(all(c['basis'] == 'CCAR-21.5（二）' for c in cats), '每种情形都带规章依据')
check(any('刹车系统失效' in c['description'] for c in cats), '情形原文取自规章附表')
print('ok   13 种应报告情形是配置, 带规章依据（规章改版在系统内维护, 不改代码）')

# ---------------- 判据 M6-2: 内容六项全空也能登记 ----------------
occ = admin.call('POST', '/das/occurrences', {
    'report_no': 'OC-A-' + STAMP, 'description': '刹车系统在使用期间失效',
    'received_at': iso(30), 'received_via': 'PHONE',
    'reporter_name_text': '使用人 张某（外部，无系统账号）'}, expect=201)
check(occ['registration_deviation'] is True and occ['registration_lag_hours'] > 4,
      '登记晚于接收超 4 小时被标为偏离（UG-DAP-12 步骤 2）')
print('ok   M6-2 报告内容六项全空仍可登记（%.1f 小时前接收，登记滞后已标记）'
      % occ['registration_lag_hours'])

gaps = admin.call('GET', '/das/occurrences/%d' % occ['id'], expect=200)
cg = admin.call('GET', '/das/occurrences/content-gaps', expect=200)
mine = [g for g in cg if g['id'] == occ['id']]
check(mine and len(mine[0]['missing']) == 7, 'M6-2 缺项被逐项列出（%s）'
      % (mine[0]['missing'] if mine else None))
print('ok   M6-2 缺的七项列得出来: %s' % '、'.join(mine[0]['missing']))

dev = admin.call('GET', '/das/occurrences/registration-deviations', expect=200)
check(any(d['id'] == occ['id'] for d in dev), '登记偏离在视图里可见')
print('ok   登记晚于接收 4 小时的偏离可查（含非工作日，日历小时）')

# ---------------- 判据 040: 没逐项判完不得下结论 ----------------
violates('DCMS-INV-040', 'POST', '/das/occurrences/%d/conclude' % occ['id'],
         {'conclusion': 'OUT_OF_SCOPE'})
print('ok   DCMS-INV-040 13 种情形没判完就下结论被拒')

# 判为"符合"却不写理由
admin.call('POST', '/das/occurrences/%d/triage-items' % occ['id'],
           {'category_seq': 7, 'verdict': 'YES'}, expect=400)
print('ok   判为"符合"而不写理由被拒——这一判会把结论推向应报告, 依据要留下')

admin.call('POST', '/das/occurrences/%d/triage-items' % occ['id'],
           {'category_seq': 7, 'verdict': 'YES',
            'note': '使用期间结构损坏引起刹车系统失效, 对应第 7 种情形'}, expect=201)
for c in cats:
    if c['seq'] != 7:
        admin.call('POST', '/das/occurrences/%d/triage-items' % occ['id'],
                   {'category_seq': c['seq'], 'verdict': 'NO'}, expect=201)
prog = admin.call('GET', '/das/occurrences/%d/triage' % occ['id'], expect=200)
check(prog['assessed'] == 13 and prog['matched'] == 1, '13 项判完, 其中 1 项符合')
print('ok   初判逐项留痕: 判完 %d 项, 符合 %d 项, 无法确定 %d 项'
      % (prog['assessed'], prog['matched'], prog['unsure']))

admin.call('POST', '/das/occurrences/%d/triage-items' % occ['id'],
           {'category_seq': 7, 'verdict': 'NO'}, expect=400)
print('ok   同一情形不得重复判断, 初判记录不可改写')

# ---------------- 判据 041: 有疑问不得结论为不属范围 ----------------
violates('DCMS-INV-041', 'POST', '/das/occurrences/%d/conclude' % occ['id'],
         {'conclusion': 'OUT_OF_SCOPE'})
print('ok   DCMS-INV-041 有一项判为符合却结论不属范围被拒（有疑问时按应报告处理）')

# ---------------- 判据 042: 免报结论须先有判定行 ----------------
violates('DCMS-INV-042', 'POST', '/das/occurrences/%d/conclude' % occ['id'],
         {'conclusion': 'EXEMPT'})
print('ok   DCMS-INV-042 结论免报却无免报判定行被拒')

admin.call('POST', '/das/occurrences/%d/conclude' % occ['id'],
           {'conclusion': 'REPORTABLE', 'containment_needed': True,
            'containment_note': '暂停该批次刹车组件的放行建议, 已通知使用人'}, expect=200)
print('ok   结论为应报告, 遏制措施同步记录（步骤 2、4）')

admin.call('POST', '/das/occurrences/%d/conclude' % occ['id'],
           {'conclusion': 'REPORTABLE', 'containment_needed': True}, expect=400)
print('ok   判定需要遏制却不写措施被拒')

# ---------------- 判据 M6-1: 不填确认存在时间 = 计时不开始 ----------------
pend = admin.call('GET', '/das/occurrences/clock-pending', expect=200)
check(any(p['id'] == occ['id'] for p in pend),
      'M6-1 属范围却无确认存在时间的被单独列出')
dl = admin.call('GET', '/das/occurrences/deadlines', expect=200)
mine = [d for d in dl if d['id'] == occ['id']]
check(mine and mine[0]['clock_state'] == 'NO_CLOCK' and mine[0]['deadline_at'] is None,
      'M6-1 未确认时没有到期时刻')
print('ok   M6-1 "不填确认存在时间"是绕过计时的另一条路, 这类记录单独可见（不是合规）')

# ---------------- 判据 M6-1: 起算点是确认存在, 不是登记 ----------------
admin.call('POST', '/das/occurrences/%d/confirmed-at' % occ['id'],
           {'confirmed_at': iso(-1)}, expect=400)
print('ok   确认存在时间不得晚于当前时间')
admin.call('POST', '/das/occurrences/%d/confirmed-at' % occ['id'],
           {'confirmed_at': iso(40)}, expect=400)
print('ok   确认存在时间不得早于首次接收时间（确认是本单位的动作）')

admin.call('POST', '/das/occurrences/%d/confirmed-at' % occ['id'],
           {'confirmed_at': iso(20)}, expect=200)
mine = [d for d in admin.call('GET', '/das/occurrences/deadlines', expect=200)
        if d['id'] == occ['id']][0]
check(mine['clock_state'] == 'RUNNING' and 19 <= mine['elapsed_hours'] <= 21,
      'M6-1 计时从确认存在起算（已过 %s 小时）' % mine['elapsed_hours'])
# 登记在接收后 30 小时才做, 若按登记起算则早已超期; 按确认起算只过了 20 小时。
# 三个起算点会给出三个明显不同的数: 登记起算约 0 小时, 接收起算约 30 小时,
# 确认起算约 20 小时。断言它落在确认那一档, 且不在另外两档附近——
# 写成"小于某个宽上界"是恒真的, 等于没断言。
check(mine['elapsed_hours'] > 15, 'M6-1 计时不是从登记时刻起算（那会接近 0）')
check(abs(mine['elapsed_hours'] - occ['registration_lag_hours']) > 5,
      'M6-1 计时也不是从首次接收起算（那会接近 %.1f）' % occ['registration_lag_hours'])
print('ok   M6-1 48 小时从确认存在起算, 与登记时间无关（登记滞后 %.1f 小时不影响）'
      % occ['registration_lag_hours'])

# ---------------- 判据 M6-1: 往后挪须留痕 ----------------
check(rejected("UPDATE das_occurrence SET confirmed_at=now() WHERE id=%s", (occ['id'],)),
      'DCMS-INV-039 直接改写确认存在时间被数据库拒绝')
print('ok   DCMS-INV-039 绕过服务层直接把起算点往后挪, 数据库拒绝')

admin.call('POST', '/das/occurrences/%d/confirmed-at' % occ['id'],
           {'confirmed_at': iso(10)}, expect=400)
print('ok   改写确认存在时间不写理由被拒')
admin.call('POST', '/das/occurrences/%d/confirmed-at' % occ['id'],
           {'confirmed_at': iso(10), 'reason': '原填错: 把接收时间当成了确认时间'}, expect=200)
d = admin.call('GET', '/das/occurrences/%d' % occ['id'], expect=200)
check(len(d['time_changes']) == 1 and d['time_changes'][0]['reason'], '改动留痕且带理由')
print('ok   写了理由可以改, 旧值、新值、改动人、理由都在（改得了, 改不掉痕迹）')

# ---------------- 判据 M6-2: 缺项不得阻止报送 ----------------
sub = admin.call('POST', '/das/occurrences/%d/submissions' % occ['id'],
                 {'channel': 'AMOS', 'reference_no': 'CAAC-' + STAMP}, expect=201)
check(sub['content_gaps'] and len(sub['content_gaps']) == 7,
      'M6-2 报送成功, 且本次仍缺的内容记在报送记录上')
check(sub['clock']['clock_state'] == 'ON_TIME', 'M6-2 10 小时前确认, 当前报送在 48 小时内')
print('ok   M6-2 七项内容全缺仍可报送, 缺项随报送记录留档（"先报已知部分, 注明后续补充"）')

admin.call('POST', '/das/occurrences/%d/submissions' % occ['id'],
           {'channel': 'EMAIL', 'reference_no': 'X'}, expect=400)
print('ok   AMOS 以外的途径不写明实际方式被拒（步骤 5）')
admin.call('POST', '/das/occurrences/%d/submissions' % occ['id'],
           {'channel': 'EMAIL', 'channel_note': '主管监察员指定邮箱, AMOS 当日不可用',
            'reference_no': 'CAAC-S-' + STAMP, 'is_supplement': True}, expect=201)
mine = [d for d in admin.call('GET', '/das/occurrences/deadlines', expect=200)
        if d['id'] == occ['id']][0]
check(mine['clock_state'] == 'ON_TIME', '补充报送不重算计时, 只认首次报送')
print('ok   补充报送另记一条, 不重算 48 小时（只认首次报送）')

check(rejected("UPDATE das_occurrence_submission SET reference_no='改掉' WHERE id=%s",
               (sub['id'],)), '报送记录不可改写')
print('ok   报送记录只增不改: 途径、时间、编号都是已发生的事实')

# ---------------- 超期与迟报 ----------------
od = admin.call('POST', '/das/occurrences', {
    'report_no': 'OC-OD-' + STAMP, 'description': '发动机失效, 超期未报',
    'received_at': iso(100), 'reporter_user_id': admin.id}, expect=201)
for c in cats:
    admin.call('POST', '/das/occurrences/%d/triage-items' % od['id'],
               {'category_seq': c['seq'], 'verdict': 'YES' if c['seq'] == 10 else 'NO',
                'note': '发动机失效, 对应第 10 种情形' if c['seq'] == 10 else None}, expect=201)
admin.call('POST', '/das/occurrences/%d/conclude' % od['id'],
           {'conclusion': 'REPORTABLE'}, expect=200)
admin.call('POST', '/das/occurrences/%d/confirmed-at' % od['id'],
           {'confirmed_at': iso(60)}, expect=200)
ov = admin.call('GET', '/das/occurrences/deadlines?state=OVERDUE', expect=200)
check(any(o['id'] == od['id'] for o in ov), '超 48 小时未报送的列为 OVERDUE')
mine = [d for d in ov if d['id'] == od['id']][0]
check(mine['elapsed_hours'] >= 48, '已过 %s 小时' % mine['elapsed_hours'])
print('ok   超 48 小时未报送显形为 OVERDUE（已过 %.0f 小时）——系统只让它无法被忽略, 不自动补救'
      % mine['elapsed_hours'])

admin.call('POST', '/das/occurrences/%d/submissions' % od['id'],
           {'channel': 'AMOS', 'reference_no': 'CAAC-LATE-' + STAMP}, expect=201)
mine = [d for d in admin.call('GET', '/das/occurrences/deadlines', expect=200)
        if d['id'] == od['id']][0]
check(mine['clock_state'] == 'LATE', '超期后才报的记为 LATE, 不因补报而变成按时')
print('ok   超期后补报记为 LATE, 不会因为"报了"就变成按时')

# 48 小时是日历小时: 到期时刻与确认时刻正好相差 48 小时, 不跳周末
gap = (dt.datetime.fromisoformat(mine['deadline_at'])
       - dt.datetime.fromisoformat(mine['confirmed_at'])).total_seconds() / 3600
check(abs(gap - 48) < 0.01, '判据 L1 到期时刻 = 确认时刻 + 48 日历小时（实际 %.2f）' % gap)
print('ok   L1 48 小时是日历小时: 到期时刻正好是确认后 48 小时, 不按工作日顺延')

# ---------------- 判据 M6-3: 免报须在任的适航管理负责人签署 ----------------
ex = admin.call('POST', '/das/occurrences', {
    'report_no': 'OC-EX-' + STAMP, 'description': '疑似维修不当导致的渗漏',
    'received_at': iso(3), 'reporter_user_id': admin.id}, expect=201)
for c in cats:
    admin.call('POST', '/das/occurrences/%d/triage-items' % ex['id'],
               {'category_seq': c['seq'], 'verdict': 'NO'}, expect=201)

r = admin.call('POST', '/das/occurrences/%d/exemption' % ex['id'],
               {'ground': 'IMPROPER_MAINTENANCE', 'rationale': '已确认由不恰当的维修造成',
                'evidence': '维修工单 WO-2026-331 与现场照片'}, expect=400)
check('适航管理负责人' in str(r), 'M6-3 拒绝理由点明须适航管理负责人签署')
print('ok   M6-3 操作人没有在任的适航管理负责人任命, 不得作免报判定（按岗位判, 不按账号角色判）')

awm = make_user(admin, 'CONFIGURATION_MANAGER', 'awm')
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", ('AWM' + STAMP, awm.id))
admin.call('POST', '/das/appointments',
           {'user_id': awm.id, 'position_code': 'AWM', 'kind': 'FORMAL'}, expect=201)
awm.call('POST', '/das/occurrences/%d/exemption' % ex['id'],
         {'ground': 'IMPROPER_MAINTENANCE', 'rationale': '已确认由不恰当的维修造成',
          'evidence': '维修工单 WO-2026-331 与现场照片'}, expect=201)
admin.call('POST', '/das/occurrences/%d/conclude' % ex['id'],
           {'conclusion': 'EXEMPT'}, expect=200)
print('ok   M6-3 在任适航管理负责人可作免报判定, 理由与证据同时留存')

check(rejected("UPDATE das_occurrence_exemption SET rationale='改掉' WHERE occurrence_id=%s",
               (ex['id'],)), '免报判定不可改写')
print('ok   免报判定不可改写不可删除（第 7 章: 不得口头决定）')

# ---------------- 判据 038: 免报的也在台账, 且不得删除 ----------------
reg = admin.call('GET', '/das/occurrences', expect=200)
ids = {x['id'] for x in reg}
check(ex['id'] in ids and od['id'] in ids and occ['id'] in ids,
      '038 含判定免报的全部在台账')
check(rejected("DELETE FROM das_occurrence WHERE id=%s", (ex['id'],)),
      'DCMS-INV-038 事件报告不得删除')
print('ok   DCMS-INV-038 所有报告（含判定不需上报的）均登记, 不得删除')

# ---------------- 步骤 9: 匿名与保密 ----------------
anon = admin.call('POST', '/das/occurrences', {
    'report_no': 'OC-AN-' + STAMP, 'description': '匿名反映的构型偏差',
    'received_at': iso(1), 'disclosure': 'ANONYMOUS'}, expect=201)
admin.call('POST', '/das/occurrences', {
    'description': '匿名却带报告人', 'received_at': iso(1),
    'disclosure': 'ANONYMOUS', 'reporter_user_id': admin.id}, expect=400)
print('ok   匿名提交不得留下报告人（匿名 ≠ 保密: 一个没有报告人, 一个有但不公开）')
admin.call('POST', '/das/occurrences', {
    'description': '具名却没有报告人', 'received_at': iso(1), 'disclosure': 'NAMED'}, expect=400)
print('ok   具名或保密提交须写明报告人')

conf = admin.call('POST', '/das/occurrences', {
    'report_no': 'OC-CF-' + STAMP, 'description': '保密反映的供应商问题',
    'received_at': iso(1), 'disclosure': 'CONFIDENTIAL', 'reporter_user_id': admin.id},
    expect=201)
reg = {x['id']: x for x in admin.call('GET', '/das/occurrences', expect=200)}
check(reg[anon['id']]['reporter_display'] is None, '匿名件台账无报告人')
check(reg[conf['id']]['reporter_display'] is None, '保密件台账不显示报告人')
check(reg[occ['id']]['reporter_display'] is not None, '具名件台账显示报告人')
print('ok   步骤 9 保密件与匿名件在台账都不显示报告人, 具名件显示')

det = admin.call('GET', '/das/occurrences/%d' % conf['id'], expect=200)
check(det['reporter_user_id'] is None and det['reporter_name_text'] is None,
      '保密件的明细接口也不返回报告人身份')
print('ok   保密件连明细接口都不返回报告人身份（不是只在列表里遮一下）')

admin.call('POST', '/das/occurrences/%d/feedback' % anon['id'],
           {'content': '已核实并关闭'}, expect=400)
print('ok   匿名件没有可反馈的对象')
admin.call('POST', '/das/occurrences/%d/feedback' % conf['id'],
           {'content': '已核实, 供应商已整改'}, expect=201)
print('ok   保密件仍须反馈——保密是不公开报告人, 不是找不到报告人')

# ---------------- 步骤 7: 调查 ----------------
admin.call('POST', '/das/occurrences/%d/investigation' % od['id'],
           {'findings': '两台发动机同批次燃油控制单元存在装配偏差',
            'root_cause': '供应商工艺文件版本未同步', 'report_ref': 'INV-' + STAMP}, expect=201)
admin.call('POST', '/das/occurrences/%d/investigation' % od['id'],
           {'findings': '', 'root_cause': 'x'}, expect=422)
print('ok   步骤 7 调查须写明发现与根本原因')
check(rejected("UPDATE das_occurrence_investigation SET root_cause='改掉' WHERE occurrence_id=%s",
               (od['id'],)), '调查记录不可改写')
print('ok   调查记录只增不改, 改结论请另记一次')

due = admin.call('GET', '/das/occurrences/feedback-due', expect=200)
check(not any(d['id'] == anon['id'] for d in due), '匿名件不进应反馈清单')
check(not any(d['id'] == conf['id'] for d in due), '已反馈的不再进清单')
print('ok   应反馈而未反馈的清单排除匿名件与已反馈件（共 %d 条待反馈）' % len(due))

print()
print('全部通过: 事件报告 48 小时链条')
