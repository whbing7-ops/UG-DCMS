"""独立性规则登记册（基础能力，判据 I1～I10／I-总／E2）。

这个登记册本身就是为了回答"哪条独立性要求有实现、有用例、这一版跑过没有"而存在的，
所以它的用例要比别处更狠一点：**登记册最大的失效模式是看上去是满的。**
下面逐条验证它拦得住各种"填满但不真"的写法：

  I-总  声称已实现却说不出反例用例在哪 → 拒绝（ck_dir_testref）
  083   声称由某触发器保证，而系统目录里没有这个对象 → 拒绝（最难发现的假覆盖）
  084   规则不得删除、执行证据不得改写、状态留痕不得删除
  086   有子维度的父项不得自己带状态（判据 I10 禁止笼统称已实现）
  E2    三栏分列：有实现 ≠ 有用例 ≠ 跑过；只有三栏齐备才判 VERIFIED
  I10   summary **不给"已实现几条"**的总数
  ck_dir_manual  实现不齐备（含"实现在、但没验证过"）必须写明人工控制
  ck_die_text    执行证据不写提交号或批次号 → 拒绝（防护全靠可追溯）
  gap_kind       缺口分三类：IMPLEMENTATION／EVIDENCE／DEFECT，处置方式不同

【断言一律写成关系式，不写绝对条数】
登记册是**只追加**的：执行证据写进去不能改不能删（084），状态改动自动留痕。所以同一
个库上跑第二遍，条数必然和第一遍不同。写成"有用例的 N 条全部未执行"这种绝对断言，
只在全新库上成立——CI 每次都是新库，于是它会一直绿，而绿的原因不是规则成立，是库是
新的。下面改成断言视图本身的关系（未执行集合 ≡ 有用例且执行次数为 0 的集合），
并且本用例改动过的种子规则在末尾原样恢复——登记册里的如实状态该由工程师来写，
不是测试顺手改掉的。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-independence.py <credentials.json>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()


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


# ================= 判据 E2：三栏分列 =================
mx = admin.call('GET', '/das/independence/matrix', expect=200)
by = {r['code']: r for r in mx}
check(len(mx) >= 16, '判据 I1～I10 及子维度均已登记（%d 条）' % len(mx))
for col in ('source_state', 'test_state', 'executions'):
    check(col in mx[0], 'E2 第三栏 %s 单独成列, 不与其它栏合并' % col)
print('ok   E2 三栏分列: 源码状态／用例状态／执行证据各自一列（%d 条规则）' % len(mx))

# 容器项不带状态，状态落在子维度上（判据 I10）
for c in ('I3', 'I10'):
    check(by[c]['verdict'] == 'CONTAINER', '%s 判为 CONTAINER, 自己不带状态' % c)
    check(by[c]['source_state'] is None and by[c]['test_state'] is None,
          '%s 的状态栏为空（状态落在子维度上）' % c)
    kids = [r for r in mx if r['parent_code'] == c]
    check(len(kids) >= 2, '%s 有 %d 个子维度' % (c, len(kids)))
check(len({r['verdict'] for r in mx if r['parent_code'] == 'I10'}) == 4,
      'I10 四个子维度的判定互不相同 —— 这正是不得笼统称"已实现"的原因')
print('ok   I10 四维状态各不相同, 父项不带笼统状态:')
for r in mx:
    if r['parent_code'] == 'I10':
        print('       %-22s %-11s %-15s %s'
              % (r['code'], r['source_state'], r['test_state'], r['verdict']))

# 判据 I10：不得笼统计数
summ = admin.call('GET', '/das/independence/summary', expect=200)
check(all('total' not in k for s in summ for k in s), 'I10 summary 不含总数字段')
check(sum(s['rules'] for s in summ) == len(mx), '分组条数之和等于规则总条数')
print('ok   I10 按 verdict 分组、不给"已实现几条":',
      '、'.join(sorted(s['verdict'] for s in summ)))

# VERIFIED 的充要条件（关系式：库里已有多少执行记录都不影响）
for r in mx:
    if r['verdict'] == 'VERIFIED':
        check(r['source_state'] == 'PRESENT' and r['test_state'] == 'DEFINED'
              and r['object_present'] is not False
              and (r['executions'] or 0) > 0 and r['last_result'] == 'PASS',
              '%s 判 VERIFIED 时三栏确实齐备' % r['code'])
    elif (r['executions'] or 0) == 0 and r['verdict'] != 'CONTAINER':
        check(r['verdict'] != 'VERIFIED', '%s 没有执行证据就不判 VERIFIED' % r['code'])
print('ok   VERIFIED ⟺ 有实现 ＋ 有反例用例 ＋ 对象在 ＋ 最近一次跑过且 PASS')

# ================= 登记册与系统对不对得上 =================
h = admin.call('GET', '/das/independence/health', expect=200)
check(h['object_missing'] == [],
      '登记册声称的每个数据库对象都在系统目录里（假覆盖检查）')
check(h['container_mismatch'] == [], '层级声明与状态落点一致')
print('ok   登记册与系统目录逐条对得上, 层级与状态落点一致')

# ================= 判据 E2：有用例 ≠ 跑过 =================
unex = {r['code'] for r in admin.call('GET', '/das/independence/unexecuted', expect=200)}
should = {r['code'] for r in mx
          if r['test_state'] == 'DEFINED' and (r['executions'] or 0) == 0}
check(unex == should,
      '未执行清单 ≡ 有反例用例且执行次数为 0 的规则（%d 条）' % len(should))
check(should, '当前确有"用例写了但这一版没跑过"的规则, 断言不空转')
print('ok   E2 有反例用例 ≠ 已执行; 未执行 %d 条单独成一类' % len(should))

# ================= 缺口分三类 =================
gaps = admin.call('GET', '/das/independence/gaps', expect=200)
gk = {}
for g in gaps:
    gk.setdefault(g['gap_kind'], []).append(g)
check('IMPLEMENTATION' in gk and 'EVIDENCE' in gk,
      '缺口按处置方式分类（IMPLEMENTATION／EVIDENCE／DEFECT）')
check(all(g['manual_control'] for g in gk['IMPLEMENTATION']),
      '实现有缺口的每一条都写明了在补齐前靠什么人工控制把关')
check({g['code'] for g in gk.get('DEFECT', [])}
      == {r['code'] for r in mx
          if r['verdict'] in ('OBJECT_MISSING', 'LAST_RUN_FAILED')},
      'DEFECT ≡ 对象缺失或上次跑失败（当场要查, 不是可写进自评的已知缺口）')
check({g['code'] for g in gk.get('EVIDENCE', [])}
      == {r['code'] for r in mx if r['verdict'] == 'NOT_EXECUTED'},
      'EVIDENCE ≡ 实现齐备只是没跑过（补救是去跑, 不是补线下控制）')
admin.call('GET', '/das/independence/gaps?kind=NOPE', expect=400)
print('ok   缺口三分: IMPLEMENTATION %d 条（均有人工控制）、EVIDENCE %d 条、DEFECT %d 条'
      % (len(gk['IMPLEMENTATION']), len(gk.get('EVIDENCE', [])),
         len(gk.get('DEFECT', []))))
for g in gk['IMPLEMENTATION']:
    print('       %-22s %-18s %s' % (g['code'], g['verdict'],
                                     (g['manual_control'] or '')[:34]))

# 三种"看似已实现"要各判各的
fm = by['I10.FILE_TYPE']
check(fm['verdict'] == 'SEMANTIC_MISMATCH',
      'I10.FILE_TYPE 判为语义不符: 代码在跑, 但校验的是文档种类而不是适航签署事项')
check(fm['test_state'] == 'NOT_APPLICABLE',
      '语义不符时用例状态记为不适用 —— 测了也不证明签署事项受控')
check(by['I10.PRODUCT_SCOPE']['verdict'] == 'NOT_IMPLEMENTED',
      'I10.PRODUCT_SCOPE 如实记为未实现（完全由纸面授权书把关）')
check(by['I8']['verdict'] == 'NO_COUNTER_EXAMPLE',
      'I8 有实现但无反例用例, 单独判一类 —— 判据 I-总: 有实现不等于有用例')
print('ok   三种"看似已实现"各判各的: 语义不符／未实现／无反例用例')

# ================= 反例 I-总：说有用例却说不出在哪 =================
check(rejected("""INSERT INTO das_independence_rule
                      (code, requirement, source_doc, source_state, enforcement_kind,
                       enforcement_object, test_state)
                  VALUES ('X1'||%s,'编的','手册','PRESENT','DB_TRIGGER',
                          'trg_das_appointment_check','DEFINED')""", (STAMP,)),
      'I-总 声称已有反例用例却不写用例位置被拒')
print('ok   I-总 说有用例必须说得出在哪一条用例（说不出等于没有）')

# ================= 反例 083：声称的对象不存在 =================
msg = reason("""INSERT INTO das_independence_rule
                    (code, requirement, source_doc, source_state, enforcement_kind,
                     enforcement_object, test_state, test_ref)
                VALUES ('X2'||%s,'编的','手册','PRESENT','DB_TRIGGER',
                        'trg_不存在的东西','DEFINED','ci/x.py')""", (STAMP,))
check('DCMS-INV-083' in msg, '083 声称由不存在的对象保证被拒')
check('假覆盖' in msg, '拒绝理由说明了为什么这类最难发现（登记册看上去是满的）')
print('ok   083 声称某触发器在保证某规则、而系统里没有这个对象 → 当场拒绝')

# ================= 反例 ck_dir_manual：不齐备却不写人工控制 =================
check(rejected("""INSERT INTO das_independence_rule
                      (code, requirement, source_doc, source_state, enforcement_kind,
                       test_state)
                  VALUES ('X4'||%s,'编的','手册','ABSENT','NONE','ABSENT')""", (STAMP,)),
      '未实现却不写人工控制被拒')
check(rejected("""INSERT INTO das_independence_rule
                      (code, requirement, source_doc, source_state, enforcement_kind,
                       enforcement_object, test_state)
                  VALUES ('X5'||%s,'编的','手册','PRESENT','DB_TRIGGER',
                          'trg_das_appointment_check','ABSENT')""", (STAMP,)),
      '实现在、但没有反例用例, 同样须写明靠什么发现违规')
print('ok   两种不齐备都要求写明人工控制: 实现有缺口／实现在但没验证过')

# ================= 反例 086：给容器项加状态 =================
msg = reason("UPDATE das_independence_rule SET source_state='PRESENT' WHERE code='I10'")
check('DCMS-INV-086' in msg, '086 给有子维度的父项加状态被拒')
check('笼统' in msg, '拒绝理由指到判据 I10 禁止的笼统称已实现')
check(rejected("""INSERT INTO das_independence_rule
                      (code, requirement, source_doc, is_container, source_state,
                       enforcement_kind, test_state)
                  VALUES ('X6'||%s,'编的','手册',true,'PRESENT','DB_TRIGGER',
                          'DEFINED')""", (STAMP,)),
      '声明为容器却自己带状态被拒')
print('ok   086 容器项不得带状态; 状态只能落在子维度上')

# ================= 状态登记闸门按岗位任命 =================
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'iawm')
db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", ('IAWM' + STAMP, awm.id))
r = awm.call('PATCH', '/das/independence/rules/I10', {'source_state': 'PRESENT'},
             expect=403)
check('适航管理负责人' in str(r), '无任命者不得改状态, 理由指到岗位')
admin.call('POST', '/das/appointments',
           {'user_id': awm.id, 'position_code': 'AWM', 'kind': 'FORMAL'}, expect=201)
r = awm.call('PATCH', '/das/independence/rules/I10', {'source_state': 'PRESENT'},
             expect=400)
check('子维度' in str(r), '容器项在服务层就被挡下, 并说明状态该落在哪')
print('ok   状态登记闸门按岗位任命（AWM）, 容器项在服务层先给出能看懂的理由')

# 服务层实现对象不按 PG 目录校验: 它写的是函数名, 不是数据库对象。
# 用种子里本来就是 SERVICE_CHECK 的 I9 原地往返, 不往登记册里塞一条删不掉的测试规则。
awm.call('PATCH', '/das/independence/rules/I9',
         {'enforcement_object': 'signers.grant（本人为自己授权时拒绝）'}, expect=200)
awm.call('PATCH', '/das/independence/rules/I9',
         {'enforcement_object': by['I9']['enforcement_object']}, expect=200)
print('ok   目录校验只施加于数据库对象, 服务层函数名不误拦')

# ================= 执行证据：事实, 不设任命门槛, 但必须可追溯 =================
snap = admin.call('GET', '/das/independence/rules/I1', expect=200)['executions'] or 0
plain = make_user(admin, 'CONFIGURATION_MANAGER', 'iplain')
ex = plain.call('POST', '/das/independence/rules/I1/executions',
                {'commit_ref': 'abc1234', 'batch_ref': 'CI-' + STAMP, 'result': 'PASS',
                 'evidence_ref': 'integration job log'}, expect=201)
check(ex['result'] == 'PASS', '无任命的账号也能登记执行证据（记录者是 CI）')
plain.call('POST', '/das/independence/rules/I1/executions',
           {'commit_ref': '   ', 'batch_ref': 'CI-x', 'result': 'PASS'}, expect=400)
plain.call('POST', '/das/independence/rules/I1/executions',
           {'commit_ref': 'abc', 'batch_ref': 'CI-x', 'result': '跑过了'}, expect=400)
plain.call('POST', '/das/independence/rules/NOPE/executions',
           {'commit_ref': 'abc', 'batch_ref': 'CI-x', 'result': 'PASS'}, expect=404)
print('ok   执行证据不设任命门槛（门槛设在这里第三栏就永远是空的）, 但提交号与批次号必填')

i1 = admin.call('GET', '/das/independence/rules/I1', expect=200)
check(i1['verdict'] == 'VERIFIED', '三栏齐备后 I1 才判 VERIFIED')
check(i1['executions'] == snap + 1,
      '执行栏只增加了这一次（%d → %d）' % (snap, i1['executions']))
check(i1['last_result'] == 'PASS' and i1['execution_log'][0]['batch_ref'].endswith(STAMP),
      '最近一次执行就是刚登记的那一次, 批次号对得上')
print('ok   E2 三栏齐备（有实现＋有反例用例＋这一版跑过 PASS）才判 VERIFIED')

# 一次失败就不再是 VERIFIED
plain.call('POST', '/das/independence/rules/I1/executions',
           {'commit_ref': 'def5678', 'batch_ref': 'CI-f' + STAMP, 'result': 'FAIL'},
           expect=201)
i1 = admin.call('GET', '/das/independence/rules/I1', expect=200)
check(i1['verdict'] == 'LAST_RUN_FAILED',
      '最近一次失败后立刻不再判 VERIFIED（而不是因为曾经 PASS 过就一直绿）')
dg = admin.call('GET', '/das/independence/gaps?kind=DEFECT', expect=200)
check([g for g in dg if g['code'] == 'I1'],
      '上次跑失败的归入 DEFECT: 当场要查的问题, 不是可以写进自评的已知缺口')
print('ok   最近一次失败 → LAST_RUN_FAILED 并归入 DEFECT, 不被当成已知缺口接受')

# ================= 反例 084：登记册不得抹改 =================
msg = reason("UPDATE das_independence_execution SET result='PASS' WHERE result='FAIL'")
check('DCMS-INV-084' in msg, '084 执行证据不得改写（更正请另记一条）')
check('DCMS-INV-084' in reason("DELETE FROM das_independence_execution WHERE id=%s",
                               (ex['id'],)), '084 执行证据不得删除')
check('DCMS-INV-084' in reason("DELETE FROM das_independence_rule WHERE code='I5'"),
      '084 规则不得删除（删掉等于让这条要求从自评里消失）')
print('ok   084 执行证据不得改写或删除, 规则不得删除 —— 改一条 FAIL 为 PASS 做不到')

# ================= 状态改动自动留痕（改完原样恢复）=================
i8 = by['I8']
before = db_query("SELECT count(*) AS n FROM das_independence_rule_change "
                  "WHERE rule_code='I8'")[0]['n']
awm.call('PATCH', '/das/independence/rules/I8',
         {'test_state': 'DEFINED',
          'test_ref': 'ci/test-das-appointments.py: ISM-ORM 互斥对反例 ' + STAMP},
         expect=200)
rows = db_query("""SELECT old_test_state, new_test_state FROM das_independence_rule_change
                    WHERE rule_code='I8' ORDER BY changed_at DESC LIMIT 1""")
check(db_query("SELECT count(*) AS n FROM das_independence_rule_change "
               "WHERE rule_code='I8'")[0]['n'] == before + 1, '状态改动自动留一条痕')
check(rows[0]['old_test_state'] == 'ABSENT' and rows[0]['new_test_state'] == 'DEFINED',
      '留痕记下改动前后的值（自评材料）')
check('DCMS-INV-084' in reason(
    "DELETE FROM das_independence_rule_change WHERE rule_code='I8'"),
    '084 状态留痕不得删除')

# 【拦的要是改完之后的行, 不是请求里传了什么】
# 一条本来就写好了用例位置的规则, 再次传 test_state='DEFINED' 而不重复传 test_ref,
# 必须放行 —— 否则该做的都做了却报"必须写明用例位置", 接下来人就去猜格式了。
awm.call('PATCH', '/das/independence/rules/I8', {'test_state': 'DEFINED'}, expect=200)
r = awm.call('PATCH', '/das/independence/rules/I8',
             {'test_state': 'DEFINED', 'test_ref': ''}, expect=400)
check('反例' in str(r), '清空用例位置时要求说得出哪条反例用例')
print('ok   按改完之后的行判: 已写过用例位置的规则重传状态不被误拦')

# I8 恢复原状: 本用例只验机制, 登记册里的如实状态该由工程师来写。
awm.call('PATCH', '/das/independence/rules/I8',
         {'test_state': i8['test_state'], 'test_ref': i8['test_ref'] or '',
          'manual_control': i8['manual_control']}, expect=200)
check(admin.call('GET', '/das/independence/rules/I8',
                 expect=200)['verdict'] == 'NO_COUNTER_EXAMPLE',
      'I8 已恢复为如实状态（有实现、无反例用例）')
print('ok   改动过的种子规则原样恢复, 留痕里保留了这一进一出')

# ================= 人工控制两个方向都按改完之后的行判 =================
r = awm.call('PATCH', '/das/independence/rules/I9', {'source_state': 'ABSENT'},
             expect=400)
check('人工控制' in str(r) and 'ck_dir_manual' not in str(r),
      '改成未实现却不补人工控制 → 说清该补什么, 而不是报一个约束名')
r = awm.call('PATCH', '/das/independence/rules/I9', {'test_state': 'ABSENT'}, expect=400)
check('验证' in str(r),
      '把"有用例"改回"无用例"同样要求补人工控制（实现在但未验证过）')
awm.call('PATCH', '/das/independence/rules/I9',
         {'source_state': 'ABSENT', 'manual_control': '暂由纸面授权书把关 ' + STAMP},
         expect=200)
awm.call('PATCH', '/das/independence/rules/I9',
         {'source_state': by['I9']['source_state'],
          'manual_control': by['I9']['manual_control'] or ''}, expect=200)
check(admin.call('GET', '/das/independence/rules/I9', expect=200)['source_state']
      == by['I9']['source_state'], 'I9 已恢复原状')
print('ok   人工控制的两个方向都按改完之后的行判, 理由说得出该补什么')

# ================= 判据 I5：可见性而非阻断 =================
mb = admin.call('GET', '/das/independence/mutual-backup', expect=200)
check(isinstance(mb, list), 'I5 互为备份组合以清单形式交独立监督核对')
check(by['I5']['verdict'] == 'PARTIAL',
      'I5 如实记为部分实现: 原文禁的是"互为备份**并**相互核查"这个合取')
check('合取' in (by['I5']['gap_note'] or ''), 'I5 的缺口说明写明了禁的是合取')
print('ok   I5 做成可见性清单而非阻断, 并如实记为部分实现（%d 对互为备份）' % len(mb))

print('\nPASS 独立性规则登记册: E2 三栏分列、I10 不笼统计数、I-总 要求反例用例、'
      '083 假覆盖、084 不得抹改、086 容器不带状态')
