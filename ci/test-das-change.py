"""M4 设计更改分类（第二批，UG-DAP-06 ＋ UG-DAW-010 的 9 项判据）。

按判据 I-总 以反例为主。这块有两条看着矛盾、其实管的是两件事的规则，混起来就会做错：

  UG-DAW-010 第 1 章：**无法判定的按重大更改处理**，直至适航管理负责人或局方确认。
  UG-DAP-06 第 4 步：**超出分类或批准权限时转局方办理，不能仅因超权限自动判为大改。**

第一条说技术上判不了，第二条说权限上不够。合成一个"拿不准就判大改"的分支，就会在签署人
权限不足时自动升级为重大更改——那是用权限问题冒充技术判断，而重大更改要走 UG-DAP-09 向
局方申请批准，等于凭一个权限缺口给产品加了一道审定。所以用例要证明这两种状态分得开。

覆盖：
  104  9 项判据少一条就不得出分类结论
  105  任一项显著影响只能是大改；判不了须填大改且**不得签署**
  106  超权限结论留空、不得填大改，且须写明转局方依据
  107  声学与排放本单位无批准权，结论只能来自局方（须带编号与日期）
  108  判据结论、分类结论、累计评估、偏离记录均不得删改；分类状态变更自动留痕
  P5   判据里没有数值阈值（「重量与平衡」的 kg／%MAC 子句已按待澄清项 9 删除）
  累计 判为小改而同项目此前另有更改、却无合并评估的，进缺口清单
  偏离 影响适航的不得批准，且须指向回到设计更改流程的那条更改

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-change.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
TODAY = dt.date.today()
C1 = 'CHG-%s-1' % STAMP
C2 = 'CHG-%s-2' % STAMP
C3 = 'CHG-%s-3' % STAMP


def release(*users):
    for u in users:
        db_execute("UPDATE user_session SET revoked_at = now() "
                   "WHERE user_id = %s AND revoked_at IS NULL", (u.id,))


def reason(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error as e:
        return str(e)
    return ''


# ================= 9 项判据 =================
crit = admin.call('GET', '/das/changes/criteria', expect=200)
check(len(crit) == 9, 'UG-DAW-010 第 2 章的 9 项判据都在（%d）' % len(crit))
check(all(c['significant_when'] and c['evidence_required'] for c in crit),
      '每项都有"什么情况算显著影响"与"判定需要的证据"')
wb = [c for c in crit if c['code'] == 'WEIGHT_BALANCE'][0]
check('kg' not in wb['significant_when'] and 'MAC' not in wb['significant_when'],
      '**判据里没有数值阈值**：kg／%MAC 子句已按待澄清项 9 删除（判据 P5）')
check('阈值' in (wb['note'] or '') or '技术标准' in (wb['note'] or ''),
      '删除的理由记在 note 里，不是悄悄去掉')
check(not any('threshold' in c or 'kg' in str(c.get('note') or '')[:0] for c in crit),
      '没有任何判据带阈值字段')
print('ok   9 项判据齐备，且没有数值阈值（体系文件是制度不是技术标准）')
for c in crit:
    print('       %d. %-12s %s' % (c['seq'], c['name_cn'], c['significant_when'][:40]))

# ================= 闸门按岗位任命 =================
plain = make_user(admin, 'ENGINEER', 'cgp')
r = plain.call('POST', '/das/changes',
               {'change_no': C1, 'title': 'x', 'purpose': 'x', 'content': 'x',
                'products': 'x', 'drawings': 'x'}, expect=403)
check('设计工程师' in str(r), '提出更改须在任设计工程师（UG-DAP-06 第 1 步）')

de = make_user(admin, 'ENGINEER', 'cgde')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'cgawm')
dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'cgdcm')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'cgam')
for u, tag in ((de, 'CGDE'), (awm, 'CGAWM'), (dcm, 'CGDCM'), (am, 'CGAM')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, pos in ((de, 'DE'), (awm, 'AWM'), (dcm, 'DCM'), (am, 'AM')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': pos, 'kind': 'FORMAL'}, expect=201)
release(plain)
print('ok   提出与判据归设计工程师；转局方与待确认归适航管理负责人')

# ================= 一个项目，两条更改（为累计影响铺路）=================
YY = TODAY.strftime('%y')


def free_no(prefix):
    used = {r['project_no'] for r in db_query(
        "SELECT project_no FROM das_project WHERE project_no LIKE %s", (prefix + '%',))}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        for seq in range(1, 100):
            no = '%s%02d%s%s' % (prefix, seq, letter, YY)
            if no not in used:
                return no
    raise AssertionError('项目编号用尽')


PRJ = free_no('UG-STC')
dcm.call('POST', '/das/projects',
         {'project_no': PRJ, 'type_code': 'STC', 'name_cn': 'M4 用例项目 ' + STAMP,
          'aircraft_type': 'B737-800'}, expect=201)
am.call('POST', '/das/projects/%s/approval' % PRJ, {'approval_ref': '立项 ' + STAMP},
        expect=201)

base = {'purpose': '客户要求', 'content': '将 12 排插座由座椅下移至侧壁',
        'products': 'B737-800 客舱座椅电源', 'drawings': PRJ + '-DWG-01 R2',
        'project_no': PRJ}
de.call('POST', '/das/changes', dict(base, change_no=C1, title='插座位置调整'),
        expect=201)
print('ok   更改申请已登记（UG-DAP-06 第 1 步）')

# ================= 反例：没做影响分析就打判据 =================
r = de.call('POST', '/das/changes/%s/criteria' % C1,
            {'criterion_code': 'STRUCTURE', 'verdict': 'NO_IMPACT',
             'rationale': '不触及'}, expect=400)
check('影响分析' in str(r) and '打的是什么影响' in str(r),
      '没有受影响对象清单就打判据被拒')
r = de.call('PUT', '/das/changes/%s/impact' % C1,
            {'impact_list': '受影响件号 3 个', 'impact_baseline_ref': '基线 BL-2026-07',
             'ad_checked': False, 'cert_basis_checked': True}, expect=400)
check('适航指令适用性' in str(r), 'AD 未核对完就登记影响分析被拒')
de.call('PUT', '/das/changes/%s/impact' % C1,
        {'impact_list': '受影响件号 3 个、图纸 2 份',
         'impact_baseline_ref': '基线 BL-2026-07，引用关系已查',
         'ad_checked': True, 'cert_basis_checked': True}, expect=200)
r = de.call('PUT', '/das/changes/%s/impact' % C1,
            {'impact_list': '再来一次', 'impact_baseline_ref': 'x',
             'ad_checked': True, 'cert_basis_checked': True}, expect=400)
check('已登记' in str(r), '影响分析不重复登记')
print('ok   影响分析四项要齐：做一半会让下一步以为分析过了')

# ================= 反例 104：判据没打完就出结论 =================
r = de.call('POST', '/das/changes/%s/classification' % C1,
            {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
             'conclusion_reason': '不触及任何判据'}, expect=400)
for name in ('重量与平衡', '结构强度', '持续适航'):
    check(name in str(r), '拒绝理由逐项报出还差「%s」' % name)
check('没有分类依据' in str(r), '说明后果：漏掉一条判据的分类在局方复查时等于没有依据')
print('ok   104 9 项判据少一条就不得出分类结论，并报出还差哪几项')

# ================= 反例：无影响不写理由 / 显著影响不给证据 =================
r = de.call('POST', '/das/changes/%s/criteria' % C1,
            {'criterion_code': 'STRUCTURE', 'verdict': 'NO_IMPACT',
             'rationale': '   '}, expect=400)
check('不得只写' in str(r), '判"无影响"却不写理由被拒（UG-DAW-010 第 3 章）')
r = de.call('POST', '/das/changes/%s/criteria' % C1,
            {'criterion_code': 'STRUCTURE', 'verdict': 'SIGNIFICANT',
             'rationale': '改变了载荷路径'}, expect=400)
check('强度分析' in str(r), '判"显著影响"却不给证据被拒，并说明该项要什么证据')
r = de.call('POST', '/das/changes/%s/criteria' % C1,
            {'criterion_code': 'NOPE', 'verdict': 'NO_IMPACT', 'rationale': 'x'},
            expect=404)
print('ok   「无影响」也要写理由；「显著影响」要指出 UG-DAW-010 列的那项证据')

# ================= 逐条打勾 → 小改 =================
for c in crit:
    de.call('POST', '/das/changes/%s/criteria' % C1,
            {'criterion_code': c['code'], 'verdict': 'NO_IMPACT',
             'rationale': '插座位于非承力侧壁，不触及%s；依据影响清单与基线查询' % c['name_cn']},
            expect=201)
r = de.call('POST', '/das/changes/%s/criteria' % C1,
            {'criterion_code': 'STRUCTURE', 'verdict': 'SIGNIFICANT',
             'rationale': '改口', 'evidence_ref': 'AR-1'}, expect=400)
check('不得改写' in str(r), '给过的判据结论不得改写（DCMS-INV-108）')
cl1 = de.call('POST', '/das/changes/%s/classification' % C1,
              {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
               'conclusion_reason': '9 项判据均无显著影响，判为小改',
               'form_no': 'UG-DOA-B737-2026-%s-FL' % STAMP[-4:]}, expect=201)
check(cl1['signed_by'] and cl1['signed_on'], '已分类须有签署人与日期')
print('ok   9 项逐条打勾后方可出结论；编号按 UG-DAP-06 第 5 步')

# ================= 反例 105：有显著影响却判小改 =================
de.call('POST', '/das/changes',
        dict(base, change_no=C2, title='主承力框开口位置调整',
             content='在 12 框增加线束通过孔', drawings=PRJ + '-DWG-05 R1'), expect=201)
de.call('PUT', '/das/changes/%s/impact' % C2,
        {'impact_list': '受影响：12 框、蒙皮 2 块', 'impact_baseline_ref': '基线 BL-2026-07',
         'ad_checked': True, 'cert_basis_checked': True}, expect=200)
for c in crit:
    sig = c['code'] == 'STRUCTURE'
    de.call('POST', '/das/changes/%s/criteria' % C2,
            {'criterion_code': c['code'],
             'verdict': 'SIGNIFICANT' if sig else 'NO_IMPACT',
             'rationale': '改变载荷路径' if sig else '不触及' + c['name_cn'],
             'evidence_ref': '强度分析 AR-2026-03' if sig else None}, expect=201)
r = de.call('POST', '/das/changes/%s/classification' % C2,
            {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
             'conclusion_reason': '工作量不大'}, expect=400)
check('结构强度' in str(r) and '任一项' in str(r),
      '有一项显著影响却判小改被拒（UG-DAW-010 第 1 章：任一项即为重大更改）')
print('ok   105 任一项显著影响即为重大更改——分类的对象是影响，不是工作量')

# ================= 反例 106：超权限不得自动判为大改 =================
r = awm.call('POST', '/das/changes/%s/classification' % C2,
             {'state': 'BEYOND_AUTHORITY', 'major_minor': 'MAJOR',
              'conclusion_reason': '超出本单位分类权限',
              'caac_referral_ref': '咨询函 2026-11'}, expect=400)
check('不能仅因超权限自动判为大改' in str(r),
      '**超权限填成大改被拒**，理由引 UG-DAP-06 第 4 步原文')
check('冒充技术判断' in str(r) and '一道审定' in str(r),
      '说明后果：凭一个权限缺口给产品加了一道审定')
r = awm.call('POST', '/das/changes/%s/classification' % C2,
             {'state': 'BEYOND_AUTHORITY', 'conclusion_reason': '超出本单位分类权限'},
             expect=400)
check('转局方办理的依据' in str(r), '标了超权限却不写转局方依据被拒')
awm.call('POST', '/das/changes/%s/classification' % C2,
         {'state': 'BEYOND_AUTHORITY',
          'conclusion_reason': '结构强度判为显著影响，但本单位该产品类别的分类权限不覆盖主承力结构',
          'caac_referral_ref': '致监管处室咨询函 UG-2026-%s' % STAMP[-3:]}, expect=201)
g = admin.call('GET', '/das/changes/gaps', expect=200)
check([x for x in g['awaiting_caac'] if x['change_no'] == C2],
      '转局方的进待办清单——它**不是「已分类」**，结论是空的')
d2 = admin.call('GET', '/das/changes/%s' % C2, expect=200)
check(d2['classification']['major_minor'] is None, '超权限时分类结论确实留空')
print('ok   106 超权限：结论留空、转局方办理，且须写明依据')

# ================= 反例 105：技术判不了是另一件事 =================
de.call('POST', '/das/changes',
        dict(base, change_no=C3, title='新材料替代', content='以等效牌号替代',
             drawings=PRJ + '-DWG-09 R1'), expect=201)
de.call('PUT', '/das/changes/%s/impact' % C3,
        {'impact_list': '受影响件号 1 个', 'impact_baseline_ref': '基线 BL-2026-07',
         'ad_checked': True, 'cert_basis_checked': True}, expect=200)
for c in crit:
    de.call('POST', '/das/changes/%s/criteria' % C3,
            {'criterion_code': c['code'], 'verdict': 'NO_IMPACT',
             'rationale': '材料等效性待确认，暂判不触及' + c['name_cn']}, expect=201)
r = awm.call('POST', '/das/changes/%s/classification' % C3,
             {'state': 'UNDETERMINED', 'major_minor': 'MINOR',
              'conclusion_reason': '材料等效性待确认'}, expect=400)
check('无法判定的按重大更改处理' in str(r), '判不了却填小改被拒')
check('两件事' in str(r) and '权限' in str(r),
      '拒绝理由点明"技术判不了"与"超权限"是两件事')
awm.call('POST', '/das/changes/%s/classification' % C3,
         {'state': 'UNDETERMINED', 'major_minor': 'MAJOR',
          'conclusion_reason': '材料等效性待确认，按 UG-DAW-010 第 1 章暂按重大更改处理'},
         expect=201)
d3 = admin.call('GET', '/das/changes/%s' % C3, expect=200)
check(d3['classification']['signed_by'] is None,
      '**判不了的分类不得签署**——按重大更改处理是暂行处置，不是已分类')
msg = reason("""UPDATE das_change_classification SET signed_by=(SELECT id FROM app_user
                 WHERE username=%s), signed_on=current_date
                WHERE change_id=(SELECT id FROM das_design_change WHERE change_no=%s)""",
             (admin.username, C3))
check('DCMS-INV-105' in msg, '绕过服务层直接签也被拦（105）')
print('ok   105 技术判不了：按大改处理但不得签署；与超权限分成两个状态')

# ================= 反例 107：声学与排放只能来自局方 =================
msg = reason("""UPDATE das_change_classification SET acoustic_result='NON_ACOUSTIC'
                WHERE change_id=(SELECT id FROM das_design_change WHERE change_no=%s)""",
             (C1,))
check('DCMS-INV-107' in msg and '没有权利' in msg,
      '内部给声学结论、不记局方编号被拒（本单位未申请该分类批准权）')
r = awm.call('POST', '/das/changes/%s/caac-result' % C1,
             {'aspect': 'acoustic', 'result': 'NON_ACOUSTIC', 'caac_ref': '  ',
              'caac_on': str(TODAY)}, expect=400)
check('局方编号' in str(r), '登记局方结论时必须带局方编号')
r = awm.call('POST', '/das/changes/%s/caac-result' % C1,
             {'aspect': 'acoustic', 'result': 'EMISSION', 'caac_ref': 'x',
              'caac_on': str(TODAY)}, expect=400)
check('ACOUSTIC' in str(r), '声学的结论取值不能填排放的')
awm.call('POST', '/das/changes/%s/caac-result' % C1,
         {'aspect': 'acoustic', 'result': 'NON_ACOUSTIC',
          'caac_ref': '局方函 CAAC-2026-%s' % STAMP[-4:], 'caac_on': str(TODAY),
          'limitation': '限客舱内饰，不含发动机舱'}, expect=201)
awm.call('POST', '/das/changes/%s/caac-result' % C1,
         {'aspect': 'emission', 'result': 'NON_EMISSION',
          'caac_ref': '局方函 CAAC-2026-%s' % STAMP[-4:], 'caac_on': str(TODAY)},
         expect=201)
d1 = admin.call('GET', '/das/changes/%s' % C1, expect=200)
check(d1['classification']['acoustic_caac_ref'] and d1['classification']['emission_caac_ref'],
      '声学与排放两项的结论都带着局方编号')
print('ok   107 声学与排放本单位无批准权：只能登记局方结论，且必须带编号与日期')

# ================= 累计影响 =================
g = admin.call('GET', '/das/changes/gaps', expect=200)
gapped = {x['change_no'] for x in g['cumulative_gap']}
check(C1 in gapped,
      '判为小改、同项目此前另有更改、却无合并评估的进缺口清单（一串小改永远是小改）')
r = de.call('POST', '/das/changes/%s/cumulative' % C1,
            {'prior_change_no': C1, 'assessment': 'x'}, expect=400)
check('自己' in str(r), '不能跟自己做累计评估')
de.call('POST', '/das/changes/%s/cumulative' % C1,
        {'prior_change_no': C2,
         'assessment': '两次更改均在同一区域，合并后仍不触及结构强度判据'}, expect=201)
g = admin.call('GET', '/das/changes/gaps', expect=200)
check(C1 not in {x['change_no'] for x in g['cumulative_gap']},
      '补了合并评估后不再出现在缺口清单里')
d1 = admin.call('GET', '/das/changes/%s' % C1, expect=200)
check(d1['cumulative'] and d1['cumulative'][0]['prior_change_no'] == C2,
      '合并评估说得出跟哪一次一起评的——布尔勾完说不出这个')
print('ok   累计影响做成关联记录：说得出跟哪几次一起评，不是一个"已考虑"的勾')

# ================= 制造偏离 =================
dev = {'requested_by': '某生产单位', 'description': '孔位偏差 2mm',
       'assessment': '影响结构强度余量', 'affects_design_data': True,
       'affects_key_characteristics': True, 'affects_airworthiness': True}
r = de.call('POST', '/das/changes/deviations',
            dict(dev, deviation_no='DEV-%s-1' % STAMP, approved=True), expect=400)
check('不得批准' in str(r) and '绕过设计更改分类' in str(r),
      '**影响适航的制造偏离不得批准**（UG-DAP-06 第 7 步）')
r = de.call('POST', '/das/changes/deviations',
            dict(dev, deviation_no='DEV-%s-2' % STAMP, approved=False), expect=400)
check('没有下文' in str(r) and '偏差还在' in str(r),
      '影响适航却不指向设计更改流程被拒，并说明后果')
de.call('POST', '/das/changes/deviations',
        dict(dev, deviation_no='DEV-%s-3' % STAMP, approved=False,
             assessment='影响结构强度余量，不予批准，转设计更改', change_no=C2), expect=201)
g = admin.call('GET', '/das/changes/gaps', expect=200)
check([x for x in g['deviation_to_change']
       if x['deviation_no'] == 'DEV-%s-3' % STAMP and x['change_no'] == C2],
      '影响适航的偏离与它转去的那条设计更改对得上')
de.call('POST', '/das/changes/deviations',
        {'deviation_no': 'DEV-%s-4' % STAMP, 'requested_by': '某生产单位',
         'description': '表面处理工艺等效替代', 'assessment': '不影响适航特性',
         'affects_design_data': False, 'affects_key_characteristics': False,
         'affects_airworthiness': False, 'approved': True}, expect=201)
print('ok   偏离：影响适航的不批准且须转设计更改；不影响的可批准')

# ================= 反例 108：不得抹改，状态变更留痕 =================
cid = db_query("""SELECT cl.id FROM das_change_classification cl
                   JOIN das_design_change d ON d.id = cl.change_id
                  WHERE d.change_no = %s""", (C1,))[0]['id']
before = db_query("""SELECT count(*) AS n FROM das_change_classification_change
                      WHERE classification_id=%s""", (cid,))[0]['n']
awm.call('POST', '/das/changes/%s/classification' % C1,
         {'state': 'CAAC_DISAGREED',
          'conclusion_reason': '局方认为属重大更改，以局方意见为准；按 UG-DAP-14 复查同类历史更改',
          'major_minor': 'MAJOR'}, expect=201)
after = db_query("""SELECT old_state, new_state FROM das_change_classification_change
                     WHERE classification_id=%s ORDER BY changed_at DESC LIMIT 1""",
                 (cid,))
check(db_query("""SELECT count(*) AS n FROM das_change_classification_change
                   WHERE classification_id=%s""", (cid,))[0]['n'] == before + 1,
      '分类状态变更自动留一条痕')
check(after[0]['old_state'] == 'CLASSIFIED' and after[0]['new_state'] == 'CAAC_DISAGREED',
      '留痕记下改动前后的状态（局方不同意见时以局方为准）')
aid = db_query("""SELECT a.id FROM das_change_criterion_assessment a
                   JOIN das_design_change d ON d.id = a.change_id
                  WHERE d.change_no=%s LIMIT 1""", (C1,))[0]['id']
check('DCMS-INV-108' in reason(
    "UPDATE das_change_criterion_assessment SET rationale='改一下' WHERE id=%s", (aid,)),
    '108 判据结论不得改写')
check('DCMS-INV-108' in reason(
    "DELETE FROM das_change_criterion_assessment WHERE id=%s", (aid,)),
    '108 判据结论不得删除')
check('DCMS-INV-108' in reason(
    "DELETE FROM das_change_classification WHERE id=%s", (cid,)),
    '108 分类结论不得删除（删掉等于那次分类从未发生过）')
check('DCMS-INV-108' in reason(
    "DELETE FROM das_change_cumulative WHERE change_id="
    "(SELECT id FROM das_design_change WHERE change_no=%s)", (C1,)),
    '108 累计评估不得删除')
check('DCMS-INV-108' in reason(
    "DELETE FROM das_manufacturing_deviation WHERE deviation_no=%s",
    ('DEV-%s-3' % STAMP,)), '108 偏离记录不得删除')
print('ok   108 UG-DAF-02 对应局方表-21-173，签了就是对外陈述——一律不得抹改')

print('\nPASS M4 设计更改分类: 104 判据要打全、105 任一显著影响即大改且判不了不得签署、'
      '106 超权限不得自动判大改、107 声学排放只能来自局方、108 不得抹改、'
      'P5 判据里没有数值阈值、累计影响说得出跟谁一起评、影响适航的偏离不得批准')
