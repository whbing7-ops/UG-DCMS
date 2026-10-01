"""记录的保存、冻结与销毁（基础能力，UG-DAW-004）。

按判据 I-总 以反例为主。这块最容易做错的是**把销毁实现成定时删除**——判据 R-总 说得
很直接：那是设计错误，它会在冻结期、关联期限未满、缺双人批准的情况下照删不误。所以
用例的重点是证明每一道前置条件都真的拦得住，而不是证明"到期能删掉"。

覆盖：
  R1   表列期限是最低内部期限
  R2   保留截止日是**计算值**：本记录期限与全部关联对象期限取最大；
       关联在役产品时算不出来，而**算不出来不等于可以销毁**
  R3   「长期」类须先有责任终止／转让的可追溯移交记录
  R4   三项核查缺一不得销毁，且每项都要留结果文本
  R5   冻结期间销毁被**拒绝**而不是被跳过；冻结须写解除条件
  R6   双人批准，且不得一人兼两角完成（按工号判）
  R7   从备份恢复须重新核验保留与访问条件，已批准销毁项须标明限制
  R8   至产品永久退役类，20 年前创建仍不可销毁
  081  受管记录不得直接 DELETE

另：冻结门槛低、解除门槛高是刻意的不对称，两头都验。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-das-retention.py <credentials.json>
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg  # noqa: E402

from dcms_http import STAMP, check, db_execute, db_query, login_admin, make_user  # noqa: E402

admin = login_admin()
TODAY = dt.date.today()


def d(days):
    return str(TODAY + dt.timedelta(days=days))


def rejected(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False


def violates(rule, client, method, path, body=None):
    r = client.call(method, path, body, expect=409)
    got = ((r or {}).get('error') or {}).get('rule')
    check(got == rule, '%s 拦下（实际报出 %s）' % (rule, got))
    return r


def cls_of(anchor, period=None):
    """按锚点取一个记录类别。类别来自 UG-DAW-004 原文，不在测试里编。"""
    rows = admin.call('GET', '/das/retention/classes', expect=200)
    for c in rows:
        if c['anchor_kind'] == anchor and (period is None or c['raw_period_text'] == period):
            return c
    raise AssertionError('未找到锚点 %s 的记录类别' % anchor)


# ================= 判据 R1 与 97 类的锚点分布 =================
summary = admin.call('GET', '/das/retention/classes/summary', expect=200)
total = sum(x['classes'] for x in summary)
check(total == 97, 'UG-DAW-004 第 2 章的 97 类记录已登记（实际 %d）' % total)
check(len({x['anchor_kind'] for x in summary}) == 7, '8 种期限表述对应 7 种锚点')
print('ok   97 类记录、8 种期限表述、7 种锚点:')
for x in summary:
    print('       %-20s ← 「%s」: %d 类' % (x['anchor_kind'], x['raw_period_text'],
                                           x['classes']))

allc = admin.call('GET', '/das/retention/classes', expect=200)
check(all(c['is_minimum'] for c in allc), 'R1 表列期限全部标为最低内部期限')
check(all(c['raw_period_text'] for c in allc), '期限栏原文逐条保留, 映射可对照复核')
print('ok   R1 表列期限是最低内部期限（法规要求更长时以法规为准）, 原文逐条保留')

# ================= 写入闸门按岗位任命 =================
C5 = cls_of('CREATED')
r = admin.call('POST', '/das/retention/records',
               {'class_code': C5['code'], 'object_type': 'CI', 'object_id': 'x' + STAMP},
               expect=403)
check('资料管理负责人' in str(r), '拒绝理由指到 UG-DAW-004 的归口负责人')
print('ok   无岗位任命者不得登记受管记录（闸门按岗位任命, 不按账号角色）')

dcm = make_user(admin, 'CONFIGURATION_MANAGER', 'rdcm')
awm = make_user(admin, 'CONFIGURATION_MANAGER', 'rawm')
both = make_user(admin, 'CONFIGURATION_MANAGER', 'rboth')
for u, tag in ((dcm, 'RDCM'), (awm, 'RAWM'), (both, 'RBOTH')):
    db_execute("UPDATE app_user SET employee_no=%s WHERE id=%s", (tag + STAMP, u.id))
admin.call('POST', '/das/appointments',
           {'user_id': dcm.id, 'position_code': 'DCM', 'kind': 'FORMAL'}, expect=201)
admin.call('POST', '/das/appointments',
           {'user_id': awm.id, 'position_code': 'AWM', 'kind': 'FORMAL'}, expect=201)
print('ok   已任命资料管理负责人与适航管理负责人（两个不同自然人）')

# ================= 判据 R2: 截止日是计算值 =================
old = dcm.call('POST', '/das/retention/records',
               {'class_code': C5['code'], 'object_type': 'CI', 'object_id': 'old' + STAMP,
                'label': '6 年前创建的 5 年期记录', 'created_on': d(-365 * 6)}, expect=201)
check(old['status']['state'] == 'DUE', '6 年前创建的 5 年期记录已到期')
print('ok   自创建起算: 6 年前的 5 年期记录状态为 DUE（截止 %s）'
      % old['status']['retention_until'])

new = dcm.call('POST', '/das/retention/records',
               {'class_code': C5['code'], 'object_type': 'CI', 'object_id': 'new' + STAMP,
                'label': '今年创建', 'created_on': d(0)}, expect=201)
check(new['status']['state'] == 'RETAINED', '今年创建的未到期')

res = dcm.call('POST', '/das/retention/records/%d/links' % old['id'],
               {'link_kind': 'PRODUCT', 'link_ref': 'B-1415', 'required_until': None,
                'note': '在役产品, 终点未到'}, expect=201)
check(res['retention_until'] is None and res['state'] == 'NO_END_DATE',
      'R2 关联在役产品后不再按自身 5 年到期')
check('不得销毁' in (res.get('note') or ''), '返回说明算不出来按不得销毁处理')
print('ok   R2 关联在役产品 -> 截止日算不出来, 状态由 DUE 变 NO_END_DATE')
print('     （只按自身算, 一条关联在役产品的 5 年记录会在产品还在飞的时候到期）')

longer = dcm.call('POST', '/das/retention/records',
                  {'class_code': C5['code'], 'object_type': 'CI',
                   'object_id': 'lnk' + STAMP, 'label': '关联已退役产品',
                   'created_on': d(-365 * 6)}, expect=201)
res = dcm.call('POST', '/das/retention/records/%d/links' % longer['id'],
               {'link_kind': 'PRODUCT', 'link_ref': 'B-9999' + STAMP,
                'required_until': d(3650)}, expect=201)
check(res['retention_until'] == d(3650) and res['state'] == 'RETAINED',
      'R2 关联对象期限更长时取较长者')
print('ok   R2 自身截止 %s 但关联要求到 %s -> 取较长者, 状态 RETAINED'
      % (longer['status']['own_until'], res['retention_until']))

# ================= 锚点未落地 =================
CA = cls_of('AUTHORISATION_END')
auth = dcm.call('POST', '/das/retention/records',
                {'class_code': CA['code'], 'object_type': 'CI',
                 'object_id': 'auth' + STAMP, 'label': '授权未终止的培训记录'}, expect=201)
un = admin.call('GET', '/das/retention/unanchored', expect=200)
mine = [x for x in un if x['id'] == auth['id']]
check(mine and '尚未发生' in mine[0]['reason'], '锚点未发生的记录可见, 并说明原因')
print('ok   锚点未落地的记录单独可见: %s' % mine[0]['reason'])

dcm.call('POST', '/das/retention/records/%d/anchor' % old['id'],
         {'anchor_on': d(-100)}, expect=400)
print('ok   自创建起算的类别不接受另填锚点（同一条记录不能有两个起算点）')

st = dcm.call('POST', '/das/retention/records/%d/anchor' % auth['id'],
              {'anchor_on': d(-365 * 6)}, expect=200)
check(st['state'] == 'DUE', '回填锚点后期限算得出来')
print('ok   回填授权终止日后: %s + 5 年 = %s, 状态 DUE' % (st['anchor_on'],
                                                          st['retention_until']))

# ================= 判据 R4 / R6: 三项核查与双人批准 =================
batch = dcm.call('POST', '/das/retention/batches',
                 {'batch_ref': 'DEST-A-' + STAMP,
                  'record_ids': [auth['id'], new['id']]}, expect=201)
check(len(batch['items']) == 2, '批次含 2 条记录')
check(batch['blockers'], '就绪视图列出阻碍项')
print('ok   建批次不等于会被销毁; 当前阻碍: %s' % '；'.join(batch['blockers']))

violates('DCMS-INV-076', dcm, 'POST',
         '/das/retention/batches/%d/execute' % batch['id'])
print('ok   DCMS-INV-076 三项核查没做完就执行销毁被拒')

dcm.call('POST', '/das/retention/batches/%d/checks' % batch['id'],
         {'check': 'inventory', 'result': ''}, expect=422)
print('ok   核查结果不得为空（只记一个"已核查"布尔值等于没核查）')

for ck, rs in (('inventory', '清单 2 条, 与台账逐条核对一致'),
               ('related_period', '逐条核对关联对象期限'),
               ('freeze', '查冻结台账, 本批次无冻结')):
    dcm.call('POST', '/das/retention/batches/%d/checks' % batch['id'],
             {'check': ck, 'result': rs}, expect=201)
print('ok   三项核查各自留下结果文本')

violates('DCMS-INV-077', dcm, 'POST', '/das/retention/batches/%d/execute' % batch['id'])
print('ok   DCMS-INV-077 没有批准就执行销毁被拒')

dcm.call('POST', '/das/retention/batches/%d/approve' % batch['id'], expect=200)
r = dcm.call('POST', '/das/retention/batches/%d/approve' % batch['id'], expect=400)
check('另一自然人' in str(r), '同一人不得占两个批准位, 理由写明')
print('ok   R6 同一人不得占两个批准位（判据 R6: 不能由一人兼任两个角色完成）')

# 一人兼两角：数据库按工号判，拦在写入那一刻
admin.call('POST', '/das/appointments',
           {'user_id': both.id, 'position_code': 'DCM', 'kind': 'FORMAL'}, expect=201)
check(rejected("""UPDATE das_retention_destruction
                     SET approved_by_dcm=%s, approved_by_dcm_at=now(),
                         approved_by_awm=%s, approved_by_awm_at=now(), executed_at=now()
                   WHERE batch_ref=%s""", (both.id, both.id, 'DEST-A-' + STAMP)),
      'DCMS-INV-077 一人兼两角完成双人批准被数据库拒绝')
print('ok   DCMS-INV-077 一人兼两角完成双人批准被拒（按工号判, 不是按账号）')

awm.call('POST', '/das/retention/batches/%d/approve' % batch['id'], expect=200)
violates('DCMS-INV-078', dcm, 'POST', '/das/retention/batches/%d/execute' % batch['id'])
print('ok   DCMS-INV-078 批次内含未到期记录时整批被拒（不是跳过那一条）')

b = admin.call('GET', '/das/retention/batches/%d' % batch['id'], expect=200)
check(any('未到期' in x for x in b['blockers']), '就绪视图指出是未到期的那一条')
print('ok   就绪视图说明阻碍: %s' % '；'.join(b['blockers']))

# ================= 判据 R5: 冻结 =================
b2 = dcm.call('POST', '/das/retention/batches',
              {'batch_ref': 'DEST-B-' + STAMP, 'record_ids': [auth['id']]}, expect=201)
for ck, rs in (('inventory', '清单 1 条'), ('related_period', '无更长要求'),
               ('freeze', '无冻结')):
    dcm.call('POST', '/das/retention/batches/%d/checks' % b2['id'],
             {'check': ck, 'result': rs}, expect=201)
dcm.call('POST', '/das/retention/batches/%d/approve' % b2['id'], expect=200)
awm.call('POST', '/das/retention/batches/%d/approve' % b2['id'], expect=200)
b2r = admin.call('GET', '/das/retention/batches/%d' % b2['id'], expect=200)
check(not b2r['blockers'], '阻碍已清空, 可执行')
print('ok   三项核查 + 双人批准齐备后阻碍清空')

dcm.call('POST', '/das/retention/freezes',
         {'record_id': auth['id'], 'freeze_kind': 'INVESTIGATION',
          'reason': '局方就该授权的签署记录开展调查', 'release_condition': ''}, expect=422)
print('ok   R5 冻结不写解除条件被拒（永远挂着与没冻结一样不可管理）')

fz = dcm.call('POST', '/das/retention/freezes',
              {'record_id': auth['id'], 'freeze_kind': 'INVESTIGATION',
               'reason': '局方就该授权的签署记录开展调查',
               'release_condition': '局方调查结论出具并关闭相关不符合项后解除'}, expect=201)
print('ok   R5 冻结门槛刻意放低: 资料管理负责人即可发起（冻结是保护性动作）')

violates('DCMS-INV-075', dcm, 'POST', '/das/retention/batches/%d/execute' % b2['id'])
print('ok   DCMS-INV-075 冻结期间销毁被**拒绝**, 不是被跳过')
print('     （跳过意味着批次里其余记录照删, 而操作人以为整批都处理了）')

r = dcm.call('POST', '/das/retention/freezes/%d/release' % fz['id'],
             {'release_note': '调查已结束'}, expect=403)
check('适航管理负责人' in str(r), '解除须在任适航管理负责人')
print('ok   解除门槛高于发起: 资料管理负责人能冻不能解（解除之后记录就可能被销毁）')

awm.call('POST', '/das/retention/freezes/%d/release' % fz['id'],
         {'release_note': ''}, expect=422)
awm.call('POST', '/das/retention/freezes/%d/release' % fz['id'],
         {'release_note': '局方调查结论已出具, 相关不符合项已关闭, 解除条件满足'}, expect=200)
print('ok   解除须写明说明: 解除条件是否满足、依据是什么')

check(rejected("UPDATE das_retention_freeze SET release_condition='随便解除' WHERE id=%s",
               (fz['id'],)), 'DCMS-INV-080 冻结的解除条件不得改写')
print('ok   DCMS-INV-080 冻结的原因与解除条件不得改写（改条件等于事后改变冻结的理由）')

ex = dcm.call('POST', '/das/retention/batches/%d/execute' % b2['id'], expect=200)
check(ex['executed_at'], '解除冻结后可执行销毁')
recs = admin.call('GET', '/das/retention/records?state=DESTROYED', expect=200)
check(any(x['id'] == auth['id'] for x in recs), '销毁后记录状态为 DESTROYED 而非消失')
print('ok   解除后执行销毁; 记录标为 DESTROYED 而不是从库里消失')

# ================= 判据 R3: 「长期」类 =================
CL = cls_of('RESPONSIBILITY', '长期')
lg = dcm.call('POST', '/das/retention/records',
              {'class_code': CL['code'], 'object_type': 'CI', 'object_id': 'lg' + STAMP,
               'label': '长期类记录', 'created_on': d(-365 * 10)}, expect=201)
check(lg['status']['state'] == 'NO_END_DATE', '「长期」类无关联对象时没有截止日')
SCOPE = 'RESP-' + STAMP
dcm.call('POST', '/das/retention/records/%d/links' % lg['id'],
         {'link_kind': 'RESPONSIBILITY', 'link_ref': SCOPE,
          'required_until': d(-30)}, expect=201)
b3 = dcm.call('POST', '/das/retention/batches',
              {'batch_ref': 'DEST-C-' + STAMP, 'record_ids': [lg['id']]}, expect=201)
for ck, rs in (('inventory', '清单 1 条'), ('related_period', '关联责任期限已过'),
               ('freeze', '无冻结')):
    dcm.call('POST', '/das/retention/batches/%d/checks' % b3['id'],
             {'check': ck, 'result': rs}, expect=201)
dcm.call('POST', '/das/retention/batches/%d/approve' % b3['id'], expect=200)
awm.call('POST', '/das/retention/batches/%d/approve' % b3['id'], expect=200)
violates('DCMS-INV-079', dcm, 'POST', '/das/retention/batches/%d/execute' % b3['id'])
print('ok   DCMS-INV-079 「长期」类无责任移交记录时不得销毁')
print('     （「长期」不是「永远」, 也不是「无限期不管」——判据 R3）')

awm.call('POST', '/das/retention/handovers',
         {'scope_ref': SCOPE, 'event_kind': 'TRANSFERRED', 'effective_on': d(-60),
          'handover_ref': 'HO-' + STAMP, 'evidence_ref': 'E-' + STAMP}, expect=400)
print('ok   转让不写接收方被拒（不写接收方的「转让」等于无人接手, 那是终止）')

awm.call('POST', '/das/retention/handovers',
         {'scope_ref': SCOPE, 'event_kind': 'TRANSFERRED', 'effective_on': d(-60),
          'transferred_to': '受让方某公司', 'handover_ref': 'HO-' + STAMP,
          'evidence_ref': '移交协议与清单存于文档库 HO-' + STAMP}, expect=201)
dcm.call('POST', '/das/retention/batches/%d/execute' % b3['id'], expect=200)
print('ok   登记可追溯移交后「长期」类可销毁')

# ================= 判据 R8 =================
CR = cls_of('PRODUCT_RETIREMENT')
l9 = dcm.call('POST', '/das/retention/records',
              {'class_code': CR['code'], 'object_type': 'CI', 'object_id': 'l9' + STAMP,
               'label': CR['name_cn'], 'created_on': d(-365 * 20)}, expect=201)
check(l9['status']['state'] == 'NO_END_DATE',
      'R8 至产品永久退役类, 20 年前创建仍无截止日')
print('ok   R8 %s（「%s」）: 20 年前创建仍不可销毁'
      % (CR['name_cn'], CR['raw_period_text']))
print('     （CCAR-21.137（十三）; 这是 R2 的特例, 不是唯一的禁止销毁条件）')

# ================= 判据 R7: 恢复核验 =================
dcm.call('POST', '/das/retention/restore-checks',
         {'restore_ref': 'RST-A-' + STAMP, 'backup_ref': 'BAK-' + STAMP,
          'retention_recheck_result': '', 'access_recheck_result': 'x'}, expect=422)
print('ok   R7 恢复不做保留条件重新核验被拒')

dcm.call('POST', '/das/retention/restore-checks',
         {'restore_ref': 'RST-B-' + STAMP, 'backup_ref': 'BAK-' + STAMP,
          'retention_recheck_result': 'a', 'access_recheck_result': 'b',
          'destroyed_items_noted': 3}, expect=400)
print('ok   R7 恢复清单有已批准销毁项却不写限制被拒')
print('     （只记一个数字等于没标明——那几条会在恢复后变成可访问的副本）')

rc = dcm.call('POST', '/das/retention/restore-checks',
              {'restore_ref': 'RST-C-' + STAMP, 'backup_ref': 'BAK-' + STAMP,
               'retention_recheck_result': '逐条重算保留截止日, 2 条已过期但未批准销毁, 保持保留',
               'access_recheck_result': '按当前岗位重新核验访问条件, 1 个账号已停用并移除访问',
               'destroyed_items_noted': 1,
               'destroyed_items_note': '恢复清单中 1 条属已批准销毁项: 标注为已销毁, 不恢复可访问副本'},
              expect=201)
check(rc['destroyed_items_noted'] == 1, 'R7 恢复核验已登记, 并标明已批准销毁项')
print('ok   R7 恢复核验登记成功: 保留与访问条件各自重新核验, 已销毁项标明限制')

# ================= 判据 R-总 / 081 =================
check(rejected("DELETE FROM das_retention_record WHERE id=%s", (new['id'],)),
      'DCMS-INV-081 受管记录不得直接 DELETE')
check(rejected("DELETE FROM das_retention_destruction WHERE batch_ref=%s",
               ('DEST-B-' + STAMP,)), '销毁批次不得删除')
check(rejected("UPDATE das_retention_destruction SET inventory_check_result='改掉'"
               " WHERE batch_ref=%s", ('DEST-B-' + STAMP,)), '已执行的批次不得再改')
check(rejected("DELETE FROM das_responsibility_handover WHERE handover_ref=%s",
               ('HO-' + STAMP,)), '移交记录不得删除')
check(rejected("UPDATE das_retention_restore_check SET access_recheck_result='改掉'"
               " WHERE restore_ref=%s", ('RST-C-' + STAMP,)), '恢复核验不得改写')
print('ok   R-总 销毁走受控动作: 记录不得直删, 批次与移交、恢复核验 append-only')

due_list = admin.call('GET', '/das/retention/due', expect=200)
print('ok   到期候选清单 %d 条——这不是待删除队列, 没有任何自动销毁' % len(due_list))
print('     （判据 R-总: 实现为定时删除即为设计错误）')

print()
print('全部通过: 记录的保存、冻结与销毁')
