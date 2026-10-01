"""上线前六项控制验证与投用闸门（基础能力，判据 N16／N6／N15／B2）。

按判据 I-总 以反例为主。这块最容易做错的是**把它实现成六个勾**——六项写成六个复选框，
谁都签得下去，而三个月后没人能判断当时到底测了什么。所以用例的重点是证明每一道
"填满但不真"的写法都真的被拦住。

覆盖：
  N16  六项（按机制展开后 7 项）未全部在**本构建**上 PASS，投用被拒且报出还差哪几项
  087  没有构建号不得投用；投用的构建必须与批次当前登记的构建一致
  087  取每项**最近一次**结果——后一次 FAIL 推翻前一次 PASS
  088  容器项"冻结"上不得记结果（本系统里它是版次冻结与记录冻结两个机制）
  N15  备份恢复须抽查 ≥5 份；三项核对须各自给结论；有一项为否不得判 PASS
  N6   验证失败即为不符合项——FAIL 必须挂 NCR
  089  验证记录与投用记录不得改写、不得删除
  090  控制项不得删除
  构建变更后之前的验证全部失效（das_release_stale），且闸门随之收紧
  执行人要么给账号、要么显式声明外部——留空账号就看不见，而清单还是干净的

另：N6 的两个周期参数（备份每日、恢复验证每季度，判据 Q2 取较严）须在时限引擎里。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-release.py <credentials.json>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
BUILD = 'rc2.45-ci-' + STAMP

# 【本用例跑在 B3（第三批）上, 并在开头把它的构建号清空】
# 两张表都是只追加的: 验证记录不得改写删除（089）, 投用记录按(批次,构建)唯一且同样
# 不得删除。所以同一个库跑第二遍, 条数必然和第一遍不同, 而"没有构建号"这个起点
# 只在全新库上成立。写成绝对条数就只在新库上绿 —— 而 CI 每次都是新库,
# 于是它会一直绿, 绿的原因不是规则成立, 是库是新的。
# 构建号清空是配置复位（das_release_batch 上没有只追加约束, 它是配置不是证据）,
# 本次构建号带 STAMP 唯一, 因此本次产生的记录与历史记录互不影响。
db_execute("UPDATE das_release_batch SET build_ref=NULL WHERE code='B3'")


def rejected(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False


def reason(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error as e:
        return str(e)
    return ''


# ================= 六项控制的内容 =================
items = admin.call('GET', '/das/release/items', expect=200)
leaves = [i for i in items if not i['is_container']]
containers = [i for i in items if i['is_container']]
check(len({i['seq'] for i in items}) == 6, 'UG-DAW-005 第 1 章的六项都在（父项编号与源文件一致）')
check(len(containers) == 1 and containers[0]['code'] == 'FREEZE',
      '「冻结」是容器项：本系统里版次冻结与记录冻结是两个机制')
check(len(leaves) == 7, '按机制展开后共 7 项要分别给证据（%d）' % len(leaves))
check(all(i['verify_content'].strip() for i in leaves),
      '每一项都写明「怎样才算验过」，不是一句「已测试」')
check(all(i['criteria_ref'] for i in leaves), '每一项都指回具体判据')
bk = [i for i in leaves if i['min_sample']]
check(len(bk) == 1 and bk[0]['min_sample'] == 5,
      '备份恢复须抽查至少 5 份（判据 N15），且做成参数不写死')
print('ok   六项（展开为 7 项）各有可核验的验证内容与判据出处:')
for i in sorted(leaves, key=lambda x: (x['seq'], x['code'])):
    print('       %d. %-17s %-10s %s' % (i['seq'], i['code'], i['name_cn'],
                                         i['criteria_ref']))

mm = admin.call('GET', '/das/release/oversight', expect=200)
check(mm['item_mismatch'] == [], '控制项层级与结果落点一致（平时应为空）')

# ================= 批次 =================
batches = admin.call('GET', '/das/release/batches', expect=200)
codes = {b['code'] for b in batches}
check({'BASE', 'B1', 'B2', 'B3'} <= codes, '设计输入第 8.6 节的四个批次都在')
b1 = [b for b in batches if b['code'] == 'B1'][0]
check('顺序不可颠倒' in b1['precondition'], 'B1 的投用前提保留了「顺序不可颠倒」')
check('独立监督覆盖' in [b for b in batches if b['code'] == 'B2'][0]['precondition'],
      'B2 的前提是第一批已投用且经过一次独立监督覆盖')
print('ok   四个批次及其投用前提按第 8.6 节登记')

# ================= 闸门按岗位任命 =================
plain = make_user(admin, 'CONFIGURATION_MANAGER', 'rlplain')
r = plain.call('PUT', '/das/release/batches/B3/build', {'build_ref': BUILD}, expect=403)
check('适航管理负责人' in str(r), '无任命者不得登记构建号')
r = plain.call('POST', '/das/release/batches/B3/verifications',
               {'item_code': 'LOGGING', 'method': 'x', 'commit_ref': 'a',
                'batch_ref': 'b', 'result': 'PASS', 'verified_by': 'x',
                'verifier_is_external': True}, expect=403)
check('资料管理负责人' in str(r), '记录验证结果的门槛写明了两个归口岗位')

awm = make_user(admin, 'CONFIGURATION_MANAGER', 'rlawm')
dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'rldcm')
for u, tag in ((awm, 'RLAWM'), (dcm, 'RLDCM')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
admin.call('POST', '/das/appointments',
           {'user_id': awm.id, 'position_code': 'AWM', 'kind': 'FORMAL'}, expect=201)
admin.call('POST', '/das/appointments',
           {'user_id': dcm.id, 'position_code': 'DCM', 'kind': 'FORMAL'}, expect=201)
print('ok   两道门槛不同高: 记结果要 AWM 或 DCM; 登记构建号与投用只有 AWM')

# ================= 没有构建号不得验证、不得投用 =================
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             {'item_code': 'LOGGING', 'method': '反例用例', 'commit_ref': 'abc1234',
              'batch_ref': 'CI-' + STAMP, 'result': 'PASS', 'verified_by': 'x',
              'verifier_is_external': True}, expect=400)
check('构建号' in str(r), '没登记构建号时不让记验证——否则这条记录指向哪一版无从判断')
r = awm.call('POST', '/das/release/batches/B3/commissioning', {}, expect=400)
check('NO_BUILD' in str(r) or '未登记' in str(r), '没有构建号不得投用')
print('ok   构建号是前提: 没有它, "上线前已验证"指向的是哪一版无人知道')

awm.call('PUT', '/das/release/batches/B3/build', {'build_ref': BUILD}, expect=200)
rd = admin.call('GET', '/das/release/readiness?batch=B3', expect=200)
check(len(rd) == 7 and all(x['state'] == 'NOT_VERIFIED' for x in rd),
      '登记构建号后 7 项全部为未验证')

# ================= 反例 087: 一项都没验就投用 =================
r = awm.call('POST', '/das/release/batches/B3/commissioning', {}, expect=400)
for name in ('身份', '授权', '独立性', '冻结·版次', '冻结·记录', '日志', '备份恢复'):
    check(name in str(r), '拒绝理由逐项报出还差「%s」' % name)
print('ok   N16 六项未验不得投用, 且报出的是"还差哪几项"而不是"不满足条件"')

# ================= 反例 088: 容器项上记结果 =================
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             {'item_code': 'FREEZE', 'method': '冻结功能已测试', 'commit_ref': 'abc1234',
              'batch_ref': 'CI-' + STAMP, 'result': 'PASS',
              'verified_by': '适航管理负责人 张三', 'verifier_is_external': True},
             expect=400)
check('两个机制' in str(r) or '机制' in str(r),
      '「冻结」上不得笼统记结果, 并说明它在本系统里是几个机制')
check('版次' in str(r) and '记录' in str(r), '拒绝理由点明是哪两个机制')
msg = reason("""INSERT INTO das_release_verification
                    (batch_code, item_code, build_ref, method, commit_ref, batch_ref,
                     result, verified_by, verifier_is_external)
                VALUES ('B3','FREEZE',%s,'x','a','b','PASS','x',true)""", (BUILD,))
check('DCMS-INV-088' in msg, '绕过服务层直写数据库同样被拦（088）')
print('ok   088 只测一个机制而记"冻结已验证"是假符合, 两层都拦')

# ================= 反例: 执行人既不给账号也不声明外部 =================
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             {'item_code': 'LOGGING', 'method': '反例用例', 'commit_ref': 'abc1234',
              'batch_ref': 'CI-' + STAMP, 'result': 'PASS', 'verified_by': '张三'},
             expect=400)
check('外部' in str(r) and '形同虚设' in str(r),
      '留空账号又不声明外部被拒, 并说明理由: 不填账号就看不见, 而清单还是干净的')
msg = reason("""INSERT INTO das_release_verification
                    (batch_code, item_code, build_ref, method, commit_ref, batch_ref,
                     result, verified_by)
                VALUES ('B3','LOGGING',%s,'x','a','b','PASS','x')""", (BUILD,))
check('ck_drv_verifier' in msg, '数据库层同样要求二者之一（ck_drv_verifier）')
print('ok   执行人要么给账号、要么显式声明外部 —— 空格不算一句明话')

# ================= 反例 N6: 失败却不挂不符合项 =================
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             {'item_code': 'LOGGING', 'method': '反例用例', 'commit_ref': 'abc1234',
              'batch_ref': 'CI-' + STAMP, 'result': 'FAIL',
              'verified_by': 'x', 'verifier_is_external': True}, expect=400)
check('不符合项' in str(r), '验证失败即为不符合项（判据 N6／N15）, FAIL 必须挂 NCR')
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             {'item_code': 'LOGGING', 'method': '反例用例', 'commit_ref': 'abc1234',
              'batch_ref': 'CI-' + STAMP, 'result': 'FAIL', 'ncr_id': 99999999,
              'verified_by': 'x', 'verifier_is_external': True}, expect=404)
print('ok   N6 失败即不符合项: 只记一句"失败"然后没有下文, 失败就只是一行字')

# ================= 反例 N15: 备份恢复的抽查 =================
base = {'item_code': 'BACKUP_RESTORE', 'method': '还原到测试环境', 'commit_ref': 'abc1234',
        'batch_ref': 'CI-' + STAMP, 'result': 'PASS',
        'verified_by': '资料管理负责人 李四'}
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             dict(base, verified_by_user=dcm.id, sample_count=3,
                  content_ok=True, version_ok=True, signature_ok=True), expect=400)
check('5 份' in str(r) and '能启动' in str(r),
      '抽查不足 5 份被拒, 并说明恢复验证不是"能启动"')
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             dict(base, verified_by_user=dcm.id, sample_count=5), expect=400)
check('三项' in str(r), '三项核对不给结论被拒')
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             dict(base, verified_by_user=dcm.id, sample_count=5, content_ok=True,
                  version_ok=True, signature_ok=False), expect=400)
check('签署记录' in str(r) and '不得写 PASS' in str(r),
      '签署记录核对为否却判 PASS 被拒, 并点明是哪一项为否')
r = dcm.call('POST', '/das/release/batches/B3/verifications',
             {'item_code': 'LOGGING', 'method': 'x', 'commit_ref': 'a',
              'batch_ref': 'b', 'result': 'PASS', 'verified_by': 'x',
              'verifier_is_external': True, 'sample_count': 5}, expect=400)
check('抽查' in str(r), '非抽查项填了抽查份数被拒')
print('ok   N15 抽查份数、三项结论、结论与结论不符三种写法全部拦住')

# ================= 正常登记七项 =================
for code in sorted(i['code'] for i in leaves if not i['min_sample']):
    dcm.call('POST', '/das/release/batches/B3/verifications',
             {'item_code': code, 'method': '按 verify_content 逐条跑反例用例',
              'commit_ref': 'abc1234', 'batch_ref': 'CI-' + STAMP, 'result': 'PASS',
              'verified_by': '适航管理负责人 张三', 'verified_by_user': awm.id,
              'evidence_ref': 'CI 日志 integration job'}, expect=201)
r = awm.call('POST', '/das/release/batches/B3/commissioning', {}, expect=400)
check('备份恢复' in str(r) and '身份' not in str(r),
      '只差备份恢复时, 报的就只有备份恢复')
dcm.call('POST', '/das/release/batches/B3/verifications',
         dict(base, verified_by_user=dcm.id, sample_count=5, content_ok=True,
              version_ok=True, signature_ok=True,
              sample_note='DWG-001 R2 / SPEC-004 R1 / QTP-002 R3 / UG-DAF-06-0012 / SB-2026-003'),
         expect=201)
rd = admin.call('GET', '/das/release/readiness?batch=B3', expect=200)
check(all(x['state'] == 'VERIFIED' for x in rd), '七项全部验过')
check(admin.call('GET', '/das/release/blockers?batch=B3', expect=200) == [],
      '阻断清单为空')
com = awm.call('POST', '/das/release/batches/B3/commissioning',
               {'approval_ref': '2026-10-01 投用签批单 ' + STAMP}, expect=201)
check(com['build_ref'] == BUILD, '投用记录绑定的是本构建')
r = awm.call('POST', '/das/release/batches/B3/commissioning', {}, expect=400)
check('已于' in str(r) and '新的构建号' in str(r),
      '同一构建不得重复投用, 并指出发了新版本该怎么做')
bb = [x for x in admin.call('GET', '/das/release/batches', expect=200)
      if x['code'] == 'B3'][0]
check(bb['current_in_service'], '当前构建已投用')
check(bb['commissionings'] >= 1, '投用次数按(批次,构建)累计（%d 次）' % bb['commissionings'])
print('ok   七项齐备方可投用, 投用记录绑定构建号')

# ================= 自验自批与外部执行都看得见 =================
ov = admin.call('GET', '/das/release/oversight', expect=200)
check([x for x in ov['self_verified'] if x['batch_code'] == 'B3'],
      '执行人同时是投用批准人的组合被列出（判据 I6 的同形问题, 不阻断）')
print('ok   自验自批列入可见性清单交独立监督核对, 不阻断（5～8 人编制下避不开）')

# ================= 反例 089／090: 不得抹改 =================
vid = db_query("""SELECT id FROM das_release_verification
                   WHERE batch_code='B3' AND item_code='LOGGING'
                   ORDER BY verified_at DESC LIMIT 1""")[0]['id']
check('DCMS-INV-089' in reason(
    "UPDATE das_release_verification SET result='FAIL' WHERE id=%s", (vid,)),
    '089 验证记录不得改写')
check('DCMS-INV-089' in reason(
    "DELETE FROM das_release_verification WHERE id=%s", (vid,)), '089 验证记录不得删除')
check('DCMS-INV-089' in reason(
    "DELETE FROM das_release_commissioning WHERE batch_code='B3'"),
    '089 投用记录不得删除')
check('DCMS-INV-090' in reason(
    "DELETE FROM das_release_check_item WHERE code='LOGGING'"), '090 控制项不得删除')
print('ok   089／090 验证记录、投用记录、控制项都不得抹改 —— 它们是上线决定的依据')

# ================= 构建变了, 之前的验证全部失效 =================
NEW = BUILD + '-b2'
awm.call('PUT', '/das/release/batches/B3/build', {'build_ref': NEW}, expect=200)
st = [x for x in admin.call('GET', '/das/release/stale', expect=200)
      if x['batch_code'] == 'B3' and x['verified_build'] == BUILD]
mine = db_query("""SELECT count(*) AS n FROM das_release_verification
                    WHERE batch_code='B3' AND build_ref=%s""", (BUILD,))[0]['n']
check(len(st) == mine,
      '本次构建上登记的 %d 条验证记录全部列为失效（实际 %d）' % (mine, len(st)))
check(mine == len(leaves),
      '本次给 %d 个叶子项各登记了一条验证记录, 断言不空转（%d）' % (len(leaves), mine))
check(all(x['current_build'] == NEW for x in st),
      '失效清单写明验的是哪个构建、当前是哪个')
bl = admin.call('GET', '/das/release/blockers?batch=B3', expect=200)
check(len(bl) == 7, '新构建上七项重新成为阻断项（%d）' % len(bl))
print('ok   换构建后旧验证一律不算 —— 否则"上线前已验证"指向的是上一版系统')

# ================= 后一次 FAIL 推翻前一次 PASS =================
awm.call('PUT', '/das/release/batches/B3/build', {'build_ref': BUILD}, expect=200)
check(admin.call('GET', '/das/release/blockers?batch=B3', expect=200) == [],
      '构建调回后旧验证重新生效（这是唯一能绕过闸门的路径, 故 set_build 同样要 AWM）')
# 来源取 INSPECTION: das_ncr 的来源取值里没有"上线前验证"这一类（见 0042 第 10 节的说明）,
# 六类里 INSPECTION（检查）最贴近 —— 这是一次计划内的检查发现了问题。
ncr = db_query("""INSERT INTO das_ncr (ncr_no, source, fact, basis_clause, evidence,
                                       created_by)
                  SELECT 'NCR-REL-'||%s, 'INSPECTION',
                         '日志不可改写的反例用例在本构建上失败',
                         'UG-DAW-005 第 1 章', 'CI 日志 CI-'||%s, id
                    FROM app_user WHERE username=%s
                  RETURNING id""", (STAMP, STAMP, admin.username))[0]['id']
dcm.call('POST', '/das/release/batches/B3/verifications',
         {'item_code': 'LOGGING', 'method': '复验', 'commit_ref': 'def5678',
          'batch_ref': 'CI-f' + STAMP, 'result': 'FAIL', 'ncr_id': ncr,
          'verified_by': '适航管理负责人 张三', 'verified_by_user': awm.id}, expect=201)
bl = admin.call('GET', '/das/release/blockers?batch=B3', expect=200)
check([x for x in bl if x['item_code'] == 'LOGGING' and x['state'] == 'FAILED'],
      '后一次 FAIL 立刻把日志项变回阻断（不是因为曾经 PASS 过就一直绿）')
check([x for x in bl if x['ncr_id'] == ncr], '阻断清单上带着那条不符合项的编号')
msg = reason("""INSERT INTO das_release_commissioning
                    (batch_code, build_ref, commissioned_on, approved_by)
                SELECT 'B3', %s, current_date, id FROM app_user WHERE username=%s""",
             (BUILD, awm.username))
check('DCMS-INV-087' in msg and '失败' in msg,
      '087 最近一次失败时投用被拒（数据库层, 绕过服务层也拦）')
print('ok   取每项最近一次结果: 先 PASS 后 FAIL 必须推翻前者, 否则改坏了照样放行')

# ================= 反例 087: 投用别的构建 =================
msg = reason("""INSERT INTO das_release_commissioning
                    (batch_code, build_ref, commissioned_on, approved_by)
                SELECT 'B2', 'who-knows', current_date, id
                  FROM app_user WHERE username=%s""", (awm.username,))
check('DCMS-INV-087' in msg, '087 批次没登记构建号时直接投用被拒')
print('ok   087 投用的构建必须与批次当前登记的构建一致')

# ================= N6 的两个周期参数 =================
# 这两条原先根本不在时限引擎里: 0039 种了 16 个参数, 没有一条是备份或恢复验证的。
# 而"备份恢复"这一项的验证内容本身依赖它们 —— 缺了只能验"今天跑过一次",
# 验不了"按规定的频次在跑"。
reg = {r['code']: r for r in admin.call('GET', '/das/deadlines', expect=200)}
check('N6.BACKUP_CYCLE' in reg and 'N6.RESTORE_VERIFY_CYCLE' in reg,
      'N6 的备份与恢复验证周期已在时限引擎里（原先一条都没有）')
rv = reg['N6.RESTORE_VERIFY_CYCLE']
check(float(rv['value_num']) == 3 and rv['value_unit'] == 'MONTH',
      '恢复验证按判据 Q2 取较严的每季度（而不是第 4 章那个每半年）')
check(rv['value_settled'], '恢复验证周期有已生效的取值')
bc = reg['N6.BACKUP_CYCLE']
check(float(bc['value_num']) == 1 and bc['value_unit'] == 'DAY', '服务器备份每日')
notes = {r['code']: r['note'] for r in db_query(
    "SELECT code, note FROM das_deadline_param WHERE code LIKE 'N6.%%'")}
check('每半年' in (notes['N6.RESTORE_VERIFY_CYCLE'] or ''),
      '源文件的频次冲突如实记在参数说明里, 没被悄悄抹掉')
unset = {r['code'] for r in admin.call('GET', '/das/deadlines/unset', expect=200)}
check('N6.BACKUP_CYCLE' not in unset and 'N6.RESTORE_VERIFY_CYCLE' not in unset,
      '两条新参数都已有初值, 不落在待填清单里')
print('ok   N6 两个周期补进时限引擎, 冲突按较严取值且冲突本身留在记录里')

print('\nPASS 上线前六项验证与投用闸门: N16 七项按机制分别给证据、'
      '087 绑构建且取最近一次、088 容器项不得笼统记、N15 抽查与三项结论、'
      'N6 失败即不符合项、089／090 不得抹改')
