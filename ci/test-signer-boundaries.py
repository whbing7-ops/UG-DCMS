"""签署授权的边界用例：生效日期未到、有效期已过、工号唯一。

关闭 UG-RPT-2026-003 第九之三节第 16 条——E 版复核指出撤销用例已有 5 条,
**但过期与生效日期边界用例确实缺失**。这个缺口是这么来的: 之前用静态搜索没搜到
撤销用例, 就断言"从未测试过", 后来发现搜错了; 反过来说, 搜到了的也不等于覆盖
全面。边界值要专门构造, 不会自己出现在正常路径里。

另覆盖判据 A1 的必要条件: 同一工号不得建第二个账号（迁移 0031）。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-signer-boundaries.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import (STAMP, check, db_execute, db_query, login_admin, make_user)  # noqa: E402

admin = login_admin()
rev = make_user(admin, 'ENGINEER', 'bd')
today = dt.date.today()

def rejected(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False

def in_force(auth_id):
    return db_query("""
        SELECT (revoked_at IS NULL AND valid_from <= current_date
                AND (valid_to IS NULL OR valid_to >= current_date)) AS f
          FROM signer_authorization WHERE id=%s""", (auth_id,))[0]['f']

# ---------------- 生效日期未到 ----------------
g = admin.call('POST', '/signers', {
    'user_id': rev.id, 'level': 'REVIEW',
    'valid_from': str(today + dt.timedelta(days=7)), 'note': '边界: 一周后生效'}, expect=201)
check(in_force(g['id']) is False, '生效日期未到的授权不在有效期内')
print('ok   valid_from 未到: 授权已登记但未生效')

# ---------------- 有效期已过 ----------------
g2 = admin.call('POST', '/signers', {
    'user_id': rev.id, 'level': 'APPROVE',
    'valid_from': str(today - dt.timedelta(days=30)),
    'valid_to': str(today - dt.timedelta(days=1)), 'note': '边界: 昨天到期'}, expect=201)
check(in_force(g2['id']) is False, '昨天到期的授权不在有效期内')
print('ok   valid_to 已过: 授权过期即失效, 不需要人工撤销')

# ---------------- 边界当天（CVE 级：先过资格评估闸门）----------------
# CVE 级授权要求有结论为"同意授权"的 UG-DAF-06（判据 E1、UG-DAP-03 步骤 3～5）。
# 三签须为三个不同自然人，故另造两人担任审核与批准。
qa_rev = make_user(admin, 'APPROVER', 'qr')
qa_apr = make_user(admin, 'APPROVER', 'qa')
for u_, t_ in ((qa_rev, 'QR'), (qa_apr, 'QA')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (t_ + STAMP, u_.id))
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s AND employee_no IS NULL",
           ('AD' + STAMP, admin.id))
admin.call('POST', '/das/qualifications', {
    'user_id': rev.id, 'sign_types': ['CVE核查'],
    'education_experience': '本科结构专业，8 年设计经验',
    'training_evidence': 'CCAR-21 与 DAS 培训已完成并考核合格',
    'indep_no_self_check': True, 'indep_no_ism_conflict': True,
    'conclusion': 'AGREE', 'reviewed_by': qa_rev.id, 'approved_by': qa_apr.id}, expect=201)
print('ok   CVE 级授权前已完成资格评估（UG-DAF-06）')

g3 = admin.call('POST', '/signers', {
    'user_id': rev.id, 'level': 'CVE',
    'valid_from': str(today), 'valid_to': str(today), 'note': '边界: 今天生效今天到期'}, expect=201)
check(in_force(g3['id']) is True, '起止日均为今天的授权在今天有效')
print('ok   边界当天: 起止日闭区间, 当天有效')

# ---------------- 起止日颠倒 ----------------
admin.call('POST', '/signers', {
    'user_id': rev.id, 'level': 'REVIEW',
    'valid_from': str(today), 'valid_to': str(today - dt.timedelta(days=1))}, expect=400)
print('ok   截止日早于起始日被拒(400)')

# ---------------- 过期后重新授权 ----------------
# 另造一人, 不复用 rev: rev 的 REVIEW 级上挂着一条"一周后生效"的授权, 那条没撤销、
# 没过期, 查重会把它算进去 —— 于是"过期后能重新授权"这条用例会被一个与过期无关的
# 原因拦下, 测的就不是它标题写的那件事了。边界用例要各自独立。
exp = make_user(admin, 'ENGINEER', 'ex')
old_g = admin.call('POST', '/signers', {
    'user_id': exp.id, 'level': 'REVIEW',
    'valid_from': str(today - dt.timedelta(days=30)),
    'valid_to': str(today - dt.timedelta(days=1)), 'note': '边界: 昨天到期'}, expect=201)
check(in_force(old_g['id']) is False, '前一条授权确已过期')
g4 = admin.call('POST', '/signers', {
    'user_id': exp.id, 'level': 'REVIEW',
    'valid_from': str(today), 'note': '过期后重新授权'}, expect=201)
check(in_force(g4['id']) is True, '重新授权后恢复有效')
check(in_force(old_g['id']) is False, '原过期授权不受影响, 各自独立计算')
print('ok   过期的授权不阻挡重新授权, 有效期各算各的')

# 反例: 未撤销且未过期的会阻挡 —— 包括生效日期还没到的那种。
# 这条和上面一条合起来才说明查重的边界在哪: 分界是"已过期", 不是"已生效"。
r = admin.call('POST', '/signers', {
    'user_id': rev.id, 'level': 'REVIEW', 'note': '应被查重拦下'}, expect=400)
check('尚未生效' in str(r) and str(today + dt.timedelta(days=7)) in str(r),
      '拒绝理由点明是哪一条在阻挡、以及它尚未生效')
print('ok   生效日期未到的授权同样阻挡重复授权, 且理由指到那一条')

# ---------------- 判据 A1 必要条件: 工号唯一 ----------------
emp = 'E' + STAMP
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (emp, rev.id))
check(rejected("""INSERT INTO app_user (username, full_name, employee_no, password_hash)
                  VALUES (%s, %s, %s, 'x')""", ('dup' + STAMP, '重号测试', emp)),
      'A1 同一工号不得建第二个账号')
print('ok   A1 工号唯一: 同号建第二个账号被拒')

gap = db_query("SELECT count(*) c FROM app_user_identity_gap WHERE id=%s", (rev.id,))
check(gap[0]['c'] == 0, 'A1 已填工号的账号不出现在缺口视图里')
print('ok   A1 缺口视图只列未填工号的账号')

# A1-2: 唯一约束拦不住同一个人取两个号——这是事实, 不是缺陷, 写成用例免得被误以为已管住
db_execute("""INSERT INTO app_user (username, full_name, employee_no, password_hash)
              VALUES (%s, %s, %s, 'x')""", ('alt' + STAMP, '重号测试', 'E9' + STAMP))
same_person = db_query("SELECT count(*) c FROM app_user WHERE full_name='重号测试'")
check(same_person[0]['c'] >= 1,
      'A1-2 同一自然人用两个不同工号建号, 数据库不会拒绝——这正是需要受控人员清单的原因')
print('ok   A1-2 同人两号数据库拦不住, 故 I3 的"不同自然人"仍未由系统实现')

# ---------------- 判据 CV1: 撤销前检查是否为他人备份 ----------------
# 名册动态增减时, 撤销甲会让乙的备份凭空落空——而乙的授权还在、名单上还写着。
bk = make_user(admin, 'ENGINEER', 'bk')
gb = admin.call('POST', '/signers', {'user_id': bk.id, 'level': 'REVIEW', 'note': 'CV1: 作备份人'},
                expect=201)
# 用 APPROVE 级构造"甲是乙的备份"这条链, 不用 REVIEW: rev 的 REVIEW 级上挂着那条
# 尚未生效的授权, 会先被查重拦下(上面已单独验过), 那样拦下的与 CV1 无关。
# rev 的 APPROVE 级只有一条昨天到期的, 查重不算它, 所以这里能发得出来。
gm = admin.call('POST', '/signers', {'user_id': rev.id, 'level': 'APPROVE',
                                     'backup_user_id': bk.id, 'note': 'CV1: 以 bk 为备份'}, expect=201)
gb2 = admin.call('POST', '/signers', {'user_id': bk.id, 'level': 'APPROVE', 'note': 'CV1: 备份人本人的授权'},
                 expect=201)

r = admin.call('POST', f'/signers/{gb2["id"]}/revoke', {'reason': 'CV1 测试'}, expect=400)
check('备份' in str(r), 'CV1 撤销被拒, 且说明了是谁会失去备份')
print('ok   CV1 撤销"他人的备份人"被拒, 并列出受影响的人')

admin.call('POST', f'/signers/{gm["id"]}/revoke', {'reason': '先撤销依赖方'}, expect=200)
admin.call('POST', f'/signers/{gb2["id"]}/revoke', {'reason': 'CV1 测试'}, expect=200)
print('ok   CV1 依赖解除后可正常撤销')

# ---------------- 判据 CV2: 备份完整性可见 ----------------
st = admin.call('GET', '/signers/backup-status', expect=200)
check(isinstance(st, list), 'CV2 备份完整性可查')
no_backup = [x for x in st if x['gap'] == '未指定备份人']
# 不能写 `or True`: 那让这条断言恒真, 等于没断言。bk 的 REVIEW 级授权未指定备份人
# 且未撤销, 按 backup_status 的 WHERE 必然在列, 所以这里本来就该硬断言。
check(any(x['username'] == bk.username for x in no_backup),
      'CV2 未指定备份人的条目被标出')
print('ok   CV2 备份完整性一眼可见（共 %d 项, 其中无备份 %d 项）' % (len(st), len(no_backup)))

print()
print('全部通过: 签署授权边界、工号唯一性与备份完整性')
