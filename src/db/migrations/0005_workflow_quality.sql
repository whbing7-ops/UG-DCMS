-- =====================================================================
-- UG-DCMS 迁移 0005 — 变更、审批、数据质量、导入批次
-- 基准依据: SRS §11 §12 / ERD §4 §9 / INV-025 / AC-IMP-01 AC-SEC-04
-- =====================================================================

-- =====================================================================
-- 变更包 — SRS-CHG-001 (V1.0 轻量)
-- =====================================================================
CREATE TABLE change_package (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    change_number  text NOT NULL,
    title          text NOT NULL,
    reason         text NOT NULL,
    impact_summary text,
    status         text NOT NULL DEFAULT 'DRAFT'
                   CHECK (status IN ('DRAFT', 'IN_REVIEW', 'APPROVED',
                                     'IMPLEMENTED', 'CANCELLED')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     uuid REFERENCES app_user (id),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     uuid REFERENCES app_user (id),
    closed_at      timestamptz,
    CONSTRAINT uq_change_package_number UNIQUE (change_number)
);
CREATE TRIGGER trg_change_package_touch BEFORE UPDATE ON change_package
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

CREATE TABLE change_package_item (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    change_package_id uuid NOT NULL REFERENCES change_package (id),
    item_type         text NOT NULL
                      CHECK (item_type IN ('PART_NUMBER', 'DESIGN_FILE', 'FILE_REVISION',
                                           'BOM_SNAPSHOT', 'DESIGN_BASELINE',
                                           'EXTERNAL_TECHNICAL_STATE')),
    target_id         uuid NOT NULL,
    target_code       text,
    disposition       text NOT NULL DEFAULT 'ADOPT'
                      CHECK (disposition IN ('ADOPT', 'NOT_AFFECTED', 'DEFER')),  -- UI §13
    note              text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_change_package_item UNIQUE (change_package_id, item_type, target_id)
);
CREATE INDEX idx_change_item_target ON change_package_item (item_type, target_id);

-- 补齐前序迁移中留空的 change_package 外键
ALTER TABLE file_revision
    ADD CONSTRAINT fk_file_revision_change_package
    FOREIGN KEY (change_package_id) REFERENCES change_package (id);
ALTER TABLE design_baseline
    ADD CONSTRAINT fk_baseline_change_package
    FOREIGN KEY (change_package_id) REFERENCES change_package (id);

-- =====================================================================
-- 审批 — SRS-APR-001, SRS-ACC-006, INV-025, AC-SEC-04
-- =====================================================================
CREATE TABLE approval_request (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_number text NOT NULL,
    request_type  text NOT NULL
                  CHECK (request_type IN ('BASIC_DRAWING_NUMBER', 'DASH_NUMBER',
                                          'DESIGN_FILE_NUMBER', 'FILE_REVISION_RELEASE',
                                          'BASELINE_RELEASE', 'EXTERNAL_TS_ACCEPT',
                                          'NUMBER_CANCELLATION', 'DICTIONARY_CHANGE',
                                          'CHANGE_PACKAGE', 'FAMILY_CLOSE',
                                          'OBJECT_OBSOLETE')),
    object_type   text NOT NULL,
    object_id     uuid,
    object_code   text,
    title         text NOT NULL,
    status        text NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED',
                                    'RETURNED', 'CANCELLED')),
    requester_id  uuid NOT NULL REFERENCES app_user (id),
    requested_at  timestamptz NOT NULL DEFAULT now(),
    closed_at     timestamptz,
    payload       jsonb,                    -- 申请时的提议内容, 供审批人看差异
    diff_summary  jsonb,                    -- UI §13: 我的审批显示差异而非最终值
    CONSTRAINT uq_approval_request_number UNIQUE (request_number)
);
CREATE INDEX idx_approval_request_status ON approval_request (status, requested_at DESC);
CREATE INDEX idx_approval_request_object ON approval_request (object_type, object_id);
CREATE INDEX idx_approval_request_requester ON approval_request (requester_id, status);

CREATE TABLE approval_step (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_request_id uuid NOT NULL REFERENCES approval_request (id),
    step_order          integer NOT NULL,
    step_name           text NOT NULL,
    required_role_code  text REFERENCES role (code),
    assignee_user_id    uuid REFERENCES app_user (id),
    is_final            boolean NOT NULL DEFAULT false,
    decision            text NOT NULL DEFAULT 'PENDING'
                        CHECK (decision IN ('PENDING', 'APPROVED', 'REJECTED', 'RETURNED')),
    decided_by          uuid REFERENCES app_user (id),
    comments            text,
    acted_at            timestamptz,
    CONSTRAINT uq_approval_step UNIQUE (approval_request_id, step_order),
    CONSTRAINT ck_approval_step_order CHECK (step_order >= 1),
    CONSTRAINT ck_approval_step_decided CHECK (
        decision = 'PENDING' OR (decided_by IS NOT NULL AND acted_at IS NOT NULL))
);
CREATE INDEX idx_approval_step_assignee ON approval_step (assignee_user_id, decision);
CREATE INDEX idx_approval_step_role ON approval_step (required_role_code, decision);
-- 一个申请只能有一个最终审批步骤
CREATE UNIQUE INDEX uq_approval_step_final ON approval_step (approval_request_id)
    WHERE is_final;

-- 补齐前序迁移中留空的 approval_request 外键
ALTER TABLE basic_drawing_family
    ADD CONSTRAINT fk_family_approval FOREIGN KEY (approval_request_id)
    REFERENCES approval_request (id);
ALTER TABLE number_allocation
    ADD CONSTRAINT fk_number_approval FOREIGN KEY (approval_request_id)
    REFERENCES approval_request (id);
ALTER TABLE file_revision
    ADD CONSTRAINT fk_file_revision_approval FOREIGN KEY (approval_request_id)
    REFERENCES approval_request (id);
ALTER TABLE design_baseline
    ADD CONSTRAINT fk_baseline_approval FOREIGN KEY (approval_request_id)
    REFERENCES approval_request (id);

-- =====================================================================
-- 数据质量 — SRS-DQ-001, 出厂门槛 §8
-- =====================================================================
CREATE TABLE data_quality_rule (
    code            text PRIMARY KEY,
    name_cn         text NOT NULL,
    severity        text NOT NULL CHECK (severity IN ('ERROR', 'WARNING', 'INFO')),
    object_type     text NOT NULL,
    description     text NOT NULL,
    blocks_release  boolean NOT NULL DEFAULT false,
    status          dict_status
);
-- ERROR 级规则必须阻止发布 (SRS-DQ-001)
ALTER TABLE data_quality_rule
    ADD CONSTRAINT ck_dq_rule_error_blocks
    CHECK (severity <> 'ERROR' OR blocks_release);

CREATE TABLE data_quality_issue (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_code      text NOT NULL REFERENCES data_quality_rule (code),
    object_type    text NOT NULL,
    object_id      uuid NOT NULL,
    object_code    text,
    severity       text NOT NULL CHECK (severity IN ('ERROR', 'WARNING', 'INFO')),
    message        text NOT NULL,
    status         text NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN', 'RESOLVED', 'WAIVED')),
    detected_at    timestamptz NOT NULL DEFAULT now(),
    resolved_at    timestamptz,
    resolved_by    uuid REFERENCES app_user (id),
    waiver_reason  text,
    CONSTRAINT ck_dq_issue_waiver CHECK (status <> 'WAIVED' OR waiver_reason IS NOT NULL),
    CONSTRAINT uq_dq_issue_open UNIQUE (rule_code, object_type, object_id, status)
);
CREATE INDEX idx_dq_issue_object ON data_quality_issue (object_type, object_id, status);
CREATE INDEX idx_dq_issue_open ON data_quality_issue (severity, status)
    WHERE status = 'OPEN';

-- =====================================================================
-- 导入批次 — SRS-IMP-001, AC-IMP-01, ERD §9
-- 迁移导入必须形成 Import Batch, 禁止后台 SQL 绕过审计与唯一约束
-- =====================================================================
CREATE TABLE import_batch (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_number    text NOT NULL,
    import_type     text NOT NULL
                    CHECK (import_type IN ('BOM', 'PART_NUMBER', 'EXTERNAL_PART',
                                           'CROSS_REFERENCE', 'ATTRIBUTE')),
    source_filename text NOT NULL,
    source_sha256   text,
    status          text NOT NULL DEFAULT 'PREVIEW'
                    CHECK (status IN ('PREVIEW', 'COMMITTED', 'ABORTED')),
    target_context  jsonb,                   -- 如 BOM 导入的父对象
    total_rows      integer NOT NULL DEFAULT 0,
    ok_rows         integer NOT NULL DEFAULT 0,
    warning_rows    integer NOT NULL DEFAULT 0,
    error_rows      integer NOT NULL DEFAULT 0,
    created_by      uuid REFERENCES app_user (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    committed_at    timestamptz,
    committed_by    uuid REFERENCES app_user (id),
    CONSTRAINT uq_import_batch_number UNIQUE (batch_number),
    -- AC-IMP-01: 有错误行的批次不得提交
    CONSTRAINT ck_import_batch_commit CHECK (status <> 'COMMITTED' OR error_rows = 0)
);

CREATE TABLE import_batch_row (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    import_batch_id     uuid NOT NULL REFERENCES import_batch (id) ON DELETE CASCADE,
    row_number          integer NOT NULL,
    raw_data            jsonb NOT NULL,
    result              text NOT NULL CHECK (result IN ('OK', 'WARNING', 'ERROR')),
    messages            jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_object_type text,
    created_object_id   uuid,
    CONSTRAINT uq_import_batch_row UNIQUE (import_batch_id, row_number)
);
CREATE INDEX idx_import_row_result ON import_batch_row (import_batch_id, result);

-- =====================================================================
-- 最近访问 — UI §3 首页
-- =====================================================================
CREATE TABLE recent_access (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES app_user (id),
    object_type text NOT NULL,
    object_id   uuid NOT NULL,
    object_code text NOT NULL,
    display_name text,
    accessed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_recent_access UNIQUE (user_id, object_type, object_id)
);
CREATE INDEX idx_recent_access_user ON recent_access (user_id, accessed_at DESC);
