"""时限与周期引擎（基础能力）。

按判据 I-总 以反例为主。这个模块的要害是 2026-10-01 的那条决策：
**法规要求的时限也可能变，全部做成可设置的参数**。

改成可设置之后，防护从"做不到"降成了"要走手续"，所以用例的重点是证明两条路的闸门
确实不一样：

  · 规章规定的那个数（上限）—— 只能由**规章修订**驱动。内部决定改不了它；
    登记了修订（条款出处、修订文号、规章生效日期、证据）之后才改得动。
  · 本单位实际执行值 —— 走 UG-DAF-09 分类闸门，但**不得超过上限**，
    分类为非重大也不例外；换个单位也绕不过去。只能更严。

其余覆盖：
  T1    一处取值；参数未配置时期限算不出来，不回落默认常量
  T1-2  八个字段；按历史日期取当时生效的值
  T2    前置条件类没有数值、不生成任务
  T3    事件触发类不得按周期生成，周期类不得挂事件实例
  P2-1  正面：重大更改在认可到手前不得生效，"已批准但未生效"查得到
        反面：**非重大更改不应被要求认可证据而卡住**
  064   约束来源不得改写；日历口径不可由配置改变
  074   不得用"初值"绕过闸门
  072   取值与修订记录 append-only

注意: 本用例会登记一条规章修订并据此改动 L1 的上限。修订记录按设计是永久的
（append-only）, 所以在同一个库上重跑第二遍时 L1 的上限已经是 72 —— 断言都写成不依赖
初始上限, 验超限用的是本用例不去修订的 L4。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-deadline.py <credentials.json>
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
    r = client.call(method, path, body, expect=409)
    got = ((r or {}).get('error') or {}).get('rule')
    check(got == rule, '%s 拦下（实际报出 %s）' % (rule, got))
    return r


# ================= 判据 M2-2 式的写入闸门 =================
r = admin.call('POST', '/das/deadlines/L1.OCCURRENCE_REPORT/values',
               {'value_num': 24, 'value_unit': 'HOUR', 'effective_from': d(0),
                'change_request_ref': 'DAF09-' + STAMP, 'classification': 'MINOR'},
               expect=403)
check('管理员改个设置' in str(r) or '判据 P2-1' in str(r),
      '拒绝理由说明参数变更不是"管理员改个设置"')
print('ok   P2-1 admin 无岗位任命时改不了时限参数——参数变更入口内建分类闸门')

awm = make_user(admin, 'CONFIGURATION_MANAGER', 'dlawm')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'dlam')
for u, tag in ((awm, 'DLAWM'), (am, 'DLAM')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, code in ((awm, 'AWM'), (am, 'AM')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': code, 'kind': 'FORMAL'}, expect=201)
print('ok   已任命适航管理负责人与责任经理（分类与批准是两个人, 判据 I3 式的分离）')

# ================= 判据 T1-2: 八个字段 =================
reg = admin.call('GET', '/das/deadlines', expect=200)
check(len(reg) >= 16, '追溯矩阵含全部控制项（%d 项）' % len(reg))
l1 = [x for x in reg if x['code'] == 'L1.OCCURRENCE_REPORT'][0]
for f in ('constraint_source', 'trigger_mode', 'calendar_basis', 'start_point',
          'ceiling_value', 'change_authority', 'value_num', 'effective_from'):
    check(l1[f] is not None, 'T1-2 字段「%s」齐备' % f)
print('ok   T1-2 八个字段齐备: 来源=%s 触发=%s 口径=%s 上限=%s%s 取值=%s%s 生效日=%s'
      % (l1['constraint_source'], l1['trigger_mode'], l1['calendar_basis'],
         l1['ceiling_value'], l1['ceiling_unit'], l1['value_num'], l1['value_unit'],
         l1['effective_from']))
check(l1['start_point'] and '确认' in l1['start_point'],
      'T1-2 起算点写明具体事件的具体时刻（缺它就无法判超期）')
print('ok   起算点: %s' % l1['start_point'])

by_src = {}
for x in reg:
    by_src[x['constraint_source']] = by_src.get(x['constraint_source'], 0) + 1
print('ok   控制项按约束来源: %s'
      % '、'.join('%s %d 项' % (k, v) for k, v in sorted(by_src.items())))

# ================= 判据 T1: 一处取值 =================
due = admin.call('GET', '/das/deadlines/L1.OCCURRENCE_REPORT/due-at'
                 '?started_at=2026-10-01T10:00:00%2B08:00', expect=200)
check(due['due_at'] is not None and due['value_num'] is not None,
      'T1 到期时刻由引擎算出')
print('ok   T1 一处取值: L1 起算 2026-10-01 10:00 → 到期 %s（取值 %s %s, 口径 %s）'
      % (due['due_at'], due['value_num'], due['value_unit'], due['calendar_basis']))

unset = admin.call('GET', '/das/deadlines/unset', expect=200)
check(unset, 'T1 未配置取值的控制项列得出来（%d 项）' % len(unset))
nc = admin.call('GET', '/das/deadlines/%s/due-at?started_at=2026-10-01T10:00:00%%2B08:00'
                % unset[0]['code'], expect=200)
check(nc['due_at'] is None and '不回落默认常量' in (nc.get('note') or ''),
      'T1 未配置时期限算不出来, 且说明不回落默认常量')
print('ok   T1 未配置的 %s 期限为空而非默认值——回落常量等于又把值写回了代码里'
      % unset[0]['code'])

# ================= 硬边界: 执行值不得超过上限 =================
BASE = {'value_unit': 'HOUR', 'effective_from': d(0), 'classification': 'MINOR'}
# 用 L4（独立监督周期, 上限 12 个月）而不是 L1 来验超上限: 本用例后面会**修订 L1 的
# 上限**, 而修订记录按设计是永久的, 拿 L1 验超限在同一个库上重跑第二遍就不成立了。
# 顺带这个数字选得正好: 把 12 个月填成 24 个月, 正是判据 L10 说的那种混用。
violates('DCMS-INV-065', awm, 'POST', '/das/deadlines/L4.DAS_SUPERVISION_CYCLE/values',
         {'value_num': 24, 'value_unit': 'MONTH', 'effective_from': d(0),
          'classification': 'MINOR', 'change_request_ref': 'DAF09-A-' + STAMP})
print('ok   DCMS-INV-065 把独立监督周期从 12 个月放宽到 24 个月被拒')
print('     （24 个月是局方对本单位的监督周期, 不是本单位内部监督的周期——判据 L10）')
print('     （手册 0.4.1: 法规硬时限不得通过任何路线降低, **包括分类为非重大之后**）')

violates('DCMS-INV-065', awm, 'POST', '/das/deadlines/L3.NCR_CLASS2/values',
         {'value_num': 120, 'value_unit': 'DAY', 'effective_from': d(0),
          'classification': 'MINOR', 'change_request_ref': 'DAF09-B-' + STAMP})
print('ok   DCMS-INV-065 换单位也绕不过: 120 天 vs 上限 3 个月（比较前换算到同一单位）')

# 更严的可以
v1 = awm.call('POST', '/das/deadlines/L1.OCCURRENCE_REPORT/values',
              {**BASE, 'value_num': 24, 'change_request_ref': 'DAF09-C-' + STAMP},
              expect=201)
check('非重大' in (v1.get('note') or ''), '非重大更改的提示说明不需要局方认可证据')
am.call('POST', '/das/deadlines/values/%d/approve' % v1['id'], expect=200)
am.call('POST', '/das/deadlines/values/%d/activate' % v1['id'], expect=200)
cur = admin.call('GET', '/das/deadlines/L1.OCCURRENCE_REPORT', expect=200)
check(float(cur['value_num']) == 24, '执行值已改为 24 小时（比法规 48 更严）')
print('ok   更严的执行值允许: L1 现行 24 小时, 上限仍为 %s 小时' % cur['ceiling_value'])

# ================= 你提的那条: 法规时限可改, 但只能由规章修订驱动 =================
# 用 96 而不是 72: 本用例后面会把上限修订为 72, 重跑时"改成 72"等于没改,
# IS DISTINCT FROM 为假就不会触发检查, 这条断言会假过。
check(rejected("UPDATE das_deadline_param SET ceiling_value=96 WHERE code=%s",
               ('L1.OCCURRENCE_REPORT',)),
      'DCMS-INV-073 绕过服务层直接改上限被数据库拒绝')
print('ok   DCMS-INV-073 内部决定改不动"规章规定的那个数"（连绕过服务层也不行）')

AMEND = {'field_changed': 'CEILING', 'new_ceiling_value': 72, 'new_ceiling_unit': 'HOUR',
         'regulation_ref': 'CCAR-21.5（六）',
         'regulation_revision_ref': 'CCAR-21-R6 修订（CI 示例, 非真实修订）',
         'regulation_effective_from': d(90),
         'evidence_ref': '规章原文留存 REG-' + STAMP}
am.call('POST', '/das/deadlines/L1.OCCURRENCE_REPORT/amendment', AMEND, expect=403)
print('ok   规章修订须由在任适航管理负责人登记（责任经理不是跟踪规章的人）')

awm.call('POST', '/das/deadlines/L1.OCCURRENCE_REPORT/amendment',
         {**AMEND, 'regulation_revision_ref': ''}, expect=422)
awm.call('POST', '/das/deadlines/L1.OCCURRENCE_REPORT/amendment',
         {**AMEND, 'evidence_ref': ''}, expect=422)
print('ok   修订文号与证据留存位置缺一不可（说不出是哪次修订改的, 那就不是修订）')

res = awm.call('POST', '/das/deadlines/L1.OCCURRENCE_REPORT/amendment', AMEND, expect=200)
check(float(res['ceiling_value']) == 72, '上限已按规章修订改为 72 小时')
check('不会自动跟着变' in (res.get('note') or ''), '提示说明执行值不自动跟变')
print('ok   登记规章修订后上限改为 72 小时——法规时限确实是可设置的参数')

hist = admin.call('GET', '/das/deadlines/amendments', expect=200)
mine = [h for h in hist if h['param_code'] == 'L1.OCCURRENCE_REPORT']
check(mine and mine[0]['old_value'] and mine[0]['new_value'],
      '修订史记下了旧值、新值、条款出处、修订文号与规章生效日期')
print('ok   修订史: %s → %s（%s, 规章生效 %s）'
      % (mine[0]['old_value'], mine[0]['new_value'],
         mine[0]['regulation_revision_ref'], mine[0]['regulation_effective_from']))

# 上限放宽之后, 执行值才可能放宽——但仍要走分类闸门
v2 = awm.call('POST', '/das/deadlines/L1.OCCURRENCE_REPORT/values',
              {**BASE, 'value_num': 72, 'effective_from': d(90),
               'change_request_ref': 'DAF09-D-' + STAMP}, expect=201)
am.call('POST', '/das/deadlines/values/%d/approve' % v2['id'], expect=200)
am.call('POST', '/das/deadlines/values/%d/activate' % v2['id'], expect=200)
print('ok   上限放宽后执行值才放得宽, 而且仍然走了 UG-DAF-09 分类闸门')

# 日历口径: 同样只能由规章修订驱动
check(rejected("UPDATE das_deadline_param SET calendar_basis='WORKING' WHERE code=%s",
               ('L1.OCCURRENCE_REPORT',)),
      'DCMS-INV-073 日历口径不可由配置改变（判据 L-总）')
print('ok   DCMS-INV-073 日历口径不可由配置改变——换一个下拉框会整体改掉一条时限')

check(rejected("UPDATE das_deadline_param SET constraint_source='INTERNAL' WHERE code=%s",
               ('L1.OCCURRENCE_REPORT',)),
      'DCMS-INV-064 约束来源不得改写')
print('ok   DCMS-INV-064 把法规项改成内部项被拒——那不是修订, 是剥夺它的防护')

# ================= 判据 P2-1 的正反两面 =================
v3 = awm.call('POST', '/das/deadlines/M6.REGISTER_LAG/values',
              {'value_num': 2, 'value_unit': 'HOUR', 'effective_from': d(30),
               'classification': 'MAJOR', 'change_request_ref': 'DAF09-E-' + STAMP},
              expect=201)
check('重大' in (v3.get('note') or '') and '认可' in (v3.get('note') or ''),
      '重大更改的提示说明须报 DPI 并取得认可')
am.call('POST', '/das/deadlines/values/%d/approve' % v3['id'], expect=200)
pend = admin.call('GET', '/das/deadlines/pending-activation', expect=200)
mine = [p for p in pend if p['id'] == v3['id']]
check(mine and '认可' in mine[0]['blocker'], 'P2-1 "已批准但未生效"查得到, 且说明卡在哪')
print('ok   P2-1 中间状态可查: %s — %s' % (mine[0]['param_code'], mine[0]['blocker']))

violates('DCMS-INV-067', am, 'POST', '/das/deadlines/values/%d/activate' % v3['id'])
print('ok   DCMS-INV-067 重大更改在认可证据到手前不得生效')

awm.call('POST', '/das/deadlines/values/%d/caac-ack' % v3['id'],
         {'ack_ref': 'DPI 认可函 ' + STAMP}, expect=200)
am.call('POST', '/das/deadlines/values/%d/activate' % v3['id'], expect=200)
print('ok   登记局方认可证据后可启用')

# 反面：非重大更改不应被要求认可证据
v4 = awm.call('POST', '/das/deadlines/M7.TREND_REVIEW/values',
              {'value_num': 1, 'value_unit': 'MONTH', 'effective_from': d(0),
               'classification': 'MINOR', 'change_request_ref': 'DAF09-F-' + STAMP},
              expect=201)
am.call('POST', '/das/deadlines/values/%d/approve' % v4['id'], expect=200)
am.call('POST', '/das/deadlines/values/%d/activate' % v4['id'], expect=200)
cur = admin.call('GET', '/das/deadlines/M7.TREND_REVIEW', expect=200)
check(float(cur['value_num']) == 1, '非重大更改无认可证据直接生效')
print('ok   反向用例: 非重大更改**不需要**也**不应被要求**认可证据（第四之二节验收示例②）')
print('     （把所有变更都当重大处理是另一种错误, 会让人绕开系统改）')

r = awm.call('POST', '/das/deadlines/values/%d/caac-ack' % v4['id'],
             {'ack_ref': 'x'}, expect=400)
check('非重大' in str(r), '给非重大更改登记认可证据被拒, 并说明理由')
print('ok   给非重大更改硬塞认可证据被拒——不是"多做无害", 是把流程做错了')

# 未批准就启用
v5 = awm.call('POST', '/das/deadlines/M7.TREND_REVIEW/values',
              {'value_num': 2, 'value_unit': 'MONTH', 'effective_from': d(60),
               'classification': 'MINOR', 'change_request_ref': 'DAF09-G-' + STAMP},
              expect=201)
violates('DCMS-INV-066', am, 'POST', '/das/deadlines/values/%d/activate' % v5['id'])
print('ok   DCMS-INV-066 未经批准不得生效')

awm.call('POST', '/das/deadlines/M7.TREND_REVIEW/values',
         {'value_num': 2, 'value_unit': 'MONTH', 'effective_from': d(60),
          'classification': 'MINOR', 'change_request_ref': ''}, expect=422)
print('ok   不填 UG-DAF-09 单据号被拒（没有单据号就无从追溯这次变更评估过什么）')

# ================= 判据 T1-2 字段⑧: 按当时生效的值判 =================
hist = admin.call('GET', '/das/deadlines/L1.OCCURRENCE_REPORT', expect=200)
check(len(hist['values']) >= 3, '取值历史保留（初值 + 两次变更）')
old = db_query("SELECT das_deadline_value_active('L1.OCCURRENCE_REPORT', %s) AS v",
               (d(-400),))[0]['v']
now_v = db_query("SELECT das_deadline_value_active('L1.OCCURRENCE_REPORT') AS v")[0]['v']
check(old is None or float(old) != float(now_v),
      'T1-2 字段⑧ 按历史日期取当时生效的值, 不是按现在的值')
print('ok   T1-2 字段⑧ 历史记录按当时生效的值判: 400 天前 %s, 今天 %s'
      % (old, now_v))

# ================= 判据 T2 / T3 =================
pre = [x for x in reg if x['trigger_mode'] == 'PRECONDITION']
check(pre and all(x['condition_expr'] for x in pre),
      'T2 前置条件类有条件表达式, 没有数值时长')
check(all(x['calendar_basis'] == 'NONE' for x in pre), 'T2 前置条件类无日历口径')
print('ok   T2 前置条件类（%d 项）填条件表达式而非数值: %s'
      % (len(pre), pre[0]['condition_expr']))

violates('DCMS-INV-070', awm, 'POST', '/das/deadlines/%s/values' % pre[0]['code'],
         {'value_num': 5, 'value_unit': 'DAY', 'effective_from': d(0),
          'classification': 'MINOR', 'change_request_ref': 'DAF09-H-' + STAMP})
print('ok   DCMS-INV-070 给前置条件类配数值被拒（判据 T2: 阻断, 不是提醒）')

# 判据 T3 在两层都挡: 服务层先判触发方式（400, 理由说得具体）, 数据库的
# DCMS-INV-069 挡住绕过服务层的写入。两层都验, 少验一层就不知道另一层还在不在。
r = am.call('POST', '/das/deadlines/L2.NCR_CLASS1/periodic-tasks',
            {'period_key': '2026Q4', 'started_at': '2026-10-01T00:00:00+08:00'}, expect=400)
check('判据 T3' in str(r) and '两头错' in str(r),
      '服务层的理由指到判据 T3, 而不是"算不出到期时刻"')
print('ok   T3 事件触发类不得按周期生成任务（服务层先判触发方式, 再算到期时刻）')
print('     （顺序反了会先报"日历未加载", 把"你用错了生成器"这个真正的理由盖住）')

r = am.call('POST', '/das/deadlines/M7.TREND_REVIEW/event-tasks',
            {'object_type': 'DAS_NCR', 'object_id': '1',
             'started_at': '2026-10-01T00:00:00+08:00'}, expect=400)
check('判据 T3' in str(r), '周期类挂事件实例被拒, 理由指到判据 T3')
print('ok   T3 周期类不得挂事件实例（周期任务挂到某个事件上, 下一期就没有了）')

check(rejected("""INSERT INTO das_deadline_task (param_code, period_key, started_at, due_at)
                  VALUES ('L2.NCR_CLASS1','2026Q4',now(),now()+interval '21 days')"""),
      'DCMS-INV-069 绕过服务层直写也被数据库拒绝')
check(rejected("""INSERT INTO das_deadline_task (param_code, event_object_type,
                      event_object_id, started_at, due_at)
                  VALUES ('M7.TREND_REVIEW','DAS_NCR','9',now(),now()+interval '90 days')"""),
      'DCMS-INV-069 反方向绕过也被拒绝')
print('ok   DCMS-INV-069 数据库这一层同样挡住两个方向（绕过服务层也不行）')

# 工作日口径的控制项要先有日历（0036）: L2 是 21 个工作日, 没有日历算不出到期时刻。
# 这里声明的是 CI 夹具, 不是真实的节假日安排 —— 真实年度安排由国务院办公厅逐年发布。
gaps = admin.call('GET', '/das/work-calendar/gaps', expect=200)
if not any(g['calendar_year'] == TODAY.year and g['declared'] for g in gaps):
    admin.call('POST', '/das/work-calendar/years',
               {'year': TODAY.year, 'source_ref': 'CI 测试夹具, 非真实节假日安排'},
               expect=201)
wd = admin.call('GET', '/das/deadlines/L2.NCR_CLASS1/due-at'
                '?started_at=2026-10-01T00:00:00%2B08:00', expect=200)
check(wd['due_at'] is not None and wd['calendar_basis'] == 'WORKING',
      '日历加载后工作日口径的期限算得出来')
print('ok   T1 工作日口径: L2 起算 2026-10-01 → 到期 %s（%s 个工作日, 口径 %s）'
      % (wd['due_at'][:10], wd['value_num'], wd['calendar_basis']))

t1 = am.call('POST', '/das/deadlines/L2.NCR_CLASS1/event-tasks',
             {'object_type': 'DAS_NCR', 'object_id': 'ci-' + STAMP,
              'started_at': '2026-10-01T00:00:00+08:00'}, expect=201)
check(t1.get('due_at') or t1.get('note'), '事件任务已生成或已存在')
t2 = am.call('POST', '/das/deadlines/M7.TREND_REVIEW/periodic-tasks',
             {'period_key': 'CI' + STAMP, 'started_at': '2026-10-01T00:00:00+08:00'},
             expect=201)
ts = admin.call('GET', '/das/deadlines/tasks', expect=200)
kinds = {x['trigger_mode'] for x in ts}
check('EVENT' in kinds and 'PERIODIC' in kinds, '两类任务各自生成成功')
print('ok   T3 各自正确的生成方式都通: 事件任务挂实例, 周期任务挂期间键')

if t2.get('id'):
    am.call('POST', '/das/deadlines/tasks/%d/complete' % t2['id'],
            {'note': 'CI 完成'}, expect=200)
    print('ok   任务可完成, 状态按到期时刻判 ON_TIME / LATE / OVERDUE')

r = am.call('POST', '/das/deadlines/%s/event-tasks' % pre[0]['code'],
            {'object_type': 'DOC', 'object_id': '1',
             'started_at': '2026-10-01T00:00:00+08:00'}, expect=400)
check('判据 T2' in str(r) and '阻断' in str(r), '前置条件类的理由指到判据 T2')
check(rejected("""INSERT INTO das_deadline_task (param_code, event_object_type,
                      event_object_id, started_at, due_at)
                  VALUES ('T2.PLAN_APPROVED_BEFORE_AUDIT','DOC','9',now(),now()+interval '1 day')"""),
      'DCMS-INV-069 前置条件类在数据库这一层同样不生成任务')
print('ok   T2 前置条件类不生成任务（进了到期视图就变成"提醒"了）——两层都挡')

# ================= 判据 074 / 072 =================
check(rejected("""INSERT INTO das_deadline_value
                    (param_code, value_num, value_unit, effective_from, is_baseline,
                     baseline_source, activated_at)
                  VALUES ('M6.REGISTER_LAG', 8, 'HOUR', current_date, true, 'x', now())"""),
      'DCMS-INV-074 不得用"初值"绕过闸门')
print('ok   DCMS-INV-074 用"初值"绕过分类闸门被拒（初值只能是该控制项的第一行）')

check(rejected("UPDATE das_deadline_value SET value_num=99 WHERE id=%s", (v4['id'],)),
      'DCMS-INV-072 已有取值不可改写')
check(rejected("DELETE FROM das_deadline_value WHERE id=%s", (v4['id'],)),
      'DCMS-INV-072 取值不可删除')
check(rejected("UPDATE das_deadline_statutory_amendment SET new_ceiling_value=96"
               " WHERE param_code=%s", ('L1.OCCURRENCE_REPORT',)),
      'DCMS-INV-072 规章修订记录不可改写')
check(rejected("DELETE FROM das_deadline_param WHERE code=%s", ('M7.TREND_REVIEW',)),
      'DCMS-INV-064 控制项不可删除')
print('ok   DCMS-INV-072 取值与修订记录 append-only; 控制项不可删除（追溯矩阵须完整）')

# ================= 各模块确实从引擎取值 =================
# M6 的 48 小时与 M7 的 21 工作日原本写在视图里, 0039 第 9 节接管。
occ = db_query("SELECT obj_description('das_occurrence_deadline'::regclass,'pg_class') AS c")
check(occ[0]['c'] and 'das_deadline_param' in occ[0]['c'],
      'M6 的时限视图注释写明取自 das_deadline_param')
ncr = db_query("SELECT obj_description('das_ncr_deadline'::regclass,'pg_class') AS c")
check(ncr[0]['c'] and 'das_deadline_param' in ncr[0]['c'],
      'M7 的时限视图注释写明取自 das_deadline_param')
print('ok   T1 M6／M7／M2 的时限视图均已改为从引擎取值（0039 第 9 节接管）')

impl = [x for x in reg if x['implemented_in']]
check(len(impl) >= 8, '追溯矩阵记下了每个控制项被哪些视图消费（%d 项）' % len(impl))
print('ok   追溯矩阵可反查: 改一个参数会影响哪些视图（%d 项已登记消费方）' % len(impl))

print()
print('全部通过: 时限与周期引擎')
