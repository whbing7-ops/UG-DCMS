-- =====================================================================
-- UG-DCMS 迁移 0012 — 结构化适用性、构型求值、Resolved BOM
-- 目标: 共用 PN / 共用 Master BOM，通过 Applicability 控制具体构型。
-- 兼容: bom_line.effectivity 文本字段保留，仅作为历史/显示备注，不参与新规则求值。
-- =====================================================================

CREATE TABLE configuration_context (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    context_code    text NOT NULL,
    name_cn         text NOT NULL,
    description     text,
    attributes      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','OBSOLETE')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      uuid REFERENCES app_user(id),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid REFERENCES app_user(id),
    CONSTRAINT uq_configuration_context_code UNIQUE(context_code),
    CONSTRAINT ck_configuration_context_attrs_object CHECK (jsonb_typeof(attributes)='object')
);
CREATE INDEX idx_configuration_context_attrs ON configuration_context USING gin(attributes);
CREATE TRIGGER trg_configuration_context_touch BEFORE UPDATE ON configuration_context
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

CREATE TABLE applicability_rule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_code       text NOT NULL,
    name_cn         text NOT NULL,
    expression      jsonb NOT NULL,
    description     text,
    status          text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','OBSOLETE')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      uuid REFERENCES app_user(id),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid REFERENCES app_user(id),
    CONSTRAINT uq_applicability_rule_code UNIQUE(rule_code),
    CONSTRAINT ck_applicability_expression_object CHECK (jsonb_typeof(expression)='object')
);
CREATE INDEX idx_applicability_rule_expression ON applicability_rule USING gin(expression);
CREATE TRIGGER trg_applicability_rule_touch BEFORE UPDATE ON applicability_rule
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

-- 一条 BOM 行最多绑定一个结构化适用性规则；未绑定 = ALL。
-- 适用性替代件需要允许同一 ITEM 下存在多条候选 BOM Line；
-- 唯一性改为 BOM + ITEM + CHILD，具体构型下是否唯一由求值器做 Overlap 校验。
ALTER TABLE bom_line DROP CONSTRAINT IF EXISTS uq_bom_line_item;
ALTER TABLE bom_line ADD CONSTRAINT uq_bom_line_item_child UNIQUE (bom_header_id, item_number, child_design_object_id);
ALTER TABLE bom_snapshot_line DROP CONSTRAINT IF EXISTS uq_bom_snapshot_line;
ALTER TABLE bom_snapshot_line ADD CONSTRAINT uq_bom_snapshot_line_item_child UNIQUE (bom_snapshot_id, item_number, child_design_object_id);

CREATE TABLE bom_line_applicability (
    bom_line_id            uuid PRIMARY KEY REFERENCES bom_line(id) ON DELETE CASCADE,
    applicability_rule_id  uuid NOT NULL REFERENCES applicability_rule(id),
    created_at             timestamptz NOT NULL DEFAULT now(),
    created_by             uuid REFERENCES app_user(id)
);
CREATE INDEX idx_bom_line_app_rule ON bom_line_applicability(applicability_rule_id);

-- 冻结 Master BOM 时同步冻结规则表达式，避免规则后续编辑影响历史快照。
ALTER TABLE bom_snapshot_line
    ADD COLUMN IF NOT EXISTS applicability_rule_code text,
    ADD COLUMN IF NOT EXISTS applicability_expression jsonb;

CREATE TABLE resolved_bom_snapshot (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    resolved_snapshot_number text NOT NULL,
    parent_design_object_id  uuid NOT NULL REFERENCES design_object(id),
    source_bom_snapshot_id   uuid REFERENCES bom_snapshot(id),
    configuration_context_id uuid REFERENCES configuration_context(id),
    context_attributes       jsonb NOT NULL,
    hash_sha256              text NOT NULL,
    line_count               integer NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    created_by               uuid REFERENCES app_user(id),
    CONSTRAINT uq_resolved_snapshot_number UNIQUE(resolved_snapshot_number),
    CONSTRAINT ck_resolved_context_object CHECK (jsonb_typeof(context_attributes)='object'),
    CONSTRAINT ck_resolved_line_count CHECK (line_count >= 0)
);
CREATE INDEX idx_resolved_snapshot_parent ON resolved_bom_snapshot(parent_design_object_id, created_at DESC);

CREATE TABLE resolved_bom_snapshot_line (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    resolved_bom_snapshot_id uuid NOT NULL REFERENCES resolved_bom_snapshot(id) ON DELETE RESTRICT,
    level                    integer NOT NULL,
    item_number              text NOT NULL,
    child_design_object_id   uuid NOT NULL REFERENCES design_object(id),
    child_object_code        text NOT NULL,
    child_display_name       text NOT NULL,
    quantity                 numeric(18,6) NOT NULL,
    extended_quantity        numeric(18,6) NOT NULL,
    unit_code                text,
    reference_designator     text,
    applicability_rule_code  text,
    applicability_expression jsonb,
    path                     text NOT NULL,
    sort_path                text NOT NULL,
    CONSTRAINT ck_resolved_qty CHECK (quantity > 0 AND extended_quantity > 0)
);
CREATE INDEX idx_resolved_line_snap ON resolved_bom_snapshot_line(resolved_bom_snapshot_id, sort_path);
CREATE INDEX idx_resolved_line_child ON resolved_bom_snapshot_line(child_design_object_id);

-- 注册新的系统不变量（若注册表存在）。
INSERT INTO invariant_registry(code, statement_cn, enforced_by, enforcement_ref, test_ref)
SELECT 'INV-026', '结构化 Applicability 未绑定时视为 ALL；绑定后必须按构型上下文求值后才可进入 Resolved BOM',
       'APP_SERVICE', 'services/applicability.py', 'test_applicability.py'
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='invariant_registry')
  AND NOT EXISTS (SELECT 1 FROM invariant_registry WHERE code='INV-026');

INSERT INTO invariant_registry(code, statement_cn, enforced_by, enforcement_ref, test_ref)
SELECT 'INV-027', 'Resolved BOM 快照必须固化构型上下文与适用性表达式，不得受后续 Master BOM/规则修改影响',
       'DB_AND_APP', 'resolved_bom_snapshot*', 'test_applicability.py'
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='invariant_registry')
  AND NOT EXISTS (SELECT 1 FROM invariant_registry WHERE code='INV-027');
