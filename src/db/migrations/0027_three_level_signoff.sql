-- 设计文件版次"编制-审核-批准"三级签署、电子签名记录、有权签署人清单。
-- 可重复执行; 不改动任何已有业务数据。历史(单级审批)版次保持原样, 不补写不存在的签署。

-- ---------------------------------------------------------------------
-- 1. 有权签署人清单: 谁, 在哪一级(审核/批准), 对哪类文件, 有效期
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signer_authorization (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES app_user (id),
    level          text NOT NULL CHECK (level IN ('REVIEW', 'APPROVE')),
    file_type_code text REFERENCES file_type (code),          -- NULL = 所有文件类型
    valid_from     date NOT NULL DEFAULT current_date,
    valid_to       date,
    note           text,
    granted_by     uuid NOT NULL REFERENCES app_user (id),
    granted_at     timestamptz NOT NULL DEFAULT now(),
    revoked_at     timestamptz,
    revoked_by     uuid REFERENCES app_user (id),
    revoke_reason  text,
    CONSTRAINT ck_signer_dates CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT ck_signer_no_self_grant CHECK (granted_by <> user_id),
    CONSTRAINT ck_signer_revoked CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_signer_auth_active ON signer_authorization (user_id, level) WHERE revoked_at IS NULL;

-- 授权记录只能撤销, 不能改写或删除(撤销之外的任何字段变化都被拒绝)
CREATE OR REPLACE FUNCTION dcms_guard_signer_authorization() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 签署授权记录不得删除, 只能撤销' USING ERRCODE = '23514';
    END IF;
    IF OLD.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 已撤销的签署授权不得再改写' USING ERRCODE = '23514';
    END IF;
    IF (NEW.user_id, NEW.level, NEW.file_type_code, NEW.valid_from, NEW.valid_to, NEW.note, NEW.granted_by, NEW.granted_at)
       IS DISTINCT FROM
       (OLD.user_id, OLD.level, OLD.file_type_code, OLD.valid_from, OLD.valid_to, OLD.note, OLD.granted_by, OLD.granted_at) THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 签署授权除撤销外不得修改' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_signer_authorization_guard ON signer_authorization;
CREATE TRIGGER trg_signer_authorization_guard BEFORE UPDATE OR DELETE ON signer_authorization
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_signer_authorization();

-- ---------------------------------------------------------------------
-- 2. 签署记录(电子签名): 不可变。姓名与账户是签署当时的快照,
--    content_sha256 把签署绑定到被签署内容(版次、变更说明、全部附件摘要)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signature_record (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type         text NOT NULL,
    object_id           uuid NOT NULL,
    approval_request_id uuid REFERENCES approval_request (id),
    submission_round    integer NOT NULL DEFAULT 1,
    level               text NOT NULL CHECK (level IN ('PREPARE', 'REVIEW', 'APPROVE')),
    meaning             text NOT NULL,
    user_id             uuid NOT NULL REFERENCES app_user (id),
    username            text NOT NULL,
    full_name           text NOT NULL,
    signed_at           timestamptz NOT NULL DEFAULT now(),
    comments            text,
    content_sha256      text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    auth_method         text NOT NULL CHECK (auth_method IN ('PASSWORD_REENTRY', 'SIMULATION')),
    client_ip           text,
    CONSTRAINT uq_signature_level UNIQUE (object_type, object_id, submission_round, level)
);
CREATE INDEX IF NOT EXISTS idx_signature_object ON signature_record (object_type, object_id, submission_round);
DROP TRIGGER IF EXISTS trg_signature_immutable ON signature_record;
CREATE TRIGGER trg_signature_immutable BEFORE UPDATE OR DELETE ON signature_record
    FOR EACH ROW EXECUTE FUNCTION dcms_block_write();

-- ---------------------------------------------------------------------
-- 3. 当前待处理步骤: 每个申请当前轮次里序号最小的待处理步骤。
--    多级审批时, 后序步骤在前序通过之前不属于任何人的待办。
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW current_pending_step AS
SELECT s.* FROM current_approval_step s
 WHERE s.decision = 'PENDING'
   AND s.step_order = (SELECT min(x.step_order) FROM current_approval_step x
                        WHERE x.approval_request_id = s.approval_request_id AND x.decision = 'PENDING');

-- ---------------------------------------------------------------------
-- 4. 数据库层强制: 同一轮次内, 各步骤的指派人与处理人互不相同且不是申请人;
--    后序步骤须在前序步骤全部通过后才能作出决定
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_check_step_people_order() RETURNS trigger AS $$
DECLARE
    v_requester uuid;
    v_req_no    text;
BEGIN
    SELECT requester_id, request_number INTO v_requester, v_req_no
      FROM approval_request WHERE id = NEW.approval_request_id;

    IF NEW.assignee_user_id IS NOT NULL THEN
        IF NEW.assignee_user_id = v_requester THEN
            RAISE EXCEPTION 'DCMS-INV-025: 申请 % 的申请人不得被指派处理其任何审批步骤', v_req_no USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT 1 FROM approval_step o
                    WHERE o.approval_request_id = NEW.approval_request_id AND o.submission_round = NEW.submission_round
                      AND o.id <> NEW.id AND o.assignee_user_id = NEW.assignee_user_id) THEN
            RAISE EXCEPTION 'DCMS-INV-026: 申请 % 的各审批步骤必须由不同的人处理', v_req_no USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.decision <> 'PENDING' AND NEW.decided_by IS NOT NULL THEN
        IF NEW.decided_by = v_requester THEN
            RAISE EXCEPTION 'DCMS-INV-025: 申请 % 的申请人不得处理其审批步骤', v_req_no USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT 1 FROM approval_step o
                    WHERE o.approval_request_id = NEW.approval_request_id AND o.submission_round = NEW.submission_round
                      AND o.id <> NEW.id AND o.decision <> 'PENDING' AND o.decided_by = NEW.decided_by) THEN
            RAISE EXCEPTION 'DCMS-INV-026: 同一人不得在申请 % 中签署多个审批步骤', v_req_no USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT 1 FROM approval_step o
                    WHERE o.approval_request_id = NEW.approval_request_id AND o.submission_round = NEW.submission_round
                      AND o.step_order < NEW.step_order AND o.decision <> 'APPROVED') THEN
            RAISE EXCEPTION 'DCMS-INV-027: 申请 % 的前序审批步骤尚未通过, 不得处理后序步骤', v_req_no USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_approval_step_people_order ON approval_step;
CREATE TRIGGER trg_approval_step_people_order AFTER INSERT OR UPDATE ON approval_step
    FOR EACH ROW EXECUTE FUNCTION dcms_check_step_people_order();

-- ---------------------------------------------------------------------
-- 5. 通知: 只在轮到某一步骤时通知它的处理人; 前序步骤通过时通知下一步
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_notify_pending() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE req approval_request; nxt approval_step;
BEGIN
 SELECT * INTO req FROM approval_request WHERE id=NEW.approval_request_id;
 IF req.status <> 'PENDING' OR NEW.submission_round <> req.submission_round THEN RETURN NEW; END IF;

 IF NEW.decision='PENDING' THEN
   IF EXISTS (SELECT 1 FROM approval_step o WHERE o.approval_request_id=NEW.approval_request_id
              AND o.submission_round=NEW.submission_round AND o.step_order<NEW.step_order AND o.decision<>'APPROVED') THEN
     RETURN NEW;                       -- 还没轮到
   END IF;
   INSERT INTO notification(user_id,request_id,submission_round,kind,title,body)
   SELECT u.id,req.id,req.submission_round,'PENDING','收到待审批申请',left(req.request_number||' · '||req.title,500)
   FROM app_user u WHERE u.is_active AND u.id<>req.requester_id AND
    (u.id=NEW.assignee_user_id OR (NEW.assignee_user_id IS NULL AND EXISTS
      (SELECT 1 FROM user_role ur WHERE ur.user_id=u.id AND ur.role_code=NEW.required_role_code)))
   ON CONFLICT DO NOTHING;
 ELSIF NEW.decision='APPROVED' AND NOT NEW.is_final THEN
   SELECT * INTO nxt FROM approval_step o WHERE o.approval_request_id=NEW.approval_request_id
      AND o.submission_round=NEW.submission_round AND o.step_order>NEW.step_order AND o.decision='PENDING'
      ORDER BY o.step_order LIMIT 1;
   IF FOUND THEN
     INSERT INTO notification(user_id,request_id,submission_round,kind,title,body)
     SELECT u.id,req.id,req.submission_round,'PENDING','收到待审批申请',left(req.request_number||' · '||req.title,500)
     FROM app_user u WHERE u.is_active AND u.id<>req.requester_id AND
      (u.id=nxt.assignee_user_id OR (nxt.assignee_user_id IS NULL AND EXISTS
        (SELECT 1 FROM user_role ur WHERE ur.user_id=u.id AND ur.role_code=nxt.required_role_code)))
     ON CONFLICT DO NOTHING;
   END IF;
 END IF;
 RETURN NEW;
END $$;
