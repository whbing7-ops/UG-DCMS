-- Operational progress is committed separately from the atomic business import.
CREATE TABLE IF NOT EXISTS data_import_job (
    slot integer PRIMARY KEY CHECK (slot = 1),
    id uuid NOT NULL UNIQUE,
    dataset_code text NOT NULL,
    state text NOT NULL CHECK (state IN ('QUEUED','RUNNING','COMPLETED','FAILED','INTERRUPTED')),
    stage text NOT NULL,
    completed integer NOT NULL DEFAULT 0,
    total integer NOT NULL DEFAULT 0,
    progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    message text NOT NULL,
    requested_by uuid NOT NULL REFERENCES app_user(id),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz
);
