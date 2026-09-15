-- Durable per-recipient messages; desktop delivery and user reading are separate.
CREATE TABLE IF NOT EXISTS notification (
 id bigserial PRIMARY KEY,
 user_id uuid NOT NULL REFERENCES app_user(id),
 request_id uuid NOT NULL REFERENCES approval_request(id),
 submission_round integer NOT NULL,
 kind text NOT NULL CHECK(kind IN ('PENDING','APPROVED','REJECTED','RETURNED')),
 title text NOT NULL,
 body text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 read_at timestamptz,
 notified_at timestamptz,
 lease_until timestamptz,
 lease_owner uuid,
 UNIQUE(user_id,request_id,submission_round,kind)
);
CREATE INDEX IF NOT EXISTS idx_notification_user ON notification(user_id,id DESC);
CREATE TABLE IF NOT EXISTS notification_device (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 session_id uuid NOT NULL REFERENCES user_session(id),
 pair_hash text UNIQUE,
 pair_expires_at timestamptz NOT NULL DEFAULT now()+interval '5 minutes',
 token_hash text UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 last_seen_at timestamptz,
 revoked_at timestamptz
);

CREATE OR REPLACE FUNCTION dcms_notify_pending() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE req approval_request;
BEGIN
 SELECT * INTO req FROM approval_request WHERE id=NEW.approval_request_id;
 IF req.status='PENDING' AND NEW.decision='PENDING' AND NEW.submission_round=req.submission_round THEN
   INSERT INTO notification(user_id,request_id,submission_round,kind,title,body)
   SELECT u.id,req.id,req.submission_round,'PENDING','收到待审批申请',left(req.request_number||' · '||req.title,500)
   FROM app_user u WHERE u.is_active AND u.id<>req.requester_id AND
    (u.id=NEW.assignee_user_id OR (NEW.assignee_user_id IS NULL AND EXISTS
      (SELECT 1 FROM user_role ur WHERE ur.user_id=u.id AND ur.role_code=NEW.required_role_code)))
   ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_notification_pending ON approval_step;
CREATE TRIGGER trg_notification_pending AFTER INSERT OR UPDATE ON approval_step
FOR EACH ROW EXECUTE FUNCTION dcms_notify_pending();

CREATE OR REPLACE FUNCTION dcms_notify_result() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN ('APPROVED','REJECTED','RETURNED') THEN
   INSERT INTO notification(user_id,request_id,submission_round,kind,title,body)
   VALUES(NEW.requester_id,NEW.id,NEW.submission_round,NEW.status,
     CASE NEW.status WHEN 'APPROVED' THEN '申请审批通过' WHEN 'REJECTED' THEN '申请已驳回，请修改' ELSE '申请已退回，请补充' END,
     left(NEW.request_number||' · '||NEW.title,500)) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_notification_result ON approval_request;
CREATE TRIGGER trg_notification_result AFTER UPDATE ON approval_request
FOR EACH ROW EXECUTE FUNCTION dcms_notify_result();

-- Existing open assignments should appear on first login after the upgrade.
INSERT INTO notification(user_id,request_id,submission_round,kind,title,body)
SELECT u.id,r.id,r.submission_round,'PENDING','收到待审批申请',left(r.request_number||' · '||r.title,500)
FROM approval_request r JOIN current_approval_step s ON s.approval_request_id=r.id
JOIN app_user u ON u.is_active AND u.id<>r.requester_id AND
 (u.id=s.assignee_user_id OR (s.assignee_user_id IS NULL AND EXISTS
  (SELECT 1 FROM user_role ur WHERE ur.user_id=u.id AND ur.role_code=s.required_role_code)))
WHERE r.status='PENDING' AND s.decision='PENDING' ON CONFLICT DO NOTHING;
