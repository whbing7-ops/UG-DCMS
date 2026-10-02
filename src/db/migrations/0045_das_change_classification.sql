-- ---------------------------------------------------------------------
-- 0045  M4 设计更改分类（判据 D1～D7、A7、UG-DAW-010 的 9 项判据）
-- ---------------------------------------------------------------------
-- 依据 UG-DAP-06《设计更改分类程序》第 6 章（7 步）、
-- UG-DAW-010《设计更改分类判据使用说明和案例》第 1～5 章、
-- 表-21-173（UG-DAF-02）、设计输入第 8.8 节。
--
-- 【三项分类是三项，不是一个字段】
-- UG-DAP-06 第 3 步: 按技术影响分别形成**大改／小改、声学／非声学、排放／非排放
-- 三项建议和依据**。而后两项本单位**没有申请批准权**: 第 4 步要求由适航管理负责人
-- 提交局方取得所需结论, 在 UG-DAF-02 记录编号、日期及限制。
-- 所以声学与排放两项在系统里**不能由内部人员签**, 只能登记局方结论 —— 内部签了,
-- 就是在没有权利的事项上作了批准。
--
-- 【两条看着矛盾、其实管的是两件事的规则】
--   ① UG-DAW-010 第 1 章: **无法判定的按重大更改处理**, 直至适航管理负责人或局方确认。
--   ② UG-DAP-06 第 4 步: **超出分类或批准权限时转局方办理, 不能仅因超权限自动判为大改。**
-- 第一条说的是**技术上**判不了, 第二条说的是**权限上**不够。把两者混成一个
-- "拿不准就判大改"的分支, 就会在签署人权限不足时自动升级为重大更改 ——
-- 那是用权限问题冒充技术判断, 而重大更改要走 UG-DAP-09 向局方申请批准,
-- 等于凭一个权限缺口给产品加了一道审定。所以本迁移把两者分成两个不同的状态:
-- UNDETERMINED（技术判不了 → 按大改处理, 待确认）与
-- BEYOND_AUTHORITY（超权限 → 转局方办理, **分类结论留空**, 不得填成 MAJOR）。
--
-- 【判据 A7／P5 的延续: 判据里不放技术阈值】
-- "重量与平衡"原文曾有"单项重量变化超过【】kg／重心移动超过【】%MAC"。
-- 待澄清项第 9 条已决策关闭: **不填, 删除该子句** —— 体系文件是制度不是技术标准,
-- 产品技术参数不进体系文件。所以本表**没有阈值字段**: 加一个就等于把那条被删掉的
-- 子句搬进系统, 而且它会变成一个没人维护、却被当成判定依据的数。
--
-- 【累计影响】
-- UG-DAW-010 第 1 章: 影响须累计考虑, 一系列小改累积后可能构成重大更改,
-- 须与此前的更改合并评估。所以分类记录要能指出"与哪几次以往更改一起评的",
-- 只判单次的系统会让一串小改永远是小改。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 9 项判据（配置）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_change_criterion (
    code         text PRIMARY KEY,
    seq          int  NOT NULL UNIQUE,
    name_cn      text NOT NULL,
    significant_when text NOT NULL,        -- 什么情况算"显著影响"（原文）
    evidence_required text NOT NULL,       -- 判定需要的证据（原文）
    source_doc   text NOT NULL,
    note         text,
    CONSTRAINT ck_dcc_text CHECK (
        length(btrim(significant_when)) > 0 AND length(btrim(evidence_required)) > 0)
);

COMMENT ON TABLE das_change_criterion IS
    'UG-DAW-010 第 2 章的 9 项判据。刻意没有数值阈值字段——「重量与平衡」的 kg／%MAC 子句已按待澄清项第 9 条删除：体系文件是制度不是技术标准（判据 P5）。';

-- ---------------------------------------------------------------------
-- 2. 更改申请（UG-DAP-06 第 1 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_design_change (
    id           bigserial PRIMARY KEY,
    change_no    text NOT NULL UNIQUE,     -- 内部更改申请编号
    project_id   bigint REFERENCES das_project (id),
    title        text NOT NULL,
    purpose      text NOT NULL,            -- 目的
    content      text NOT NULL,            -- 内容
    products     text NOT NULL,            -- 涉及产品
    drawings     text NOT NULL,            -- 涉及图号
    raised_by    uuid NOT NULL REFERENCES app_user (id),
    raised_on    date NOT NULL DEFAULT current_date,
    -- 影响分析（第 2 步）: 依据《构型项目清单》列出受影响对象, 并核对 AD 与审定基础。
    impact_list  text,
    impact_baseline_ref text,              -- 查过的基线与引用关系
    ad_checked   boolean,                  -- 是否核对过适航指令适用性
    cert_basis_checked boolean,            -- 是否核对过现行审定基础
    impact_by    uuid REFERENCES app_user (id),
    impact_on    date,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ddc_text CHECK (
        length(btrim(title)) > 0 AND length(btrim(purpose)) > 0
        AND length(btrim(content)) > 0 AND length(btrim(products)) > 0
        AND length(btrim(drawings)) > 0),
    -- 影响分析要么没做, 要么四项齐全。做了一半的影响分析比没做更糟:
    -- 它会让下一步以为分析过了。
    CONSTRAINT ck_ddc_impact CHECK (
        (impact_list IS NULL AND impact_by IS NULL AND impact_on IS NULL
         AND ad_checked IS NULL AND cert_basis_checked IS NULL)
        OR (length(btrim(coalesce(impact_list, ''))) > 0 AND impact_by IS NOT NULL
            AND impact_on IS NOT NULL AND ad_checked IS NOT NULL
            AND cert_basis_checked IS NOT NULL))
);

COMMENT ON TABLE das_design_change IS
    'UG-DAP-06 第 1～2 步：更改申请与影响分析。影响分析要么没做、要么四项齐全——做了一半会让下一步以为分析过了。';

-- ---------------------------------------------------------------------
-- 3. 逐条判据的打勾与依据（UG-DAW-010 第 3 章第 3 步）
-- ---------------------------------------------------------------------
-- 原文: 按第 2 章逐条判据打勾并写明依据, **不得只写"无影响"而无理由**。
-- 所以这张表里"无影响"也必须带 rationale, 由 CHECK 保证。
CREATE TABLE IF NOT EXISTS das_change_criterion_assessment (
    id           bigserial PRIMARY KEY,
    change_id    bigint NOT NULL REFERENCES das_design_change (id),
    criterion_code text NOT NULL REFERENCES das_change_criterion (code),
    -- SIGNIFICANT 显著影响 / NO_IMPACT 无影响 / NOT_APPLICABLE 不适用
    verdict      text NOT NULL,
    rationale    text NOT NULL,            -- 依据, 无影响也要写
    evidence_ref text,                     -- 对应证据（强度分析、FMEA 对比……）
    assessed_by  uuid NOT NULL REFERENCES app_user (id),
    assessed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dcca UNIQUE (change_id, criterion_code),
    CONSTRAINT ck_dcca_verdict CHECK
        (verdict IN ('SIGNIFICANT', 'NO_IMPACT', 'NOT_APPLICABLE')),
    -- 判据原文: 不得只写"无影响"而无理由。
    CONSTRAINT ck_dcca_rationale CHECK (length(btrim(rationale)) > 0),
    -- 判为显著影响时须指出证据: UG-DAW-010 第 2 章每一项都列了"判定需要的证据"。
    CONSTRAINT ck_dcca_evidence CHECK (
        verdict <> 'SIGNIFICANT' OR length(btrim(coalesce(evidence_ref, ''))) > 0)
);

-- ---------------------------------------------------------------------
-- 4. 分类结论（UG-DAF-02 / 表-21-173）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_change_classification (
    id           bigserial PRIMARY KEY,
    change_id    bigint NOT NULL REFERENCES das_design_change (id),
    -- 第一项: 大改／小改。本单位有批准权, 由授权人员签。
    -- 取值里**没有** MAJOR_BY_AUTHORITY 之类的东西 —— 见 state 的说明。
    major_minor  text,                     -- MAJOR / MINOR
    -- 判定状态。把"技术判不了"与"超权限"分开, 理由见文件头。
    state        text NOT NULL,
    -- 第二、三项: 声学与排放。本单位**未申请批准权**, 只能登记局方结论。
    acoustic_result text,                  -- 局方给出的声学分类结论
    acoustic_caac_ref text,                -- 局方编号
    acoustic_caac_on date,
    acoustic_limitation text,
    emission_result text,
    emission_caac_ref text,
    emission_caac_on date,
    emission_limitation text,
    -- 编号（第 5 步）: 设计机构许可证编号-型号-年份-流水号-FL。
    -- 同 0043 的符合性声明: **不校验许可证编号部分**, 本单位 DOA 尚在申请中。
    form_no      text,
    conclusion_reason text NOT NULL,       -- 结论及理由（第 3 章第 5 步）
    signed_by    uuid REFERENCES app_user (id),
    signed_on    date,
    caac_referral_ref text,                -- 转局方办理的依据（超权限或无权利时）
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dcl_change UNIQUE (change_id),
    CONSTRAINT ck_dcl_mm CHECK (major_minor IS NULL OR major_minor IN ('MAJOR', 'MINOR')),
    CONSTRAINT ck_dcl_state CHECK (state IN
        ('CLASSIFIED',        -- 已分类并签署
         'UNDETERMINED',      -- 技术上判不了: 按重大更改处理, 待 AWM 或局方确认
         'BEYOND_AUTHORITY',  -- 超出分类或批准权限: 转局方办理, 结论留空
         'CAAC_DISAGREED')),  -- 局方有不同意见, 以局方意见为准
    CONSTRAINT ck_dcl_reason CHECK (length(btrim(conclusion_reason)) > 0),
    CONSTRAINT ck_dcl_acoustic CHECK (
        acoustic_result IS NULL OR acoustic_result IN ('ACOUSTIC', 'NON_ACOUSTIC')),
    CONSTRAINT ck_dcl_emission CHECK (
        emission_result IS NULL OR emission_result IN ('EMISSION', 'NON_EMISSION'))
);

COMMENT ON COLUMN das_change_classification.state IS
    'UNDETERMINED 是技术上判不了（按重大更改处理，待确认）；BEYOND_AUTHORITY 是权限不够（转局方办理，结论留空）。两者不可合并：合并后权限不足会自动升级为重大更改，而那是用权限问题冒充技术判断，UG-DAP-06 第 4 步明文禁止。';

-- ---------------------------------------------------------------------
-- 5. 累计影响（UG-DAW-010 第 1 章）
-- ---------------------------------------------------------------------
-- 一系列小改累积后可能构成重大更改, 须与此前的更改合并评估。
-- 做成关联表而不是一个布尔"已考虑累计影响": 布尔勾完说不出跟哪几次一起评的。
CREATE TABLE IF NOT EXISTS das_change_cumulative (
    id           bigserial PRIMARY KEY,
    change_id    bigint NOT NULL REFERENCES das_design_change (id),
    prior_change_id bigint NOT NULL REFERENCES das_design_change (id),
    assessment   text NOT NULL,            -- 合并评估的结论
    assessed_by  uuid NOT NULL REFERENCES app_user (id),
    assessed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dccu UNIQUE (change_id, prior_change_id),
    CONSTRAINT ck_dccu_self CHECK (change_id <> prior_change_id),
    CONSTRAINT ck_dccu_text CHECK (length(btrim(assessment)) > 0)
);

-- ---------------------------------------------------------------------
-- 6. 制造偏离／让座（UG-DAP-06 第 7 步）
-- ---------------------------------------------------------------------
-- 原文: 授权人员评估对设计数据、关键特性和适航的影响并记录;
-- **影响适航的不得批准, 回到设计更改流程。**
CREATE TABLE IF NOT EXISTS das_manufacturing_deviation (
    id           bigserial PRIMARY KEY,
    deviation_no text NOT NULL UNIQUE,
    requested_by text NOT NULL,            -- 生产方（可能是外部单位）
    requested_on date NOT NULL,
    description  text NOT NULL,
    affects_design_data boolean NOT NULL,
    affects_key_characteristics boolean NOT NULL,
    affects_airworthiness boolean NOT NULL,
    assessment   text NOT NULL,
    -- 影响适航时**不得批准**: approved 只能为 false, 且须指向回到设计更改流程的那条更改。
    approved     boolean NOT NULL,
    change_id    bigint REFERENCES das_design_change (id),
    assessed_by  uuid NOT NULL REFERENCES app_user (id),
    assessed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dmd_text CHECK (
        length(btrim(requested_by)) > 0 AND length(btrim(description)) > 0
        AND length(btrim(assessment)) > 0),
    -- 写成 (affects AND NOT approved) OR (...) 不行: 任一侧为 NULL 时整式为 NULL,
    -- 而 CHECK 在 NULL 时放行。三列都是 NOT NULL, 但用 CASE 写才经得起以后放开非空。
    CONSTRAINT ck_dmd_airworthiness CHECK (
        CASE WHEN affects_airworthiness
             THEN approved = false AND change_id IS NOT NULL
             ELSE true
        END)
);

COMMENT ON TABLE das_manufacturing_deviation IS
    'UG-DAP-06 第 7 步：制造偏离／让步。影响适航的不得批准，且须指向回到设计更改流程的那条更改（ck_dmd_airworthiness）。';

-- ---------------------------------------------------------------------
-- 7. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-104: 9 项判据未逐条给出结论, 不得出分类结论。
-- DCMS-INV-105: 任一判据为显著影响 → 只能是重大更改。
-- DCMS-INV-106: 超权限不得填成重大更改（UG-DAP-06 第 4 步）。
-- DCMS-INV-107: 声学与排放两项本单位无权利, 结论只能来自局方。
CREATE OR REPLACE FUNCTION dcms_check_das_change_classification() RETURNS trigger AS $$
DECLARE
    v_total  int;
    v_done   int;
    v_missing text;
    v_sig    text;
BEGIN
    SELECT count(*) INTO v_total FROM das_change_criterion;
    SELECT count(*) INTO v_done FROM das_change_criterion_assessment
     WHERE change_id = NEW.change_id;

    -- 超权限或局方不同意见的两种状态下, 逐条打勾仍然要做（分类建议是本单位的义务,
    -- 转局方办理的是**批准**, 不是**分析**）。所以这道校验对所有状态生效。
    IF v_done < v_total THEN
        SELECT string_agg(c.seq || '. ' || c.name_cn, '、' ORDER BY c.seq)
          INTO v_missing
          FROM das_change_criterion c
         WHERE NOT EXISTS (SELECT 1 FROM das_change_criterion_assessment a
                            WHERE a.change_id = NEW.change_id
                              AND a.criterion_code = c.code);
        RAISE EXCEPTION 'DCMS-INV-104: 下列判据还没有给出结论, 不得出分类结论: %。'
                        'UG-DAW-010 第 3 章: 按第 2 章逐条判据打勾并写明依据, 不得只写"无影响"而无理由。'
                        '漏掉一条判据的分类, 在局方复查时等于没有分类依据。', v_missing;
    END IF;

    SELECT string_agg(c.name_cn, '、' ORDER BY c.seq) INTO v_sig
      FROM das_change_criterion_assessment a
      JOIN das_change_criterion c ON c.code = a.criterion_code
     WHERE a.change_id = NEW.change_id AND a.verdict = 'SIGNIFICANT';

    -- 判据原文: 判据中**任一项**达到"显著影响"即为重大更改。
    IF v_sig IS NOT NULL AND NEW.major_minor = 'MINOR' THEN
        RAISE EXCEPTION 'DCMS-INV-105: 「%」已判为显著影响, 不得分类为小改。'
                        'UG-DAW-010 第 1 章: 判据中任一项达到"显著影响"即为重大更改。',
                        v_sig;
    END IF;

    -- 【超权限不得自动判为大改】
    IF NEW.state = 'BEYOND_AUTHORITY' THEN
        IF NEW.major_minor IS NOT NULL THEN
            RAISE EXCEPTION 'DCMS-INV-106: 超出分类或批准权限时结论须留空, 转局方办理（UG-DAP-06 第 4 步: '
                            '不能仅因超权限自动判为大改）。填成 % 是用权限问题冒充技术判断 —— '
                            '而重大更改要走 UG-DAP-09 向局方申请批准, 等于凭一个权限缺口给产品加了一道审定。',
                            NEW.major_minor;
        END IF;
        IF length(btrim(coalesce(NEW.caac_referral_ref, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-106: 超权限时须写明转局方办理的依据（函件或咨询编号）。'
                            '只标一个"超权限"而不转办, 这条更改就卡在那里没人管。';
        END IF;
    END IF;

    -- 技术上判不了: 按重大更改处理, 但要留"待确认"的痕迹, 不能当成已分类。
    IF NEW.state = 'UNDETERMINED' THEN
        IF NEW.major_minor IS DISTINCT FROM 'MAJOR' THEN
            RAISE EXCEPTION 'DCMS-INV-105: 无法判定的按重大更改处理（UG-DAW-010 第 1 章）, '
                            '直至适航管理负责人或局方确认。此处须填 MAJOR, 实际为 %。',
                            coalesce(NEW.major_minor, '空');
        END IF;
        IF NEW.signed_by IS NOT NULL THEN
            RAISE EXCEPTION 'DCMS-INV-105: 判不了的分类不得签署。按重大更改处理是**暂行处置**, '
                            '待适航管理负责人或局方确认后再签 —— 签了就成了已分类, 而它还没定。';
        END IF;
    END IF;

    IF NEW.state = 'CLASSIFIED' THEN
        IF NEW.major_minor IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-104: 已分类却没有大改／小改结论。';
        END IF;
        IF NEW.signed_by IS NULL OR NEW.signed_on IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-104: 已分类须有签署人与签署日期（UG-DAF-02 由授权人员签署）。';
        END IF;
    END IF;

    -- 声学与排放: 本单位未申请批准权, 结论只能来自局方, 故给了结论就必须有局方编号与日期。
    IF NEW.acoustic_result IS NOT NULL
       AND (length(btrim(coalesce(NEW.acoustic_caac_ref, ''))) = 0
            OR NEW.acoustic_caac_on IS NULL) THEN
        RAISE EXCEPTION 'DCMS-INV-107: 声学分类本单位未申请批准权, 结论只能来自局方 —— '
                        '须记录局方编号与日期（UG-DAP-06 第 4 步）。内部给结论就是在没有权利的事项上作了批准。';
    END IF;
    IF NEW.emission_result IS NOT NULL
       AND (length(btrim(coalesce(NEW.emission_caac_ref, ''))) = 0
            OR NEW.emission_caac_on IS NULL) THEN
        RAISE EXCEPTION 'DCMS-INV-107: 排放分类本单位未申请批准权, 结论只能来自局方 —— '
                        '须记录局方编号与日期（UG-DAP-06 第 4 步）。';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_change_classification_check ON das_change_classification;
CREATE TRIGGER trg_das_change_classification_check
    BEFORE INSERT OR UPDATE ON das_change_classification
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_change_classification();

-- DCMS-INV-108: 判据结论、分类结论、累计评估、偏离记录均不得删除或改写。
-- UG-DAF-02 是局方表-21-173 的本单位版本, 签了就是对外陈述。
-- 另: UG-DAP-06 第 5 步"作废号保留记录" —— 作废也是留记录, 不是删掉。
CREATE OR REPLACE FUNCTION dcms_guard_das_change_append() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-INV-108: % 不得%。UG-DAF-02 对应局方表-21-173, 签了就是对外陈述; '
                    'UG-DAP-06 第 5 步连作废号都要保留记录 —— 更正请另记一条并写明理由。',
                    TG_TABLE_NAME,
                    CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_criterion_assessment_append ON das_change_criterion_assessment;
CREATE TRIGGER trg_das_criterion_assessment_append
    BEFORE UPDATE OR DELETE ON das_change_criterion_assessment
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_change_append();

DROP TRIGGER IF EXISTS trg_das_cumulative_append ON das_change_cumulative;
CREATE TRIGGER trg_das_cumulative_append
    BEFORE UPDATE OR DELETE ON das_change_cumulative
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_change_append();

DROP TRIGGER IF EXISTS trg_das_deviation_append ON das_manufacturing_deviation;
CREATE TRIGGER trg_das_deviation_append
    BEFORE UPDATE OR DELETE ON das_manufacturing_deviation
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_change_append();

-- 分类结论允许 UPDATE（局方不同意见要改 state 为 CAAC_DISAGREED, 待确认转已分类）,
-- 但不得删除, 且每次改动留痕。
CREATE TABLE IF NOT EXISTS das_change_classification_change (
    id           bigserial PRIMARY KEY,
    classification_id bigint NOT NULL REFERENCES das_change_classification (id),
    old_state    text,
    new_state    text,
    old_major_minor text,
    new_major_minor text,
    changed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION dcms_log_das_classification_change() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-108: 分类结论不得删除。局方有不同意见时改 state 为 CAAC_DISAGREED '
                        '并按 UG-DAP-14 复查同类历史更改 —— 删掉等于那次分类从未发生过。';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state
       OR NEW.major_minor IS DISTINCT FROM OLD.major_minor THEN
        INSERT INTO das_change_classification_change
               (classification_id, old_state, new_state, old_major_minor, new_major_minor)
        VALUES (OLD.id, OLD.state, NEW.state, OLD.major_minor, NEW.major_minor);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_classification_log ON das_change_classification;
CREATE TRIGGER trg_das_classification_log
    BEFORE UPDATE OR DELETE ON das_change_classification
    FOR EACH ROW EXECUTE FUNCTION dcms_log_das_classification_change();

-- ---------------------------------------------------------------------
-- 8. 视图
-- ---------------------------------------------------------------------

-- 8.1 判据打勾的完整性。少一条就不能出结论（DCMS-INV-104）。
CREATE OR REPLACE VIEW das_change_criterion_coverage AS
SELECT d.id AS change_id, d.change_no, d.title,
       (SELECT count(*) FROM das_change_criterion) AS criteria_total,
       count(a.id) AS assessed,
       count(*) FILTER (WHERE a.verdict = 'SIGNIFICANT') AS significant,
       coalesce(string_agg(c.name_cn, '、' ORDER BY c.seq)
                FILTER (WHERE a.verdict = 'SIGNIFICANT'), '') AS significant_items,
       coalesce((SELECT string_agg(c2.name_cn, '、' ORDER BY c2.seq)
                   FROM das_change_criterion c2
                  WHERE NOT EXISTS (SELECT 1 FROM das_change_criterion_assessment a2
                                     WHERE a2.change_id = d.id
                                       AND a2.criterion_code = c2.code)), '')
                                                                   AS missing_items
  FROM das_design_change d
  LEFT JOIN das_change_criterion_assessment a ON a.change_id = d.id
  LEFT JOIN das_change_criterion c ON c.code = a.criterion_code
 GROUP BY d.id, d.change_no, d.title
 ORDER BY d.change_no;

-- 8.2 转局方办理而未取得结论的。
-- **这一档不是"已分类"**: 超权限的分类结论留空, 等局方办理结果。
-- 没有这张表, 这些更改会在台账里显示为"有分类记录", 而结论其实是空的。
CREATE OR REPLACE VIEW das_change_awaiting_caac AS
SELECT cl.id, d.change_no, d.title, cl.state, cl.caac_referral_ref,
       cl.acoustic_result, cl.emission_result,
       (cl.acoustic_result IS NULL) AS acoustic_pending,
       (cl.emission_result IS NULL) AS emission_pending,
       cl.created_at
  FROM das_change_classification cl
  JOIN das_design_change d ON d.id = cl.change_id
 WHERE cl.state IN ('BEYOND_AUTHORITY', 'UNDETERMINED', 'CAAC_DISAGREED')
    OR cl.acoustic_result IS NULL OR cl.emission_result IS NULL
 ORDER BY cl.created_at;

-- 8.3 没有做累计影响评估的更改。
-- UG-DAW-010 第 1 章: 一系列小改累积后可能构成重大更改。本视图列出判为小改、
-- 而同一项目下此前另有更改、却没有任何合并评估记录的 —— 这正是"一串小改永远是小改"
-- 会发生的地方。
CREATE OR REPLACE VIEW das_change_cumulative_gap AS
SELECT d.id AS change_id, d.change_no, d.title, p.project_no,
       (SELECT count(*) FROM das_design_change o
         WHERE o.project_id = d.project_id AND o.id <> d.id
           AND o.raised_on <= d.raised_on)                           AS prior_changes,
       (SELECT count(*) FROM das_change_cumulative cu
         WHERE cu.change_id = d.id)                                  AS cumulative_rows
  FROM das_design_change d
  JOIN das_change_classification cl ON cl.change_id = d.id
  LEFT JOIN das_project p ON p.id = d.project_id
 WHERE cl.major_minor = 'MINOR'
   AND d.project_id IS NOT NULL
   AND (SELECT count(*) FROM das_design_change o
         WHERE o.project_id = d.project_id AND o.id <> d.id
           AND o.raised_on <= d.raised_on) > 0
   AND (SELECT count(*) FROM das_change_cumulative cu
         WHERE cu.change_id = d.id) = 0
 ORDER BY d.change_no;

COMMENT ON VIEW das_change_cumulative_gap IS
    '判为小改、同项目此前另有更改、却没有任何合并评估记录的更改。UG-DAW-010 第 1 章要求累计考虑——这张表列出的正是「一串小改永远是小改」会发生的地方。';

-- 8.4 影响适航的制造偏离。按原文一律不得批准, 且须回到设计更改流程。
CREATE OR REPLACE VIEW das_deviation_to_change AS
SELECT dv.deviation_no, dv.requested_by, dv.requested_on, dv.approved,
       d.change_no, d.title
  FROM das_manufacturing_deviation dv
  LEFT JOIN das_design_change d ON d.id = dv.change_id
 WHERE dv.affects_airworthiness
 ORDER BY dv.requested_on DESC;

-- ---------------------------------------------------------------------
-- 9. 9 项判据（配置数据，UG-DAW-010 第 2 章原文）
-- ---------------------------------------------------------------------
INSERT INTO das_change_criterion
    (code, seq, name_cn, significant_when, evidence_required, source_doc, note)
VALUES
('WEIGHT_BALANCE', 1, '重量与平衡',
 '超出已批准的重量重心包线',
 '重量重心计算书、称重记录', 'UG-DAW-010 第 2 章',
 '原文曾有"或单项重量变化超过【】kg／重心移动超过【】%MAC"。待澄清项第 9 条已决策关闭: '
 '**不填, 删除该子句** —— 体系文件是制度不是技术标准, 产品技术参数不进体系文件'
 '（判据 P5）。故本表没有阈值字段: 加一个就等于把那条被删掉的子句搬进系统, '
 '而且它会变成一个没人维护、却被当成判定依据的数'),
('STRUCTURE', 2, '结构强度',
 '改变载荷路径、主要结构元件、连接形式、材料牌号或热处理状态；使安全裕度下降',
 '强度分析、试验报告、材料对比', 'UG-DAW-010 第 2 章', NULL),
('SAFETY', 3, '可靠性与安全性',
 '改变失效模式、失效影响等级或失效概率；影响已声明的安全性评估结论',
 'FMEA／FHA／SSA 对比分析', 'UG-DAW-010 第 2 章', NULL),
('SYSTEM', 4, '系统性能与功能',
 '改变系统架构、控制逻辑、软件等级、接口或余度；性能超出已批准指标',
 '系统说明、软件等级评估、试验', 'UG-DAW-010 第 2 章', NULL),
('FLIGHT', 5, '飞行与运行特性',
 '影响操纵品质、稳定性、性能数据、使用限制或飞行手册内容',
 '飞行试验、性能计算、AFM 对比', 'UG-DAW-010 第 2 章',
 '本单位不开展验证试飞（UG-DAP-07 第 13 步）, 涉及飞行试验的验证按手册范围更改的前置要求办理'),
('NOISE_EMISSION', 6, '噪声与排放',
 '影响已批准的噪声或排放符合性结论',
 '噪声/排放符合性分析', 'UG-DAW-010 第 2 章',
 '本判据与分类结论里的声学／排放两项不是同一件事: 这里判的是"有没有影响"'
 '（本单位要做的分析）, 那里是"分类结论"（本单位未申请批准权, 须由局方给出）'),
('CERT_BASIS', 7, '审定基础',
 '引入新的适用条款、专用条件、等效安全或豁免；改变原审定基础',
 '审定基础对比表', 'UG-DAW-010 第 2 章', NULL),
('AD', 8, '适航指令',
 '影响某项 AD 的适用性或其规定的措施',
 'AD 适用性分析', 'UG-DAW-010 第 2 章',
 '涉及 AD 的须通知适航管理负责人（UG-DAP-06 第 6 步）'),
('ICA', 9, '持续适航',
 '改变维修任务、检查间隔、寿命限制或 ICA 内容',
 'ICA 对比、维修大纲影响分析', 'UG-DAW-010 第 2 章', NULL)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 10. 把本模块登进"范围校验尚未施加的签署路径"（0044 的清单）
-- ---------------------------------------------------------------------
-- UG-DAP-06 第 4 步原文: 具备相应有效授权的人员**仅在许可权利范围内**批准大改／小改分类。
-- 这正是判据 I10.PRODUCT_SCOPE 管的那件事, 而本模块**没有施加**范围校验:
-- 更改申请的"涉及产品"是自由文本（UG-DAP-06 第 1 步的原文要求就是"说明涉及产品和图号"）,
-- 不是可比对的结构化引用, 所以 das_signer_scope_covers 在这里给不出 true/false。
--
-- 不登进那张清单的后果是清单**不完整**: 0044 的用例从系统目录反查谁在调覆盖函数,
-- 那道闸门只拦得住"新增了强制点却没改清单"; 反方向 ——
-- **新增了一条没有强制点的签署路径** —— 它拦不住。所以这里主动登记。
--
-- 只改 VALUES 的行, **不改列集**: 0039 第 9 节栽过一次, 替换视图时改了列集,
-- 备份恢复的往返流程（恢复一份已含新 schema 的备份再从 0001 重放）会在改动之前就失败。
CREATE OR REPLACE VIEW das_signer_scope_unenforced AS
SELECT * FROM (VALUES
    ('文件三级签署（files／approval_step）', '调用方只传文件类型与专业, 不传产品标的',
     '签署流程未与项目实体关联; 关联后才谈得上范围校验'),
    ('签署授权的授予与撤销（signers.grant／revoke）', '授予时不涉及具体标的',
     '不适用: 这是授权管理, 不是签署'),
    ('设计更改分类（UG-DAF-02, M4）',
     '更改申请的"涉及产品"是自由文本, 不是可比对的结构化引用',
     'UG-DAP-06 第 4 步要求"仅在许可权利范围内"批准分类 —— 这一条目前由纸面授权书把关。'
     '要做成校验, 须先把更改申请的涉及产品改成件号/图号族引用（与 I10.PRODUCT_SCOPE 同源）'),
    ('制造符合性声明（UG-DAF-12）', '尚未建模（M3 的下一片）',
     '试验前的制造符合性声明按 UG-DAP-07 第 3 步签署, 标的是试验产品'),
    ('小改批准（UG-DAP-08）', '尚未建模（M5）', '标的是具体更改对象')
  ) AS t(path, why_unenforced, note);
