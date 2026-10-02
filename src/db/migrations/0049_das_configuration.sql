-- ---------------------------------------------------------------------
-- 0049  M5 构型管理（UG-DAP-05，9 步）
-- ---------------------------------------------------------------------
-- 依据 UG-DAP-05《设计构型管理程序》第 6 章（9 步）、UG-DAW-009《构型标识和基线规则》、
-- UG-DAF-10（构型项目清单）、UG-DAF-11（交付构型记录）、设计输入第 8.1 节。
--
-- 【按 2026-10-02 的决定选闸门: 只在便宜且不含糊的地方设】
-- 这份程序里有四条写着"不得"的规则, 都只需要一句话就能判, 而判错的后果都很实在:
--   ① 第 2 步: 不可互换或单向互换的更改**必须更换件号**。
--   ② 第 3 步: 基线由授权人员批准后在系统中冻结, **冻结后只能通过批准的更改修改**。
--   ③ 第 5 步: **未明确有效性范围的更改不得发布。**
--   ④ 第 7 步 b: 交付前核查的差异**未关闭的不得放行**。
-- 这四条做成闸门。
--
-- 其余的**不做逐项录入**, 做成记录＋线下证据（0047）:
--   第 7 步 a 的设计内部一致性核查要比对图纸、数模、BOM、规范、工艺、手册六者之间的
--   参数、版次和引用标准 —— 这不是系统判得了的事, 也不该逼人把六方比对录成表格。
--   第 6 步的构型纪实做成视图（按产品、按日期查得出"某台产品由哪些件号和版次构成"）,
--   那是查询能力, 不是闸门。
--
-- 【为什么互换性那一条值得设闸门】
-- 原文: 完全互换的更改升版次; 不可互换或单向互换的更改**必须更换件号**,
-- 并在更改单中说明互换性结论。
-- 判错的后果不在系统里 —— 一个不可互换的件沿用旧件号发出去, 现场会把它装到装不上
-- 或装上去不安全的位置, 而装的人手里的件号是对的。这正是件号制度要防的那件事,
-- 所以它值得一道硬闸门, 而且判起来只要一个布尔。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 构型项目清单（UG-DAF-10，第 1 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_config_item (
    id           bigserial PRIMARY KEY,
    project_id   bigint NOT NULL REFERENCES das_project (id),
    item_kind    text NOT NULL,             -- DRAWING / BOM / SPEC / SOFTWARE / TECH_COND
    identifier   text NOT NULL,             -- 图号／件号／规范号／软件编号
    name_cn      text NOT NULL,
    part_number_id uuid REFERENCES part_number (id),   -- 对得上件号的挂上去
    -- 第 1 步原文: 凡影响**形状、配合、功能、适航性或可追溯性**的…均须纳入。
    -- 所以纳入理由必须指明是哪一类影响 —— 这一栏是清单的依据,
    -- 空着就说不出为什么这个项目在清单里、那个不在。
    inclusion_reason text NOT NULL,
    -- 授权人员确认（第 1 步: 形成 UG-DAF-10, 经授权人员确认）
    confirmed_by uuid REFERENCES app_user (id),
    confirmed_on date,
    form_ref     text,                      -- UG-DAF-10 的编号
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dci UNIQUE (project_id, identifier),
    CONSTRAINT ck_dci_kind CHECK (item_kind IN
        ('DRAWING', 'BOM', 'SPEC', 'SOFTWARE', 'TECH_COND')),
    CONSTRAINT ck_dci_text CHECK (
        length(btrim(identifier)) > 0 AND length(btrim(name_cn)) > 0
        AND length(btrim(inclusion_reason)) > 0),
    CONSTRAINT ck_dci_confirmed CHECK ((confirmed_by IS NULL) = (confirmed_on IS NULL))
);

COMMENT ON COLUMN das_config_item.inclusion_reason IS
    '纳入构型控制的理由：影响形状／配合／功能／适航性／可追溯性中的哪一类（第 1 步原文）。空着就说不出为什么这个项目在清单里、那个不在。';

-- ---------------------------------------------------------------------
-- 2. 基线（第 3 步，三类）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_config_baseline (
    id           bigserial PRIMARY KEY,
    project_id   bigint NOT NULL REFERENCES das_project (id),
    -- 原文: 至少建立三类 —— 审定基线／设计基线／交付基线。
    baseline_kind text NOT NULL,
    code         text NOT NULL UNIQUE,      -- 基线编号
    description  text NOT NULL,
    -- 交付基线是"每台（架）产品交付时的实际构型", 所以要能挂到序列号上。
    serial_no    text,
    -- 冻结: 由授权人员批准后在系统中冻结, 冻结后只能通过批准的更改修改。
    frozen_at    timestamptz,
    frozen_by    uuid REFERENCES app_user (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dcb_kind CHECK (baseline_kind IN ('CERT', 'DESIGN', 'DELIVERY')),
    CONSTRAINT ck_dcb_text CHECK (
        length(btrim(code)) > 0 AND length(btrim(description)) > 0),
    CONSTRAINT ck_dcb_frozen CHECK ((frozen_at IS NULL) = (frozen_by IS NULL)),
    -- 交付基线对应具体一台产品, 没有序列号的交付基线说不出是哪一台。
    CONSTRAINT ck_dcb_serial CHECK (
        CASE WHEN baseline_kind = 'DELIVERY'
             THEN length(btrim(coalesce(serial_no, ''))) > 0
             ELSE true
        END)
);

CREATE TABLE IF NOT EXISTS das_config_baseline_item (
    id           bigserial PRIMARY KEY,
    baseline_id  bigint NOT NULL REFERENCES das_config_baseline (id),
    config_item_id bigint NOT NULL REFERENCES das_config_item (id),
    revision     text NOT NULL,             -- 该项目在本基线中的版次
    effectivity  text,                      -- 适用架次或序列号范围（UG-DAW-009）
    -- 冻结后要改这一行, 只能凭一条已批准的更改（第 3 步原文）。
    change_no    text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dcbi UNIQUE (baseline_id, config_item_id),
    CONSTRAINT ck_dcbi_rev CHECK (length(btrim(revision)) > 0)
);

-- ---------------------------------------------------------------------
-- 3. 互换性结论（第 2 步）
-- ---------------------------------------------------------------------
-- 原文: 完全互换的更改升版次; **不可互换或单向互换的更改必须更换件号**,
-- 并在更改单中说明互换性结论。
CREATE TABLE IF NOT EXISTS das_interchangeability (
    id           bigserial PRIMARY KEY,
    change_id    bigint NOT NULL REFERENCES das_design_change (id),
    config_item_id bigint REFERENCES das_config_item (id),
    -- FULL 完全互换 / ONE_WAY 单向互换 / NONE 不可互换
    verdict      text NOT NULL,
    rationale    text NOT NULL,             -- 互换性结论的依据
    -- 完全互换 → 升版次; 其余 → 换件号。两者各记实际做法, 由 DCMS-INV-118 对照。
    old_part_number text,
    new_part_number text,
    old_revision text,
    new_revision text,
    decided_by   uuid NOT NULL REFERENCES app_user (id),
    decided_on   date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dint_verdict CHECK (verdict IN ('FULL', 'ONE_WAY', 'NONE')),
    CONSTRAINT ck_dint_rationale CHECK (length(btrim(rationale)) > 0)
);

COMMENT ON TABLE das_interchangeability IS
    'UG-DAP-05 第 2 步的互换性结论。不可互换或单向互换的更改必须更换件号（DCMS-INV-118）——判错的后果不在系统里：一个不可互换的件沿用旧件号发出去，现场会把它装到装不上或装上去不安全的位置，而装的人手里的件号是对的。';

-- ---------------------------------------------------------------------
-- 4. 有效性与贯彻销项（第 5 步）
-- ---------------------------------------------------------------------
-- 原文: 由授权人员确定实施范围: 自哪个架次／序列号起实施、是否追溯改装已交付产品、
-- 有无过渡期并存; 在更改单和 BOM 中记录有效性并通知生产、采购和持续适航;
-- 跟踪实施至完成并销项。**未明确有效性范围的更改不得发布。**
CREATE TABLE IF NOT EXISTS das_change_effectivity (
    id           bigserial PRIMARY KEY,
    change_id    bigint NOT NULL REFERENCES das_design_change (id),
    effective_from_unit text NOT NULL,      -- 自哪个架次／序列号起实施
    retrofit_delivered boolean NOT NULL,    -- 是否追溯改装已交付产品
    retrofit_scope text,                    -- 追溯时的范围
    transition_coexist boolean NOT NULL,    -- 有无过渡期并存
    transition_note text,
    notified_production boolean NOT NULL,
    notified_procurement boolean NOT NULL,
    notified_airworthiness boolean NOT NULL,
    decided_by   uuid NOT NULL REFERENCES app_user (id),
    decided_on   date NOT NULL,
    -- 跟踪实施至完成并销项
    closed_on    date,
    closure_note text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dce_change UNIQUE (change_id),
    CONSTRAINT ck_dce_from CHECK (length(btrim(effective_from_unit)) > 0),
    -- 要追溯改装就得说清范围; 有过渡期并存就得说清怎么并存。
    -- 写成布尔而不写范围, 等于说了"要追溯"但没人知道追到哪。
    CONSTRAINT ck_dce_retrofit CHECK (
        NOT retrofit_delivered OR length(btrim(coalesce(retrofit_scope, ''))) > 0),
    CONSTRAINT ck_dce_transition CHECK (
        NOT transition_coexist OR length(btrim(coalesce(transition_note, ''))) > 0),
    CONSTRAINT ck_dce_closure CHECK (
        (closed_on IS NULL) = (closure_note IS NULL))
);

-- ---------------------------------------------------------------------
-- 5. 构型核查与差异（第 7 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_config_audit (
    id           bigserial PRIMARY KEY,
    project_id   bigint NOT NULL REFERENCES das_project (id),
    -- INTERNAL_CONSISTENCY 设计内部一致性（图纸、数模、BOM、规范、工艺、手册六者互相一致）
    -- PRE_DELIVERY        交付前核查（实物 as-built 与设计 as-designed 逐项比对）
    audit_kind   text NOT NULL,
    serial_no    text,                      -- 交付前核查针对具体一台
    scope_note   text NOT NULL,
    -- 【六方比对不录进系统】第 7 步 a 要比对图纸、数模、BOM、规范、工艺、手册之间的
    -- 参数、版次和引用标准。这不是系统判得了的事, 也不该逼人把六方比对录成表格 ——
    -- 按 2026-10-02 的决定走线下, 结论与证据记在这里与 das_offline_approval。
    conclusion   text NOT NULL,
    audited_by   uuid NOT NULL REFERENCES app_user (id),
    audited_on   date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dca_kind CHECK (audit_kind IN ('INTERNAL_CONSISTENCY', 'PRE_DELIVERY')),
    CONSTRAINT ck_dca_text CHECK (
        length(btrim(scope_note)) > 0 AND length(btrim(conclusion)) > 0),
    CONSTRAINT ck_dca_serial CHECK (
        CASE WHEN audit_kind = 'PRE_DELIVERY'
             THEN length(btrim(coalesce(serial_no, ''))) > 0
             ELSE true
        END)
);

-- 差异逐条记录并处置（第 7 步 b）。未关闭的差异不得放行 —— DCMS-INV-120。
CREATE TABLE IF NOT EXISTS das_config_discrepancy (
    id           bigserial PRIMARY KEY,
    audit_id     bigint NOT NULL REFERENCES das_config_audit (id),
    description  text NOT NULL,
    -- 第 8 步: 发现**未经批准的**构型差异（实物与设计不一致、擅自使用旧版次）,
    -- 按 UG-DAP-14 记不符合项, 并按 UG-DAP-12 判断是否属应报告的事件。
    unapproved   boolean NOT NULL DEFAULT false,
    ncr_id       bigint REFERENCES das_ncr (id),
    occurrence_checked boolean NOT NULL DEFAULT false,
    disposition  text,
    closed_on    date,
    closed_by    uuid REFERENCES app_user (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dcd_desc CHECK (length(btrim(description)) > 0),
    CONSTRAINT ck_dcd_closed CHECK (
        (closed_on IS NULL) = (closed_by IS NULL)),
    -- 关闭要写处置。只填个日期的"已关闭"说不出它是怎么关的。
    CONSTRAINT ck_dcd_disposition CHECK (
        closed_on IS NULL OR length(btrim(coalesce(disposition, ''))) > 0),
    -- 第 8 步: 未经批准的构型差异须按 UG-DAP-14 记不符合项, 并按 UG-DAP-12 判断
    -- 是否属应报告的事件。两件事都要做过才算处置完。
    CONSTRAINT ck_dcd_unapproved CHECK (
        NOT unapproved OR closed_on IS NULL
        OR (ncr_id IS NOT NULL AND occurrence_checked))
);

-- ---------------------------------------------------------------------
-- 6. 交付构型记录（UG-DAF-11，第 9 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_delivery_config (
    id           bigserial PRIMARY KEY,
    project_id   bigint NOT NULL REFERENCES das_project (id),
    serial_no    text NOT NULL,
    form_ref     text NOT NULL,             -- UG-DAF-11 的编号
    -- 原文: 列出件号、版次、序列号、已实施的更改和遗留差异。
    part_list    text NOT NULL,             -- 件号与版次清单
    implemented_changes text NOT NULL,      -- 已实施的更改
    -- 遗留差异要么写明, 要么显式声明"无" —— 空着分不出"没有"与"忘了写",
    -- 而这一栏空着发出去, 接收方以为这台产品没有遗留差异。
    residual_discrepancies text,
    no_residual_declared boolean NOT NULL DEFAULT false,
    baseline_id  bigint REFERENCES das_config_baseline (id),
    prepared_by  uuid NOT NULL REFERENCES app_user (id),
    delivered_on date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_ddc UNIQUE (project_id, serial_no),
    CONSTRAINT ck_ddc_text CHECK (
        length(btrim(serial_no)) > 0 AND length(btrim(form_ref)) > 0
        AND length(btrim(part_list)) > 0 AND length(btrim(implemented_changes)) > 0),
    CONSTRAINT ck_ddc_residual CHECK (
        no_residual_declared
        OR length(btrim(coalesce(residual_discrepancies, ''))) > 0)
);

COMMENT ON COLUMN das_delivery_config.residual_discrepancies IS
    '遗留差异。要么写明、要么显式声明「无」——空着分不出「没有」与「忘了写」，而这一栏空着发出去，接收方以为这台产品没有遗留差异。';

-- ---------------------------------------------------------------------
-- 7. 四道闸门
-- ---------------------------------------------------------------------

-- DCMS-INV-118: 不可互换或单向互换的更改必须更换件号（第 2 步）。
CREATE OR REPLACE FUNCTION dcms_check_das_interchangeability() RETURNS trigger AS $$
BEGIN
    IF NEW.verdict IN ('ONE_WAY', 'NONE') THEN
        IF length(btrim(coalesce(NEW.new_part_number, ''))) = 0
           OR NEW.new_part_number = NEW.old_part_number THEN
            RAISE EXCEPTION 'DCMS-INV-118: 互换性结论为%时**必须更换件号**（UG-DAP-05 第 2 步）。'
                            '判错的后果不在系统里: 一个不可互换的件沿用旧件号发出去, '
                            '现场会把它装到装不上、或装上去不安全的位置 —— 而装的人手里的件号是对的。'
                            '这正是件号制度要防的那件事。',
                            CASE NEW.verdict WHEN 'NONE' THEN '不可互换' ELSE '单向互换' END;
        END IF;
    ELSE
        -- 完全互换 → 升版次。换了件号反而说明结论和做法不一致。
        IF length(btrim(coalesce(NEW.new_revision, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-118: 完全互换的更改升版次（UG-DAP-05 第 2 步）, 须记明新版次。';
        END IF;
        IF NEW.new_part_number IS NOT NULL
           AND NEW.new_part_number <> coalesce(NEW.old_part_number, '') THEN
            RAISE EXCEPTION 'DCMS-INV-118: 结论是完全互换却换了件号 —— 结论与做法不一致。'
                            '要么结论不是完全互换, 要么不该换件号; 两者必有一个是错的。';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_interchangeability_check ON das_interchangeability;
CREATE TRIGGER trg_das_interchangeability_check
    BEFORE INSERT OR UPDATE ON das_interchangeability
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_interchangeability();

-- DCMS-INV-119: 基线冻结后只能通过批准的更改修改（第 3 步）。
CREATE OR REPLACE FUNCTION dcms_check_das_baseline_item() RETURNS trigger AS $$
DECLARE
    v_frozen timestamptz;
    v_code   text;
    v_state  text;
    v_mm     text;
BEGIN
    SELECT frozen_at, code INTO v_frozen, v_code
      FROM das_config_baseline WHERE id = COALESCE(NEW.baseline_id, OLD.baseline_id);
    IF v_frozen IS NULL THEN
        RETURN COALESCE(NEW, OLD);        -- 未冻结, 随便改
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-119: 基线 % 已冻结, 其构型项目不得删除（UG-DAP-05 第 3 步: '
                        '冻结后只能通过批准的更改修改）。', v_code;
    END IF;
    -- 冻结后的新增或改动, 必须凭一条**已批准**的更改。
    IF length(btrim(coalesce(NEW.change_no, ''))) = 0 THEN
        RAISE EXCEPTION 'DCMS-INV-119: 基线 % 已冻结, 改动须凭一条已批准的更改并记明更改单号'
                        '（UG-DAP-05 第 3 步）。冻结的意思就是只能这样改。', v_code;
    END IF;
    SELECT cl.state, cl.major_minor INTO v_state, v_mm
      FROM das_design_change d
      LEFT JOIN das_change_classification cl ON cl.change_id = d.id
     WHERE d.change_no = NEW.change_no;
    IF v_state IS NULL THEN
        RAISE EXCEPTION 'DCMS-INV-119: 更改单 % 还没有分类结论, 不算已批准的更改。'
                        '冻结基线只能通过**批准的**更改修改（UG-DAP-05 第 3 步; 分类按 UG-DAP-06）。',
                        NEW.change_no;
    END IF;
    IF v_state <> 'CLASSIFIED' THEN
        RAISE EXCEPTION 'DCMS-INV-119: 更改单 % 的分类状态为 %, 不算已批准。'
                        '（超权限转局方、判不了待确认、局方有不同意见, 三种都还没定。）',
                        NEW.change_no, v_state;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_baseline_item_check ON das_config_baseline_item;
CREATE TRIGGER trg_das_baseline_item_check
    BEFORE INSERT OR UPDATE OR DELETE ON das_config_baseline_item
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_baseline_item();

-- 基线一旦冻结不得解冻。解冻等于让"冻结"这件事不成立 ——
-- 构型要退回, 应当另建一条基线并说明它取代了哪一条。
CREATE OR REPLACE FUNCTION dcms_check_das_baseline() RETURNS trigger AS $$
BEGIN
    IF OLD.frozen_at IS NOT NULL AND NEW.frozen_at IS NULL THEN
        RAISE EXCEPTION 'DCMS-INV-119: 基线 % 已冻结, 不得解冻。解冻等于让"冻结"这件事不成立 —— '
                        '构型要退回, 应当另建一条基线并说明它取代了哪一条。', OLD.code;
    END IF;
    IF OLD.frozen_at IS NOT NULL
       AND (NEW.baseline_kind <> OLD.baseline_kind OR NEW.code <> OLD.code
            OR NEW.project_id <> OLD.project_id) THEN
        RAISE EXCEPTION 'DCMS-INV-119: 基线 % 已冻结, 其类别、编号与所属项目不得改动。', OLD.code;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_baseline_check ON das_config_baseline;
CREATE TRIGGER trg_das_baseline_check
    BEFORE UPDATE ON das_config_baseline
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_baseline();

-- DCMS-INV-120: 未关闭的差异不得放行（第 7 步 b）。
-- 落在交付构型记录上: 该台产品的交付前核查还有未关闭的差异时, 不得出交付构型记录。
CREATE OR REPLACE FUNCTION dcms_check_das_delivery_config() RETURNS trigger AS $$
DECLARE
    v_open text;
BEGIN
    SELECT string_agg('#' || d.id || ' ' || left(d.description, 40), '；')
      INTO v_open
      FROM das_config_discrepancy d
      JOIN das_config_audit a ON a.id = d.audit_id
     WHERE a.project_id = NEW.project_id
       AND a.audit_kind = 'PRE_DELIVERY'
       AND a.serial_no = NEW.serial_no
       AND d.closed_on IS NULL;
    IF v_open IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-120: 本台产品（序列号 %）的交付前核查还有未关闭的差异, 不得放行: %。'
                        'UG-DAP-05 第 7 步 b: 差异逐条记录并处置, **未关闭的差异不得放行**。',
                        NEW.serial_no, v_open;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_delivery_config_check ON das_delivery_config;
CREATE TRIGGER trg_das_delivery_config_check
    BEFORE INSERT ON das_delivery_config
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_delivery_config();

-- DCMS-INV-121: 交付构型记录、互换性结论、有效性决定不得删除或改写。
-- 交付构型记录随产品移交给客户, 改它等于改一份已经交出去的东西。
CREATE OR REPLACE FUNCTION dcms_guard_das_config_append() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-INV-121: % 不得%。交付构型记录随产品移交客户, 互换性结论决定了件号怎么走, '
                    '有效性决定了改装范围 —— 这三件都是已经对外生效的判断。更正请另记一条并写明理由。',
                    TG_TABLE_NAME,
                    CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_delivery_config_append ON das_delivery_config;
CREATE TRIGGER trg_das_delivery_config_append
    BEFORE UPDATE OR DELETE ON das_delivery_config
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_config_append();

DROP TRIGGER IF EXISTS trg_das_interchangeability_append ON das_interchangeability;
CREATE TRIGGER trg_das_interchangeability_append
    BEFORE UPDATE OR DELETE ON das_interchangeability
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_config_append();

-- ---------------------------------------------------------------------
-- 8. 视图
-- ---------------------------------------------------------------------

-- 8.1 构型纪实（第 6 步）: 某台产品由哪些件号和版次构成、已实施哪些更改。
-- 这是**查询能力**而不是闸门 —— 原文要的就是"做到可按产品、按日期查询"。
CREATE OR REPLACE VIEW das_config_status AS
SELECT b.project_id, p.project_no, b.id AS baseline_id, b.code AS baseline_code,
       b.baseline_kind, b.serial_no, b.frozen_at,
       i.item_kind, i.identifier, i.name_cn, bi.revision, bi.effectivity,
       bi.change_no,
       (SELECT cl.major_minor FROM das_design_change d
          JOIN das_change_classification cl ON cl.change_id = d.id
         WHERE d.change_no = bi.change_no)                         AS change_class
  FROM das_config_baseline b
  JOIN das_project p ON p.id = b.project_id
  JOIN das_config_baseline_item bi ON bi.baseline_id = b.id
  JOIN das_config_item i ON i.id = bi.config_item_id
 ORDER BY p.project_no, b.code, i.identifier;

COMMENT ON VIEW das_config_status IS
    '构型纪实（UG-DAP-05 第 6 步）：某台产品由哪些件号和版次构成、已实施哪些更改。这是查询能力而不是闸门——原文要的就是「做到可按产品、按日期查询」。';

-- 8.2 **未明确有效性范围的更改**（第 5 步: 不得发布）。
-- 已分类但没有有效性记录的, 列在这里。放在视图而不是闸门上, 因为"发布"这个动作
-- 散在 M5 小改发布、M8 对外发布等多处; 闸门落在各自的发布点更准,
-- 这张表是给构型管理员盯的那份清单。
CREATE OR REPLACE VIEW das_change_effectivity_missing AS
SELECT d.change_no, d.title, p.project_no, cl.state, cl.major_minor, cl.signed_on
  FROM das_design_change d
  JOIN das_change_classification cl ON cl.change_id = d.id
  LEFT JOIN das_project p ON p.id = d.project_id
 WHERE cl.state = 'CLASSIFIED'
   AND NOT EXISTS (SELECT 1 FROM das_change_effectivity e WHERE e.change_id = d.id)
 ORDER BY cl.signed_on;

COMMENT ON VIEW das_change_effectivity_missing IS
    '已分类却没有有效性记录的更改。UG-DAP-05 第 5 步：未明确有效性范围的更改不得发布——这张表是构型管理员要盯的清单。';

-- 8.3 贯彻未销项的更改（第 5 步: 跟踪实施至完成并销项）。
CREATE OR REPLACE VIEW das_change_effectivity_open AS
SELECT d.change_no, d.title, e.effective_from_unit, e.retrofit_delivered,
       e.retrofit_scope, e.transition_coexist, e.decided_on,
       (current_date - e.decided_on)                               AS days_open
  FROM das_change_effectivity e
  JOIN das_design_change d ON d.id = e.change_id
 WHERE e.closed_on IS NULL
 ORDER BY e.decided_on;

-- 8.4 未关闭的构型差异。未经批准的那些还要看 NCR 与事件判断做了没有（第 8 步）。
CREATE OR REPLACE VIEW das_config_discrepancy_open AS
SELECT d.id, a.project_id, p.project_no, a.audit_kind, a.serial_no,
       d.description, d.unapproved, d.ncr_id, d.occurrence_checked,
       a.audited_on,
       CASE WHEN d.unapproved AND d.ncr_id IS NULL
            THEN '未经批准的构型差异尚未按 UG-DAP-14 记不符合项'
            WHEN d.unapproved AND NOT d.occurrence_checked
            THEN '尚未按 UG-DAP-12 判断是否属应报告的事件'
       END                                                         AS pending_action
  FROM das_config_discrepancy d
  JOIN das_config_audit a ON a.id = d.audit_id
  JOIN das_project p ON p.id = a.project_id
 WHERE d.closed_on IS NULL
 ORDER BY a.audited_on;

-- 8.5 构型项目清单里还没经授权人员确认的（第 1 步）。
CREATE OR REPLACE VIEW das_config_item_unconfirmed AS
SELECT i.id, p.project_no, i.item_kind, i.identifier, i.name_cn,
       i.inclusion_reason, i.created_at
  FROM das_config_item i
  JOIN das_project p ON p.id = i.project_id
 WHERE i.confirmed_by IS NULL
 ORDER BY i.created_at;

-- 8.6 互换性结论台账。换件号的那些单列, 因为它们影响现场。
CREATE OR REPLACE VIEW das_interchangeability_register AS
SELECT t.id, d.change_no, d.title, i.identifier, t.verdict,
       CASE t.verdict WHEN 'FULL' THEN '完全互换（升版次）'
                      WHEN 'ONE_WAY' THEN '单向互换（换件号）'
                      ELSE '不可互换（换件号）' END                AS verdict_cn,
       t.old_part_number, t.new_part_number, t.old_revision, t.new_revision,
       (t.new_part_number IS NOT NULL
        AND t.new_part_number <> coalesce(t.old_part_number, ''))  AS part_number_changed,
       t.rationale, t.decided_on
  FROM das_interchangeability t
  JOIN das_design_change d ON d.id = t.change_id
  LEFT JOIN das_config_item i ON i.id = t.config_item_id
 ORDER BY t.decided_on DESC;
