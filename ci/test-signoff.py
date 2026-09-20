"""设计文件版次三级签署(编制-审核-批准)、电子签名、有权签署人清单集成测试(真实 HTTP + PostgreSQL)。

用法: DCMS_BASE_URL=http://127.0.0.1:8080 PYTHONPATH=src/backend python ci/test-signoff.py <credentials.json>
"""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dcms_http import (Client, STAMP, approve, check, db_execute, db_query, login_admin,  # noqa: E402
                       make_user, review, submit_for_signoff)

admin = login_admin()
prep = make_user(admin, 'ENGINEER', 'pr')
rev = make_user(admin, 'ENGINEER', 'rv')
apr = make_user(admin, 'APPROVER', 'ap')
other = make_user(admin, 'APPROVER', 'ot')          # 有角色但始终不授权
viewer = make_user(admin, 'VIEWER', 'vw')

# ---------------- 有权签署人清单 ----------------
admin.call('POST', '/signers', {'user_id': admin.id, 'level': 'REVIEW'}, expect=400)
print('ok   不得给自己授权(400)')
admin.call('POST', '/signers', {'user_id': viewer.id, 'level': 'REVIEW'}, expect=400)
print('ok   没有可签署角色的账户不能被授权(400)')
prep.call('POST', '/signers', {'user_id': rev.id, 'level': 'REVIEW'}, expect=403)
print('ok   普通工程师不能维护清单(403)')
viewer.call('GET', '/signers', expect=200)
print('ok   任何登录用户可查看清单')

num = 'SO-' + str(time.time_ns())[-10:]
admin.call('POST', '/files', {'file_number': num, 'file_type_code': 'DWG', 'title_cn': '【测试】三级签署'}, expect=201)
r1 = prep.call('POST', f'/files/{num}/revisions', {'change_summary': '初版'}, expect=201)
prep.upload(f'/revisions/{r1["id"]}/attachments', f'so-{STAMP}.pdf', b'signoff ' + uuid.uuid4().bytes, 'RELEASED_PDF')

check(rev.id not in [c['id'] for c in prep.call('GET', f'/revisions/{r1["id"]}/signers?level=REVIEW', expect=200)], '尚未授权的人不在可选审核人里')
submit_for_signoff(prep, r1['id'], rev, apr, expect=400)
print('ok   未授权的人不能被选为审核/批准人(400)')

g_rev = admin.call('POST', '/signers', {'user_id': rev.id, 'level': 'REVIEW', 'note': '测试任命'}, expect=201)
g_apr = admin.call('POST', '/signers', {'user_id': apr.id, 'level': 'APPROVE', 'file_type_code': 'DWG'}, expect=201)
admin.call('POST', '/signers', {'user_id': rev.id, 'level': 'REVIEW'}, expect=400)
print('ok   重复授权(400)')
cand_r = [c['id'] for c in prep.call('GET', f'/revisions/{r1["id"]}/signers?level=REVIEW')]
cand_a = [c['id'] for c in prep.call('GET', f'/revisions/{r1["id"]}/signers?level=APPROVE')]
check(rev.id in cand_r and apr.id in cand_a and other.id not in cand_a and prep.id not in cand_r + cand_a,
      '候选人只含已授权者，且不含编制人和未授权者')

# ---------------- 提交(编制签署) ----------------
prep.call('POST', f'/revisions/{r1["id"]}/submit', {'reviewer_user_id': rev.id, 'approver_user_id': apr.id, 'password': 'wrong-Pass-1!'}, expect=400)
check(prep.call('GET', f'/revisions/{r1["id"]}')['status'] == 'WORKING', '口令错误时提交失败，版次仍是编制中')
submit_for_signoff(prep, r1['id'], apr, apr, expect=400)
print('ok   审核人与批准人是同一人(400)')
submit_for_signoff(prep, r1['id'], prep, apr, expect=400)
print('ok   编制人不能兼任审核人(400)')
submit_for_signoff(prep, r1['id'], rev, other, expect=400)
print('ok   未在清单内的批准人(400)')
req = submit_for_signoff(prep, r1['id'], rev, apr)
d = prep.call('GET', f'/revisions/{r1["id"]}')
check(d['status'] == 'IN_REVIEW' and d['signoff'][0]['state'] == 'SIGNED' and d['signoff'][1]['state'] == 'PENDING', '提交后编制已签署、审核待签署')

check(any(str(x['object_id']) == r1['id'] for x in rev.call('GET', '/approvals/inbox')), '审核人的待办里有这份申请')
check(all(str(x['object_id']) != r1['id'] for x in apr.call('GET', '/approvals/inbox')), '批准人此时还没有这份待办(未轮到)')
check(db_query("SELECT count(*) AS n FROM notification WHERE user_id=%s AND request_id=%s", (apr.id, req['id']))[0]['n'] == 0,
      '批准人此时没有收到通知')
check(db_query("SELECT count(*) AS n FROM notification WHERE user_id=%s AND request_id=%s", (rev.id, req['id']))[0]['n'] == 1,
      '审核人收到了通知')

# ---------------- 顺序与身份 ----------------
approve(apr, r1['id'], expect=400)
print('ok   审核未通过时批准人不能批准(400)')
review(apr, r1['id'], expect=400)
print('ok   批准人不能代替审核人审核(400)')
review(other, r1['id'], expect=400)
print('ok   无关的人不能审核(400)')
review(rev, r1['id'], expect=400, password='wrong-Pass-1!')
print('ok   审核口令错误(400)')
review(rev, r1['id'])
d = prep.call('GET', f'/revisions/{r1["id"]}')
check(d['signoff'][1]['state'] == 'SIGNED' and d['signoff'][2]['state'] == 'PENDING' and d['status'] == 'IN_REVIEW', '审核签署后轮到批准，版次仍在审核中')
check(any(str(x['object_id']) == r1['id'] for x in apr.call('GET', '/approvals/inbox')), '审核通过后批准人才有待办')
check(db_query("SELECT count(*) AS n FROM notification WHERE user_id=%s AND request_id=%s", (apr.id, req['id']))[0]['n'] == 1,
      '审核通过后批准人才收到通知')
approve(rev, r1['id'], expect=400)
print('ok   审核人不能兼任批准人(400)')
approve(apr, r1['id'], expect=400, password='wrong-Pass-1!')
print('ok   批准口令错误(400)')
approve(apr, r1['id'])
d = prep.call('GET', f'/revisions/{r1["id"]}')
check(d['status'] == 'RELEASED' and [s['state'] for s in d['signoff']] == ['SIGNED'] * 3, '三级签署完成，版次发布')
check(len({s['content_sha256'] for s in d['signoff']}) == 1, '三级签署绑定的是同一份内容摘要')
check(all(s['auth_method'] == 'PASSWORD_REENTRY' for s in d['signoff']), '签署方式为口令重新确认')

# ---------------- 数据库层强制 ----------------
import psycopg  # noqa: E402
def rejected(sql, params=()):
    try:
        db_execute(sql, params)
    except psycopg.Error:
        return True
    return False
check(rejected("UPDATE signature_record SET comments='篡改' WHERE object_id=%s", (r1['id'],)), '签署记录不可修改')
check(rejected("DELETE FROM signature_record WHERE object_id=%s", (r1['id'],)), '签署记录不可删除')
check(rejected("DELETE FROM signer_authorization WHERE id=%s", (g_rev['id'],)), '授权记录不可删除')
check(rejected("UPDATE signer_authorization SET valid_to='2099-01-01' WHERE id=%s", (g_rev['id'],)), '授权记录除撤销外不可修改')

# ---------------- 第二个版次: 授权撤销与中途失效 ----------------
r2 = prep.call('POST', f'/files/{num}/revisions', {'change_summary': '二版'}, expect=201)
prep.upload(f'/revisions/{r2["id"]}/attachments', f'so2-{STAMP}.pdf', b'v2 ' + uuid.uuid4().bytes, 'RELEASED_PDF')
submit_for_signoff(prep, r2['id'], rev, apr)
rows = db_query("SELECT id FROM approval_step WHERE approval_request_id=(SELECT approval_request_id FROM file_revision WHERE id=%s) ORDER BY step_order", (r2['id'],))
check(rejected("UPDATE approval_step SET assignee_user_id=%s WHERE id=%s", (rev.id, rows[1]['id'])), '数据库拒绝把同一人指派到两个步骤')
check(rejected("UPDATE approval_step SET decision='APPROVED', decided_by=%s, acted_at=now() WHERE id=%s", (apr.id, rows[1]['id'])),
      '数据库拒绝在审核通过前处理批准步骤')
admin.call('POST', f'/signers/{g_rev["id"]}/revoke', {'reason': '测试撤销'}, expect=200)
review(rev, r2['id'], expect=400)
print('ok   审核人授权被撤销后不能再签署(400)')
admin.call('POST', f'/signers/{g_rev["id"]}/revoke', {'reason': '重复'}, expect=400)
print('ok   重复撤销(400)')
check(rev.id not in [c['id'] for c in prep.call('GET', f'/revisions/{r2["id"]}/signers?level=REVIEW')], '撤销后不再出现在候选人中')
admin.call('POST', '/signers', {'user_id': rev.id, 'level': 'REVIEW', 'note': '重新任命'}, expect=201)
review(rev, r2['id'])

# ---------------- 退回后重走整个流程 ----------------
rq = rev.call('GET', '/approvals/inbox')
check(all(str(x['object_id']) != r2['id'] for x in rq), '审核人已处理，不再在其待办里')
apr.call('POST', f'/approvals/{req["id"]}/return?reason=x', expect=400)   # 旧申请已结束
ar2 = prep.call('GET', f'/revisions/{r2["id"]}')['approval_request_id']
apr.call('POST', f'/approvals/{ar2}/return?reason=材料不足', expect=200)
d = prep.call('GET', f'/revisions/{r2["id"]}')
check(d['status'] == 'WORKING', '批准人退回后版次回到编制中')
req2 = submit_for_signoff(prep, r2['id'], rev, apr)
d = prep.call('GET', f'/revisions/{r2["id"]}')
check(req2['submission_round'] == 2 and [s['state'] for s in d['signoff']] == ['SIGNED', 'PENDING', 'PENDING'],
      '退回后重新提交是第 2 轮，三级重新签署')
review(rev, r2['id'])
approve(apr, r2['id'])
check(db_query("SELECT count(*) AS n FROM signature_record WHERE object_id=%s", (r2['id'],))[0]['n'] == 5, '两轮共 5 条签署记录(第 1 轮编制+审核，第 2 轮三级)，第 1 轮的没有被覆盖')

# ---------------- 审核人(只有工程师角色)能在自己的步骤上退回/驳回 ----------------
r3 = prep.call('POST', f'/files/{num}/revisions', {'change_summary': '三版'}, expect=201)
prep.upload(f'/revisions/{r3["id"]}/attachments', f'so3-{STAMP}.pdf', b'v3 ' + uuid.uuid4().bytes, 'RELEASED_PDF')
submit_for_signoff(prep, r3['id'], rev, apr)
ar3 = prep.call('GET', f'/revisions/{r3["id"]}')['approval_request_id']
apr.call('POST', f'/approvals/{ar3}/return?reason=x', expect=400)          # 还没轮到批准人
rev.call('POST', f'/approvals/{ar3}/return?reason=图纸信息不全', expect=200)
check(prep.call('GET', f'/revisions/{r3["id"]}')['status'] == 'WORKING', '审核人(工程师角色)可以退回自己步骤上的申请')

# ---------------- 审计 ----------------
acts = {r['action'] for r in db_query("SELECT action FROM audit_log WHERE object_code LIKE %s OR object_code=%s", (f'{num}%', 'x'))}
check({'REVISION_SUBMIT', 'REVISION_REVIEW', 'REVISION_RELEASE'} <= acts, '提交、审核、发布都写入审计')
check(db_query("SELECT count(*) AS n FROM audit_log WHERE action='SIGNATURE_AUTH' AND result='DENIED'")[0]['n'] >= 3, '签署口令错误被记入审计')
print('ALL OK')
