"""线下审批与证据上传（判据 B1、N-总、N1）。

2026-10-02 的业务决策：**系统实现不了的走线下审批，证据资料上传系统即可。**

这个决定把**判定过程**搬到了线下（9 项判据逐条录进系统对 5～8 人的编制过重，而过重的
负担不会让符合性变好，只会让人绕过系统），但把**结论与证据**留在了系统里。所以本用例的
重点是证明：走线下这条路**不是免检通道**——它有自己的三道门，缺一不算。

覆盖：
  B1   人工替代流程六项缺一不得登记；线下责任人要写岗位不写部门
  117  未经责任经理批准的替代流程不得据以开展业务；停用后仍挂上来的进越界清单
  115  线下审批记录必须有证据文件（没有上传的证据就不成立）
  N1   须写明纸质原件存放位置；补录时间与实际审批日期分开保存，补录不得早于审批
  N-总 复核前不具有权威性——未复核时 M4 的闸门照旧拦住
  116  线下审批与证据不得删改；只有"复核人／复核日期"可以从空填上
  两条路 判据一条不录、走线下即可出分类结论；两边都没有则拒
  可见性 补录人自己复核不阻断，但进清单交独立监督核对

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-offline.py <credentials.json>
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import BASE, STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
TODAY = dt.date.today()
PROC = 'M4.CRITERIA.%s' % STAMP
CHG = 'CHG-OFF-%s' % STAMP
CHG2 = 'CHG-OFF2-%s' % STAMP
PDF = b'%PDF-1.4\n% UG-DAF-02 scan placeholder\n' + b'0' * 2048


def upload(client, path, fields, filename, content, expect=None):
    """发一个 multipart 请求。dcms_http.Client.upload 是给文件版次附件用的, 字段不一样。"""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        if v is None:
            continue
        parts.append(
            ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
             % (boundary, k, v)).encode())
    parts.append(
        ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
         'Content-Type: application/pdf\r\n\r\n' % (boundary, filename)).encode())
    parts.append(content)
    parts.append(('\r\n--%s--\r\n' % boundary).encode())
    body = b''.join(parts)
    req = urllib.request.Request(
        BASE + path, method='POST', data=body,
        headers={'Authorization': 'Bearer ' + client.token,
                 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            data, status = res.read(), res.status
    except urllib.error.HTTPError as e:
        data, status = e.read(), e.code
    if expect is not None and status != expect:
        raise AssertionError('POST %s: expected %s, got %s: %r'
                             % (path, expect, status, data[:400]))
    return json.loads(data) if data else None


def reason(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error as e:
        return str(e)
    return ''


# ================= 造人 =================
de = make_user(admin, 'ENGINEER', 'ofde')
am = make_user(admin, 'CONFIGURATION_MANAGER', 'ofam')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'ofawm')
for u, tag in ((de, 'OFDE'), (am, 'OFAM'), (awm, 'OFAWM')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
for u, pos in ((de, 'DE'), (am, 'AM'), (awm, 'AWM')):
    admin.call('POST', '/das/appointments',
               {'user_id': u.id, 'position_code': pos, 'kind': 'FORMAL'}, expect=201)

# ================= 判据 B1：六项缺一不可 =================
six = {'offline_owner': '适航管理负责人（召集相关专业评审）',
       'forms_and_ledger': 'UG-DAF-02 及内部补充分类检查表；纸质台账存适航管理办公室，'
                           '资料管理负责人保管',
       'reconcile_frequency': '每月核对线下台账与系统的分类记录是否一致',
       'backfill_rule': '评审结论签署后 3 个工作日内补录；补录由资料管理负责人复核',
       'exit_condition': 'M4 判据录入上线并通过 N16 六项验证后停用；'
                         '历史分类的扫描件保留，不回填判据行',
       'self_assessment_text': '9 项判据的逐条判定由线下评审执行，系统保存评审结论与'
                               '签署件扫描，未由系统实现逐条判定'}
base = {'code': PROC, 'scope_note': 'UG-DAW-010 第 2 章 9 项判据的逐条判定与依据',
        'requirement_ref': 'UG-DAW-010 第 3 章第 3 步'}
for miss in ('reconcile_frequency', 'exit_condition', 'self_assessment_text'):
    r = admin.call('POST', '/das/offline/processes',
                   dict(base, **{**six, miss: '  '}), expect=400)
    check('缺一不得开展对应业务' in str(r), 'B1 六项缺「%s」被拒' % miss)
r = admin.call('POST', '/das/offline/processes',
               dict(base, **{**six, 'offline_owner': '技术部'}), expect=409)
check('ck_dmp_owner' in str(r) or '约束' in str(r),
      '线下责任人写成部门被拒（判据 B1：具体岗位，不写部门）')
print('ok   B1 六项缺一不得登记；线下责任人要写岗位不写部门')

admin.call('POST', '/das/offline/processes', dict(base, **six), expect=201)
ps = {p['code']: p for p in admin.call('GET', '/das/offline/processes', expect=200)}
check(ps[PROC]['state'] == 'DRAFT', '刚登记的替代流程是 DRAFT —— 还不能据以开展业务')
print('ok   登记完还不能用：判据 B1 要的是**已批准的**流程')

# ================= 一条更改，判据一条都不录 =================
de.call('POST', '/das/changes',
        {'change_no': CHG, 'title': '标牌文字调整', 'purpose': '客户要求',
         'content': '调整标牌文字，不涉及限制值', 'products': 'B737-800 客舱',
         'drawings': 'DWG-OFF R1'}, expect=201)
de.call('PUT', '/das/changes/%s/impact' % CHG,
        {'impact_list': '受影响件号 1 个', 'impact_baseline_ref': '基线 BL-2026-07',
         'ad_checked': True, 'cert_basis_checked': True}, expect=200)

# ================= 反例 104：两边都拿不出依据 =================
r = de.call('POST', '/das/changes/%s/classification' % CHG,
            {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
             'conclusion_reason': '不触及任何判据'}, expect=400)
check('两条路选一条' in str(r), '两边都没有依据时，拒绝理由给出两条路')
check('das/offline/approvals' in str(r) and CHG in str(r),
      '并给出线下那条路具体怎么走（含对象类型与对象编号）')
check('已复核' in str(r) and '证据文件' in str(r),
      '说明线下那条路的两个硬条件')
print('ok   104 拒的不是"没录进系统"，是"两边都拿不出判定依据"')

# ================= 反例 117：引用未批准的替代流程 =================
fields = {'object_type': 'DAS_DESIGN_CHANGE', 'object_key': CHG,
          'manual_process_code': PROC, 'subject': 'UG-DAW-010 9 项判据线下评审',
          'approver': '适航管理负责人 张三', 'approved_on': str(TODAY),
          'form_ref': 'UG-DAF-02-2026-%s' % STAMP[-4:],
          'conclusion': '9 项判据均不触及，判为小改',
          'original_location': '适航管理办公室 2026 年卷 第 7 件'}
r = upload(awm, '/das/offline/approvals', fields, 'daf02.pdf', PDF, expect=400)
check('尚未经责任经理批准' in str(r), '引用未批准的替代流程被拒（判据 B1）')
check('既不在系统里、也不在一个受控的线下流程里' in str(r),
      '拒绝理由说明了后果')
r = awm.call('POST', '/das/offline/processes/%s/approval' % PROC, {}, expect=403)
check('责任经理' in str(r), '批准替代流程须在任责任经理，适航管理负责人不行')
am.call('POST', '/das/offline/processes/%s/approval' % PROC, {}, expect=201)
ps = {p['code']: p for p in admin.call('GET', '/das/offline/processes', expect=200)}
check(ps[PROC]['state'] == 'IN_FORCE', '批准后转 IN_FORCE')
print('ok   117 未批准的替代流程不得据以开展业务；批准归责任经理')

# ================= 反例 115／N1：证据与原件 =================
r = upload(awm, '/das/offline/approvals', fields, 'empty.pdf', b'', expect=400)
check('证据资料上传系统' in str(r) and '比没有记录更坏' in str(r),
      '空文件被拒，并说明为什么"只有结论没有扫描件"更坏')
r = upload(awm, '/das/offline/approvals',
           dict(fields, original_location='  '), 'daf02.pdf', PDF, expect=400)
check('原件存放位置' in str(r), '不写纸质原件存放位置被拒（判据 N1：扫描件与原件一并归档）')
r = upload(awm, '/das/offline/approvals',
           dict(fields, approved_on=str(TODAY + dt.timedelta(days=7))),
           'daf02.pdf', PDF, expect=400)
check('将来' in str(r), '审批日期是将来被拒：补录的是已经发生的事')
msg = reason("""INSERT INTO das_offline_approval
                    (object_type, object_key, subject, approver, approved_on, form_ref,
                     conclusion, recorded_by)
                SELECT 'X', 'Y', 'Z', 'W', current_date + 7, 'F', 'C', id
                  FROM app_user WHERE username=%s""", (admin.username,))
check('ck_doa_order' in msg, '数据库层同样拦住补录时间早于审批日期（ck_doa_order）')
print('ok   115／N1 没有上传的证据不成立；原件位置必填；补录不得早于审批')

# ================= 正常登记：一个请求做完两件事 =================
a1 = upload(awm, '/das/offline/approvals', fields, 'UG-DAF-02 签署件.pdf', PDF,
            expect=201)
check(a1['evidence'] and a1['evidence'][0]['sha256'],
      '登记与上传是一个请求（DCMS-INV-115 不允许没有证据的记录存在）')
det = admin.call('GET', '/das/offline/approvals/%s' % a1['id'], expect=200)
check(det['evidence_files'] == 1 and det['reviewed'] is False, '已登记、尚未复核')
check(det['backfill_lag_days'] == 0, '补录滞后天数算得出来（判据 N1 要两个时间都留）')
print('ok   登记与上传一个请求做完：没有证据就没有记录')

# ================= N-总：复核前不具有权威性 =================
un = {x['id'] for x in admin.call('GET', '/das/offline/approvals/unreviewed',
                                  expect=200)}
check(a1['id'] in un, '未复核的进清单——这不是"待办"，是"还不能当依据用"')
r = de.call('POST', '/das/changes/%s/classification' % CHG,
            {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
             'conclusion_reason': '线下评审判为小改'}, expect=400)
check('两条路选一条' in str(r),
      '**未复核时 M4 的闸门照旧拦住**（判据 N-总：复核前不具有权威性）')
cov = admin.call('GET', '/das/offline/coverage', expect=200)
path = {x['change_no']: x for x in cov['change_path']}
check(path[CHG]['evidence_path'] == 'NEITHER',
      '未复核时这条更改在两条路上都算"拿不出依据"')
print('ok   N-总 复核前不具有权威性：未复核的线下记录过不了 M4 的闸门')

admin.call('POST', '/das/offline/approvals/%s/review' % a1['id'], {}, expect=201)
cov = admin.call('GET', '/das/offline/coverage', expect=200)
path = {x['change_no']: x for x in cov['change_path']}
check(path[CHG]['evidence_path'] == 'OFFLINE' and path[CHG]['offline_covered'],
      '复核后转为 OFFLINE 承接')
check(path[CHG]['criteria_in_system'] == 0,
      '系统内判据 0 行 —— 这正是"系统不用管那么细"的样子')
cl = de.call('POST', '/das/changes/%s/classification' % CHG,
             {'state': 'CLASSIFIED', 'major_minor': 'MINOR',
              'conclusion_reason': '标牌文字调整，9 项判据的逐条判定见线下评审记录 '
                                   + fields['form_ref'],
              'form_no': 'UG-DOA-OFF-%s-FL' % STAMP[-4:]}, expect=201)
check(cl['major_minor'] == 'MINOR', '**判据一条不录也能出分类结论——依据在线下**')
print('ok   两条路都走得通：判据录进系统，或线下审批＋证据上传＋复核')

# ================= 自评要点清的那个数 =================
cov = admin.call('GET', '/das/offline/coverage', expect=200)
by = {x['object_type']: x for x in cov['by_object_type']}
check(by['DAS_DESIGN_CHANGE']['approvals'] >= 1
      and PROC in by['DAS_DESIGN_CHANGE']['manual_processes'],
      '按对象类型汇总：哪几类业务靠线下承接、挂的是哪条替代流程')
check(all(x['evidence_path'] in ('IN_SYSTEM', 'OFFLINE', 'NEITHER')
          for x in cov['change_path']),
      '每条更改都说得出依据走的是系统内还是线下')
print('ok   自评那一栏有底稿：走线下不是问题，说不清哪些走了线下才是问题')

# ================= 可见性：补录人自己复核 =================
sr = {x['id'] for x in admin.call('GET', '/das/offline/self-reviewed', expect=200)}
check(a1['id'] not in sr, '本例由 admin 复核、awm 补录，不算自验自批')
a2 = upload(awm, '/das/offline/approvals',
            dict(fields, object_key=CHG2, form_ref='UG-DAF-02-2026-%sB' % STAMP[-4:],
                 subject='另一次线下评审'), 'daf02b.pdf', PDF, expect=201)
awm.call('POST', '/das/offline/approvals/%s/review' % a2['id'], {}, expect=201)
sr = {x['id'] for x in admin.call('GET', '/das/offline/self-reviewed', expect=200)}
check(a2['id'] in sr,
      '补录人自己复核**不阻断**但进清单 —— 5～8 人编制下避不开，但要看得见')
print('ok   自验自批列入可见性清单交独立监督核对（同判据 I5／I6 的处理）')

# ================= 反例 116：不得删改 =================
check('DCMS-INV-116' in reason(
    "UPDATE das_offline_approval SET conclusion='改一下' WHERE id=%s", (a1['id'],)),
    '116 线下审批的实质内容不得改写')
check('DCMS-INV-116' in reason(
    "DELETE FROM das_offline_approval WHERE id=%s", (a1['id'],)),
    '116 线下审批不得删除')
check('DCMS-INV-116' in reason(
    "UPDATE das_offline_evidence SET sha256=%s WHERE approval_id=%s",
    ('b' * 64, a1['id'])), '116 证据文件的摘要不得改写')
check('DCMS-INV-116' in reason(
    "DELETE FROM das_offline_evidence WHERE approval_id=%s", (a1['id'],)),
    '116 证据文件不得删除')
r = admin.call('POST', '/das/offline/approvals/%s/review' % a1['id'], {}, expect=400)
check('已复核' in str(r), '不重复复核')
print('ok   116 它替代的是系统里的审批，所以一样不可抹改')

# ================= 停用后仍在用 =================
am.call('POST', '/das/offline/processes/%s/retirement' % PROC,
        {'on': str(TODAY - dt.timedelta(days=1))}, expect=201)
ps = {p['code']: p for p in admin.call('GET', '/das/offline/processes', expect=200)}
check(ps[PROC]['state'] == 'RETIRED', '停用后转 RETIRED')
r = upload(awm, '/das/offline/approvals',
           dict(fields, object_key='CHG-AFTER-%s' % STAMP,
                form_ref='UG-DAF-02-2026-%sC' % STAMP[-4:]),
           'daf02c.pdf', PDF, expect=400)
check('已于' in str(r) and '退出条件' in str(r),
      '停用之后再挂线下审批被拒（判据 B1 第六项：退出条件）')
over = admin.call('GET', '/das/offline/coverage', expect=200)['process_overrun']
check([x for x in over if x['code'] == PROC],
      '已发生的越界使用列出来 —— 系统和线下两套并行时两套说法迟早不一致')
print('ok   B1 第六项：退出条件要一刀切清，停用后的越界使用看得见')

print('\nPASS 线下审批与证据上传: B1 六项、117 须已批准、115 没有证据就不成立、'
      'N1 原件位置与两个时间、N-总 复核前不具有权威性、116 不得抹改、'
      '两条路二者之一且说得清哪条走了线下')
