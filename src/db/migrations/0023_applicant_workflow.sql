-- Keep a stable application number across resubmissions; prior rounds stay immutable.
ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS submission_round integer NOT NULL DEFAULT 1;
ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS closure_action text;
ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS closure_reason text;
CREATE TABLE IF NOT EXISTS approval_round_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_request_id uuid NOT NULL REFERENCES approval_request(id),
    submission_round integer NOT NULL,
    snapshot jsonb NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(approval_request_id, submission_round)
);
DROP TRIGGER IF EXISTS trg_approval_round_immutable ON approval_round_history;
CREATE TRIGGER trg_approval_round_immutable BEFORE UPDATE OR DELETE ON approval_round_history
FOR EACH ROW EXECUTE FUNCTION dcms_block_write();

-- Preserve decided steps in place. Each resubmission gets a new set of steps;
-- the original audit trigger remains active and is never bypassed.
ALTER TABLE approval_step ADD COLUMN IF NOT EXISTS submission_round integer NOT NULL DEFAULT 1;
ALTER TABLE approval_step DROP CONSTRAINT IF EXISTS uq_approval_step;
ALTER TABLE approval_step ADD CONSTRAINT uq_approval_step UNIQUE (approval_request_id, submission_round, step_order);
DROP INDEX IF EXISTS uq_approval_step_final;
CREATE UNIQUE INDEX uq_approval_step_final ON approval_step (approval_request_id, submission_round) WHERE is_final;

CREATE OR REPLACE FUNCTION dcms_guard_step_round() RETURNS trigger AS $$
DECLARE current_round integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT submission_round INTO current_round FROM approval_request WHERE id=NEW.approval_request_id;
        NEW.submission_round := current_round;
        RETURN NEW;
    END IF;
    SELECT submission_round INTO current_round FROM approval_request WHERE id=OLD.approval_request_id;
    IF OLD.submission_round <> current_round THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 历史轮次审批步骤不可修改或删除' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.submission_round,NEW.approval_request_id) IS DISTINCT FROM
        (OLD.submission_round,OLD.approval_request_id) THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 审批步骤不可迁移到其他申请或轮次' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_approval_step_round ON approval_step;
CREATE TRIGGER trg_approval_step_round BEFORE INSERT OR UPDATE OR DELETE ON approval_step
FOR EACH ROW EXECUTE FUNCTION dcms_guard_step_round();

-- This simple updatable view keeps object-specific actions scoped to the
-- active round, including withdrawn steps that still say PENDING.
CREATE OR REPLACE VIEW current_approval_step AS
SELECT s.* FROM approval_step s
WHERE s.submission_round=(SELECT r.submission_round FROM approval_request r WHERE r.id=s.approval_request_id);
