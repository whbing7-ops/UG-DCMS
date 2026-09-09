-- =====================================================================
-- UG-DCMS 迁移 0003 — 设计对象身份层与主数据
-- 基准依据: SRS §4 §5 §6 §7 / ERD §3 §4 §5 / INV-001~005 016 018 021 023
-- =====================================================================

-- 生命周期状态域 — DD §3
CREATE DOMAIN lifecycle_status AS text
    NOT NULL DEFAULT 'DRAFT'
    CHECK (VALUE IN ('DRAFT', 'IN_REVIEW', 'RELEASED', 'OBSOLETE'));

-- =====================================================================
-- 统一设计对象身份层 — SRS-OBJ-001, ERD §1
-- BOM / 关系 / 属性 / 构型追溯全部引用 design_object
-- =====================================================================
CREATE TABLE design_object (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type      text NOT NULL
                     CHECK (object_type IN ('INTERNAL_PART', 'EXTERNAL_PART', 'SOFTWARE')),
    object_code      text NOT NULL,            -- Full P/N / NS::EXT-PN / 软件编号
    display_name     text NOT NULL,
    lifecycle_status lifecycle_status,
    data_origin      text NOT NULL DEFAULT 'NATIVE'
                     CHECK (data_origin IN ('NATIVE', 'LEGACY')),          -- ERD §9
    data_maturity    text NOT NULL DEFAULT 'L0'
                     CHECK (data_maturity IN ('L0', 'L1', 'L2', 'L3', 'L4')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       uuid REFERENCES app_user (id),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       uuid REFERENCES app_user (id),
    obsoleted_at     timestamptz,
    obsoleted_by     uuid REFERENCES app_user (id),
    CONSTRAINT uq_design_object_code UNIQUE (object_code)
);
CREATE INDEX idx_design_object_code ON design_object (object_code, object_type);   -- ERD §7
CREATE INDEX idx_design_object_status ON design_object (lifecycle_status, object_type);
CREATE INDEX idx_design_object_name ON design_object (lower(display_name));
CREATE TRIGGER trg_design_object_touch BEFORE UPDATE ON design_object
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

-- =====================================================================
-- 基本图号设计族 — SRS-FAM-001/002/003, INV-005, INV-023
-- 与 part_number 是两个独立实体, 1:N
-- =====================================================================
CREATE TABLE basic_drawing_family (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    basic_drawing_number  text NOT NULL,                                  -- UQ-01
    primary_class_code    text NOT NULL REFERENCES primary_class (code),
    physical_class_id     uuid NOT NULL REFERENCES physical_class (id),
    object_level_code     text NOT NULL REFERENCES object_level (code),
    family_name_cn        text NOT NULL,
    family_name_en        text NOT NULL,
    core_term_id          uuid NOT NULL REFERENCES naming_core_term (id),  -- INV-023
    qualifier_1_id        uuid REFERENCES naming_qualifier (id),
    qualifier_2_id        uuid REFERENCES naming_qualifier (id),
    primary_function_id   uuid REFERENCES function_item (id),
    family_definition     text NOT NULL,
    allowed_variation     text NOT NULL,
    excluded_variation    text NOT NULL,
    status                text NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING', 'ACTIVE', 'CLOSED')),  -- DD §3
    similar_check_at      timestamptz,
    similar_check_result  jsonb,
    reuse_decision        text CHECK (reuse_decision IN ('REUSE_EXISTING', 'NEW_FAMILY')),
    new_family_reason     text,
    approval_request_id   uuid,
    approved_by           uuid REFERENCES app_user (id),
    approved_at           timestamptz,
    closed_reason         text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    created_by            uuid REFERENCES app_user (id),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            uuid REFERENCES app_user (id),
    CONSTRAINT uq_basic_drawing_number UNIQUE (basic_drawing_number),
    CONSTRAINT ck_family_qualifiers CHECK (qualifier_2_id IS NULL OR qualifier_1_id IS NOT NULL),
    CONSTRAINT ck_family_qualifier_distinct CHECK (qualifier_1_id IS DISTINCT FROM qualifier_2_id
                                                   OR qualifier_1_id IS NULL),
    -- AC-FAM-01: 必须先做相似族检索并给出复用/新建决定
    CONSTRAINT ck_family_similar_check CHECK (
        status = 'PENDING'
        OR (similar_check_at IS NOT NULL AND reuse_decision IS NOT NULL
            AND (reuse_decision <> 'NEW_FAMILY' OR new_family_reason IS NOT NULL))
    )
);
CREATE INDEX idx_family_class ON basic_drawing_family (primary_class_code, physical_class_id);
CREATE INDEX idx_family_core_term ON basic_drawing_family (core_term_id);
CREATE INDEX idx_family_name ON basic_drawing_family (lower(family_name_cn));
CREATE TRIGGER trg_family_touch BEFORE UPDATE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

-- =====================================================================
-- Dash P/N — SRS-PN-001/002/003, INV-001/002/003
-- current_baseline_id 的 FK 在 0005 建立 design_baseline 之后补
-- =====================================================================
CREATE TABLE part_number (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_object_id        uuid NOT NULL REFERENCES design_object (id),   -- INV-001
    basic_drawing_family_id uuid NOT NULL REFERENCES basic_drawing_family (id),  -- FK-01
    dash_number             integer NOT NULL,
    full_part_number        text NOT NULL,
    formal_name_cn          text NOT NULL,
    formal_name_en          text NOT NULL,
    object_level_code       text NOT NULL REFERENCES object_level (code),
    lifecycle_status        lifecycle_status,
    current_baseline_id     uuid,
    difference_summary      text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    created_by              uuid REFERENCES app_user (id),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    updated_by              uuid REFERENCES app_user (id),
    CONSTRAINT uq_part_number_design_object UNIQUE (design_object_id),     -- INV-001
    CONSTRAINT uq_part_number_full UNIQUE (full_part_number),              -- UQ-02
    CONSTRAINT uq_part_number_family_dash UNIQUE (basic_drawing_family_id, dash_number), -- UQ-03 / INV-002
    CONSTRAINT ck_part_number_dash CHECK (dash_number BETWEEN 1 AND 999)   -- CK-01 / INV-003
);
CREATE INDEX idx_part_family_dash ON part_number (basic_drawing_family_id, dash_number);
CREATE INDEX idx_part_status ON part_number (lifecycle_status);
CREATE INDEX idx_part_name ON part_number (lower(formal_name_cn));
CREATE TRIGGER trg_part_number_touch BEFORE UPDATE ON part_number
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

-- =====================================================================
-- 外部件 — SRS-EXT-001/002/003, INV-016/017
-- 身份 (namespace + external_part_number) 与技术状态分离
-- =====================================================================
CREATE TABLE external_part (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_object_id     uuid NOT NULL REFERENCES design_object (id),
    namespace_id         uuid NOT NULL REFERENCES namespace (id),
    external_part_number text NOT NULL,
    manufacturer_id      uuid REFERENCES manufacturer (id),
    name_cn              text NOT NULL,
    name_en              text,
    lifecycle_status     lifecycle_status,
    created_at           timestamptz NOT NULL DEFAULT now(),
    created_by           uuid REFERENCES app_user (id),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           uuid REFERENCES app_user (id),
    CONSTRAINT uq_external_part_design_object UNIQUE (design_object_id),
    CONSTRAINT uq_external_part_ns_pn UNIQUE (namespace_id, external_part_number)  -- UQ-04 / INV-016
);
CREATE INDEX idx_ext_part_ns_pn ON external_part (namespace_id, external_part_number);
CREATE INDEX idx_ext_part_pn ON external_part (upper(external_part_number));
CREATE TRIGGER trg_external_part_touch BEFORE UPDATE ON external_part
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

CREATE TABLE external_technical_state (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_part_id        uuid NOT NULL REFERENCES external_part (id),
    state_sequence          integer NOT NULL,
    supplier_revision       text,
    supplier_document       text,
    supplier_document_date  date,
    hash_sha256             text,
    status                  text NOT NULL DEFAULT 'DRAFT'
                            CHECK (status IN ('DRAFT', 'IN_REVIEW', 'ACCEPTED',
                                              'SUPERSEDED', 'REJECTED')),
    accepted_by             uuid REFERENCES app_user (id),
    accepted_at             timestamptz,
    notes                   text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    created_by              uuid REFERENCES app_user (id),
    CONSTRAINT uq_ext_ts_sequence UNIQUE (external_part_id, state_sequence),
    CONSTRAINT ck_ext_ts_sequence CHECK (state_sequence >= 1),
    CONSTRAINT ck_ext_ts_accepted CHECK (status <> 'ACCEPTED'
                                         OR (accepted_by IS NOT NULL AND accepted_at IS NOT NULL))
);
CREATE INDEX idx_ext_ts_part ON external_technical_state (external_part_id, status);

-- =====================================================================
-- 软件对象 — SRS-SW-001/002, INV-018
-- =====================================================================
CREATE TABLE software_object (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_object_id    uuid NOT NULL REFERENCES design_object (id),
    software_number     text NOT NULL,
    name_cn             text NOT NULL,
    name_en             text,
    software_type       text NOT NULL
                        CHECK (software_type IN ('SOFTWARE', 'FIRMWARE',
                                                 'CONFIG_DATA', 'LOADABLE')),
    lifecycle_status    lifecycle_status,
    current_version_id  uuid,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          uuid REFERENCES app_user (id),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          uuid REFERENCES app_user (id),
    CONSTRAINT uq_software_object_design_object UNIQUE (design_object_id),
    CONSTRAINT uq_software_number UNIQUE (software_number)
);
CREATE TRIGGER trg_software_object_touch BEFORE UPDATE ON software_object
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

CREATE TABLE software_version (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    software_object_id  uuid NOT NULL REFERENCES software_object (id),
    version             text NOT NULL,
    build               text NOT NULL DEFAULT '',
    hash_sha256         text,
    status              text NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT', 'IN_REVIEW', 'RELEASED', 'SUPERSEDED')),
    released_at         timestamptz,
    released_by         uuid REFERENCES app_user (id),
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          uuid REFERENCES app_user (id),
    CONSTRAINT uq_software_version UNIQUE (software_object_id, version, build)
);
ALTER TABLE software_object
    ADD CONSTRAINT fk_software_current_version
    FOREIGN KEY (current_version_id) REFERENCES software_version (id)
    DEFERRABLE INITIALLY DEFERRED;

-- =====================================================================
-- 功能分配 — SRS-FUN-001, UQ-07, INV-021
-- =====================================================================
CREATE TABLE object_function (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_object_id  uuid NOT NULL REFERENCES design_object (id),
    function_item_id  uuid NOT NULL REFERENCES function_item (id),
    function_role     text NOT NULL CHECK (function_role IN ('PRIMARY', 'AUXILIARY')),
    notes             text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        uuid REFERENCES app_user (id),
    CONSTRAINT uq_object_function UNIQUE (design_object_id, function_item_id)
);
-- INV-021 / UQ-07: 同一 DesignObject 最多一个 PRIMARY 功能
CREATE UNIQUE INDEX uq_object_function_primary ON object_function (design_object_id)
    WHERE function_role = 'PRIMARY';

-- =====================================================================
-- 属性值 — SRS-ATT-001
-- =====================================================================
CREATE TABLE object_attribute_value (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_object_id        uuid NOT NULL REFERENCES design_object (id),
    attribute_definition_id uuid NOT NULL REFERENCES attribute_definition (id),
    value_text              text,
    value_number            numeric(30, 8),
    value_integer           bigint,
    value_boolean           boolean,
    value_date              date,
    value_enum_code         text,
    value_reference_id      uuid,
    unit_code               text REFERENCES unit (code),
    created_at              timestamptz NOT NULL DEFAULT now(),
    created_by              uuid REFERENCES app_user (id),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    updated_by              uuid REFERENCES app_user (id),
    CONSTRAINT uq_object_attribute UNIQUE (design_object_id, attribute_definition_id),
    -- 恰好一个 value_* 非空
    CONSTRAINT ck_attr_value_one CHECK (
        (CASE WHEN value_text IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN value_number IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN value_integer IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN value_boolean IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN value_date IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN value_enum_code IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN value_reference_id IS NOT NULL THEN 1 ELSE 0 END) = 1)
);
CREATE INDEX idx_attr_value_object ON object_attribute_value (design_object_id);
CREATE INDEX idx_attr_value_def ON object_attribute_value (attribute_definition_id, value_number);
CREATE INDEX idx_attr_value_text ON object_attribute_value (attribute_definition_id, lower(value_text));
CREATE TRIGGER trg_attr_value_touch BEFORE UPDATE ON object_attribute_value
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

-- =====================================================================
-- 对象关系 — DD §8
-- =====================================================================
CREATE TABLE object_relationship (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_object_id   uuid NOT NULL REFERENCES design_object (id),
    target_object_id   uuid NOT NULL REFERENCES design_object (id),
    relationship_type  text NOT NULL
                       CHECK (relationship_type IN ('SUPERSEDES', 'ALTERNATE',
                                                    'INTERCHANGEABLE', 'DERIVED_FROM',
                                                    'REPLACEMENT_FOR')),
    interchangeability text CHECK (interchangeability IN ('TWO_WAY', 'ONE_WAY', 'NONE')),
    effective_from     date,
    effective_to       date,
    reason             text,
    status             text NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'CANCELLED')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    created_by         uuid REFERENCES app_user (id),
    CONSTRAINT ck_relationship_not_self CHECK (source_object_id <> target_object_id),
    CONSTRAINT ck_relationship_dates CHECK (effective_to IS NULL OR effective_from IS NULL
                                            OR effective_to >= effective_from),
    CONSTRAINT uq_object_relationship UNIQUE (source_object_id, target_object_id, relationship_type)
);
CREATE INDEX idx_relationship_source ON object_relationship (source_object_id, status);
CREATE INDEX idx_relationship_target ON object_relationship (target_object_id, status);

-- =====================================================================
-- 交叉引用 — DD §8, SRS-SRH-001, AC-SEARCH-01
-- normalized_value 用于 43025-0400 / 0430250400 / 430250400 归一化匹配 (UI §12)
-- =====================================================================
CREATE TABLE cross_reference (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_object_id  uuid NOT NULL REFERENCES design_object (id),
    reference_type    text NOT NULL
                      CHECK (reference_type IN ('OEM_PN', 'CUSTOMER_PN', 'LEGACY_PN',
                                                'SUPPLIER_PN', 'ALTERNATE_IDENTIFIER')),
    namespace_id      uuid REFERENCES namespace (id),
    reference_value   text NOT NULL,
    normalized_value  text NOT NULL,
    notes             text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        uuid REFERENCES app_user (id),
    CONSTRAINT uq_cross_reference UNIQUE (design_object_id, reference_type,
                                          namespace_id, reference_value)
);
CREATE INDEX idx_cross_reference ON cross_reference (reference_value);            -- ERD §7
CREATE INDEX idx_cross_reference_norm ON cross_reference (normalized_value);
CREATE INDEX idx_cross_reference_object ON cross_reference (design_object_id);

-- =====================================================================
-- 发号中心 — SRS-NUM-001~004, INV-004
-- 号码一经 ALLOCATED / CANCELLED 永久保留且不得复用
-- =====================================================================
CREATE TABLE number_allocation (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    number_type             text NOT NULL
                            CHECK (number_type IN ('BASIC_DRAWING', 'DASH',
                                                   'SOFTWARE_NUMBER', 'DESIGN_FILE')),
    basic_drawing_family_id uuid REFERENCES basic_drawing_family (id),
    allocated_number        text NOT NULL,
    numeric_sequence        integer,
    status                  text NOT NULL DEFAULT 'RESERVED'
                            CHECK (status IN ('RESERVED', 'ALLOCATED', 'CANCELLED')),
    reserve_reason          text,
    cancel_reason           text,
    skip_reason             text,                       -- SRS-NUM-004 跳号理由
    target_object_type      text,
    target_object_id        uuid,
    requested_by            uuid REFERENCES app_user (id),
    requested_at            timestamptz NOT NULL DEFAULT now(),
    approval_request_id     uuid,
    approved_by             uuid REFERENCES app_user (id),
    approved_at             timestamptz,
    allocated_at            timestamptz,
    cancelled_at            timestamptz,
    CONSTRAINT ck_number_dash_family CHECK (number_type <> 'DASH'
                                            OR basic_drawing_family_id IS NOT NULL),
    CONSTRAINT ck_number_cancel_reason CHECK (status <> 'CANCELLED' OR cancel_reason IS NOT NULL)
);
-- 同一号码空间内号码唯一 (DASH 的空间是 family, 其余是全局)
CREATE UNIQUE INDEX uq_number_allocation ON number_allocation (
    number_type,
    COALESCE(basic_drawing_family_id, '00000000-0000-0000-0000-000000000000'::uuid),
    allocated_number
);
CREATE INDEX idx_number_allocation_status ON number_allocation (number_type, status);
CREATE INDEX idx_number_allocation_seq ON number_allocation (
    number_type, basic_drawing_family_id, numeric_sequence);
CREATE INDEX idx_number_allocation_target ON number_allocation (target_object_type, target_object_id);
