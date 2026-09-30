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
admin.call('POST', '/das/appointments',
           {'user_id': a.id, 'position_code': 'ISM', 'kind': 'FORMAL'}, expect=201)
print('ok   独立监督负责人任命成功')

# 规则一：专任岗位不得兼任
admin.call('POST', '/das/appointments',
           {'user_id': a.id, 'position_code': 'DE', 'kind': 'FORMAL'}, expect=400)
print('ok   DCMS-INV-031 专任岗位不得兼任其他岗位')

# 规则二：独立监督不得承担被监督的运行活动（反向：先运行岗后独立监督）
admin.call('POST', '/das/appointments',
           {'user_id': b.id, 'position_code': 'PM', 'kind': 'FORMAL'}, expect=201)
admin.call('POST', '/das/appointments',
           {'user_id': b.id, 'position_code': 'ISM', 'kind': 'FORMAL'}, expect=400)
print('ok   DCMS-INV-032 已任运行岗者不得再任独立监督负责人')

# 规则三：显式互斥对——适航管理负责人与独立监督负责人
admin.call('POST', '/das/appointments',
           {'user_id': c.id, 'position_code': 'AWM', 'kind': 'FORMAL'}, expect=201)
admin.call('POST', '/das/appointments',
           {'user_id': c.id, 'position_code': 'ISM', 'kind': 'FORMAL'}, expect=400)
print('ok   适航管理负责人不得兼任独立监督负责人')

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
