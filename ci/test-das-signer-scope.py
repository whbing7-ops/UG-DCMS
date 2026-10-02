"""签署授权的产品／型号／件号范围（判据 I10.PRODUCT_SCOPE）。

按判据 I-总 以反例为主。这块已经做错过一次：PR #5 给授权加了自由文本 `product_scope`，
而 `is_authorized()` 从不读它——手册 3.2 要求强制执行的一条控制在系统里成了装饰，还会
出现在授权名单上让审查者以为系统在管。所以这个用例的第一要务是证明**范围真的被读了**，
第二要务是证明**读不到的地方如实承认读不到**。

覆盖：
  I10.PRODUCT_SCOPE 范围是结构化引用、可比对；覆盖判断**三值**，NULL ≠ 放行
  100  限定在某类项目上、范围条目却写别的批准类型 → 自相矛盾的登记被拒
  101  已有范围条目不得再声明"由纸面把关"（反之亦然）
  102  范围条目与项目限定不得删改（判据 N14：授权只可撤销不可删除）
  103  符合性声明签署时：白名单外的型号被拒；受托授权签自家 STC 项目被拒
  ck_dss_one／match  一行恰好一种粒度，粒度与引用须对应
  ck_dss_limit  限制栏为空须显式声明"无限制"——空格分不出"无限制"与"忘了抄"
  I9   不得给自己的授权登记范围（能给自己加范围就等于能给自己扩权）
  如实状态  未施加范围校验的签署路径逐条列出；没校验范围签出的声明数得出来

【断言写成关系式】
授权与范围都是只追加的（撤销不删除、范围不得删改），同一个库跑第二遍条数必然不同。
所以不写绝对条数，只断言状态机与覆盖判断本身。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-signer-scope.py <credentials.json>
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
    used = {r['project_no'] for r in db_query(
        "SELECT project_no FROM das_project WHERE project_no LIKE %s", (prefix + '%',))}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        for seq in range(1, 100):
            no = '%s%02d%s%s' % (prefix, seq, letter, YY)
            if no not in used:
                return no
    raise AssertionError('项目编号用尽: ' + prefix)


def release(*users):
    """把用完的账号的会话撤掉。

    并发账号数有许可上限（默认 10）。本用例要造的角色不少: 签署人两名、资格评估的
    审核与批准两名、三个岗位任命、再加受托与无范围各一名 —— 一路攒下来就超限,
    而超限的表现是下一次 make_user 登录失败报 KeyError: 'access_token',
    看上去像是认证坏了, 和会话数没有半点关系。
    """
    for u in users:
        db_execute("UPDATE user_session SET revoked_at = now() "
                   "WHERE user_id = %s AND revoked_at IS NULL", (u.id,))


def reason(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error as e:
        return str(e)
    return ''


# ================= 如实状态先摆出来 =================
une = admin.call('GET', '/das/signer-scope/unenforced', expect=200)
check(len(une) >= 3, '未施加范围校验的签署路径逐条列出（%d 条）' % len(une))
check(all(u['why_unenforced'] for u in une), '每一条都写明为什么没施加')
check(any('不传产品标的' in u['why_unenforced'] for u in une),
      '文件三级签署的原因是调用方传不出标的, 不是"漏了"')
# 【反方向的漂移也要防】
# 从系统目录反查只拦得住"新增了强制点却没改清单"。反方向——**新增了一条没有强制点的
# 签署路径**——目录查不出来, 只能靠每加一条就登记。M4 的分类签署是第一个这样的例子:
# UG-DAP-06 第 4 步要求"仅在许可权利范围内"批准分类, 而更改申请的涉及产品是自由文本。
check(any('UG-DAF-02' in u['path'] or 'M4' in u['path'] for u in une),
      '设计更改分类（M4）也登进了未施加清单 —— 它同样要求"仅在许可权利范围内"签署')
# 【这张清单是手写的, 所以要有一道防漂移的闸门】
# das_signer_scope_unenforced 是 VALUES 列表, 和设计输入第 5.1 节那张表同一个毛病:
# 手写的表必然随代码漂移, 而漂移方向总是偏向"看上去是满的"——哪天有人把范围校验接进
# 文件三级签署, 这张清单不会知道, 自评就会继续照抄"仅符合性声明一处"。
# 所以从系统目录反查: 谁在调 das_signer_scope_covers。调用方变了这条断言就会失败,
# 迫使那张清单一起改。
callers = {r['p'] for r in db_query(
    """SELECT proname AS p FROM pg_proc
        WHERE prosrc LIKE '%%das_signer_scope_covers%%'
          AND proname <> 'das_signer_scope_covers'""")}
check(callers == {'dcms_check_statement_scope'},
      '调用范围校验的只有符合性声明那一处（实际: %s）——这条断言一旦失败, '
      '说明新增了强制点, das_signer_scope_unenforced 必须同步改'
      % ('、'.join(sorted(callers)) or '无'))
print('ok   防漂移: 从系统目录反查强制点, 手写清单与实际一致')
print('ok   判据 I10.PRODUCT_SCOPE 的如实状态: 只有符合性声明一条路在校验')
for u in une:
    print('       · %-34s %s' % (u['path'][:34], u['why_unenforced'][:34]))

reg = admin.call('GET', '/das/independence/matrix?verdict=PARTIAL', expect=200)
ps = [r for r in admin.call('GET', '/das/independence/matrix', expect=200)
      if r['code'] == 'I10.PRODUCT_SCOPE'][0]
check(ps['source_state'] == 'PARTIAL',
      '登记册里 I10.PRODUCT_SCOPE 是**部分实现**而不是已实现')
check('只有符合性声明' in (ps['manual_control'] or '')
      or '符合性声明' in (ps['manual_control'] or ''),
      '人工控制写明了哪一处在校验、哪几处仍靠纸面')
check('装饰' in (ps['gap_note'] or '') or 'product_scope' in (ps['gap_note'] or ''),
      '缺口说明记着 PR #5 那个装饰字段的教训')
chg = db_query("""SELECT old_source_state, new_source_state
                    FROM das_independence_rule_change
                   WHERE rule_code='I10.PRODUCT_SCOPE'
                   ORDER BY changed_at LIMIT 1""")
check(chg and chg[0]['old_source_state'] == 'ABSENT'
      and chg[0]['new_source_state'] == 'PARTIAL',
      '状态由未实现改为部分实现, 并自动留痕')
print('ok   登记册: ABSENT → PARTIAL, 留痕在案, 措辞说得出在哪一处施加')

# ================= 造人、授权、项目 =================
cm = make_user(admin, 'CONFIGURATION_MANAGER', 'scm')
signer = make_user(admin, 'ENGINEER', 'ssig')
other = make_user(admin, 'ENGINEER', 'soth')
for u, tag in ((cm, 'SCM'), (signer, 'SSIG'), (other, 'SOTH')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))

# CVE 级授权要先过资格评估闸门（判据 E1、UG-DAP-03 步骤 3～5）: 须有结论为"同意授权"
# 的 UG-DAF-06, 且编审批三人不同。本用例测的是范围, 不是资格, 所以这里只是把闸门过掉。
qa_rev = make_user(admin, 'APPROVER', 'sqr')
qa_apr = make_user(admin, 'APPROVER', 'sqa')
for u_, t_ in ((qa_rev, 'SQR'), (qa_apr, 'SQA')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (t_ + STAMP, u_.id))
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s AND employee_no IS NULL",
           ('SAD' + STAMP, admin.id))


def qualify(u, what='CVE核查'):
    admin.call('POST', '/das/qualifications', {
        'user_id': u.id, 'sign_types': [what],
        'education_experience': '本科结构专业，8 年设计经验',
        'training_evidence': 'CCAR-21 与 DAS 培训已完成并考核合格',
        'indep_no_self_check': True, 'indep_no_ism_conflict': True,
        'conclusion': 'AGREE', 'reviewed_by': qa_rev.id, 'approved_by': qa_apr.id},
        expect=201)


def grant_cve(u):
    qualify(u)
    return admin.call('POST', '/signers', {'user_id': u.id, 'level': 'CVE'}, expect=201)


a1 = grant_cve(signer)
a2 = grant_cve(other)
AUTH1, AUTH2 = a1['id'], a2['id']

dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'sdcm')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'sam')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'sawm')
for u, tag in ((dcm, 'SDCM'), (am, 'SAM'), (awm, 'SAWM')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, pos in ((dcm, 'DCM'), (am, 'AM'), (awm, 'AWM')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': pos, 'kind': 'FORMAL'}, expect=201)
STC_NO, SUP_NO = free_no('UG-STC'), free_no('UG-SUP')
dcm.call('POST', '/das/projects',
         {'project_no': STC_NO, 'type_code': 'STC', 'name_cn': '范围用例 STC ' + STAMP,
          'aircraft_type': 'B737-800'}, expect=201)
dcm.call('POST', '/das/projects',
         {'project_no': SUP_NO, 'type_code': 'SUP', 'name_cn': '范围用例受托 ' + STAMP,
          'aircraft_type': 'A320'}, expect=201)
for no in (STC_NO, SUP_NO):
    am.call('POST', '/das/projects/%s/approval' % no, {'approval_ref': '立项 ' + STAMP},
            expect=201)
print('ok   两条 CVE 授权、两个项目（STC/B737-800 与受托/A320）已就位')

# ================= UNDECLARED 这一档 =================
r = admin.call('GET', '/das/signer-scope/%s' % AUTH1, expect=200)
check(not r['scope'] and not r['waiver'], '新授权既无范围条目也无声明')
und = {x['auth_id'] for x in admin.call('GET', '/das/signer-scope/undeclared', expect=200)}
check(AUTH1 in und and AUTH2 in und,
      'UNDECLARED 清单列出它们 —— 这一档按手册 3.2 什么都不该能签, 必须清零')
print('ok   既无范围也无声明的授权单列一档, 不是静默放行')

# ================= 反例：一行两种粒度、粒度与引用对不上 =================
base = {'approval_type_code': 'STC', 'source_ref': 'UG-DAM-01-附2 第 1 项',
        'no_limitation_declared': True}
r = admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
               dict(base, scope_kind='AIRCRAFT_TYPE', aircraft_type='B737-800',
                    product_category='座椅'), expect=400)
check('一行两义' in str(r), '一行同时挂两种粒度被拒, 并说明后果')
r = admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
               dict(base, scope_kind='PART_NUMBER', aircraft_type='B737-800'),
               expect=400)
check('件号' in str(r), '粒度写件号却给型号被拒')
r = admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
               dict(base, scope_kind='NOPE', aircraft_type='B737-800'), expect=400)
check('粒度' in str(r), '未知粒度被拒')
r = admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
               {'approval_type_code': 'NOPE', 'scope_kind': 'AIRCRAFT_TYPE',
                'aircraft_type': 'B737-800', 'source_ref': 'x',
                'no_limitation_declared': True}, expect=404)
check('批准类型' in str(r), '批准类型引用 das_project_type, 不是自由文本')
print('ok   范围是结构化引用: 粒度恰好一种、引用须对应、批准类型须存在')

# ================= 反例：限制栏空着又不声明 =================
r = admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
               {'approval_type_code': 'STC', 'scope_kind': 'AIRCRAFT_TYPE',
                'aircraft_type': 'B737-800', 'source_ref': 'x'}, expect=400)
check('忘了抄' in str(r), '限制栏空着又不声明无限制被拒, 并说明为什么空格不行')
r = admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
               dict(base, scope_kind='AIRCRAFT_TYPE', aircraft_type='B737-800',
                    limitation='仅客舱内饰'), expect=400)
check('二者取一' in str(r), '既声明无限制又写限制内容被拒')
r = admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
               {'approval_type_code': 'STC', 'scope_kind': 'AIRCRAFT_TYPE',
                'aircraft_type': 'B737-800', 'source_ref': '  ',
                'no_limitation_declared': True}, expect=400)
check('纸面依据' in str(r), '不写纸面授权书条目被拒: 系统里的范围要能回到纸面')
print('ok   限制栏为空必须是一句明话; 范围条目必须回得到纸面授权书')

# ================= 反例 I9：给自己的授权加范围 =================
a3 = grant_cve(cm)
r = cm.call('POST', '/das/signer-scope/%s/entries' % a3['id'],
            dict(base, scope_kind='AIRCRAFT_TYPE', aircraft_type='B737-800'),
            expect=400)
check('扩权' in str(r) and 'I9' in str(r),
      '不得给自己的授权登记范围 —— 能给自己加范围就等于能给自己扩权')
print('ok   I9 的同形问题: 自己给自己的授权加范围被拒')

# ================= 登记范围，覆盖判断三值 =================
admin.call('POST', '/das/signer-scope/%s/entries' % AUTH1,
           {'approval_type_code': 'STC', 'scope_kind': 'AIRCRAFT_TYPE',
            'aircraft_type': 'B737-800', 'source_ref': 'UG-DAM-01-附2 第 1 项 ' + STAMP,
            'limitation': '仅客舱内饰改装, 不含结构主承力件'}, expect=201)
reg1 = [x for x in admin.call('GET', '/das/signer-scope', expect=200)
        if x['auth_id'] == AUTH1][0]
check(reg1['scope_state'] == 'SYSTEM_ENFORCED', '登记范围后状态为系统管范围')
check(AUTH1 not in {x['auth_id'] for x in
                    admin.call('GET', '/das/signer-scope/undeclared', expect=200)},
      '不再出现在 UNDECLARED 清单里')

q = '/das/signer-scope/%s/covers' % AUTH1
check(admin.call('GET', q + '?approval_type_code=STC&aircraft_type=B737-800',
                 expect=200)['covered'] is True, '白名单内 → true')
r = admin.call('GET', q + '?approval_type_code=STC&aircraft_type=A320', expect=200)
check(r['covered'] is False and '不得签署' in r['meaning'],
      '白名单外 → false, 并指到手册 3.2')
check(admin.call('GET', q + '?approval_type_code=PMA&aircraft_type=B737-800',
                 expect=200)['covered'] is False, '批准类型不符 → false')
r = admin.call('GET', q + '?approval_type_code=STC', expect=200)
check(r['covered'] is None and '判不了' in r['meaning'],
      '**不给标的 → NULL（判不了），不是放行**')
r = admin.call('GET', '/das/signer-scope/%s/covers?aircraft_type=B737-800' % AUTH2,
               expect=200)
check(r['covered'] is None, '没有范围条目的授权 → NULL（判不了）')
print('ok   覆盖判断三值: true / false（白名单外）/ NULL（判不了, 不等于放行）')

# ================= 反例 101：范围与声明不得并存 =================
r = admin.call('POST', '/das/signer-scope/%s/waiver' % AUTH1,
               {'paper_ref': '纸面授权书 2026-01', 'reason': '存量授权'}, expect=400)
check('必有一个是假的' in str(r), '已有范围条目不得再声明由纸面把关')
msg = reason("""INSERT INTO das_signer_scope_waiver
                    (auth_id, paper_ref, reason, declared_by)
                SELECT %s, 'X', 'Y', id FROM app_user WHERE username=%s""",
             (AUTH1, admin.username))
check('DCMS-INV-101' in msg, '绕过服务层直写数据库同样被拦（101）')
admin.call('POST', '/das/signer-scope/%s/waiver' % AUTH2,
           {'paper_ref': '纸面授权书 ' + STAMP,
            'reason': '存量授权, 范围暂由纸面把关'}, expect=201)
po = {x['auth_id'] for x in admin.call('GET', '/das/signer-scope/paper-only', expect=200)}
check(AUTH2 in po, '声明进 paper-only 清单交独立监督核对那份授权书')
msg = reason("""INSERT INTO das_signer_scope
                    (auth_id, approval_type_code, scope_kind, aircraft_type,
                     source_ref, no_limitation_declared)
                VALUES (%s, 'STC', 'AIRCRAFT_TYPE', 'B737-800', 'x', true)""", (AUTH2,))
check('DCMS-INV-100' in msg, '已声明由纸面把关的授权不得再登记范围条目（反向同一条）')
print('ok   101 范围条目与"由纸面把关"的声明互斥, 两个方向都拦')

# ================= 反例 100：限定与范围自相矛盾 =================
admin.call('POST', '/das/signer-scope/%s/project-binds' % AUTH1,
           {'project_no': SUP_NO, 'reason': '受托项目专用 ' + STAMP}, expect=409)
# 上面这条必失败: AUTH1 已有 STC 范围条目, 限定到受托项目后两者矛盾。
# 换一条干净的授权来验限定本身。
a4 = admin.call('POST', '/signers', {'user_id': signer.id, 'level': 'REVIEW'},
                expect=201)
admin.call('POST', '/das/signer-scope/%s/project-binds' % a4['id'],
           {'project_no': SUP_NO, 'reason': '受托项目专用 ' + STAMP}, expect=201)
r = admin.call('POST', '/das/signer-scope/%s/entries' % a4['id'],
               {'approval_type_code': 'STC', 'scope_kind': 'AIRCRAFT_TYPE',
                'aircraft_type': 'B737-800', 'source_ref': 'x',
                'no_limitation_declared': True}, expect=409)
check('自相矛盾' in str(r) and '授权名单' in str(r),
      '限定在受托项目、范围却写 STC 被拒, 并说明它会出现在授权名单上')
admin.call('POST', '/das/signer-scope/%s/entries' % a4['id'],
           {'approval_type_code': 'SUP', 'scope_kind': 'AIRCRAFT_TYPE',
            'aircraft_type': 'A320', 'source_ref': '受托技术协议 ' + STAMP,
            'no_limitation_declared': True}, expect=201)
print('ok   100 项目限定与范围批准类型须相容（按数组成员判, 不按子串）')

qa4 = '/das/signer-scope/%s/covers' % a4['id']
check(admin.call('GET', qa4 + '?approval_type_code=SUP&aircraft_type=A320',
                 expect=200)['covered'] is True, '受托授权覆盖 A320')
print('ok   关联维度与范围维度各自独立: 限定了项目不等于范围相符')

# ================= 反例 102：范围不得删改 =================
sid = db_query("SELECT id FROM das_signer_scope WHERE auth_id=%s LIMIT 1",
               (AUTH1,))[0]['id']
check('DCMS-INV-102' in reason(
    "UPDATE das_signer_scope SET aircraft_type='A320' WHERE id=%s", (sid,)),
    '102 范围条目不得改写 —— 能改就能悄悄把窄授权改宽')
check('DCMS-INV-102' in reason("DELETE FROM das_signer_scope WHERE id=%s", (sid,)),
      '102 范围条目不得删除')
bid = db_query("SELECT id FROM das_signer_project_bind WHERE auth_id=%s LIMIT 1",
               (a4['id'],))[0]['id']
check('DCMS-INV-102' in reason(
    "DELETE FROM das_signer_project_bind WHERE id=%s", (bid,)), '102 项目限定不得删除')
print('ok   102 范围是授权内容的一部分: 要改先撤销原授权再重新授权（判据 N14）')

# ================= 反例 103：符合性声明的范围校验 =================
# 让 signer 成为非在任责任经理的签署人, 写明授权依据后签 —— 范围校验是唯一的变量。
stmt = {'completion_confirm_ref': 'UG-DAF-17-' + STAMP[-4:],
        'verification_docs_ref': 'AR-01 R2',
        'signed_under_authority_ref': 'UG-DAM-01-附2 授权书 ' + STAMP}
SUP2 = free_no('UG-SUP')
dcm.call('POST', '/das/projects',
         {'project_no': SUP2, 'type_code': 'SUP', 'name_cn': '另一受托 ' + STAMP,
          'aircraft_type': 'A320'}, expect=201)
am.call('POST', '/das/projects/%s/approval' % SUP2, {'approval_ref': '立项'}, expect=201)
# AUTH1 的范围是 STC/B737-800。拿它去签 A320 的项目 → 白名单外。
# 但受托项目本来就不能签声明（094 先拦）, 所以要用一个 STC 项目、型号不在白名单内。
STC2 = free_no('UG-STC')
dcm.call('POST', '/das/projects',
         {'project_no': STC2, 'type_code': 'STC', 'name_cn': '型号不在白名单 ' + STAMP,
          'aircraft_type': 'A350'}, expect=201)
am.call('POST', '/das/projects/%s/approval' % STC2, {'approval_ref': '立项'}, expect=201)
r = signer.call('POST', '/das/projects/%s/statements' % STC2,
                dict(stmt, statement_no='UG-DOA-SM-2026-A' + STAMP[-4:]), expect=409)
check('未列入的产品类别不得签署' in str(r),
      '**白名单外的型号被拒**, 理由指到手册 3.2 —— 范围真的被读了')
check('A350' in str(r), '报错点明是哪个型号不在范围内')
check('任何一条' in str(r),
      '措辞是"任何一条授权的范围内"——该签署人同时持两条授权, 逐条判过才敢这么说')
check('其它项目' not in str(r),
      '另一条授权限定在受托项目上并不该盖住真正的原因（型号不在范围内）')
# 白名单内的型号可签
ok1 = signer.call('POST', '/das/projects/%s/statements' % STC_NO,
                  dict(stmt, statement_no='UG-DOA-SM-2026-B' + STAMP[-4:]), expect=201)
row = db_query("SELECT scope_checked FROM das_compliance_statement WHERE id=%s",
               (ok1['id'],))[0]
check(row['scope_checked'] is True, '白名单内签成, 并记下"本次已校验范围"')
print('ok   103 符合性声明处范围真的在校验: 白名单外被拒, 白名单内记为已校验')

# 受托授权签自家 STC 项目 → 项目限定拦下
sig2 = make_user(admin, 'ENGINEER', 'ssig2')
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", ('SSIG2' + STAMP, sig2.id))
a5 = grant_cve(sig2)
admin.call('POST', '/das/signer-scope/%s/project-binds' % a5['id'],
           {'project_no': SUP_NO, 'reason': '受托专用 ' + STAMP}, expect=201)
admin.call('POST', '/das/signer-scope/%s/entries' % a5['id'],
           {'approval_type_code': 'SUP', 'scope_kind': 'AIRCRAFT_TYPE',
            'aircraft_type': 'A320', 'source_ref': '技术协议 ' + STAMP,
            'no_limitation_declared': True}, expect=201)
r = sig2.call('POST', '/das/projects/%s/statements' % STC_NO,
              dict(stmt, statement_no='UG-DOA-SM-2026-C' + STAMP[-4:]), expect=409)
check('受托授权只限该受托项目' in str(r),
      '**受托授权不得顺带签自家 STC 的资料**（设计输入第九之二节的关联约束）')
print('ok   103 关联约束落地: 受托授权签自家项目被拒')

# 没有范围条目的签署人 → 判不了, 放行但记入缺口清单
# 先把用完的账号的会话撤掉, 否则并发会话数超上限, 下一次登录会失败。
release(cm, qa_rev, qa_apr, other)
sig3 = make_user(admin, 'ENGINEER', 'ssig3')
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", ('SSIG3' + STAMP, sig3.id))
grant_cve(sig3)
ok2 = sig3.call('POST', '/das/projects/%s/statements' % STC_NO,
                dict(stmt, statement_no='UG-DOA-SM-2026-D' + STAMP[-4:]), expect=201)
row = db_query("SELECT scope_checked FROM das_compliance_statement WHERE id=%s",
               (ok2['id'],))[0]
check(row['scope_checked'] is None, '判不了时记为 NULL, 不记成"已校验"')
gap = {g['id'] for g in admin.call('GET', '/das/signer-scope/statement-gap', expect=200)}
check(ok2['id'] in gap and ok1['id'] not in gap,
      '没校验范围就签出去的声明进缺口清单, 已校验的不进 —— 自评要数得出这个数')
print('ok   判不了时放行但看得见: 自评数得出有多少份声明没校验过范围')

print('\nPASS 签署授权的产品范围: 范围是结构化引用且真的被读、覆盖判断三值且 NULL 不放行、'
      '100 限定与范围相容、101 与声明互斥、102 不得删改、103 白名单外与越项目被拒、'
      'I10.PRODUCT_SCOPE 如实记为部分实现')
