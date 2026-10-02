"""M5 构型管理（UG-DAP-05，9 步）。

按 2026-10-02 的决定选闸门：**只在便宜且不含糊的地方设**。这份程序里有四条写着「不得」
的规则，都只需要一句话就能判，而判错的后果都很实在——用例就围着这四条写：

  118  不可互换或单向互换的更改**必须更换件号**；完全互换换了件号同样是矛盾
  119  基线冻结后只能凭一条**已批准**的更改修改；**不得解冻**
  有效性 要追溯就得说清范围，有过渡期就得说清怎么并存（第 5 步）
  120  交付前核查的差异**未关闭的不得放行**（第 7 步 b）

另：第 8 步的未经批准的构型差异，关闭前要按 UG-DAP-14 记不符合项**并**按 UG-DAP-12
判断是否属应报告的事件——两件事都要做过。

**不做逐项录入的**：第 7 步 a 的六方比对（图纸、数模、BOM、规范、工艺、手册）走线下，
这里只记结论——所以用例也不去测那六方怎么比，只测结论记得下来、证据挂得上。

【每个反例只能因为它自己那条规则失败】
这一轮踩过五次，所以每个反例前先把别的前置条件补齐。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-config.py <credentials.json>
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
SN = 'SN-%s' % STAMP[-6:]


def free_no(prefix):
    used = {r['project_no'] for r in db_query(
        "SELECT project_no FROM das_project WHERE project_no LIKE %s", (prefix + '%',))}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        for seq in range(1, 100):
            no = '%s%02d%s%s' % (prefix, seq, letter, YY)
            if no not in used:
                return no
    raise AssertionError('项目编号用尽')


def reason(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error as e:
        return str(e)
    return ''


# ================= 造人造项目 =================
pm = make_user(admin, 'ENGINEER', 'cfpm')
cfg = make_user(admin, 'CONFIGURATION_MANAGER', 'cfcfg')
de = make_user(admin, 'ENGINEER', 'cfde')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'cfawm')
dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'cfdcm')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'cfam')
for u, tag in ((pm, 'CFPM'), (cfg, 'CFCFG'), (de, 'CFDE'), (awm, 'CFAWM'),
               (dcm, 'CFDCM'), (am, 'CFAM')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, pos in ((pm, 'PM'), (cfg, 'CFG'), (de, 'DE'), (awm, 'AWM'), (dcm, 'DCM'),
               (am, 'AM')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': pos, 'kind': 'FORMAL'}, expect=201)

PRJ = free_no('UG-STC')
dcm.call('POST', '/das/projects',
         {'project_no': PRJ, 'type_code': 'STC', 'name_cn': '构型用例 ' + STAMP,
          'aircraft_type': 'B737-800'}, expect=201)
am.call('POST', '/das/projects/%s/approval' % PRJ, {'approval_ref': '立项 ' + STAMP},
        expect=201)
print('ok   项目与六个岗位任命已就位')

# ================= 第 1 步：构型项目清单 =================
# 【用有权限的身份测校验，否则先被岗位门槛拦成 403，待测的那条根本没跑到】
# 纳入构型项目要项目负责人或构型管理员（第 1 步），de 没这个岗 ——
# 用 de 测"纳入理由不得为空"，测到的是权限而不是校验。
r = pm.call('POST', '/das/config/items',
            {'project_no': PRJ, 'item_kind': 'DRAWING', 'identifier': 'DWG-X',
             'name_cn': 'x', 'inclusion_reason': '  '}, expect=400)
check('纳入理由' in str(r), '纳入理由不得为空——空着就说不出为什么这个项目在清单里')
# 岗位门槛单独测一次, 这样两件事各自有自己的用例
r = de.call('POST', '/das/config/items',
            {'project_no': PRJ, 'item_kind': 'DRAWING', 'identifier': 'DWG-Y',
             'name_cn': 'y', 'inclusion_reason': '影响功能'}, expect=403)
check('项目负责人' in str(r), '纳入构型项目须项目负责人或构型管理员（第 1 步）')
r = pm.call('POST', '/das/config/items',
            {'project_no': PRJ, 'item_kind': 'NOPE', 'identifier': 'DWG-X',
             'name_cn': 'x', 'inclusion_reason': '影响功能'}, expect=400)
check('项目类别' in str(r), '未知类别被拒')
ITEMS = [('DRAWING', PRJ + '-DWG-01', '座椅电源插座安装图', '影响形状与配合'),
         ('BOM', PRJ + '-BOM-01', '客舱电源 BOM', '影响可追溯性'),
         ('SPEC', PRJ + '-SPEC-01', '插座技术规范', '影响功能与适航性')]
for kind, ident, name, why in ITEMS:
    pm.call('POST', '/das/config/items',
            {'project_no': PRJ, 'item_kind': kind, 'identifier': ident,
             'name_cn': name, 'inclusion_reason': why,
             'form_ref': 'UG-DAF-10-%s' % STAMP[-4:]}, expect=201)
r = pm.call('POST', '/das/config/items',
            {'project_no': PRJ, 'item_kind': 'DRAWING', 'identifier': ITEMS[0][1],
             'name_cn': 'x', 'inclusion_reason': 'y'}, expect=400)
check('已有' in str(r), '同一项目的清单里不重复纳入同一标识')
unconf = admin.call('GET', '/das/config/gaps', expect=200)['item_unconfirmed']
check(len([x for x in unconf if x['project_no'] == PRJ]) == 3,
      '尚未经授权人员确认的列在清单里（第 1 步）')
for _, ident, _, _ in ITEMS:
    awm.call('POST', '/das/config/items/%s/%s/confirmation' % (PRJ, ident), expect=201)
unconf = admin.call('GET', '/das/config/gaps', expect=200)['item_unconfirmed']
check(not [x for x in unconf if x['project_no'] == PRJ], '确认后不再出现在清单里')
print('ok   第 1 步 构型项目清单：纳入理由必填，须经授权人员确认')

# ================= 第 3 步：基线与冻结 =================
r = cfg.call('POST', '/das/config/baselines',
             {'project_no': PRJ, 'baseline_kind': 'DELIVERY',
              'code': 'BL-%s-D' % STAMP[-4:], 'description': '交付构型'}, expect=400)
check('序列号' in str(r), '交付基线没有序列号被拒——说不出是哪一台')
BL = 'BL-%s-DESIGN' % STAMP[-4:]
cfg.call('POST', '/das/config/baselines',
         {'project_no': PRJ, 'baseline_kind': 'DESIGN', 'code': BL,
          'description': '图纸和 BOM 冻结，可向生产和供应商发放'}, expect=201)
r = awm.call('POST', '/das/config/baselines/%s/freeze' % BL, expect=400)
check('空表' in str(r), '基线里一个项目都没有就冻结被拒')
for _, ident, _, _ in ITEMS:
    cfg.call('POST', '/das/config/baselines/%s/items' % BL,
             {'identifier': ident, 'revision': 'R1',
              'effectivity': '自 B-1415 起全机适用'}, expect=201)
r = cfg.call('POST', '/das/config/baselines/%s/freeze' % BL, expect=403)
check('适航管理负责人' in str(r), '冻结基线须授权人员（第 3 步）')
fz = awm.call('POST', '/das/config/baselines/%s/freeze' % BL, expect=201)
check(fz['frozen_at'], '基线已冻结')
r = awm.call('POST', '/das/config/baselines/%s/freeze' % BL, expect=400)
check('不得解冻' in str(r), '不重复冻结，并说明不得解冻')
msg = reason("UPDATE das_config_baseline SET frozen_at=NULL WHERE code=%s", (BL,))
check('DCMS-INV-119' in msg and '不成立' in msg,
      '**不得解冻**：解冻等于让"冻结"这件事不成立')
print('ok   第 3 步 三类基线；交付基线要序列号；冻结须授权人员且不得解冻')

# ================= 反例 119：冻结后的改动要凭已批准的更改 =================
r = cfg.call('POST', '/das/config/baselines/%s/items' % BL,
             {'identifier': ITEMS[0][1], 'revision': 'R2'}, expect=400)
check('凭一条已批准的更改' in str(r), '冻结后不写更改单号被拒')
# 造一条更改, 但先不出分类结论
CHG = 'CHG-CFG-%s' % STAMP
de.call('POST', '/das/changes',
        {'change_no': CHG, 'title': '插座位置调整', 'purpose': '客户要求',
         'content': '插座由座椅下移至侧壁', 'products': 'B737-800 客舱',
         'drawings': ITEMS[0][1], 'project_no': PRJ}, expect=201)
de.call('PUT', '/das/changes/%s/impact' % CHG,
        {'impact_list': '受影响 3 项', 'impact_baseline_ref': BL,
         'ad_checked': True, 'cert_basis_checked': True}, expect=200)
msg = reason("""INSERT INTO das_config_baseline_item
                    (baseline_id, config_item_id, revision, change_no)
                SELECT b.id, i.id, 'R2', %s
                  FROM das_config_baseline b, das_config_item i
                 WHERE b.code=%s AND i.identifier=%s""", (CHG, BL, ITEMS[1][1]))
check('DCMS-INV-119' in msg and '还没有分类结论' in msg,
      '更改还没有分类结论时不算"已批准的更改"')
# 出分类结论
crit = admin.call('GET', '/das/changes/criteria', expect=200)
for c in crit:
    de.call('POST', '/das/changes/%s/criteria' % CHG,
            {'criterion_code': c['code'], 'verdict': 'NO_IMPACT',
             'rationale': '不触及' + c['name_cn']}, expect=201)
de.call('POST', '/das/changes/%s/classification' % CHG,
        {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
         'conclusion_reason': '9 项判据均无显著影响',
         'form_no': 'UG-DOA-CFG-%s-FL' % STAMP[-4:]}, expect=201)
up = cfg.call('POST', '/das/config/baselines/%s/items' % BL,
              {'identifier': ITEMS[1][1], 'revision': 'R2', 'change_no': CHG,
               'effectivity': '自 B-1415 第 12 架起'}, expect=201)
check(up['previous_revision'] == 'R1' and up['revision'] == 'R2',
      '冻结基线里升版次改的是那一行本身（第 3 步：只能通过批准的更改修改），不是加一行')
r = cfg.call('POST', '/das/config/baselines/%s/items' % BL,
             {'identifier': ITEMS[1][1], 'revision': 'R2'}, expect=400)
check('凭一条已批准的更改' in str(r), '同一行再改仍然要更改单号')
print('ok   119 冻结后只能凭**已批准**的更改修改（转局方／待确认／局方异议都不算）')

# ================= 反例 118：互换性 =================
base = {'change_no': CHG, 'rationale': '接口尺寸与安装孔位改变，旧件装不上新位置'}
r = de.call('POST', '/das/config/interchangeability',
            dict(base, verdict='NONE', old_part_number='UG-N40-1A'), expect=400)
check('必须更换件号' in str(r), '**不可互换却不换件号被拒**')
check('装的人手里的件号是对的' in str(r),
      '拒绝理由说明了后果：现场会把它装到装不上或装上去不安全的位置')
r = de.call('POST', '/das/config/interchangeability',
            dict(base, verdict='ONE_WAY', old_part_number='UG-N40-1A',
                 new_part_number='UG-N40-1A'), expect=400)
check('必须更换件号' in str(r), '单向互换沿用同一件号被拒')
r = de.call('POST', '/das/config/interchangeability',
            dict(base, verdict='FULL', old_revision='R1'), expect=400)
check('升版次' in str(r), '完全互换不记新版次被拒')
r = de.call('POST', '/das/config/interchangeability',
            dict(base, verdict='FULL', old_revision='R1', new_revision='R2',
                 old_part_number='UG-N40-1A', new_part_number='UG-N40-1B'),
            expect=400)
check('结论与做法不一致' in str(r), '结论是完全互换却换了件号被拒')
r = de.call('POST', '/das/config/interchangeability',
            dict(base, verdict='NONE', old_part_number='UG-N40-1A',
                 new_part_number='UG-N40-1C', rationale='  '), expect=400)
check('依据' in str(r), '互换性结论不写依据被拒（第 2 步：在更改单中说明互换性结论）')
t = de.call('POST', '/das/config/interchangeability',
            dict(base, verdict='NONE', identifier=ITEMS[0][1],
                 old_part_number='UG-N40-1A', new_part_number='UG-N40-1C'),
            expect=201)
check(t['new_part_number'] == 'UG-N40-1C', '不可互换且换了件号，通过')
reg = [x for x in admin.call('GET', '/das/config/interchangeability', expect=200)
       if x['id'] == t['id']][0]
check(reg['part_number_changed'] and '不可互换' in reg['verdict_cn'],
      '台账上标出换过件号的那些——它们影响现场')
msg = reason("UPDATE das_interchangeability SET verdict='FULL' WHERE id=%s", (t['id'],))
check('DCMS-INV-121' in msg, '121 互换性结论不得改写（它决定了件号怎么走）')
print('ok   118 互换性：不可互换／单向互换必须换件号；完全互换换件号同样是矛盾')

# ================= 第 5 步：有效性 =================
miss = admin.call('GET', '/das/config/gaps', expect=200)['effectivity_missing']
check([x for x in miss if x['change_no'] == CHG],
      '已分类却没有有效性记录的进清单（第 5 步：未明确有效性范围的更改不得发布）')
eff = {'change_no': CHG, 'effective_from_unit': '自 B-1415 第 12 架起',
       'notified_production': True, 'notified_procurement': True,
       'notified_airworthiness': True}
r = cfg.call('POST', '/das/config/effectivity',
             dict(eff, retrofit_delivered=True, transition_coexist=False), expect=400)
check('追到哪' in str(r), '要追溯改装却不写范围被拒')
r = cfg.call('POST', '/das/config/effectivity',
             dict(eff, retrofit_delivered=False, transition_coexist=True), expect=400)
check('怎么并存' in str(r), '有过渡期并存却不说明被拒')
r = cfg.call('POST', '/das/config/effectivity',
             dict(eff, effective_from_unit='  ', retrofit_delivered=False,
                  transition_coexist=False), expect=400)
check('架次' in str(r), '不写自哪个架次起实施被拒')
cfg.call('POST', '/das/config/effectivity',
         dict(eff, retrofit_delivered=True,
              retrofit_scope='已交付的 B-1415、B-1416 两架，随下次定检改装',
              transition_coexist=True,
              transition_note='第 12 架之前用 R1，之后用 R2，过渡期 3 个月并存'),
         expect=201)
miss = admin.call('GET', '/das/config/gaps', expect=200)['effectivity_missing']
check(not [x for x in miss if x['change_no'] == CHG], '定了有效性后不再出现在清单里')
op = admin.call('GET', '/das/config/gaps', expect=200)['effectivity_open']
check([x for x in op if x['change_no'] == CHG], '贯彻未销项的进另一份清单')
r = cfg.call('POST', '/das/config/effectivity/%s/closure' % CHG,
             {'closure_note': '  '}, expect=400)
check('怎么完成的' in str(r), '销项不写实施情况被拒')
cfg.call('POST', '/das/config/effectivity/%s/closure' % CHG,
         {'closure_note': 'B-1415、B-1416 已于定检改装完成，第 12 架起按 R2 生产'},
         expect=201)
op = admin.call('GET', '/das/config/gaps', expect=200)['effectivity_open']
check(not [x for x in op if x['change_no'] == CHG], '销项后不再出现在未销项清单里')
print('ok   第 5 步 有效性：要追溯就说清范围，有过渡期就说清怎么并存，销项要写实施情况')

# ================= 第 7 步：核查与差异 =================
r = cfg.call('POST', '/das/config/audits',
             {'project_no': PRJ, 'audit_kind': 'PRE_DELIVERY',
              'scope_note': 'as-built 与 as-designed 逐项比对',
              'conclusion': '见差异清单'}, expect=400)
check('序列号' in str(r), '交付前核查没有序列号被拒（针对具体一台）')
a1 = cfg.call('POST', '/das/config/audits',
              {'project_no': PRJ, 'audit_kind': 'INTERNAL_CONSISTENCY',
               'scope_note': '图纸、数模、BOM、规范、工艺、手册六者的参数、版次与引用标准',
               'conclusion': '六方比对由线下评审执行，结论一致，见线下记录'}, expect=201)
check(a1['audit_kind'] == 'INTERNAL_CONSISTENCY',
      '设计内部一致性核查只记结论——六方比对不逼人录成表格（2026-10-02 的决定）')
a2 = cfg.call('POST', '/das/config/audits',
              {'project_no': PRJ, 'audit_kind': 'PRE_DELIVERY', 'serial_no': SN,
               'scope_note': 'as-built 与 as-designed 逐项比对',
               'conclusion': '发现 2 项差异，逐条记录'}, expect=201)
d1 = cfg.call('POST', '/das/config/audits/%s/discrepancies' % a2['id'],
              {'description': '实物插座为 R1 版，设计已升 R2'}, expect=201)
d2 = cfg.call('POST', '/das/config/audits/%s/discrepancies' % a2['id'],
              {'description': '擅自使用旧版次线束，未经批准', 'unapproved': True},
              expect=201)
print('ok   第 7 步 核查与差异：六方比对只记结论，差异逐条记录')

# ================= 反例 120：未关闭的差异不得放行 =================
dlv = {'project_no': PRJ, 'serial_no': SN,
       'form_ref': 'UG-DAF-11-%s' % STAMP[-4:],
       'part_list': '%s R2、%s R2、%s R1' % (ITEMS[0][1], ITEMS[1][1], ITEMS[2][1]),
       'implemented_changes': CHG, 'no_residual_declared': True}
r = cfg.call('POST', '/das/config/deliveries', dlv, expect=400)
check('未关闭的差异不得放行' in str(r), '**有未关闭差异时不得出交付构型记录**')
check(str(d1['id']) in str(r) or '实物插座' in str(r), '报出是哪几条差异没关')
# 关闭第一条
r = cfg.call('POST', '/das/config/discrepancies/%s/closure' % d1['id'],
             {'disposition': '  '}, expect=400)
check('怎么关的' in str(r), '关闭不写处置被拒')
cfg.call('POST', '/das/config/discrepancies/%s/closure' % d1['id'],
         {'disposition': '实物已换装 R2 版插座，复查一致'}, expect=201)
# 第二条是未经批准的差异 → 要 NCR + 事件判断
r = cfg.call('POST', '/das/config/discrepancies/%s/closure' % d2['id'],
             {'disposition': '已换回正确版次'}, expect=400)
check('UG-DAP-14' in str(r) and '不符合项' in str(r),
      '未经批准的构型差异须按 UG-DAP-14 记不符合项（第 8 步）')
ncr = db_query("""INSERT INTO das_ncr (ncr_no, source, fact, basis_clause, evidence,
                                       created_by)
                  SELECT 'NCR-CFG-'||%s, 'INSPECTION',
                         '交付前核查发现擅自使用旧版次线束', 'UG-DAP-05 第 8 步',
                         '构型核查记录 %s', id
                    FROM app_user WHERE username=%s
                  RETURNING id""", (STAMP, a2['id'], admin.username))[0]['id']
r = cfg.call('POST', '/das/config/discrepancies/%s/closure' % d2['id'],
             {'disposition': '已换回正确版次', 'ncr_id': ncr}, expect=400)
check('UG-DAP-12' in str(r) and '48 小时' in str(r),
      '还须按 UG-DAP-12 判断是否属应报告的事件，并说明跳过的后果')
cfg.call('POST', '/das/config/discrepancies/%s/closure' % d2['id'],
         {'disposition': '已换回正确版次，并按 UG-DAP-14 开 NCR、按 UG-DAP-12 判为非报告事件',
          'ncr_id': ncr, 'occurrence_checked': True}, expect=201)
print('ok   第 8 步 未经批准的构型差异：记 NCR **并** 判断是否应报告，两件都要做')

# ================= 第 9 步：交付构型记录 =================
r = cfg.call('POST', '/das/config/deliveries',
             dict(dlv, no_residual_declared=False), expect=400)
check('忘了写' in str(r),
      '遗留差异要么写明要么显式声明「无」——空着发出去，接收方以为没有遗留差异')
dv = cfg.call('POST', '/das/config/deliveries', dict(dlv, baseline_code=BL), expect=201)
check(dv['serial_no'] == SN, '差异全关闭后方可出交付构型记录')
msg = reason("UPDATE das_delivery_config SET part_list='改一下' WHERE id=%s", (dv['id'],))
check('DCMS-INV-121' in msg and '移交客户' in msg,
      '121 交付构型记录不得改写——它随产品移交客户')
print('ok   第 9 步 交付构型记录：差异全关才放行，遗留差异栏不许空着')

# ================= 第 6 步：构型纪实是查询能力 =================
st = admin.call('GET', '/das/config/status?project_no=%s' % PRJ, expect=200)
check(len(st) == 3, '构型纪实查得出该基线由哪几个项目构成（%d）' % len(st))
by = {x['identifier']: x for x in st}
check(by[ITEMS[1][1]]['revision'] == 'R2' and by[ITEMS[1][1]]['change_no'] == CHG,
      '改过的那一项带着版次与更改单号')
check(by[ITEMS[1][1]]['change_class'] == 'MINOR', '并带出该更改的分类结论')
check(by[ITEMS[0][1]]['revision'] == 'R1', '没改的那一项还是原版次')
check(all(x['effectivity'] for x in st if x['identifier'] != ITEMS[1][1]),
      '有效性范围跟着基线项目走（UG-DAW-009）')
print('ok   第 6 步 构型纪实：按产品查得出"由哪些件号和版次构成、已实施哪些更改"')

print('\nPASS M5 构型管理: 118 互换性必须换件号、119 冻结后只能凭已批准的更改且不得解冻、'
      '第 5 步有效性说得清范围与并存、120 未关闭差异不得放行、'
      '第 8 步未经批准的差异要记 NCR 并判断是否应报告、121 交付记录不得改写')
