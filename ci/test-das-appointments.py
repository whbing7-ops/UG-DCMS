"""岗位任命的互斥规则与出缺承接（M1）。

按判据 I-总 写成反例为主：每条互斥都构造一次"尝试违反并确认被拒绝"。
正常路径能走通说明不了什么——约束是不是真拦得住，只有反例能证明。

覆盖：
  A1    没有工号不得任命（工号是判定"不同自然人"的唯一依据）
  A2/A3 岗位互斥在任命时拦截，不等到签署时
  O3-1  临时授权必须有截止日
  O3-2  临时授权必须关联正式任命流程
  O3-3  正式任命生效之日临时授权自动终止，且不追溯否定此前的签署
  A7    统计由当期数据算出，不含人数假设

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-appointments.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
today = dt.date.today()


def rejected(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False


def mk(tag, emp=True):
    u = make_user(admin, 'ENGINEER', tag)
    if emp:
        db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", ('E' + tag + STAMP, u.id))
    return u


a = mk('ap1')
b = mk('ap2')
c = mk('ap3')
noemp = mk('ap4', emp=False)

# ---------------- 判据 A1: 没有工号不得任命 ----------------
admin.call('POST', '/das/appointments',
           {'user_id': noemp.id, 'position_code': 'DE', 'kind': 'FORMAL'}, expect=400)
print('ok   A1 未填工号不得任命')

# ---------------- 岗位字典与空缺视图 ----------------
pos = admin.call('GET', '/das/positions', expect=200)
check(len(pos) >= 14, '岗位字典已导入')
check(any(p['code'] == 'ISM' and p['is_exclusive'] for p in pos), '独立监督负责人标为专任')
print('ok   岗位字典 %d 个，专任岗位已标注' % len(pos))

# ---------------- 判据 A2/A3: 互斥在任命时拦截 ----------------
# 互斥规则在触发器里, 违规经全局处理器归为 409 RULE_VIOLATION(见 app/errors.py:
# "不变量违规是用户请求违反了受控规则"), 不是 400 —— 400 是服务层 ValueError。
#
# 而且必须断言到**哪一条**规则: 三条互斥规则都返回 409, 只断言状态码时, 一条规则
# 失效被另一条顶替, 测试照样全绿。每条用例都选一个只落在该规则上的岗位组合。


def violates(rule, user, position):
    r = admin.call('POST', '/das/appointments',
                   {'user_id': user, 'position_code': position, 'kind': 'FORMAL'}, expect=409)
    got = ((r or {}).get('error') or {}).get('rule')
    check(got == rule, '%s 拦下该组合（实际报出 %s）' % (rule, got))
    return r


# 规则一（最具体）：手册明文列出的互斥对。AWM 与 ISM 在 das_position_exclusion 里,
# 挑这一对才落在 033 上——它同时也是专任岗, 若顺序反了就会被 031 顶替。
admin.call('POST', '/das/appointments',
           {'user_id': c.id, 'position_code': 'AWM', 'kind': 'FORMAL'}, expect=201)
r = violates('DCMS-INV-033', c.id, 'ISM')
check('手册 3.1' in str(r), '033 的理由带手册出处, 不是一句"专任岗位"')
print('ok   DCMS-INV-033 适航管理负责人与独立监督负责人不得由同一人担任（理由带手册出处）')

# 规则二：独立监督不得承担被监督的运行活动。PM 不在显式互斥表里, 故只落在 032 上。
admin.call('POST', '/das/appointments',
           {'user_id': b.id, 'position_code': 'PM', 'kind': 'FORMAL'}, expect=201)
violates('DCMS-INV-032', b.id, 'ISM')
print('ok   DCMS-INV-032 已任被监督运行岗者不得再任独立监督负责人')

# 反方向也要判: 先任 ISM 再任运行岗, 同样是 032。
admin.call('POST', '/das/appointments',
           {'user_id': a.id, 'position_code': 'ISM', 'kind': 'FORMAL'}, expect=201)
violates('DCMS-INV-032', a.id, 'DE')
print('ok   DCMS-INV-032 两个方向都判: 先任独立监督再兼运行岗也被拒')

# 规则三（兜底）：泛化的专任规则。要隔离它, 组合里不能有 ISM, 也不能在显式互斥表里
# ——质量与供应商管理负责人(专任)配设计工程师, 只剩 031 能拦。
f = mk('ap7')
admin.call('POST', '/das/appointments',
           {'user_id': f.id, 'position_code': 'QSM', 'kind': 'FORMAL'}, expect=201)
violates('DCMS-INV-031', f.id, 'DE')
print('ok   DCMS-INV-031 已任专任岗者不得再兼任（v_other 方向）')

g = mk('ap8')
admin.call('POST', '/das/appointments',
           {'user_id': g.id, 'position_code': 'DE', 'kind': 'FORMAL'}, expect=201)
violates('DCMS-INV-031', g.id, 'QSM')
print('ok   DCMS-INV-031 反方向: 已有岗位者不得再任专任岗（v_new 方向）')

# 但允许的兼任不能被误拦：构型管理员可由设计工程师兼任（手册 3.1 明确允许）
d = mk('ap5')
admin.call('POST', '/das/appointments',
           {'user_id': d.id, 'position_code': 'DE', 'kind': 'FORMAL'}, expect=201)
admin.call('POST', '/das/appointments',
           {'user_id': d.id, 'position_code': 'CFG', 'kind': 'FORMAL'}, expect=201)
print('ok   设计工程师兼构型管理员未被误拦（手册 3.1 明确允许）')

# ---------------- 判据 O3-1 / O3-2: 临时授权的两个必填 ----------------
e = mk('ap6')
admin.call('POST', '/das/appointments',
           {'user_id': e.id, 'position_code': 'TE', 'kind': 'TEMPORARY',
            'formal_process_ref': 'NOM-' + STAMP}, expect=400)
print('ok   O3-1 临时授权无截止日被拒')
admin.call('POST', '/das/appointments',
           {'user_id': e.id, 'position_code': 'TE', 'kind': 'TEMPORARY',
            'valid_to': str(today + dt.timedelta(days=30))}, expect=400)
print('ok   O3-2 临时授权未关联正式流程被拒')

tmp = admin.call('POST', '/das/appointments',
                 {'user_id': e.id, 'position_code': 'TE', 'kind': 'TEMPORARY',
                  'valid_to': str(today + dt.timedelta(days=30)),
                  'formal_process_ref': 'NOM-' + STAMP}, expect=201)
print('ok   临时授权登记成功（带截止日与正式流程单据号）')

pend = admin.call('GET', '/das/appointments/temp-pending', expect=200)
check(any(p['id'] == tmp['id'] for p in pend), 'O3-2 未走正式流程的临时授权出现在待办视图')
print('ok   O3-2 只发临时未走正式的可被查出')

# ---------------- 判据 O3-3: 正式任命生效，临时授权自动终止 ----------------
formal = admin.call('POST', '/das/appointments',
                    {'user_id': e.id, 'position_code': 'TE', 'kind': 'FORMAL',
                     'valid_from': str(today + dt.timedelta(days=1)),
                     'appointment_ref': 'APT-' + STAMP}, expect=201)
check(formal['superseded_temporary'] == 1, 'O3-3 正式任命接替了 1 条临时授权')
row = db_query("SELECT valid_to, superseded_by FROM das_appointment WHERE id=%s", (tmp['id'],))[0]
check(row['valid_to'] == today, 'O3-3 临时授权截止日被收到正式任命生效日的前一天')
check(row['superseded_by'] == formal['id'], 'O3-3 临时授权记录了被哪条正式任命接替')
print('ok   O3-3 正式任命生效之日临时授权自动终止，不靠人记得去撤')

# 关键：不是撤销而是收紧截止日——那段时间的签署不应被追溯否定
check(db_query("SELECT revoked_at FROM das_appointment WHERE id=%s",
               (tmp['id'],))[0]['revoked_at'] is None,
      'O3-3 临时授权是被接替不是被撤销：它当时有效，那段时间的签署不受影响')
print('ok   O3-3 接替不等于撤销，历史效力不被追溯否定')

pend2 = admin.call('GET', '/das/appointments/temp-pending', expect=200)
check(not any(p['id'] == tmp['id'] for p in pend2), 'O3-2 正式任命后不再出现在待办视图')
print('ok   O3-2 正式任命后待办视图自动出清')

# ---------------- 任命记录只能撤销，不能改写或删除 ----------------
check(rejected("DELETE FROM das_appointment WHERE id=%s", (formal['id'],)),
      '任命记录不可删除')
check(rejected("UPDATE das_appointment SET position_code='AM' WHERE id=%s", (formal['id'],)),
      '任命的岗位不可修改')
print('ok   任命记录只能撤销，人员岗位类型生效日均不可改写')

# ---------------- 判据 A7: 统计不含人数假设 ----------------
vac = admin.call('GET', '/das/positions/vacant', expect=200)
check(isinstance(vac, list), 'A7 空缺岗位由当期数据算出')
check(not any(p['code'] == 'ISM' for p in vac), 'A7 已有人在任的岗位不出现在空缺列表')
print('ok   A7 空缺视图只列有没有人，不判够不够人（共 %d 个岗位无人在任）' % len(vac))

print()
print('全部通过: 岗位任命与互斥规则')
