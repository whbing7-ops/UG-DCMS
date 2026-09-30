"""资格评估（UG-DAF-06）与登记册月度核对（M1 最后两块）。

按判据 I-总 写成反例为主：

  E1    没有"同意授权"的资格评估，不得授 CVE 级
  I3    评估表的编制／审核／批准须为三个不同**自然人**——用工号判，不是账号
  A1    没填工号的账号不能担任评估表的签署人
  步骤4 同意授权而独立性两项未勾，等于跳过独立性核对
  N13   每月核对系统登记册与《授权人员名单》；漏做的月份可见
  N12   核对的第二人复核，复核人不得是核对人本人

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-qualification.py <credentials.json>
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


def mk(tag, role='ENGINEER', emp=True):
    u = make_user(admin, role, tag)
    if emp:
        db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag.upper() + STAMP, u.id))
    return u


db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s AND (employee_no IS NULL OR employee_no='')",
           ('ADM' + STAMP, admin.id))
subject = mk('qs')
rv = mk('qv', 'APPROVER')
ap = mk('qp', 'APPROVER')
noemp = mk('qn', 'APPROVER', emp=False)

BASE = {'user_id': subject.id, 'sign_types': ['CVE核查'],
        'education_experience': '本科结构专业，8 年设计经验',
        'training_evidence': 'CCAR-21 与 DAS 培训已完成并考核合格',
        'indep_no_self_check': True, 'indep_no_ism_conflict': True,
        'conclusion': 'AGREE'}

# ---------------- 判据 E1: 没有资格评估不得授 CVE ----------------
r = admin.call('POST', '/signers', {'user_id': subject.id, 'level': 'CVE'}, expect=400)
check('资格评估' in str(r) and 'UG-DAP-03' in str(r), 'E1 拒绝理由指到依据')
print('ok   E1 无资格评估不得授 CVE 级（400，理由带依据）')

# 但 REVIEW/APPROVE 不受此闸门限制——范围问题，见 signers.grant 的注释
admin.call('POST', '/signers', {'user_id': subject.id, 'level': 'REVIEW'}, expect=201)
print('ok   REVIEW 级不受资格评估闸门限制（UG-DAF-06 管的是适航签署事项，不是文档签署级别）')

# ---------------- 步骤 4: 同意授权而独立性未勾 ----------------
admin.call('POST', '/das/qualifications',
           {**BASE, 'indep_no_self_check': False,
            'reviewed_by': rv.id, 'approved_by': ap.id}, expect=400)
print('ok   步骤4 同意授权而独立性两项未全勾被拒')

# 以下三条的规则在数据库触发器里(0034), 违规经全局处理器归为 409 RULE_VIOLATION,
# 不是 400 —— 400 只给服务层的 ValueError。断言到具体规则号, 否则三条互相顶替也全绿。


def violates(rule, body, note):
    r = admin.call('POST', '/das/qualifications', body, expect=409)
    got = ((r or {}).get('error') or {}).get('rule')
    check(got == rule, '%s %s（实际报出 %s）' % (rule, note, got))
    return r


# ---------------- 判据 A1: 签署人须有工号 ----------------
violates('DCMS-INV-035', {**BASE, 'reviewed_by': noemp.id, 'approved_by': ap.id},
         '未填工号者不得担任评估表签署人')
print('ok   A1/035 未填工号者不得担任评估表签署人')

# ---------------- 判据 I3: 三签须三个不同自然人 ----------------
violates('DCMS-INV-036', {**BASE, 'reviewed_by': rv.id, 'approved_by': rv.id},
         '审核与批准为同一人不成立三签')
print('ok   I3/036 审核与批准为同一人被拒')

violates('DCMS-INV-037', {**BASE, 'reviewed_by': subject.id, 'approved_by': ap.id},
         '被评估人不得签本人的评估表')
print('ok   037 被评估人不得担任本人评估表的签署人')

# 同一自然人两个账号：工号相同 → 仍应被判为同一人
alt = mk('qx', 'APPROVER', emp=False)
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", ('QV' + STAMP, alt.id))
check(rejected("""INSERT INTO das_qualification_assessment
        (user_id, sign_types, education_experience, training_evidence,
         indep_no_self_check, indep_no_ism_conflict, conclusion,
         prepared_by, reviewed_by, approved_by)
        VALUES (%s, ARRAY['CVE核查'], 'x', 'y', true, true, 'AGREE', %s, %s, %s)""",
                (subject.id, admin.id, rv.id, alt.id)),
      'I3 两个账号同工号被判为同一自然人，三签不成立')
print('ok   I3 同一自然人开两个账号仍被判为同一人——工号判，不是账号判')

# ---------------- 正常路径 ----------------
qa = admin.call('POST', '/das/qualifications',
                {**BASE, 'reviewed_by': rv.id, 'approved_by': ap.id,
                 'form_ref': 'DAF06-' + STAMP}, expect=201)
print('ok   资格评估登记成功')

cur = admin.call('GET', '/das/qualifications?user_id=' + subject.id, expect=200)
check(cur and cur[0]['in_force'], '评估当前有效')
admin.call('POST', '/signers', {'user_id': subject.id, 'level': 'CVE'}, expect=201)
print('ok   E1 资格评估通过后可授 CVE 级')

# ---------------- 评估记录不可改写 ----------------
check(rejected("UPDATE das_qualification_assessment SET conclusion='REJECT' WHERE id=%s",
               (qa['id'],)), '评估结论不可改写')
check(rejected("DELETE FROM das_qualification_assessment WHERE id=%s", (qa['id'],)),
      '评估记录不可删除')
print('ok   评估记录不可改写不可删除，改判请另评一次')

# ---------------- 判据 N13: 月度核对 ----------------
snap = admin.call('GET', '/das/register/snapshot', expect=200)
check(snap['count'] >= 1 and 'rows' in snap, 'N13 系统侧快照可取')
print('ok   N13 系统侧快照可取（%d 条有效授权）——另一侧是受控 Word 文档，系统读不到'
      % snap['count'])

admin.call('POST', '/das/register/reconciliation',
           {'roster_ref': '', 'differences': 0}, expect=422)
print('ok   N13 不写名单版次被拒')
admin.call('POST', '/das/register/reconciliation',
           {'roster_ref': 'UG-DAM-01-附2 版次 00', 'differences': 2}, expect=400)
print('ok   N13 有差异却不写差异内容与纠正动作被拒——只记数字等于没核对')

rec = admin.call('POST', '/das/register/reconciliation',
                 {'roster_ref': 'UG-DAM-01-附2 版次 00', 'differences': 0}, expect=201)
# 不写成 `== snap['count'] or >= 1`: 加了那个 or 之后条数对不上也照样通过,
# 而"核对记录里的条数必须等于当时的系统侧条数"正是这条记录唯一的实质内容。
check(rec['system_count'] == snap['count'],
      'N13 核对记录的条数等于快照条数（快照 %s, 记录 %s）' % (snap['count'], rec['system_count']))
print('ok   N13 本月核对记录登记成功')

admin.call('POST', '/das/register/reconciliation',
           {'roster_ref': 'UG-DAM-01-附2 版次 00', 'differences': 0}, expect=400)
print('ok   N13 同月重复核对被拒')

# 只核对当月时, das_reconciliation_due 的月份区间从最早一条记录起算, 区间就只有
# 当月一个月, done 恒为真 —— 此时断言"漏做的月份可见"是空的(原来写成
# `or len(due) == 1`, 永远走后一半)。要真验它, 得先造出一段有空档的历史:
# 补一条三个月前的核对, 区间随之拉长, 中间两个月就是真实的漏做。
back = (today.replace(day=1) - dt.timedelta(days=75)).replace(day=1)
admin.call('POST', '/das/register/reconciliation',
           {'period_month': str(back), 'roster_ref': 'UG-DAM-01-附2 版次 00',
            'differences': 0}, expect=201)

due = admin.call('GET', '/das/register/reconciliation', expect=200)
cur_m = [d for d in due if d['period_month'] == str(today.replace(day=1))]
check(cur_m and cur_m[0]['done'], 'N13 本月显示已核对')
check(len(due) >= 3, 'N13 月份区间已从最早一条记录拉长（实际 %d 个月）' % len(due))
missing = [d['period_month'] for d in due if not d['done']]
check(missing, 'N13 中间未核对的月份被列为漏做')
check(str(back) not in missing and str(today.replace(day=1)) not in missing,
      'N13 已核对的两个月不在漏做之列')
print('ok   N13 到期视图区间 %d 个月，其中 %d 个月漏做：%s'
      % (len(due), len(missing), '、'.join(missing)))

# ---------------- 判据 N12: 第二人复核 ----------------
check(rejected("UPDATE das_register_reconciliation SET reviewed_by=%s WHERE id=%s",
               (admin.id, rec['id'])),
      'N12 复核人不得是核对人本人')
print('ok   N12 核对人自己复核自己被数据库拒绝')

check(rejected("UPDATE das_register_reconciliation SET differences=9 WHERE id=%s", (rec['id'],)),
      '核对记录的差异数不可改写')
print('ok   核对记录的月份、核对人、快照、差异数均不可改写')

print()
print('全部通过: 资格评估与登记册月度核对')
