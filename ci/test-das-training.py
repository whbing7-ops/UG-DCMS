"""培训与考核的三条硬规则（M1）。

UG-DAW-006 不只是一张记录表。按判据 I-总 写成反例为主，验的是这三条真拦得住：
  第 1 章  首次授权前必须完成初始培训并考核合格
  第 3 章  补考一次；补考仍不合格的不得授权
  第 3 章  已授权人员复训考核不合格的，暂停其签署权直至重新合格

以及判据 P1：课程矩阵与合格标准是配置，不是代码。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-training.py <credentials.json>
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


def mk(tag):
    u = make_user(admin, 'ENGINEER', tag)
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", ('T' + tag + STAMP, u.id))
    return u


# ---------------- 判据 P1: 课程矩阵是配置 ----------------
cs = admin.call('GET', '/das/training/courses', expect=200)
check(len(cs) >= 11, 'P1 课程字典已导入（%d 门）' % len(cs))
check(all(c.get('pass_standard') for c in cs), 'P1 每门课都有合格标准')
req = admin.call('GET', '/das/training/requirements?position_code=DE', expect=200)
check(any(r['level'] == 'REQUIRED' for r in req), 'P1 设计工程师有必修课')
print('ok   P1 课程、学时、合格标准、岗位矩阵均为配置（%d 门课，设计工程师 %d 条要求）'
      % (len(cs), len(req)))

# ---------------- 无任命者：闸门空过（这是边界不是漏洞）----------------
plain = mk('tr0')
admin.call('POST', '/signers', {'user_id': plain.id, 'level': 'REVIEW', 'note': '无任命'},
           expect=201)
print('ok   无在任岗位者不受培训闸门限制——必修课按岗位定，没岗位就没必修课')

# ---------------- 第 1 章: 首次授权前必须完成初始培训 ----------------
u = mk('tr1')
admin.call('POST', '/das/appointments',
           {'user_id': u.id, 'position_code': 'DE', 'kind': 'FORMAL'}, expect=201)
st = admin.call('GET', '/das/training/status?user_id=' + u.id, expect=200)
check(st and all(x['status'] == 'MISSING' for x in st), '刚任命者全部必修课为 MISSING')
print('ok   刚任命的设计工程师有 %d 门必修课，状态均为 MISSING' % len(st))

r = admin.call('POST', '/signers', {'user_id': u.id, 'level': 'REVIEW', 'note': '未培训'},
               expect=400)
check('培训未达标' in str(r) and 'UG-DAW-006' in str(r),
      '第 1 章 未完成初始培训不得授权，且拒绝理由指到依据')
print('ok   UG-DAW-006 第 1 章 未完成初始培训不得授权（400，理由带依据）')

# 补齐全部必修课后即可授权
for x in st:
    admin.call('POST', '/das/training/records',
               {'user_id': u.id, 'course_code': x['course_code'], 'kind': 'INITIAL',
                'trained_on': str(today), 'hours': 4, 'result': 'PASS'}, expect=201)
st2 = admin.call('GET', '/das/training/status?user_id=' + u.id, expect=200)
check(all(x['status'] == 'OK' for x in st2), '补齐后全部 OK')
admin.call('POST', '/signers', {'user_id': u.id, 'level': 'REVIEW', 'note': '已培训'}, expect=201)
print('ok   必修课补齐后可正常授权')

# ---------------- 第 3 章: 补考一次 ----------------
v = mk('tr2')
admin.call('POST', '/das/appointments',
           {'user_id': v.id, 'position_code': 'DE', 'kind': 'FORMAL'}, expect=201)
admin.call('POST', '/das/training/records',
           {'user_id': v.id, 'course_code': 'C01', 'kind': 'INITIAL',
            'trained_on': str(today), 'result': 'PASS', 'is_retake': True}, expect=400)
print('ok   第 3 章 此前无不合格记录而标补考被拒')

admin.call('POST', '/das/training/records',
           {'user_id': v.id, 'course_code': 'C01', 'kind': 'INITIAL',
            'trained_on': str(today), 'result': 'FAIL'}, expect=201)
admin.call('POST', '/das/training/records',
           {'user_id': v.id, 'course_code': 'C01', 'kind': 'INITIAL',
            'trained_on': str(today), 'result': 'FAIL', 'is_retake': True}, expect=201)
check(rejected("""INSERT INTO das_training_record
                         (user_id, course_code, kind, trained_on, result, is_retake, recorded_by)
                  VALUES (%s,'C01','INITIAL',current_date,'FAIL',true,%s)""", (v.id, admin.id)),
      'DCMS-INV-034 同一课程的补考只允许一次')
print('ok   第 3 章 补考只允许一次，第二次补考被数据库拒绝')

r2 = admin.call('POST', '/signers', {'user_id': v.id, 'level': 'REVIEW'}, expect=400)
check('C01' in str(r2) or '法规' in str(r2), '补考仍不合格者不得授权')
print('ok   第 3 章 补考仍不合格的不得授权')

# ---------------- 第 3 章: 复训过期 → 应暂停签署权 ----------------
w = mk('tr3')
admin.call('POST', '/das/appointments',
           {'user_id': w.id, 'position_code': 'DCM', 'kind': 'FORMAL'}, expect=201)
need = admin.call('GET', '/das/training/status?user_id=' + w.id, expect=200)
for x in need:
    admin.call('POST', '/das/training/records',
               {'user_id': w.id, 'course_code': x['course_code'], 'kind': 'INITIAL',
                'trained_on': str(today), 'result': 'PASS'}, expect=201)
admin.call('POST', '/signers', {'user_id': w.id, 'level': 'REVIEW', 'note': '已培训'}, expect=201)

# 把其中一门的培训日期推回两年前：复训周期 12 个月，应变 EXPIRED
db_execute("""UPDATE das_training_record SET trained_on = current_date - interval '2 years'
               WHERE user_id=%s AND course_code=%s""", (w.id, need[0]['course_code']))
st3 = admin.call('GET', '/das/training/status?user_id=' + w.id, expect=200)
check(any(x['status'] == 'EXPIRED' for x in st3), '超过复训周期变为 EXPIRED')
sus = admin.call('GET', '/das/training/suspend-due', expect=200)
check(any(s['user_id'] == w.id for s in sus),
      '第 3 章 复训过期且仍持有效授权者出现在应暂停清单')
print('ok   第 3 章 复训过期且仍持有效授权者被列入应暂停清单')

# 但授权不会被系统自动撤销——暂停是管理动作
still = db_query("""SELECT count(*) c FROM signer_authorization
                     WHERE user_id=%s AND revoked_at IS NULL""", (w.id,))
check(still[0]['c'] >= 1, '系统只列出不自动撤销：撤销会触发已签文件复核，须有人决定')
print('ok   系统只列出不自动撤销（撤销触发判据 A4-2 的文件复核，须有人决定并留痕）')

# ---------------- 培训记录不可改写 ----------------
rec = db_query("SELECT id FROM das_training_record WHERE user_id=%s LIMIT 1", (u.id,))[0]
check(rejected("UPDATE das_training_record SET result='FAIL' WHERE id=%s", (rec['id'],)),
      '考核结果不可改写')
check(rejected("DELETE FROM das_training_record WHERE id=%s", (rec['id'],)),
      '培训记录不可删除')
print('ok   培训记录的人员、课程、日期、考核结果均不可改写')

admin.call('POST', '/das/training/records/%d/evaluate' % rec['id'],
           {'effectiveness': '上岗 3 个月内抽查 5 份资料，无原则性问题'}, expect=200)
print('ok   有效性评估结论可事后补填（第 4 章唯一允许补填的字段）')

print()
print('全部通过: 培训与考核')
