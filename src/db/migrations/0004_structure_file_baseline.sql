-- =====================================================================
-- UG-DCMS 迁移 0004 — BOM、设计文件/版次、设计基线
-- 基准依据: SRS §8 §9 §10 / ERD §3 §4 §5 §6 / INV-006~015 022
-- =====================================================================

-- =====================================================================
-- 工作 BOM — SRS-BOM-001/003, INV-010
-- QTY / ITEM / Designator / Effectivity 只存在于 BOM 行, 不进 Part Master
-- =====================================================================
CREATE TABLE bom_header (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_design_object_id  uuid NOT NULL REFERENCES design_object (id),
    status                   text NOT NULL DEFAULT 'WORKING'
                             CHECK (status IN ('WORKING', 'SUPERSEDED')),
    working_sequence         integer NOT NULL DEFAULT 1,
    notes                    text,
    last_validated_at        timestamptz,
    last_validation_result   jsonb,
    created_at               timestamptz NOT NULL DEFAULT now(),
    created_by               uuid REFERENCES app_user (id),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    updated_by               uuid REFERENCES app_user (id),
    CONSTRAINT uq_bom_header_seq UNIQUE (parent_design_object_id, working_sequence)
);
-- 一个父对象同时只有一个 WORKING BOM
CREATE UNIQUE INDEX uq_bom_header_working ON bom_header (parent_design_object_id)
    WHERE status = 'WORKING';
CREATE INDEX idx_bom_parent ON bom_header (parent_design_object_id);          -- ERD §7
CREATE TRIGGER trg_bom_header_touch BEFORE UPDATE ON bom_header
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

CREATE TABLE bom_line (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_header_id           uuid NOT NULL REFERENCES bom_header (id) ON DELETE CASCADE,
    item_number             text NOT NULL,
    child_design_object_id  uuid NOT NULL REFERENCES design_object (id),     -- FK-02
    quantity                numeric(18, 6) NOT NULL,
    unit_code               text REFERENCES unit (code),
    reference_designator    text,
    effectivity             text,
    notes                   text,
    sort_order              integer NOT NULL DEFAULT 0,
    created_at              timestamptz NOT NULL DEFAULT now(),
    created_by              uuid REFERENCES app_user (id),
    CONSTRAINT ck_bom_line_qty CHECK (quantity > 0),                          -- CK-02 / AC 数据完整性
    CONSTRAINT uq_bom_line_item UNIQUE (bom_header_id, item_number)
);
CREATE INDEX idx_bom_child ON bom_line (child_design_object_id);              -- ERD §7
CREATE INDEX idx_bom_line_header ON bom_line (bom_header_id, sort_order);

-- =====================================================================
-- 不可变 BOM 快照 — SRS-BOM-004, INV-015
-- 正式基线只引用快照, 绝不引用"当前 BOM"
-- =====================================================================
CREATE TABLE bom_snapshot (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_design_object_id  uuid NOT NULL REFERENCES design_object (id),
    snapshot_number          text NOT NULL,
    source_bom_header_id     uuid REFERENCES bom_header (id),
    hash_sha256              text NOT NULL,
    line_count               integer NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    created_by               uuid REFERENCES app_user (id),
    CONSTRAINT uq_bom_snapshot_number UNIQUE (snapshot_number),
    CONSTRAINT ck_bom_snapshot_lines CHECK (line_count >= 0)
);
CREATE INDEX idx_bom_snapshot_parent ON bom_snapshot (parent_design_object_id, created_at DESC);

CREATE TABLE bom_snapshot_line (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_snapshot_id         uuid NOT NULL REFERENCES bom_snapshot (id),
    item_number             text NOT NULL,
    child_design_object_id  uuid NOT NULL REFERENCES design_object (id),
    child_object_code       text NOT NULL,          -- 冗余固化, 保证历史可读
    child_display_name      text NOT NULL,
    quantity                numeric(18, 6) NOT NULL,
    unit_code               text,
    reference_designator    text,
    effectivity             text,
    notes                   text,
    sort_order              integer NOT NULL DEFAULT 0,
    CONSTRAINT ck_bom_snapshot_line_qty CHECK (quantity > 0),
    CONSTRAINT uq_bom_snapshot_line UNIQUE (bom_snapshot_id, item_number)
);
CREATE INDEX idx_bom_snapshot_line_child ON bom_snapshot_line (child_design_object_id);
CREATE INDEX idx_bom_snapshot_line_snap ON bom_snapshot_line (bom_snapshot_id, sort_order);

-- =====================================================================
-- 设计文件 — SRS-FIL-001/002, INV-006
-- 文件身份与版次分离
-- =====================================================================
CREATE TABLE design_file (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_number                 text NOT NULL,
    file_type_code              text NOT NULL REFERENCES file_type (code),
    title_cn                    text NOT NULL,
    title_en                    text,
    status                      text NOT NULL DEFAULT 'ACTIVE'
                                CHECK (status IN ('ACTIVE', 'OBSOLETE')),
    owner_user_id               uuid REFERENCES app_user (id),
    current_released_revision_id uuid,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    created_by                  uuid REFERENCES app_user (id),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    updated_by                  uuid REFERENCES app_user (id),
    CONSTRAINT uq_design_file_number UNIQUE (file_number)
);
CREATE INDEX idx_design_file_title ON design_file (lower(title_cn));
CREATE TRIGGER trg_design_file_touch BEFORE UPDATE ON design_file
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

CREATE TABLE file_revision (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_file_id      uuid NOT NULL REFERENCES design_file (id),
    revision_number     text NOT NULL,          -- 新文件 00/01/02; 历史兼容字母 (ERD §9)
    revision_sequence   integer NOT NULL,
    status              text NOT NULL DEFAULT 'WORKING'
                        CHECK (status IN ('WORKING', 'IN_REVIEW', 'RELEASED',
                                          'SUPERSEDED', 'CANCELLED')),        -- DD §3
    revision_date       date,
    change_summary      text,
    change_package_id   uuid,
    prepared_by         uuid REFERENCES app_user (id),
    checked_by          uuid REFERENCES app_user (id),
    approved_by         uuid REFERENCES app_user (id),
    approval_request_id uuid,
    released_at         timestamptz,
    superseded_at       timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          uuid REFERENCES app_user (id),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          uuid REFERENCES app_user (id),
    CONSTRAINT uq_file_revision UNIQUE (design_file_id, revision_number),      -- UQ-05
    CONSTRAINT uq_file_revision_seq UNIQUE (design_file_id, revision_sequence),
    CONSTRAINT ck_file_revision_released CHECK (
        status NOT IN ('RELEASED', 'SUPERSEDED')
        OR (approved_by IS NOT NULL AND released_at IS NOT NULL))
);
CREATE INDEX idx_file_revision_file ON file_revision (design_file_id, revision_sequence DESC);
CREATE INDEX idx_file_revision_status ON file_revision (status);

ALTER TABLE design_file
    ADD CONSTRAINT fk_design_file_current_rev
    FOREIGN KEY (current_released_revision_id) REFERENCES file_revision (id)
    DEFERRABLE INITIALLY DEFERRED;

-- 附件 — SRS-FIL-003/004, INV-007, AC-DATA-05
CREATE TABLE revision_attachment (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_revision_id  uuid NOT NULL REFERENCES file_revision (id),
    attachment_role   text NOT NULL
                      CHECK (attachment_role IN ('PRIMARY_NATIVE', 'RELEASED_PDF',
                                                 'DERIVED_STEP', 'DERIVED_DXF',
                                                 'REFERENCE')),               -- DD §7
    filename          text NOT NULL,
    storage_key       text NOT NULL,             -- 存储抽象键, 不保存人工 Windows 路径
    mime_type         text NOT NULL,
    size_bytes        bigint NOT NULL,
    sha256            text NOT NULL,             -- INV-007 / AC-DATA-05
    integrity_checked_at timestamptz,
    integrity_status  text NOT NULL DEFAULT 'UNKNOWN'
                      CHECK (integrity_status IN ('UNKNOWN', 'OK', 'MISMATCH', 'MISSING')),
    uploaded_by       uuid REFERENCES app_user (id),
    uploaded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_revision_attachment_key UNIQUE (storage_key),
    CONSTRAINT ck_attachment_size CHECK (size_bytes >= 0),
    CONSTRAINT ck_attachment_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX idx_attachment_revision ON revision_attachment (file_revision_id, attachment_role);
-- 一个版次内每种主要角色只允许一份 (派生格式与参考件可多份)
CREATE UNIQUE INDEX uq_attachment_primary_role ON revision_attachment (file_revision_id, attachment_role)
    WHERE attachment_role IN ('PRIMARY_NATIVE', 'RELEASED_PDF');

-- =====================================================================
-- 设计定义关系 — SRS-FIL-005/006, INV-022
-- 定义 P/N 与设计文件的逻辑关系; 实际采用版次由基线锁定
-- =====================================================================
CREATE TABLE design_definition_link (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_object_id   uuid NOT NULL REFERENCES design_object (id),
    design_file_id     uuid NOT NULL REFERENCES design_file (id),
    relation_type      text NOT NULL
                       CHECK (relation_type IN ('PRIMARY_DEFINITION', 'SUPPORTING_DEFINITION',
                                                'INTERFACE_DEFINITION', 'QUALIFICATION_EVIDENCE')),
    applicability_note text,
    is_active          boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now(),
    created_by         uuid REFERENCES app_user (id),
    deactivated_at     timestamptz,
    deactivated_by     uuid REFERENCES app_user (id),
    CONSTRAINT uq_definition_link UNIQUE (design_object_id, design_file_id, relation_type)
);
CREATE INDEX idx_definition_object ON design_definition_link (design_object_id);   -- ERD §7
CREATE INDEX idx_definition_file ON design_definition_link (design_file_id);
-- 一个对象同时只有一个有效主设计定义 (INV-022 的结构保障)
CREATE UNIQUE INDEX uq_definition_primary ON design_definition_link (design_object_id)
    WHERE relation_type = 'PRIMARY_DEFINITION' AND is_active;

-- =====================================================================
-- 设计基线 — SRS-BL-001~008, INV-011/012/013
-- =====================================================================
CREATE TABLE design_baseline (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    part_number_id      uuid NOT NULL REFERENCES part_number (id),
    baseline_sequence   integer NOT NULL,
    baseline_code       text NOT NULL,            -- BL-001, BL-002 ...
    status              text NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT', 'IN_REVIEW', 'RELEASED',
                                          'SUPERSEDED', 'CANCELLED')),
    is_current          boolean NOT NULL DEFAULT false,
    reason              text NOT NULL,
    change_package_id   uuid,
    copied_from_baseline_id uuid REFERENCES design_baseline (id),
    content_hash        text,                     -- SRS-BL-007
    validation_result   jsonb,
    prepared_by         uuid REFERENCES app_user (id),
    approved_by         uuid REFERENCES app_user (id),
    approval_request_id uuid,
    released_at         timestamptz,
    superseded_at       timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          uuid REFERENCES app_user (id),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          uuid REFERENCES app_user (id),
    CONSTRAINT uq_baseline_sequence UNIQUE (part_number_id, baseline_sequence),    -- UQ-06
    CONSTRAINT uq_baseline_code UNIQUE (part_number_id, baseline_code),
    CONSTRAINT ck_baseline_sequence CHECK (baseline_sequence >= 1),
    CONSTRAINT ck_baseline_current CHECK (NOT is_current OR status = 'RELEASED'),
    CONSTRAINT ck_baseline_released CHECK (
        status NOT IN ('RELEASED', 'SUPERSEDED')
        OR (approved_by IS NOT NULL AND released_at IS NOT NULL AND content_hash IS NOT NULL))
);
CREATE INDEX idx_baseline_part ON design_baseline (part_number_id, status);        -- ERD §7
-- INV-013: 同一 P/N 同一时刻只能有一个 Current Baseline
CREATE UNIQUE INDEX uq_baseline_one_current ON design_baseline (part_number_id)
    WHERE is_current;

-- FK-03: current_baseline 必须属于该 P/N (值正确性由 0005 触发器保证)
ALTER TABLE part_number
    ADD CONSTRAINT fk_part_current_baseline
    FOREIGN KEY (current_baseline_id) REFERENCES design_baseline (id)
    DEFERRABLE INITIALLY DEFERRED;

-- 基线项 — SRS-BL-002, INV-012 (只允许指向确定版次的硬引用)
CREATE TABLE baseline_item (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_baseline_id          uuid NOT NULL REFERENCES design_baseline (id),
    item_type                   text NOT NULL
                                CHECK (item_type IN ('FILE_REVISION', 'BOM_SNAPSHOT',
                                                     'EXTERNAL_TECHNICAL_STATE',
                                                     'SOFTWARE_VERSION')),
    file_revision_id            uuid REFERENCES file_revision (id),
    bom_snapshot_id             uuid REFERENCES bom_snapshot (id),
    external_technical_state_id uuid REFERENCES external_technical_state (id),
    software_version_id         uuid REFERENCES software_version (id),
    item_role                   text,             -- PRIMARY_DEFINITION / SUPPORTING_... 等
    sequence                    integer NOT NULL DEFAULT 0,
    notes                       text,
    -- INV-012: 恰好一个目标非空, 且与 item_type 匹配; 无任何 "latest/current" 字段
    CONSTRAINT ck_baseline_item_target CHECK (
        (CASE WHEN file_revision_id IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN bom_snapshot_id IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN external_technical_state_id IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN software_version_id IS NOT NULL THEN 1 ELSE 0 END) = 1),
    CONSTRAINT ck_baseline_item_type_match CHECK (
        (item_type = 'FILE_REVISION'             AND file_revision_id IS NOT NULL) OR
        (item_type = 'BOM_SNAPSHOT'              AND bom_snapshot_id IS NOT NULL) OR
        (item_type = 'EXTERNAL_TECHNICAL_STATE'  AND external_technical_state_id IS NOT NULL) OR
        (item_type = 'SOFTWARE_VERSION'          AND software_version_id IS NOT NULL))
);
CREATE INDEX idx_baseline_item_baseline ON baseline_item (design_baseline_id, item_type, sequence);
CREATE INDEX idx_baseline_item_file_rev ON baseline_item (file_revision_id);
CREATE INDEX idx_baseline_item_snapshot ON baseline_item (bom_snapshot_id);
CREATE INDEX idx_baseline_item_ext_ts ON baseline_item (external_technical_state_id);
-- 一个基线内同一目标不得重复出现
CREATE UNIQUE INDEX uq_baseline_item_file_rev ON baseline_item (design_baseline_id, file_revision_id)
    WHERE file_revision_id IS NOT NULL;
CREATE UNIQUE INDEX uq_baseline_item_snapshot ON baseline_item (design_baseline_id, bom_snapshot_id)
    WHERE bom_snapshot_id IS NOT NULL;
