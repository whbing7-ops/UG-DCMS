"""Personal work queue and session-scoped, read-only Windows notification devices."""
import secrets
from .. import errors
from ..db import fetch_one, fetch_all, execute, scalar
from ..security import hash_token
from . import approvals, drafts

# Old pending notifications become irrelevant after withdrawal/reassignment.
VISIBLE = """(n.kind<>'PENDING' OR (r.status='PENDING' AND r.submission_round=n.submission_round
 AND EXISTS(SELECT 1 FROM current_approval_step s WHERE s.approval_request_id=r.id AND s.decision='PENDING'
 AND (s.assignee_user_id=n.user_id OR (s.assignee_user_id IS NULL AND EXISTS
 (SELECT 1 FROM user_role ur WHERE ur.user_id=n.user_id AND ur.role_code=s.required_role_code))))))"""

def messages(conn,user_id,limit=30):
    return fetch_all(conn,f'''SELECT n.id,n.title,n.body,n.kind,n.created_at,n.read_at,n.request_id,
       '#/approval/'||n.request_id AS url FROM notification n JOIN approval_request r ON r.id=n.request_id
       WHERE n.user_id=%s AND {VISIBLE} ORDER BY (n.read_at IS NOT NULL),n.id DESC LIMIT %s''',(user_id,limit))

def unread(conn,user_id):
    return scalar(conn,f'''SELECT count(*) FROM notification n JOIN approval_request r ON r.id=n.request_id
       WHERE n.user_id=%s AND n.read_at IS NULL AND {VISIBLE}''',(user_id,))

def overview(conn,actor):
    uid=actor['user_id']
    inbox=approvals.inbox(conn,actor)
    returned=fetch_all(conn,"""SELECT id,request_number,title,status,object_type,object_id FROM approval_request
      WHERE requester_id=%s AND status IN ('REJECTED','RETURNED') AND NOT(payload ? 'draft_deleted_at')
      ORDER BY requested_at DESC LIMIT 100""",(uid,))
    draft_rows=drafts.list_mine(conn,actor)
    summary=approvals.summary(conn,actor)
    stuck=[r for r in approvals.my_requests(conn,actor) if r['is_overdue']]
    return dict(inbox=inbox,returned=returned,drafts=draft_rows[:20],draft_count=len(draft_rows),
      inbox_count=summary['inbox'],overdue_days=approvals.OVERDUE_DAYS,
      overdue_inbox_count=summary['inbox_overdue'],stuck=stuck[:20],messages=messages(conn,uid),unread=unread(conn,uid),
      desktop_connected=bool(scalar(conn,"""SELECT EXISTS(SELECT 1 FROM notification_device d JOIN user_session s ON s.id=d.session_id
        WHERE s.user_id=%s AND s.revoked_at IS NULL AND s.expires_at>now() AND d.revoked_at IS NULL
        AND d.last_seen_at>now()-interval '60 seconds')""",(uid,))))

def mark_read(conn,uid,ids):
    execute(conn,'UPDATE notification SET read_at=COALESCE(read_at,now()) WHERE user_id=%s AND id=ANY(%s)',(uid,ids))
    return {'unread':unread(conn,uid)}

def pair(conn,actor):
    # New pairing codes replace unredeemed codes for this session only.
    execute(conn,'DELETE FROM notification_device WHERE session_id=%s AND token_hash IS NULL',(actor['session_id'],))
    code=secrets.token_urlsafe(18)
    fetch_one(conn,'INSERT INTO notification_device(session_id,pair_hash) VALUES(%s,%s) RETURNING id',
              (actor['session_id'],hash_token(code)))
    return {'code':code,'expires_in':300}

def redeem(conn,code):
    token=secrets.token_urlsafe(32)
    d=fetch_one(conn,"""UPDATE notification_device d SET pair_hash=NULL,token_hash=%s,last_seen_at=now()
      FROM user_session s JOIN app_user u ON u.id=s.user_id
      WHERE d.pair_hash=%s AND d.pair_expires_at>now() AND d.revoked_at IS NULL AND d.token_hash IS NULL
      AND s.id=d.session_id AND s.revoked_at IS NULL AND s.expires_at>now() AND u.is_active AND NOT u.must_change_password
      RETURNING d.id,u.full_name,s.expires_at""",(hash_token(token),hash_token(code)))
    if not d: raise errors.unauthorized('绑定码无效、已使用或已过期，请在登录首页重新生成')
    return {'token':token,'full_name':d['full_name'],'expires_at':d['expires_at']}

def device(conn,authorization):
    if not authorization or not authorization.startswith('Bearer '): raise errors.unauthorized('请重新绑定通知助手')
    d=fetch_one(conn,"""SELECT d.id,s.user_id,s.expires_at,u.full_name FROM notification_device d
      JOIN user_session s ON s.id=d.session_id JOIN app_user u ON u.id=s.user_id
      WHERE d.token_hash=%s AND d.revoked_at IS NULL AND s.revoked_at IS NULL AND s.expires_at>now()
       AND u.is_active AND NOT u.must_change_password FOR UPDATE OF d""",(hash_token(authorization[7:].strip()),))
    if not d: raise errors.unauthorized('登录已退出、过期或绑定已停用，请登录网页后重新绑定')
    execute(conn,'UPDATE notification_device SET last_seen_at=now() WHERE id=%s',(d['id'],))
    return d

def poll(conn,d):
    rows=fetch_all(conn,f'''SELECT n.id,n.title,n.body,n.kind,'#/approval/'||n.request_id AS url
      FROM notification n JOIN approval_request r ON r.id=n.request_id
      WHERE n.user_id=%s AND n.read_at IS NULL AND n.notified_at IS NULL AND {VISIBLE}
      AND (n.lease_until IS NULL OR n.lease_until<now() OR n.lease_owner=%s)
      ORDER BY n.id LIMIT 20 FOR UPDATE OF n SKIP LOCKED''',(d['user_id'],d['id']))
    if rows: execute(conn,"UPDATE notification SET lease_owner=%s,lease_until=now()+interval '60 seconds' WHERE id=ANY(%s)",
                     (d['id'],[r['id'] for r in rows]))
    return {'items':rows,'full_name':d['full_name'],'expires_at':d['expires_at']}

def acknowledge(conn,d,ids):
    execute(conn,'''UPDATE notification SET notified_at=COALESCE(notified_at,now()),lease_until=NULL,lease_owner=NULL
      WHERE user_id=%s AND lease_owner=%s AND id=ANY(%s)''',(d['user_id'],d['id'],ids))
    return {'ok':True}

def disconnect(conn,actor):
    execute(conn,'''UPDATE notification_device d SET revoked_at=now() FROM user_session s
      WHERE s.id=d.session_id AND s.user_id=%s AND d.revoked_at IS NULL''',(actor['user_id'],))
    return {'ok':True}
