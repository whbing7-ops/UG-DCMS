"""M8 证后与对外发布（UG-DAP-11，17 步）。

按 2026-10-02 的决定选闸门：第 7 章那几条「不得」，每条都只要一句话就能判，而后果都在
系统外——资料已经发给运营人了。

  ck_dpd_statement  局方批准的资料标明实际批准依据，**不得加注虚假的本单位批准声明**
  122  已发布的资料**只能由原批准人或其上级撤销**（不得由编制人或使用部门自行作废）
  122  **止用通知未取得全部接收确认前，不得认为撤销已完成**
  123  **撤销期间，相关资料不得继续用于改装、生产或放行**（也不得再分发、不得引为答复依据）
  124  偏离已批准设计类的询问**不得以答复代替更改**；对外答复不得超出已批准的设计数据范围
  125  发布资料、撤销记录、权益转让与许可不得删除

另：第 9 步判为不安全状态时要给出事件报告编号（CCAR-21.5 那条链的入口）；
第 15 步**判「不相关」也必须记**；第 16 步的 30 天在时限引擎里（法规时限）。

【「其上级」在系统里是个解释】
系统里没有组织层级表，硬造一个会变成又一个与事实不符的常量（判据 A7 刚因为写死人数被改
过一次）。所以判为：原批准人本人，或在任责任经理／适航管理负责人。用例要验这个解释落到
了拒绝理由里，而不是默默放行。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-holder.py <credentials.json>
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
SB = 'SB-%s' % STAMP[-6:]
ICA = 'ICA-%s' % STAMP[-6:]
CD = 'CD-%s' % STAMP[-6:]
CERT = 'STC-CAAC-2026-H%s' % STAMP[-4:]
OFFICIAL = ('本资料由经CAAC批准的DOA-2026-001号设计机构许可证持有人'
            '按照设计机构许可项目单明确的权利范围进行批准。')


def free_no(prefix):
    used = {r['project_no'] for r in db_query(
        "SELECT project_no FROM das_project WHERE project_no LIKE %s", (prefix + '%',))}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        for seq in range(1, 100):
            no = '%s%02d%s%s' % (prefix, seq, letter, YY)
            if no not in used:
                return no
    raise AssertionError('项目编号用尽')


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


# ================= 造人、项目、设计批准 =================
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'hdawm')
dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'hddcm')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'hdam')
de = make_user(admin, 'ENGINEER', 'hdde')
other = make_user(admin, 'ENGINEER', 'hdoth')
for u, tag in ((awm, 'HDAWM'), (dcm, 'HDDCM'), (am, 'HDAM'), (de, 'HDDE'),
               (other, 'HDOTH')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, pos in ((awm, 'AWM'), (dcm, 'DCM'), (am, 'AM'), (de, 'DE')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': pos, 'kind': 'FORMAL'}, expect=201)

PRJ = free_no('UG-STC')
dcm.call('POST', '/das/projects',
         {'project_no': PRJ, 'type_code': 'STC', 'name_cn': 'M8 用例 ' + STAMP,
          'aircraft_type': 'B737-800'}, expect=201)
am.call('POST', '/das/projects/%s/approval' % PRJ, {'approval_ref': '立项 ' + STAMP},
        expect=201)
crit = admin.call('GET', '/das/changes/criteria', expect=200)
CHG = 'CHG-H-%s' % STAMP
de.call('POST', '/das/changes',
        {'change_no': CHG, 'title': 'ICA 修订', 'purpose': '证后', 'content': 'x',
         'products': 'B737-800', 'drawings': 'DWG-H', 'project_no': PRJ}, expect=201)
de.call('PUT', '/das/changes/%s/impact' % CHG,
        {'impact_list': '1 项', 'impact_baseline_ref': '基线', 'ad_checked': True,
         'cert_basis_checked': True}, expect=200)
for c in crit:
    de.call('POST', '/das/changes/%s/criteria' % CHG,
            {'criterion_code': c['code'], 'verdict': 'NO_IMPACT',
             'rationale': '不触及' + c['name_cn']}, expect=201)
de.call('POST', '/das/changes/%s/classification' % CHG,
        {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
         'conclusion_reason': '均无显著影响', 'form_no': 'UG-DOA-H-%s-FL' % STAMP[-4:]},
        expect=201)
# 设计批准（M8 挂在证件上, 不挂在项目上）
awm.call('POST', '/das/projects/%s/statements' % PRJ,
         {'statement_no': 'UG-DOA-SM-2026-H%s' % STAMP[-4:],
          'completion_confirm_ref': 'UG-DAF-17-H', 'verification_docs_ref': 'AR-H R1',
          'signed_under_authority_ref': '授权书 ' + STAMP}, expect=201)
awm.call('POST', '/das/projects/%s/design-approval' % PRJ,
         {'certificate_no': CERT, 'issued_on': str(TODAY),
          'product_scope_ref': 'B737-800 B-1415'}, expect=201)
print('ok   项目、分类、符合性声明、设计批准已就位（M8 挂在证件上）')

# ================= 反例：局方批准的资料加注本单位声明 =================
pub = {'certificate_no': CERT, 'doc_kind': 'SERVICE_BULLETIN', 'doc_no': SB,
       'revision': 'R1', 'title': '座椅电源插座改装服务通告'}
r = awm.call('POST', '/das/holder/docs',
             dict(pub, approval_basis='CAAC_APPROVED',
                  caac_approval_ref='局方批复 CAAC-2026-0777',
                  statement_text=OFFICIAL), expect=400)
check('不得加注' in str(r) and '责任主体不同' in str(r),
      '**局方批准的资料不得加注本单位批准声明**（第 7 章），并说明为什么')
r = awm.call('POST', '/das/holder/docs',
             dict(pub, approval_basis='CAAC_APPROVED'), expect=400)
check('实际批准依据' in str(r), '局方批准的资料不标明实际批准依据被拒')
r = awm.call('POST', '/das/holder/docs',
             dict(pub, approval_basis='DOA_SCOPE',
                  caac_approval_ref='局方批复 X'), expect=400)
check('二者取一' in str(r), '本单位批准的资料不记局方批准依据')
msg = reason("""INSERT INTO das_published_doc
                    (approval_id, doc_kind, doc_no, revision, title, approval_basis,
                     statement_text, approved_by, approved_on)
                SELECT a.id, 'ICA', %s, 'R9', 'x', 'CAAC_APPROVED', %s, u.id, current_date
                  FROM das_design_approval a, app_user u
                 WHERE a.certificate_no=%s AND u.username=%s""",
             (ICA, OFFICIAL, CERT, admin.username))
check('ck_dpd_statement' in msg, '数据库层同样拦住（ck_dpd_statement）')
print('ok   局方批准与本单位批准分得清，声明只能附在后者上')

# ================= 发布与分发 =================
doc = awm.call('POST', '/das/holder/docs',
               dict(pub, approval_basis='DOA_SCOPE', statement_text=OFFICIAL,
                    released_on=str(TODAY)), expect=201)
check(doc['approval_basis'] == 'DOA_SCOPE', '本单位在权利范围内批准的服务通告已登记')
r = other.call('POST', '/das/holder/docs/%s/R1/distribution' % SB,
               {'recipient': '某运营人', 'recipient_kind': 'OPERATOR'}, expect=403)
check('资料管理负责人' in str(r), '分发归资料管理负责人（第 4 步）')
RECIPIENTS = [('东海航空', 'OPERATOR'), ('某改装单位', 'MODIFIER'),
              ('某生产单位', 'PRODUCER')]
dist = []
for who, kind in RECIPIENTS:
    dist.append(dcm.call('POST', '/das/holder/docs/%s/R1/distribution' % SB,
                         {'recipient': who, 'recipient_kind': kind}, expect=201))
st = [x for x in admin.call('GET', '/das/holder/docs', expect=200)
      if x['doc_no'] == SB][0]
check(st['recipients'] == 3 and st['unacknowledged'] == 3,
      '分发清单与未确认数可追溯（第 7 章）')
r = dcm.call('POST', '/das/holder/distribution/%s/ack' % dist[0]['id'],
             {'ack_ref': '  '}, expect=400)
check('查不到证据' in str(r), '接收确认不写依据被拒')
for d in dist:
    dcm.call('POST', '/das/holder/distribution/%s/ack' % d['id'],
             {'ack_ref': '回函 %s-%s' % (d['recipient'], STAMP)}, expect=201)
st = [x for x in admin.call('GET', '/das/holder/docs', expect=200)
      if x['doc_no'] == SB][0]
check(st['unacknowledged'] == 0, '三个接收方都已确认')
print('ok   第 4 步 发布对象名单与接收确认可追溯')

# ================= 撤销与召回（第 7～11 步）=================
r = de.call('POST', '/das/holder/revocations',
            {'doc_no': SB, 'revision': 'R1', 'trigger_kind': 'DOC_ERROR',
             'trigger_note': 'x'}, expect=403)
check('适航管理负责人' in str(r), '启动撤销归适航管理负责人（第 7 步）')
rev = awm.call('POST', '/das/holder/revocations',
               {'doc_no': SB, 'revision': 'R1', 'trigger_kind': 'CAAC_FOUND_WRONG',
                'trigger_note': '局方认定该服务通告引用的分类结论有误，实为大改'},
               expect=201)
check(rev['stop_use_recipients'] == 3,
      '止用通知的收件人自动取自分发清单（第 8 步：向**所有已接收该资料的对象**发出）')
print('ok   第 7 步 启动撤销；止用通知收件人取自分发清单')

# ================= 反例 123：撤销期间不得继续用 =================
r = dcm.call('POST', '/das/holder/docs/%s/R1/distribution' % SB,
             {'recipient': '又一个运营人', 'recipient_kind': 'OPERATOR'}, expect=400)
check('撤销期间' in str(r) and '不得继续用于改装、生产或放行' in str(r),
      '**撤销期间的资料不得再分发**（第 7 章）')
INQ = 'TI-%s' % STAMP[-6:]
awm.call('POST', '/das/holder/inquiries',
         {'inquiry_no': INQ, 'received_from': '东海航空机务',
          'question': '插座安装扭矩是多少', 'category': 'USAGE'}, expect=201)
r = awm.call('POST', '/das/holder/inquiries/%s/answer' % INQ,
             {'answer': '按服务通告第 3 节', 'within_approved_data': True,
              'based_on_doc_no': SB, 'based_on_revision': 'R1'}, expect=400)
check('撤销流程中' in str(r), '**撤销期间的资料不得引为答复依据**')
print('ok   123 撤销期间: 不得再分发, 也不得引为答复依据')

# ================= 反例 122：撤销只能由原批准人或其上级 =================
msg = reason("""UPDATE das_doc_revocation SET decided_by=(SELECT id FROM app_user
                 WHERE username=%s), decided_on=current_date,
                 revoked_approval_ref='x', revocation_reason='y'
                WHERE id=%s""", (de.username, rev['id']))
check('DCMS-INV-122' in msg and '原批准人或其上级' in msg,
      '**编制人不得自行作废**（第 7 章）')
check('没有组织层级表' in msg and 'A7' in msg,
      '拒绝理由写明了"其上级"在系统里怎么判, 以及为什么不造层级表')
r = de.call('POST', '/das/holder/revocations/%s/decision' % rev['id'],
            {'revoked_approval_ref': 'x', 'revocation_reason': 'y'}, expect=403)
check('原批准人或其上级' in str(r), '服务层同样拦，并给出能看懂的理由')
print('ok   122 撤销只能由原批准人或其上级；"其上级"的解释写进了拒绝理由')

# ================= 第 8～9 步 =================
r = dcm.call('POST', '/das/holder/revocations/%s/stop-use' % rev['id'],
             {'scope': '  ', 'interim_measures': 'x'}, expect=400)
check('暂停使用的范围' in str(r), '止用通知不写范围被拒')
dcm.call('POST', '/das/holder/revocations/%s/stop-use' % rev['id'],
         {'scope': 'B-1415 全部已发出的 R1 版，暂停据此改装',
          'interim_measures': '已改装的暂不放行，待更正资料发出后复查'}, expect=201)
r = awm.call('POST', '/das/holder/revocations/%s/impact' % rev['id'],
             {'affected_scope': 'B-1415 共 2 架', 'retrofit_done_note': '1 架已改装',
              'unsafe_condition': True}, expect=400)
check('UG-DAP-12' in str(r) and '断在这里' in str(r),
      '判为不安全状态却不给事件报告编号被拒（CCAR-21.5 那条链的入口）')
awm.call('POST', '/das/holder/revocations/%s/impact' % rev['id'],
         {'affected_scope': 'B-1415 共 2 架，序列号 001～002',
          'retrofit_done_note': '1 架已改装，1 架未开始',
          'unsafe_condition': False}, expect=201)
print('ok   第 8～9 步 止用通知要写范围与临时措施；判不安全就要报事件')

# ================= 反例 122：止用未全部确认不得结案 =================
r = awm.call('POST', '/das/holder/revocations/%s/decision' % rev['id'],
             {'revoked_approval_ref': '  ', 'revocation_reason': 'y'}, expect=400)
check('批准编号' in str(r), '撤销决定不注明被撤销的批准编号被拒（第 10 步）')
awm.call('POST', '/das/holder/revocations/%s/decision' % rev['id'],
         {'revoked_approval_ref': '%s R1（批准于 %s）' % (SB, TODAY),
          'revocation_reason': '局方认定引用的分类结论有误，实为大改'}, expect=201)
st = [x for x in admin.call('GET', '/das/holder/docs', expect=200)
      if x['doc_no'] == SB][0]
check(st['revoked'], '签署撤销决定后资料标注为已撤销并回收（第 10 步）')
pending = [x for x in admin.call('GET', '/das/holder/gaps',
                                 expect=200)['revocation_pending_ack']
           if x['id'] == rev['id']]
check(pending and pending[0]['unacknowledged'] == 3,
      '止用通知还有 3 个未确认，列在盯办清单里')
r = awm.call('POST', '/das/holder/revocations/%s/closure' % rev['id'],
             {'caac_report_ref': '报局方 UG-2026-%s' % STAMP[-3:],
              'similar_review_note': '同类服务通告 2 份已排查，无相同问题'}, expect=400)
check('不得认为撤销已完成' in str(r), '**止用通知未全部确认前不得结案**（第 7 章）')
check('那边还在用' in str(r), '拒绝理由说明了后果')
check('东海航空' in str(r), '报出还差谁')
acks = db_query("""SELECT a.id FROM das_stop_use_ack a WHERE a.revocation_id=%s
                    ORDER BY a.id""", (rev['id'],))
dcm.call('POST', '/das/holder/stop-use/%s/chase' % acks[0]['id'], expect=201)
ch = dcm.call('POST', '/das/holder/stop-use/%s/chase' % acks[0]['id'], expect=201)
check(ch['chase_count'] == 2, '未确认的逐一跟催，次数记得下来（第 8 步）')
for a in acks:
    dcm.call('POST', '/das/holder/stop-use/%s/ack' % a['id'],
             {'ack_ref': '止用回函 %s-%s' % (a['id'], STAMP)}, expect=201)
r = awm.call('POST', '/das/holder/revocations/%s/closure' % rev['id'],
             {'caac_report_ref': 'x', 'similar_review_note': '  '}, expect=400)
check('同类排查' in str(r), '不做举一反三检查被拒（第 11 步）')
cl = awm.call('POST', '/das/holder/revocations/%s/closure' % rev['id'],
              {'caac_report_ref': '报局方 UG-2026-%s' % STAMP[-3:],
               'similar_review_note': '同类服务通告 2 份已排查，无相同问题'}, expect=201)
check(cl['closed_on'], '全部确认 + 报局方 + 同类排查后方可结案')
msg = reason("""UPDATE das_doc_revocation SET closed_on=current_date WHERE id=%s""",
             (rev['id'],))
print('ok   122 止用全部确认 + 报局方 + 举一反三, 三件齐备才算撤销完成')

# ================= 第 13 步：技术询问三类 =================
INQ2 = 'TI2-%s' % STAMP[-6:]
awm.call('POST', '/das/holder/inquiries',
         {'inquiry_no': INQ2, 'received_from': '某改装单位',
          'question': '能否把插座位置再往前移 50mm', 'category': 'DEVIATION'},
         expect=201)
r = awm.call('POST', '/das/holder/inquiries/%s/answer' % INQ2,
             {'answer': '可以，按现场情况处理', 'within_approved_data': True},
             expect=400)
check('不得以答复代替更改' in str(r), '**偏离类询问不得以答复代替更改**（第 13 步 b）')
check('绕过设计更改分类' in str(r), '拒绝理由说明了后果')
r = awm.call('POST', '/das/holder/inquiries/%s/answer' % INQ2,
             {'answer': 'x', 'within_approved_data': True, 'change_no': 'CHG-NOPE'},
             expect=404)
check('分类结论' in str(r), '指向一条不存在或没有分类结论的更改被拒')
awm.call('POST', '/das/holder/inquiries/%s/answer' % INQ2,
         {'answer': '该偏离已按 UG-DAP-06 立更改单 %s，分类为小改，另发更正资料' % CHG,
          'within_approved_data': True, 'change_no': CHG}, expect=201)
INQ3 = 'TI3-%s' % STAMP[-6:]
awm.call('POST', '/das/holder/inquiries',
         {'inquiry_no': INQ3, 'received_from': '东海航空',
          'question': '插座在使用中发热异常', 'category': 'DEFECT'}, expect=201)
r = awm.call('POST', '/das/holder/inquiries/%s/answer' % INQ3,
             {'answer': '已知悉', 'within_approved_data': True}, expect=400)
check('UG-DAP-12' in str(r) and '48 小时' in str(r),
      '故障缺陷类须转 UG-DAP-12 并记明事件报告编号')
awm.call('POST', '/das/holder/inquiries/%s/answer' % INQ3,
         {'answer': '已按 UG-DAP-12 登记并评估', 'within_approved_data': True,
          'occurrence_ref': 'OCC-2026-%s' % STAMP[-4:]}, expect=201)
# 超出已批准数据范围不得发出
INQ4 = 'TI4-%s' % STAMP[-6:]
awm.call('POST', '/das/holder/inquiries',
         {'inquiry_no': INQ4, 'received_from': '某运营人', 'question': 'x',
          'category': 'USAGE'}, expect=201)
r = awm.call('POST', '/das/holder/inquiries/%s/answer' % INQ4,
             {'answer': 'x', 'within_approved_data': False}, expect=400)
check('不得超出已批准的设计数据范围' in str(r), '核对为"超出"时不得发出')
# 非 AWM 答复须有书面指定
r = de.call('POST', '/das/holder/inquiries/%s/answer' % INQ4,
            {'answer': 'x', 'within_approved_data': True, 'based_on_doc_no': ICA,
             'based_on_revision': 'R1'}, expect=403)
check('书面指定' in str(r), '非在任适航管理负责人答复须写明书面指定的依据')
print('ok   124 三类各走各的路；偏离类不得以答复代替更改；超范围不得发出')

# ================= 第 15 步：相似性评估 =================
r = awm.call('POST', '/das/holder/similarity',
             {'source_kind': 'CAAC_BULLETIN', 'source_ref': 'CAAC-B-2026-01',
              'source_summary': '某型客舱电源插座过热', 'comparison_note': '已比较六项',
              'verdict': 'RELATED', 'rationale': '系统原理与失效模式相同'}, expect=400)
check('适航性的影响' in str(r), '判为相关却不评估对适航性的影响被拒')
awm.call('POST', '/das/holder/similarity',
         {'source_kind': 'CAAC_BULLETIN', 'source_ref': 'CAAC-B-2026-%s' % STAMP[-3:],
          'source_summary': '某型客舱电源插座过热',
          'comparison_note': '结构形式、材料、工艺、系统原理、使用环境、失效模式逐项比较，'
                             '其中系统原理与失效模式相同',
          'verdict': 'RELATED', 'rationale': '同为 28V 直流插座，同一失效模式',
          'airworthiness_impact': '可能影响客舱安全，已评估为需发服务通告',
          'action_taken': '拟发服务通告'}, expect=201)
# 判"不相关"也必须记
awm.call('POST', '/das/holder/similarity',
         {'source_kind': 'EXTERNAL_EVENT', 'source_ref': 'EXT-%s' % STAMP[-3:],
          'source_summary': '某型起落架作动筒失效',
          'comparison_note': '结构形式、材料、系统原理均不同',
          'verdict': 'UNRELATED', 'rationale': '本单位不设计起落架系统'}, expect=201)
sim = admin.call('GET', '/das/holder/similarity', expect=200)
check([x for x in sim if x['verdict'] == 'UNRELATED'],
      '**判「不相关」的也在台账里**——原文：评估结论无论是否相关均须记录')
print('ok   第 15 步 相似性评估：判相关要评估影响，判不相关也必须记')

# ================= 第 16 步：权益转让与法规 30 天 =================
reg = {r['code']: r for r in admin.call('GET', '/das/deadlines', expect=200)}
check('L11.STC_TRANSFER_NOTICE' in reg,
      '**30 天在时限引擎里**（原文标了「（法规）」），不是本模块的一个字段')
p = reg['L11.STC_TRANSFER_NOTICE']
check(float(p['ceiling_value']) == 30 and p['ceiling_unit'] == 'DAY',
      '上限 30 天，受规章修订闸门保护')
check(p['constraint_source'] == 'STATUTORY' and '规章修订' in p['change_authority'],
      '约束来源是法规，变更权限是规章修订——内部决定改不动它')
tr = am.call('POST', '/das/holder/transfers',
             {'certificate_no': CERT, 'transfer_kind': 'TRANSFER',
              'counterparty': '某受让单位', 'counterparty_address': '某市某区',
              'scope_note': 'B737-800 客舱电源改装权益全部',
              'condition_check_note': '已核对 CCAR-21.116 的可转让条件，均满足',
              'continued_airworthiness_handover': '持续适航文件与 ICA 修订责任一并移交',
              'agreement_ref': '转让协议 %s' % STAMP,
              'effective_on': str(TODAY - dt.timedelta(days=40))}, expect=201)
check(tr['caac_notice_due_on'] == str(TODAY - dt.timedelta(days=10)),
      '返回里给出法规 30 天的到期日')
due = [x for x in admin.call('GET', '/das/holder/gaps',
                             expect=200)['transfer_notice_due']
       if x['id'] == tr['id']]
check(due and due[0]['pending'] and due[0]['days_late'] == 10,
      '未通知且已逾期 10 天，列在盯办清单里')
nt = awm.call('POST', '/das/holder/transfers/%s/notice' % tr['id'],
              {'notice_ref': '致局方函 UG-2026-%s' % STAMP[-3:],
               'ack_ref': '局方收文回执'}, expect=201)
check(nt['late_days'] == 10 and '已逾期' in nt['note'],
      '通知时如实报出逾期天数，并指回那条法规时限')
print('ok   第 16 步 30 天是法规时限（在引擎里）；逾期如实算出来')

# ================= 第 17 步：书面许可七项必填 =================
lic = {'certificate_no': CERT, 'licensee': '某改装单位',
       'products_and_scope': 'B737-800，仅客舱电源插座改装',
       'validity_note': '自签署起 3 年，可续',
       'controlled_data_note': '受控资料经资料管理负责人发放，修订 10 日内传递',
       'support_note': '持续适航支持由本单位承担，响应 5 个工作日',
       'config_feedback_note': '每次改装后 15 日内反馈构型记录',
       'termination_note': '任一方提前 60 日书面通知可终止',
       'agreement_ref': '许可协议 %s' % STAMP,
       'acceptability_note': '已对照 CCAR-21.119 确认局方可接受性'}
for miss in ('controlled_data_note', 'config_feedback_note', 'termination_note'):
    r = am.call('POST', '/das/holder/licences', dict(lic, **{miss: '  '}), expect=400)
    check('说不清许到哪为止' in str(r), '书面许可缺「%s」被拒（第 17 步逐条列明）' % miss)
am.call('POST', '/das/holder/licences', lic, expect=201)
print('ok   第 17 步 书面许可：原文逐条列明的各项全部必填')

# ================= 反例 125：不得删除 =================
check('DCMS-INV-125' in reason(
    "DELETE FROM das_published_doc WHERE doc_no=%s", (SB,)),
    '125 发布资料不得删除——它发给过运营人')
check('DCMS-INV-125' in reason(
    "DELETE FROM das_doc_revocation WHERE id=%s", (rev['id'],)),
    '125 撤销记录不得删除')
check('DCMS-INV-125' in reason(
    "DELETE FROM das_stc_transfer WHERE id=%s", (tr['id'],)),
    '125 权益转让不得删除——通知报给过局方')
check('DCMS-INV-125' in reason(
    "DELETE FROM das_stc_licence WHERE agreement_ref=%s", ('许可协议 %s' % STAMP,)),
    '125 书面许可不得删除——许可给过实施方')
print('ok   125 它们都对外生效过：删掉等于那件事从未发生过')

print('\nPASS M8 证后与对外发布: 局方批准不得加注本单位声明、122 撤销只能由原批准人或'
      '其上级且止用未全确认不得结案、123 撤销期间不得继续用、124 偏离类不得以答复代替'
      '更改、第 15 步判不相关也要记、第 16 步 30 天在时限引擎里、125 不得删除')
