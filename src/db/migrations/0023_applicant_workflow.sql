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
