-- =====================================================================
-- UG-DCMS 迁移 0002 — 受控数据字典
-- 基准依据: UG-DCMS-DD-001 §3~§9 / SRS §6 SRS-CLS/FUN/NAM/ATT / INV-023
-- 原则(DD §1): 已使用词条不得删除, 只能 ACTIVE/DEPRECATED
-- =====================================================================

-- 所有字典表统一使用该状态域
CREATE DOMAIN dict_status AS text
    NOT NULL DEFAULT 'ACTIVE'
    CHECK (VALUE IN ('ACTIVE', 'DEPRECATED'));

-- ---------------------------------------------------------------------
-- 一级技术类别 — SRS-CLS-001, DD §4 (固化)
-- ---------------------------------------------------------------------
CREATE TABLE primary_class (
    code       text PRIMARY KEY,
    name_cn    text NOT NULL,
    name_en    text NOT NULL,
    definition text,
    status     dict_status,
    sort_order integer NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------
-- 二级物理分类 — 受控字典, 普通用户不可自定义; 驱动属性模板 SRS-ATT-002
-- ---------------------------------------------------------------------
CREATE TABLE physical_class (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code               text NOT NULL,
    primary_class_code text NOT NULL REFERENCES primary_class (code),
    name_cn            text NOT NULL,
    name_en            text NOT NULL,
    definition         text,
    status             dict_status,
    sort_order         integer NOT NULL DEFAULT 0,
    CONSTRAINT uq_physical_class_code UNIQUE (code)
);
CREATE INDEX idx_physical_class_primary ON physical_class (primary_class_code);

-- ---------------------------------------------------------------------
-- 对象层级 — DD §4
-- ---------------------------------------------------------------------
CREATE TABLE object_level (
    code       text PRIMARY KEY,
    name_cn    text NOT NULL,
    name_en    text NOT NULL,
    status     dict_status,
    sort_order integer NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------
-- 功能分类 — SRS-FUN-001, F01~F16 一级功能域 + 二级功能
-- ---------------------------------------------------------------------
CREATE TABLE function_domain (
    code       text PRIMARY KEY,
    name_cn    text NOT NULL,
    name_en    text NOT NULL,
    definition text,
    status     dict_status,
    sort_order integer NOT NULL DEFAULT 0
);

CREATE TABLE function_item (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        text NOT NULL,
    domain_code text NOT NULL REFERENCES function_domain (code),
    name_cn     text NOT NULL,
    name_en     text NOT NULL,
    definition  text,
    status      dict_status,
    sort_order  integer NOT NULL DEFAULT 0,
    CONSTRAINT uq_function_item_code UNIQUE (code)
);
CREATE INDEX idx_function_item_domain ON function_item (domain_code);

-- ---------------------------------------------------------------------
-- 命名词典 — DD §5, SRS-NAM-001/002, INV-023
-- ---------------------------------------------------------------------
CREATE TABLE naming_core_term (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code               text NOT NULL,          -- 如 T1-010
    name_cn            text NOT NULL,          -- 正式中文 Canonical Term
    name_en            text NOT NULL,
    primary_class_code text NOT NULL REFERENCES primary_class (code),
    definition         text,
    status             dict_status,
    created_at         timestamptz NOT NULL DEFAULT now(),
    created_by         uuid REFERENCES app_user (id),
    CONSTRAINT uq_core_term_code UNIQUE (code)
);
-- Canonical Term 唯一: 禁止产生多个同名核心词 (DD §1)
CREATE UNIQUE INDEX uq_core_term_name_cn ON naming_core_term (name_cn);
CREATE INDEX idx_core_term_class ON naming_core_term (primary_class_code, status);

CREATE TABLE naming_qualifier (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code           text NOT NULL,
    name_cn        text NOT NULL,
    name_en        text NOT NULL,
    qualifier_type text NOT NULL
                   CHECK (qualifier_type IN ('GEOMETRY', 'STRUCTURE', 'FUNCTION',
                                             'PRINCIPLE', 'INTERFACE')),
    definition     text,
    status         dict_status,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     uuid REFERENCES app_user (id),
    CONSTRAINT uq_qualifier_code UNIQUE (code)
);
CREATE UNIQUE INDEX uq_qualifier_name_cn ON naming_qualifier (name_cn);

-- 别名: 仅用于检索与迁移, 不得作为新对象正式名称 (DD §10)
CREATE TABLE term_alias (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type text NOT NULL CHECK (target_type IN ('CORE_TERM', 'QUALIFIER',
                                                     'PHYSICAL_CLASS', 'FUNCTION_ITEM',
                                                     'MATERIAL', 'SURFACE_TREATMENT')),
    target_id   uuid NOT NULL,
    alias_text  text NOT NULL,
    language    text NOT NULL DEFAULT 'CN' CHECK (language IN ('CN', 'EN')),
    purpose     text NOT NULL DEFAULT 'SEARCH'
                CHECK (purpose IN ('SEARCH', 'MIGRATION', 'HISTORICAL')),
    CONSTRAINT uq_term_alias UNIQUE (target_type, target_id, alias_text)
);
CREATE INDEX idx_term_alias_text ON term_alias (lower(alias_text));

-- ---------------------------------------------------------------------
-- 受限词 — DD §5, SRS-NAM-003, AC-NAM-01
-- BLOCK = 不能提交; WARN_APPROVAL = 需批准; WARN = 仅提示
-- ---------------------------------------------------------------------
CREATE TABLE restricted_term (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restricted_type text NOT NULL
                    CHECK (restricted_type IN ('PROJECT', 'CUSTOMER', 'REVISION',
                                               'MARKETING', 'AIRCRAFT', 'LOCATION',
                                               'NHA', 'MATERIAL', 'COLOR', 'PARAMETER')),
    control_level   text NOT NULL
                    CHECK (control_level IN ('BLOCK', 'WARN_APPROVAL', 'WARN')),
    pattern         text NOT NULL,
    is_regex        boolean NOT NULL DEFAULT false,
    example         text,
    message_cn      text,
    status          dict_status,
    CONSTRAINT uq_restricted_term UNIQUE (restricted_type, pattern)
);
CREATE INDEX idx_restricted_term_active ON restricted_term (control_level)
    WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------
-- 单位 — DD §6
-- ---------------------------------------------------------------------
CREATE TABLE unit_dimension (
    code              text PRIMARY KEY,
    name_cn           text NOT NULL,
    name_en           text NOT NULL,
    default_unit_code text,
    status            dict_status
);

CREATE TABLE unit (
    code            text PRIMARY KEY,
    dimension_code  text NOT NULL REFERENCES unit_dimension (code),
    symbol          text NOT NULL,
    name_cn         text NOT NULL,
    factor_to_base  numeric(30, 12) NOT NULL DEFAULT 1,
    offset_to_base  numeric(30, 12) NOT NULL DEFAULT 0,
    status          dict_status
);
ALTER TABLE unit_dimension
    ADD CONSTRAINT fk_unit_dimension_default
    FOREIGN KEY (default_unit_code) REFERENCES unit (code) DEFERRABLE INITIALLY DEFERRED;

-- ---------------------------------------------------------------------
-- 材料与表面处理 — SRS-ATT-003, DD §6
-- ---------------------------------------------------------------------
CREATE TABLE material (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code            text NOT NULL,
    material_family text NOT NULL,
    grade           text,
    specification   text,
    condition       text,
    name_cn         text NOT NULL,
    name_en         text,
    density_g_cm3   numeric(10, 4),
    status          dict_status,
    CONSTRAINT uq_material_code UNIQUE (code)
);

CREATE TABLE surface_treatment (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code          text NOT NULL,
    process_type  text NOT NULL,
    specification text,
    type_class    text,
    color         text,
    name_cn       text NOT NULL,
    name_en       text,
    status        dict_status,
    CONSTRAINT uq_surface_treatment_code UNIQUE (code)
);

-- ---------------------------------------------------------------------
-- 属性模型 — SRS-ATT-001 (Definition + Template + Value)
-- ---------------------------------------------------------------------
CREATE TABLE attribute_enum_group (
    code    text PRIMARY KEY,
    name_cn text NOT NULL,
    status  dict_status
);

CREATE TABLE attribute_enum_value (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    enum_group_code  text NOT NULL REFERENCES attribute_enum_group (code),
    value_code       text NOT NULL,
    name_cn          text NOT NULL,
    name_en          text,
    sort_order       integer NOT NULL DEFAULT 0,
    status           dict_status,
    CONSTRAINT uq_attr_enum_value UNIQUE (enum_group_code, value_code)
);

CREATE TABLE attribute_definition (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                 text NOT NULL,
    name_cn              text NOT NULL,
    name_en              text NOT NULL,
    data_type            text NOT NULL
                         CHECK (data_type IN ('TEXT', 'NUMBER', 'INTEGER', 'BOOLEAN',
                                              'DATE', 'ENUM', 'REFERENCE')),
    unit_dimension_code  text REFERENCES unit_dimension (code),
    default_unit_code    text REFERENCES unit (code),
    enum_group_code      text REFERENCES attribute_enum_group (code),
    reference_target     text CHECK (reference_target IN ('MATERIAL', 'SURFACE_TREATMENT',
                                                          'DESIGN_OBJECT', 'MANUFACTURER')),
    min_value            numeric(30, 8),
    max_value            numeric(30, 8),
    max_length           integer,
    is_searchable        boolean NOT NULL DEFAULT false,
    is_comparable        boolean NOT NULL DEFAULT false,
    definition           text,
    status               dict_status,
    CONSTRAINT uq_attribute_definition_code UNIQUE (code),
    -- 数据类型与配套配置必须自洽
    CONSTRAINT ck_attr_def_enum CHECK (data_type <> 'ENUM' OR enum_group_code IS NOT NULL),
    CONSTRAINT ck_attr_def_ref  CHECK (data_type <> 'REFERENCE' OR reference_target IS NOT NULL),
    CONSTRAINT ck_attr_def_unit CHECK (unit_dimension_code IS NULL OR data_type IN ('NUMBER', 'INTEGER'))
);

CREATE TABLE attribute_template (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code              text NOT NULL,
    name_cn           text NOT NULL,
    physical_class_id uuid REFERENCES physical_class (id),
    status            dict_status,
    CONSTRAINT uq_attribute_template_code UNIQUE (code)
);
-- 一个二级物理分类最多绑定一个 ACTIVE 模板 (SRS-ATT-002)
CREATE UNIQUE INDEX uq_attribute_template_class ON attribute_template (physical_class_id)
    WHERE status = 'ACTIVE' AND physical_class_id IS NOT NULL;

CREATE TABLE attribute_template_item (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attribute_template_id   uuid NOT NULL REFERENCES attribute_template (id),
    attribute_definition_id uuid NOT NULL REFERENCES attribute_definition (id),
    is_required             boolean NOT NULL DEFAULT false,
    is_key_attribute        boolean NOT NULL DEFAULT false,
    sort_order              integer NOT NULL DEFAULT 0,
    CONSTRAINT uq_attribute_template_item UNIQUE (attribute_template_id, attribute_definition_id)
);

-- ---------------------------------------------------------------------
-- 文件类型 — DD §7
-- ---------------------------------------------------------------------
CREATE TABLE file_type (
    code       text PRIMARY KEY,
    name_cn    text NOT NULL,
    name_en    text NOT NULL,
    category   text NOT NULL DEFAULT 'DESIGN'
               CHECK (category IN ('DESIGN', 'INTERFACE', 'QUALIFICATION', 'REFERENCE')),
    definition text,
    status     dict_status
);

-- ---------------------------------------------------------------------
-- 命名空间与制造商 — SRS-EXT-001, INV-016
-- ---------------------------------------------------------------------
CREATE TABLE namespace (
    id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code    text NOT NULL,
    name_cn text NOT NULL,
    name_en text,
    kind    text NOT NULL
            CHECK (kind IN ('MANUFACTURER', 'STANDARD', 'CUSTOMER', 'OEM',
                            'SUPPLIER', 'INTERNAL_LEGACY', 'OTHER')),
    status  dict_status,
    CONSTRAINT uq_namespace_code UNIQUE (code)
);

CREATE TABLE manufacturer (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL,
    name_cn      text NOT NULL,
    name_en      text,
    cage_code    text,
    namespace_id uuid REFERENCES namespace (id),
    status       dict_status,
    CONSTRAINT uq_manufacturer_code UNIQUE (code)
);

-- ---------------------------------------------------------------------
-- 字典表禁止物理删除 (DD §1 / UI §14)
-- ---------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['primary_class', 'physical_class', 'object_level',
                             'function_domain', 'function_item', 'naming_core_term',
                             'naming_qualifier', 'restricted_term', 'unit_dimension',
                             'unit', 'material', 'surface_treatment',
                             'attribute_enum_group', 'attribute_enum_value',
                             'attribute_definition', 'attribute_template',
                             'file_type', 'namespace', 'manufacturer']
    LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%1$s_no_delete BEFORE DELETE ON %1$I
             FOR EACH ROW EXECUTE FUNCTION dcms_block_write()', t);
    END LOOP;
END $$;
