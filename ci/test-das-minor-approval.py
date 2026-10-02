"""M5 设计小改批准（第二批，UG-DAP-08／AP-21-18 表-21-174）。

按判据 I-总 以反例为主。这块有两件事特别容易漏：

**一是前提会失效。** 小改批准的前提是「已有签署的分类表且为小改」，而分类结论会变——
UG-DAP-06 的「局方对分类有不同意见时以局方意见为准」。一变，建立在它上面的批准就失去了
前提，**而资料已经发出去了**：这不是历史问题，是现在外面有一份带着不成立批准的资料。
只在插入时校验一次是不够的。

**二是声明必须是原文。** 第 7 章：批准声明必须使用局方规定的原文。改一个字，这段声明的
法律效力就说不清了。而它带着占位符【设计机构许可证编号】，本单位的 DOA 尚在申请中——
所以须附声明的资料暂时发不出去，这是如实状态，不是缺陷。

覆盖：
  109  三个条件没核全、或核了不满足，不得发布（UG-DAP-08 第 1 步：任一不满足不得批准）
  110  必须指向一条已签署且为小改的分类结论
  111  批准人非在任适航管理负责人时须写明纸面授权依据（5 类适航签署事项之一）
  112  须附声明的必须附且逐字相符；不使用声明的不得附；占位符未替换不得发布
  113  批准、条件核对、发布记录不得删改
  114  受控声明原文不得删除；改动留旧版（已发出去的资料印的是旧版那段话）
  PZ   编号后缀分得出这是哪一类批准（FL 分类／PZ 小改／SM 符合性声明）
  失效 分类结论后来变了的批准持续列出，交 M7 按 UG-DAP-14 处理

【每个反例只能因为它自己那条规则失败】
这一组写的时候踩过两次：测「编号没有 PZ 后缀」和「限制条件空着」时没写授权依据，
结果先被 111 拦下，待测的约束根本没跑到，而断言照样通过。所以下面每个反例都先把别的
前置条件补齐。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-minor-approval.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
TODAY = dt.date.today()
CHG = 'CHG-M5-%s' % STAMP
CHG2 = 'CHG-M5B-%s' % STAMP
PZ1 = 'UG-DOA-B737-2026-%s-PZ' % STAMP[-4:]
PZ2 = 'UG-DOA-B737-2026-%sB-PZ' % STAMP[-4:]
OFFICIAL = ('本资料由经CAAC批准的DOA-2026-001号设计机构许可证持有人'
            '按照设计机构许可项目单明确的权利范围进行批准。')


def release_sessions(*users):
    for u in users:
        db_execute("UPDATE user_session SET revoked_at = now() "
                   "WHERE user_id = %s AND revoked_at IS NULL", (u.id,))


def reason(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error as e:
        return str(e)
    return ''


# ================= 受控的批准声明与资料种类 =================
st = admin.call('GET', '/das/minor-approvals/statement', expect=200)
cur = st['current']
check(cur and '按照设计机构许可项目单明确的权利范围进行批准' in cur['template'],
      '局方规定的批准声明原文在系统里（受控配置，不是代码里的字符串常量）')
check(cur['placeholder'] == '【设计机构许可证编号】', '占位符就是原文里那个')
kinds = {k['code']: k for k in st['doc_kinds']}
need = {c for c, k in kinds.items() if k['requires_statement']}
no_need = {c for c, k in kinds.items() if not k['requires_statement']}
check(need == {'SERVICE_BULLETIN', 'AFM_SUPPLEMENT', 'ICA'},
      '须附声明的三类：服务通告、飞行手册补充、持续适航文件')
check(no_need == {'COMPLIANCE_DOC', 'CERT_PLAN', 'PRODUCTION_DATA'},
      '**不使用**此声明的三类：符合性文件、审定计划、生产用设计数据（第 5 步原文）')
check(all('不使用此声明' in kinds[c]['reason'] for c in no_need),
      '不使用的那三类都写明了理由，不是默认值')
print('ok   声明原文受控；资料种类两分，哪一类附哪一类不附都有原文依据')

bl = admin.call('GET', '/das/minor-approvals/blocker', expect=200)
check(bl and '尚不存在' in bl[0]['note'],
      '许可证编号这道门摆明了是如实状态，不是缺陷')
check('服务通告' in bl[0]['blocked_kinds'] and '符合性文件' in bl[0]['unaffected_kinds'],
      '被挡住的与不受影响的分得清')
print('ok   DOA 在申请中 → 须附声明的资料暂时发不出去，并说明硬塞编号才是问题')

# ================= 造人造前提 =================
de = make_user(admin, 'ENGINEER', 'made')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'maawm')
dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'madcm')
other = make_user(admin, 'ENGINEER', 'maoth')
for u, tag in ((de, 'MADE'), (awm, 'MAAWM'), (dcm, 'MADCM'), (other, 'MAOTH')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, pos in ((de, 'DE'), (awm, 'AWM'), (dcm, 'DCM')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': pos, 'kind': 'FORMAL'}, expect=201)

crit = admin.call('GET', '/das/changes/criteria', expect=200)


def make_minor_change(no, title):
    """造一条判为小改并已签署的更改（本用例的前提）。"""
    de.call('POST', '/das/changes',
            {'change_no': no, 'title': title, 'purpose': '用例准备',
             'content': '侧壁插座位置微调', 'products': 'B737-800 客舱',
             'drawings': 'DWG-%s R1' % no}, expect=201)
    de.call('PUT', '/das/changes/%s/impact' % no,
            {'impact_list': '受影响件号 1 个', 'impact_baseline_ref': '基线 BL-2026-07',
             'ad_checked': True, 'cert_basis_checked': True}, expect=200)
    for c in crit:
        de.call('POST', '/das/changes/%s/criteria' % no,
                {'criterion_code': c['code'], 'verdict': 'NO_IMPACT',
                 'rationale': '不触及' + c['name_cn']}, expect=201)
    de.call('POST', '/das/changes/%s/classification' % no,
            {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
             'conclusion_reason': '9 项判据均无显著影响',
             'form_no': 'UG-DOA-%s-FL' % no}, expect=201)


make_minor_change(CHG, '小改用例 A')
make_minor_change(CHG2, '小改用例 B')
print('ok   两条已签署的小改分类已就位（断言不空转的前提）')

# ================= 反例 110：前提不是已签署的小改 =================
MAJ = 'CHG-M5MAJ-%s' % STAMP
de.call('POST', '/das/changes',
        {'change_no': MAJ, 'title': '主承力框开口', 'purpose': 'x', 'content': 'x',
         'products': 'B737-800 结构', 'drawings': 'DWG-M'}, expect=201)
de.call('PUT', '/das/changes/%s/impact' % MAJ,
        {'impact_list': '12 框', 'impact_baseline_ref': '基线', 'ad_checked': True,
         'cert_basis_checked': True}, expect=200)
for c in crit:
    sig = c['code'] == 'STRUCTURE'
    de.call('POST', '/das/changes/%s/criteria' % MAJ,
            {'criterion_code': c['code'],
             'verdict': 'SIGNIFICANT' if sig else 'NO_IMPACT',
             'rationale': '改变载荷路径' if sig else '不触及',
             'evidence_ref': '强度分析 AR-01' if sig else None}, expect=201)
de.call('POST', '/das/changes/%s/classification' % MAJ,
        {'state': 'CLASSIFIED', 'major_minor': 'MAJOR',
         'conclusion_reason': '结构强度显著影响'}, expect=201)
base = {'approved_scope': 'B737-800 客舱侧壁插座', 'cve_check_ref': 'CVE 核查 2026-04',
        'change_doc_ref': '更改说明 + DWG R1', 'no_limitation_declared': True,
        'authority_ref': 'UG-DAM-01-附2 第 4 项授权书 ' + STAMP}
r = awm.call('POST', '/das/minor-approvals',
             dict(base, change_no=MAJ, form_no='UG-DOA-X-%s-PZ' % STAMP[-4:]),
             expect=400)
check('已有签署的分类表且为小改' in str(r) and 'MAJOR' in str(r),
      '拿判为大改的分类去批小改被拒（UG-DAP-08 第 1 步 a）')
print('ok   110 小改批准的前提是已签署且为小改的分类结论')

# ================= 反例：编号后缀与限制条件（授权依据先补齐）=================
r = awm.call('POST', '/das/minor-approvals',
             dict(base, change_no=CHG, form_no='UG-DOA-B737-2026-9002-FL'), expect=400)
check('PZ' in str(r) and 'FL' in str(r),
      '编号后缀不是 PZ 被拒，并说明 FL／PZ／SM 分别是什么')
r = awm.call('POST', '/das/minor-approvals',
             {'change_no': CHG, 'form_no': PZ1,
              'approved_scope': 'B737-800 客舱侧壁插座',
              'cve_check_ref': 'CVE 核查', 'change_doc_ref': '更改说明',
              'authority_ref': '授权书 ' + STAMP}, expect=400)
check('忘了写' in str(r), '限制条件空着又不声明「无限制」被拒')
r = awm.call('POST', '/das/minor-approvals',
             dict(base, change_no=CHG, form_no=PZ1, limitations='不含结构件'),
             expect=400)
check('二者取一' in str(r), '既声明无限制又写限制内容被拒')
print('ok   编号后缀与限制条件各自为自己那条规则失败（授权依据已先补齐）')

# ================= 反例 111：非在任 AWM 且不写授权依据 =================
r = other.call('POST', '/das/minor-approvals',
               {'change_no': CHG, 'form_no': PZ1,
                'approved_scope': 'B737-800 客舱侧壁插座',
                'cve_check_ref': 'CVE 核查', 'change_doc_ref': '更改说明',
                'no_limitation_declared': True}, expect=403)
check('适航管理负责人' in str(r), '无岗位任命者不得签小改批准')
r = de.call('POST', '/das/minor-approvals',
            {'change_no': CHG, 'form_no': PZ1,
             'approved_scope': 'B737-800 客舱侧壁插座', 'cve_check_ref': 'CVE 核查',
             'change_doc_ref': '更改说明', 'no_limitation_declared': True}, expect=403)
check('5 类适航签署事项' in str(r) and '待澄清项' in str(r),
      '非在任 AWM 签署须写明纸面授权依据，并说明系统为什么判不了授权范围')
print('ok   111 小改批准是 5 类适航签署事项之一，系统不冒充校验授权范围')

# ================= 批准 =================
a1 = de.call('POST', '/das/minor-approvals',
             dict(base, change_no=CHG, form_no=PZ1,
                  limitations='不含结构主承力件；不改变电气负载分配',
                  no_limitation_declared=False), expect=201)
check(a1['form_no'] == PZ1, '小改批准已建')
reg = admin.call('GET', '/das/minor-approvals', expect=200)
mine = [x for x in reg if x['form_no'] == PZ1][0]
check(mine['signed_under_delegation'], '由授权人员签的在台账上标出来')
print('ok   批准已建；由"授权人员"而非在任 AWM 签的在台账上看得见')

# ================= 反例 109：条件没核全就发布 =================
rel = {'doc_kind': 'PRODUCTION_DATA', 'doc_ref': 'DWG-01 R3',
       'distributed_to': '生产、采购', 'archived_ref': '归档 ' + STAMP}
r = dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ1, rel, expect=400)
for cn in ('已有签署的分类表且为小改', '权利范围内', '批准人在授权范围内'):
    check(cn in str(r), '发布前报出还差条件「%s」' % cn[:8])
r = de.call('POST', '/das/minor-approvals/%s/conditions' % PZ1,
            {'condition_code': 'WITHIN_DOA_SCOPE', 'satisfied': True, 'evidence': '  '},
            expect=400)
check('怎么核的' in str(r), '条件不写依据被拒：一个勾说不出哪一条是怎么核的')
de.call('POST', '/das/minor-approvals/%s/conditions' % PZ1,
        {'condition_code': 'CLASSIFIED_MINOR', 'satisfied': True,
         'evidence': '已核对 UG-DAF-02 编号与签署，结论为小改'}, expect=201)
de.call('POST', '/das/minor-approvals/%s/conditions' % PZ1,
        {'condition_code': 'WITHIN_DOA_SCOPE', 'satisfied': False,
         'evidence': '项目单附录 F 未覆盖该专业领域'}, expect=201)
de.call('POST', '/das/minor-approvals/%s/conditions' % PZ1,
        {'condition_code': 'APPROVER_AUTHORISED', 'satisfied': True,
         'evidence': '已核对纸面授权书的签署事项范围'}, expect=201)
r = de.call('POST', '/das/minor-approvals/%s/conditions' % PZ1,
            {'condition_code': 'WITHIN_DOA_SCOPE', 'satisfied': True,
             'evidence': '改一下'}, expect=400)
check('不得改写' in str(r), '条件核对结论不得改写，变了要另建一条批准')
r = dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ1, rel, expect=400)
check('权利范围内' in str(r) and '任一项不满足' in str(r),
      '有条件不满足时不得发布')
check('不得仅因无批准权限自动改判大改' in str(r),
      '拒绝理由带上第 7 章那条：超权限的提交局方办理，分类结论保持原样')
gap = admin.call('GET', '/das/minor-approvals/condition-gaps', expect=200)
check([x for x in gap if x['form_no'] == PZ1 and x['unsatisfied'] == 1],
      '条件不满足的进缺口清单，且还没有发布记录')
print('ok   109 三条条件要全核过且满足才放行；不满足时分类结论保持原样')

# ================= 另建一条，三条都满足 =================
a2 = de.call('POST', '/das/minor-approvals',
             dict(base, change_no=CHG2, form_no=PZ2), expect=201)
for code, ev in (('CLASSIFIED_MINOR', '已核对 UG-DAF-02 编号与签署，结论为小改'),
                 ('WITHIN_DOA_SCOPE', '项目单附录 F 覆盖该专业领域，已核对'),
                 ('APPROVER_AUTHORISED', '已核对纸面授权书的签署事项范围')):
    de.call('POST', '/das/minor-approvals/%s/conditions' % PZ2,
            {'condition_code': code, 'satisfied': True, 'evidence': ev}, expect=201)
r = de.call('POST', '/das/minor-approvals/%s/releases' % PZ2, rel, expect=403)
check('资料管理负责人' in str(r), '发布与归档归资料管理负责人（第 5～6 步）')
dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ2, rel, expect=201)
print('ok   生产用设计数据：不使用声明，三条条件齐备后可发布')

# ================= 反例 112：声明的四种错法 =================
r = dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ2,
             dict(rel, doc_kind='COMPLIANCE_DOC', doc_ref='CD-01 R1',
                  statement_text=OFFICIAL), expect=400)
check('不使用' in str(r) and '说成了设计批准' in str(r),
      '给「不使用此声明」的种类附上声明被拒，并说明后果')
r = dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ2,
             dict(rel, doc_kind='SERVICE_BULLETIN', doc_ref='SB-2026-003'), expect=400)
check('须附局方规定的批准声明' in str(r), '服务通告不附声明被拒')
r = dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ2,
             dict(rel, doc_kind='SERVICE_BULLETIN', doc_ref='SB-2026-003',
                  statement_text=cur['template']), expect=400)
check('占位符' in str(r) and '假的许可证编号' in str(r),
      '占位符没替换被拒，并说明硬塞一个编号的后果')
r = dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ2,
             dict(rel, doc_kind='SERVICE_BULLETIN', doc_ref='SB-2026-003',
                  statement_text=OFFICIAL.replace('设计机构许可项目单', '许可项目单')),
             expect=409)
check('原文不符' in str(r), '声明被改了几个字被拒（第 7 章：必须使用原文）')
dcm.call('POST', '/das/minor-approvals/%s/releases' % PZ2,
         dict(rel, doc_kind='SERVICE_BULLETIN', doc_ref='SB-2026-003',
              statement_text=OFFICIAL, distributed_to='使用者、生产、采购',
              caac_notified=True), expect=201)
d = admin.call('GET', '/das/minor-approvals/%s' % PZ2, expect=200)
sb = [x for x in d['releases'] if x['doc_kind'] == 'SERVICE_BULLETIN'][0]
check(sb['statement_text'] == OFFICIAL, '声明逐字与受控原文相符方可发布')
check(sb['caac_notified'], '必要时通知局方（第 6 步）')
print('ok   112 声明：不附不行、附错种类不行、占位符未替换不行、改一个字不行')

# ================= 前提失效：分类结论后来变了 =================
inv0 = {x['form_no'] for x in
        admin.call('GET', '/das/minor-approvals/invalidated', expect=200)}
check(PZ2 not in inv0, '分类还是已签署小改时，批准不在失效清单里')
awm.call('POST', '/das/changes/%s/classification' % CHG2,
         {'state': 'CAAC_DISAGREED', 'major_minor': 'MAJOR',
          'conclusion_reason': '局方认为属重大更改，以局方意见为准；'
                               '按 UG-DAP-14 复查同类历史更改'}, expect=201)
inv = admin.call('GET', '/das/minor-approvals/invalidated', expect=200)
mine = [x for x in inv if x['form_no'] == PZ2]
check(mine, '**分类结论一变，建立在它上面的批准立刻进失效清单**')
check(mine[0]['releases'] >= 2,
      '失效清单带上已发布的资料份数（%d）—— 外面那几份资料带着一个不成立的批准'
      % mine[0]['releases'])
check('不是小改' in mine[0]['why'] or '已不是' in mine[0]['why'],
      '写明为什么失效')
print('ok   前提会失效: 只在插入时校验一次不够, 要持续列出并交 M7 按 UG-DAP-14 处理')

# ================= 反例 113／114：不得抹改 =================
aid = db_query("SELECT id FROM das_minor_approval WHERE form_no=%s", (PZ2,))[0]['id']
check('DCMS-INV-113' in reason(
    "UPDATE das_minor_approval SET approved_scope='改一下' WHERE id=%s", (aid,)),
    '113 小改批准不得改写（UG-DAF-04 对应表-21-174，签了就是对外作出的批准）')
check('DCMS-INV-113' in reason(
    "DELETE FROM das_minor_approval WHERE id=%s", (aid,)), '113 小改批准不得删除')
check('DCMS-INV-113' in reason(
    "UPDATE das_minor_approval_condition SET satisfied=true WHERE approval_id=%s", (aid,)),
    '113 条件核对不得改写')
check('DCMS-INV-113' in reason(
    "DELETE FROM das_minor_approval_release WHERE approval_id=%s", (aid,)),
    '113 发布记录不得删除（它是分发证据）')
check('DCMS-INV-114' in reason(
    "DELETE FROM das_approval_statement WHERE code='DOA_MINOR_APPROVAL'"),
    '114 受控声明原文不得删除：已发出去的资料印的就是这段话')
before = db_query("SELECT count(*) AS n FROM das_approval_statement_history")[0]['n']
db_execute("""UPDATE das_approval_statement SET template = template || %s
               WHERE code='DOA_MINOR_APPROVAL'""", ('（试改 %s）' % STAMP,))
check(db_query("SELECT count(*) AS n FROM das_approval_statement_history")[0]['n']
      == before + 1, '114 声明原文改动留旧版')
db_execute("""UPDATE das_approval_statement SET template = %s
               WHERE code='DOA_MINOR_APPROVAL'""", (cur['template'],))
check(admin.call('GET', '/das/minor-approvals/statement',
                 expect=200)['current']['template'] == cur['template'],
      '原文已恢复（本用例只验机制，不改受控文本）')
print('ok   113／114 批准与发布记录不得抹改；声明原文改动留旧版并可追')

# ================= M5 已登进"范围校验尚未施加"清单 =================
une = admin.call('GET', '/das/signer-scope/unenforced', expect=200)
check(any('M5' in u['path'] or 'UG-DAF-04' in u['path'] for u in une),
      '小改批准登进了未施加清单 —— 第 1 步条件 c) 要判的是 5 类适航签署事项之一')
print('ok   M5 主动登记: "批准人在授权范围内"系统判不全, 由 111 要求说得出依据')

print('\nPASS M5 设计小改批准: 109 三条条件任一不满足不得发布、110 前提须为已签署小改、'
      '111 非在任 AWM 须写明授权依据、112 声明必须是原文且种类分得清、'
      '113／114 不得抹改、前提失效后持续列出')
