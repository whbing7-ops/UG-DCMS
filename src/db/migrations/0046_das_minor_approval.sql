-- ---------------------------------------------------------------------
-- 0046  M5 设计小改批准（UG-DAP-08、AP-21-18 表-21-174）
-- ---------------------------------------------------------------------
-- 依据 UG-DAP-08《设计小改批准程序》第 2 章（适用范围）、第 6 章（6 步）、
-- 第 7 章（关键控制点）, UG-DAF-04（编号后缀 PZ）。
--
-- 【第 1 步的三个条件是三条, 任一不满足不得批准】
-- 原文: a) 已有签署的分类表且为小改; b) 在本单位设计机构许可项目单的权利范围内;
-- c) 批准人在授权范围内。**任一项不满足, 不得批准。**
-- 所以这三条各自落一行记录, 不是一个"条件已核对"的勾 —— 勾完说不出哪一条是怎么核的。
--
-- 【与 M4 的硬连接, 以及它会失效这件事】
-- 条件 a) 要求"已有签署的分类表且为小改"。所以小改批准必须指向一条
-- state='CLASSIFIED' 且 major_minor='MINOR' 的分类结论。
-- **而分类结论会变**: UG-DAP-06 的"局方对分类有不同意见时以局方意见为准"
-- （M4 的 CAAC_DISAGREED）。一旦底下那条分类不再是已签署的小改,
-- 建立在它上面的小改批准就失去了前提 —— 这不是历史问题, 是**现在发出去的资料
-- 带着一个不成立的批准**。所以有 das_minor_approval_invalidated 把这些列出来,
-- 由 M7 按 UG-DAP-14 处理。只在插入时校验一次是不够的。
--
-- 【批准声明必须是局方规定的原文】
-- 第 5 步给了原文, 第 7 章再强调一遍"批准声明必须使用局方规定的原文"。
-- 所以声明文本是**受控配置**, 不是代码里的字符串常量: 写进代码, 改一个字要发版本,
-- 而且没人能在系统里看到现行文本是什么（判据 P1～P5 的同一个道理）。
-- 发布时逐字比对, 不相符即拒。
--
-- 【声明里的许可证编号现在填不出来】
-- 原文的声明带占位符【设计机构许可证编号】, 而本单位的 DOA 尚在申请中 ——
-- 这个号还不存在。于是**对外发布暂时过不了这道门**, 这是如实状态而不是缺陷:
-- das_approval_statement_blocker 把它列出来。
-- 硬塞一个编号进去才是缺陷: 那份资料会带着一个假的许可证编号发出去。
--
-- 【超权限不得改判, 与 M4 第 4 步同一条规则】
-- 第 7 章: 超出本单位许可范围或批准权限的更改, 不得自行批准; 按技术影响保留大改／小改
-- 分类建议, 提交局方办理或先申请权限变更, **不得仅因无批准权限自动改判大改**。
-- M4 已在 DCMS-INV-106 实现这一条。本模块这一侧的落点是: 条件 b)／c) 不满足时
-- **不得批准**, 而分类结论**保持原样**（不得回头把它改成大改）。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 批准声明（受控配置）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_approval_statement (
    code         text PRIMARY KEY,
    -- 局方规定的原文, 含占位符。逐字比对的基准。
    template     text NOT NULL,
    placeholder  text,                     -- 须替换的占位符
    source_doc   text NOT NULL,
    effective_from date NOT NULL,
    note         text,
    CONSTRAINT ck_das_text CHECK (length(btrim(template)) > 0)
);

COMMENT ON TABLE das_approval_statement IS
    '局方规定的批准声明原文（受控配置，不是代码里的字符串常量）。UG-DAP-08 第 7 章：批准声明必须使用局方规定的原文——所以发布时逐字比对。';

-- ---------------------------------------------------------------------
-- 2. 对外发布的资料种类（配置）
-- ---------------------------------------------------------------------
-- 第 5 步把种类分成两类, 原文列得很清楚:
--   须附声明: 服务通告、飞行手册补充、持续适航文件;
--   **不使用此声明**: 符合性文件、审定计划、生产用设计数据。
-- 做成配置而不是代码里的 if: 这是"哪些资料对外代表设计批准"的判断,
-- 它会随权利范围变, 而每次变都要能看出变过。
CREATE TABLE IF NOT EXISTS das_release_doc_kind (
    code         text PRIMARY KEY,
    name_cn      text NOT NULL,
    requires_statement boolean NOT NULL,
    reason       text NOT NULL,
    source_doc   text NOT NULL,
    CONSTRAINT ck_drdk_reason CHECK (length(btrim(reason)) > 0)
);

-- ---------------------------------------------------------------------
-- 3. 小改批准（UG-DAF-04，编号后缀 PZ）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_minor_approval (
    id           bigserial PRIMARY KEY,
    -- 指向 M4 的分类结论。条件 a): 已有签署的分类表且为小改。
    classification_id bigint NOT NULL REFERENCES das_change_classification (id),
    -- 编号: 第 4 步"编号后缀 PZ"。同 0043／0045, 不校验许可证编号部分。
    form_no      text NOT NULL UNIQUE,
    -- 第 4 步: 载明范围和限制条件。两者都必须写明 ——
    -- 小改批准是"在权利范围内"作的批准, 范围写不清就等于没有边界。
    approved_scope text NOT NULL,
    limitations  text,
    no_limitation_declared boolean NOT NULL DEFAULT false,
    -- 第 3 步: CVE 核查符合性（UG-DAP-07）。
    cve_check_ref text NOT NULL,
    cve_user_id  uuid REFERENCES app_user (id),
    -- 第 2 步: 更改说明、图纸/数据、必要的验证依据。
    change_doc_ref text NOT NULL,
    -- 第 4 步: 授权人员签署。"批准人在授权范围内"这一条系统判不全（见条件表的说明）,
    -- 所以要么是在任适航管理负责人, 要么写明纸面授权依据 —— 同 0043 的符合性声明。
    approved_by  uuid NOT NULL REFERENCES app_user (id),
    approved_on  date NOT NULL,
    authority_ref text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dma_pz CHECK (form_no ~ '-PZ(-|$)' OR form_no ~ 'PZ$'),
    CONSTRAINT ck_dma_text CHECK (
        length(btrim(approved_scope)) > 0 AND length(btrim(cve_check_ref)) > 0
        AND length(btrim(change_doc_ref)) > 0),
    -- 限制条件为空必须是一句明话（同 0044 的 ck_dss_limit）。
    CONSTRAINT ck_dma_limit CHECK (
        no_limitation_declared OR length(btrim(coalesce(limitations, ''))) > 0)
);

COMMENT ON TABLE das_minor_approval IS
    'UG-DAF-04 小改批准（编号后缀 PZ）。必须指向一条已签署且为小改的分类结论；分类结论后来变了的由 das_minor_approval_invalidated 列出。';

-- ---------------------------------------------------------------------
-- 4. 第 1 步的三个核对条件
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_minor_approval_condition (
    id           bigserial PRIMARY KEY,
    approval_id  bigint NOT NULL REFERENCES das_minor_approval (id),
    condition_code text NOT NULL,          -- CLASSIFIED_MINOR / WITHIN_DOA_SCOPE /
                                           -- APPROVER_AUTHORISED
    satisfied    boolean NOT NULL,
    evidence     text NOT NULL,            -- 怎么核的
    checked_by   uuid NOT NULL REFERENCES app_user (id),
    checked_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dmac UNIQUE (approval_id, condition_code),
    CONSTRAINT ck_dmac_code CHECK (condition_code IN
        ('CLASSIFIED_MINOR', 'WITHIN_DOA_SCOPE', 'APPROVER_AUTHORISED')),
    CONSTRAINT ck_dmac_evidence CHECK (length(btrim(evidence)) > 0)
);

COMMENT ON TABLE das_minor_approval_condition IS
    'UG-DAP-08 第 1 步的三个核对条件，各自一行并写明怎么核的。任一不满足不得批准（DCMS-INV-109）——一个「条件已核对」的勾说不出哪一条是怎么核的。';

-- ---------------------------------------------------------------------
-- 5. 发布（第 5～6 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_minor_approval_release (
    id           bigserial PRIMARY KEY,
    approval_id  bigint NOT NULL REFERENCES das_minor_approval (id),
    doc_kind     text NOT NULL REFERENCES das_release_doc_kind (code),
    doc_ref      text NOT NULL,            -- 资料编号与版次
    -- 须附声明的种类: 这里存的是**实际印在资料上的那段文字**, 发布时与受控原文逐字比对。
    statement_text text,
    distributed_to text NOT NULL,          -- 通知了谁（使用者、生产、采购）
    caac_notified boolean NOT NULL DEFAULT false,
    archived_ref text NOT NULL,            -- 归档记录
    released_by  uuid NOT NULL REFERENCES app_user (id),
    released_on  date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dmar_text CHECK (
        length(btrim(doc_ref)) > 0 AND length(btrim(distributed_to)) > 0
        AND length(btrim(archived_ref)) > 0)
);

-- ---------------------------------------------------------------------
-- 6. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-109: 三个条件未全部核过且满足, 不得批准（UG-DAP-08 第 1 步）。
-- DCMS-INV-110: 底下那条分类必须是已签署的小改。
-- DCMS-INV-111: 批准人不是在任适航管理负责人时须写明纸面授权依据。
CREATE OR REPLACE FUNCTION dcms_check_das_minor_approval() RETURNS trigger AS $$
DECLARE
    v_state text;
    v_mm    text;
    v_holds boolean;
BEGIN
    SELECT state, major_minor INTO v_state, v_mm
      FROM das_change_classification WHERE id = NEW.classification_id;
    IF v_state <> 'CLASSIFIED' OR v_mm <> 'MINOR' THEN
        RAISE EXCEPTION 'DCMS-INV-110: 小改批准的前提是"已有签署的分类表且为小改"（UG-DAP-08 第 1 步 a）, '
                        '而该分类结论现在是 state=% / %。'
                        '不是已签署的小改就批准, 批的是一个还没定性或定性为大改的更改。',
                        v_state, coalesce(v_mm, '结论留空');
    END IF;

    -- "批准人在授权范围内"这一条系统判不全: 授权的第三维校验的是 DCMS 文档种类,
    -- 不是 UG-DAM-01-附2 的 5 类适航签署事项, 而"小改批准"正是那 5 类之一
    -- （判据 I10.FILE_TYPE 语义不符, 待澄清项第 1 条）。所以这里要的是说得出依据,
    -- 不冒充校验。
    v_holds := EXISTS (SELECT 1 FROM das_appointment_in_force
                        WHERE user_id = NEW.approved_by AND position_code = 'AWM');
    IF NOT v_holds AND length(btrim(coalesce(NEW.authority_ref, ''))) = 0 THEN
        RAISE EXCEPTION 'DCMS-INV-111: 批准人不是在任适航管理负责人时, 须写明纸面授权依据。'
                        '"小改批准"是 UG-DAM-01-附2 的 5 类适航签署事项之一, 而系统判不了'
                        '某份授权是否覆盖这件事（授权的第三维校验的是文档种类, 待澄清项第 1 条）—— '
                        '所以这里要的是说得出依据, 由独立监督核对。';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_minor_approval_check ON das_minor_approval;
CREATE TRIGGER trg_das_minor_approval_check
    BEFORE INSERT OR UPDATE ON das_minor_approval
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_minor_approval();

-- DCMS-INV-109 的另一半: 发布之前三个条件要全部核过且满足。
-- 放在发布那一步而不是批准那一步, 是因为条件记录要先引用 approval_id ——
-- 鸡生蛋。所以批准行可以先建, 但**未过条件不得发布**, 而没发布的批准不发生对外效果。
-- 另: 须附声明的种类必须附, 且逐字与受控原文相符（第 5 步、第 7 章）。
CREATE OR REPLACE FUNCTION dcms_check_das_minor_release() RETURNS trigger AS $$
DECLARE
    v_total   int;
    v_ok      int;
    v_missing text;
    v_bad     text;
    v_requires boolean;
    v_kind_cn text;
    v_tmpl    text;
    v_ph      text;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE satisfied) INTO v_total, v_ok
      FROM das_minor_approval_condition WHERE approval_id = NEW.approval_id;
    IF v_total < 3 THEN
        SELECT string_agg(x.cn, '、') INTO v_missing
          FROM (VALUES ('CLASSIFIED_MINOR', 'a) 已有签署的分类表且为小改'),
                       ('WITHIN_DOA_SCOPE', 'b) 在本单位设计机构许可项目单的权利范围内'),
                       ('APPROVER_AUTHORISED', 'c) 批准人在授权范围内')) AS x(code, cn)
         WHERE NOT EXISTS (SELECT 1 FROM das_minor_approval_condition c
                            WHERE c.approval_id = NEW.approval_id
                              AND c.condition_code = x.code);
        RAISE EXCEPTION 'DCMS-INV-109: 第 1 步的核对条件还差: %。三条各自要有核对记录与依据。',
                        v_missing;
    END IF;
    IF v_ok < v_total THEN
        SELECT string_agg(x.cn, '、') INTO v_bad
          FROM (VALUES ('CLASSIFIED_MINOR', 'a) 已有签署的分类表且为小改'),
                       ('WITHIN_DOA_SCOPE', 'b) 在本单位设计机构许可项目单的权利范围内'),
                       ('APPROVER_AUTHORISED', 'c) 批准人在授权范围内')) AS x(code, cn)
          JOIN das_minor_approval_condition c
            ON c.approval_id = NEW.approval_id AND c.condition_code = x.code
         WHERE NOT c.satisfied;
        RAISE EXCEPTION 'DCMS-INV-109: 下列条件不满足, 不得批准发布: %。UG-DAP-08 第 1 步: 任一项不满足, 不得批准。'
                        '注意第 7 章: 超出许可范围或批准权限的, 提交局方办理或先申请权限变更, '
                        '**不得仅因无批准权限自动改判大改** —— 分类结论保持原样。',
                        v_bad;
    END IF;

    SELECT requires_statement, name_cn INTO v_requires, v_kind_cn
      FROM das_release_doc_kind WHERE code = NEW.doc_kind;
    IF v_requires THEN
        SELECT template, placeholder INTO v_tmpl, v_ph
          FROM das_approval_statement WHERE code = 'DOA_MINOR_APPROVAL';
        IF length(btrim(coalesce(NEW.statement_text, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-112: 「%」对外发布须附局方规定的批准声明（UG-DAP-08 第 5 步）。', v_kind_cn;
        END IF;
        -- 占位符还在, 说明许可证编号没填。
        IF v_ph IS NOT NULL AND position(v_ph in NEW.statement_text) > 0 THEN
            RAISE EXCEPTION 'DCMS-INV-112: 声明里的占位符「%」还没替换成设计机构许可证编号。'
                            '本单位的 DOA 尚在申请中, 这个号还不存在 —— 所以对外发布暂时过不了这道门, '
                            '这是如实状态。硬塞一个编号进去才是问题: 那份资料会带着一个假的许可证编号发出去。',
                            v_ph;
        END IF;
        -- 逐字比对: 把占位符位置以外的文字与受控原文比。
        -- 做法是把原文按占位符切开, 要求声明文本以前半段开头、以后半段结尾,
        -- 而且中间那段（填进去的编号）不为空。
        IF v_ph IS NOT NULL THEN
            IF position(split_part(v_tmpl, v_ph, 1) in NEW.statement_text) <> 1
               OR right(NEW.statement_text, length(split_part(v_tmpl, v_ph, 2)))
                  <> split_part(v_tmpl, v_ph, 2)
               OR length(NEW.statement_text)
                  <= length(split_part(v_tmpl, v_ph, 1))
                     + length(split_part(v_tmpl, v_ph, 2)) THEN
                RAISE EXCEPTION 'DCMS-INV-112: 批准声明与局方规定的原文不符（UG-DAP-08 第 7 章: 必须使用原文）。'
                                '受控原文见 das_approval_statement。改写一个字, 这段声明的法律效力就说不清了。';
            END IF;
        ELSIF NEW.statement_text <> v_tmpl THEN
            RAISE EXCEPTION 'DCMS-INV-112: 批准声明与局方规定的原文不符（UG-DAP-08 第 7 章）。';
        END IF;
    ELSE
        IF length(btrim(coalesce(NEW.statement_text, ''))) > 0 THEN
            RAISE EXCEPTION 'DCMS-INV-112: 「%」**不使用**此声明（UG-DAP-08 第 5 步原文）。'
                            '给符合性文件、审定计划或生产用设计数据附上"按权利范围进行批准"的声明, '
                            '是把不代表设计批准的资料说成了设计批准。', v_kind_cn;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_minor_release_check ON das_minor_approval_release;
CREATE TRIGGER trg_das_minor_release_check
    BEFORE INSERT OR UPDATE ON das_minor_approval_release
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_minor_release();

-- DCMS-INV-113: 批准、条件核对、发布记录均不得删除或改写。
-- UG-DAF-04 对应 AP-21-18 表-21-174, 签了就是对外批准。
CREATE OR REPLACE FUNCTION dcms_guard_das_minor_append() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-INV-113: % 不得%。UG-DAF-04 对应 AP-21-18 表-21-174, 签了就是对外作出的批准; '
                    '发布记录是分发证据。更正请另记一条并写明理由。',
                    TG_TABLE_NAME,
                    CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_minor_approval_append ON das_minor_approval;
CREATE TRIGGER trg_das_minor_approval_append
    BEFORE UPDATE OR DELETE ON das_minor_approval
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_minor_append();

DROP TRIGGER IF EXISTS trg_das_minor_condition_append ON das_minor_approval_condition;
CREATE TRIGGER trg_das_minor_condition_append
    BEFORE UPDATE OR DELETE ON das_minor_approval_condition
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_minor_append();

DROP TRIGGER IF EXISTS trg_das_minor_release_append ON das_minor_approval_release;
CREATE TRIGGER trg_das_minor_release_append
    BEFORE UPDATE OR DELETE ON das_minor_approval_release
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_minor_append();

-- DCMS-INV-114: 受控的批准声明原文不得随手改。
-- 改它等于改对外批准的法律表述, 所以要留旧版 —— 已发出去的资料印的是旧版那段话。
CREATE TABLE IF NOT EXISTS das_approval_statement_history (
    id           bigserial PRIMARY KEY,
    code         text NOT NULL,
    old_template text NOT NULL,
    new_template text NOT NULL,
    changed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION dcms_log_das_statement_change() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-114: 批准声明原文不得删除。已发出去的资料印的就是这段话, '
                        '删掉之后无从核对那些资料印得对不对。';
    END IF;
    IF NEW.template IS DISTINCT FROM OLD.template THEN
        INSERT INTO das_approval_statement_history (code, old_template, new_template)
        VALUES (OLD.code, OLD.template, NEW.template);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_statement_log ON das_approval_statement;
CREATE TRIGGER trg_das_statement_log
    BEFORE UPDATE OR DELETE ON das_approval_statement
    FOR EACH ROW EXECUTE FUNCTION dcms_log_das_statement_change();

-- ---------------------------------------------------------------------
-- 7. 视图
-- ---------------------------------------------------------------------

-- 7.1 **底下那条分类已经不是已签署的小改了的小改批准。**
-- 这是本模块最要紧的一张视图。插入时校验过一次, 但 M4 的分类结论会变
-- （局方有不同意见时以局方意见为准）。一旦变了, 建立在它上面的批准就失去了前提,
-- 而**资料已经发出去了** —— 这不是历史问题, 是现在外面有一份带着不成立批准的资料。
-- 交 M7 按 UG-DAP-14 记不符合项处理。
CREATE OR REPLACE VIEW das_minor_approval_invalidated AS
SELECT a.id, a.form_no, a.approved_on, d.change_no, d.title,
       cl.state AS classification_state, cl.major_minor,
       (SELECT count(*) FROM das_minor_approval_release r WHERE r.approval_id = a.id)
                                                                   AS releases,
       CASE WHEN cl.state <> 'CLASSIFIED' THEN '分类结论已不是"已签署"状态'
            WHEN cl.major_minor <> 'MINOR' THEN '分类结论已不是小改'
       END AS why
  FROM das_minor_approval a
  JOIN das_change_classification cl ON cl.id = a.classification_id
  JOIN das_design_change d ON d.id = cl.change_id
 WHERE cl.state <> 'CLASSIFIED' OR cl.major_minor <> 'MINOR'
 ORDER BY a.approved_on DESC;

COMMENT ON VIEW das_minor_approval_invalidated IS
    '底下那条分类已不是「已签署的小改」的小改批准。插入时校验过一次是不够的：分类结论会变（局方有不同意见时以局方为准），而资料已经发出去了。交 M7 按 UG-DAP-14 处理。';

-- 7.2 条件没核全、或核了不满足的批准。没发布之前不发生对外效果, 但要看得见。
CREATE OR REPLACE VIEW das_minor_approval_condition_gap AS
SELECT a.id, a.form_no, a.approved_on,
       (SELECT count(*) FROM das_minor_approval_condition c
         WHERE c.approval_id = a.id)                               AS checked,
       (SELECT count(*) FROM das_minor_approval_condition c
         WHERE c.approval_id = a.id AND NOT c.satisfied)           AS unsatisfied,
       (SELECT count(*) FROM das_minor_approval_release r
         WHERE r.approval_id = a.id)                               AS releases
  FROM das_minor_approval a
 WHERE (SELECT count(*) FROM das_minor_approval_condition c
         WHERE c.approval_id = a.id) < 3
    OR EXISTS (SELECT 1 FROM das_minor_approval_condition c
                WHERE c.approval_id = a.id AND NOT c.satisfied)
 ORDER BY a.approved_on DESC;

-- 7.3 **许可证编号这道门。**
-- 声明原文带占位符, 而本单位的 DOA 尚在申请中。对外发布过不了这道门是如实状态,
-- 不是缺陷 —— 把它列出来, 省得有人以为是程序坏了去绕。
CREATE OR REPLACE VIEW das_approval_statement_blocker AS
SELECT s.code, s.template, s.placeholder,
       '设计机构许可证编号尚不存在（DOA 在申请中）：须附声明的资料暂不能对外发布。'
       '这是如实状态，不是缺陷——硬塞一个编号进去，那份资料会带着一个假的许可证编号发出去'
                                                                   AS note,
       (SELECT string_agg(k.name_cn, '、' ORDER BY k.code)
          FROM das_release_doc_kind k WHERE k.requires_statement)  AS blocked_kinds,
       (SELECT string_agg(k.name_cn, '、' ORDER BY k.code)
          FROM das_release_doc_kind k WHERE NOT k.requires_statement)
                                                                   AS unaffected_kinds
  FROM das_approval_statement s
 WHERE s.placeholder IS NOT NULL;

-- 7.4 小改批准台账。
CREATE OR REPLACE VIEW das_minor_approval_register AS
SELECT a.id, a.form_no, d.change_no, d.title, p.project_no,
       a.approved_scope, a.limitations, a.no_limitation_declared,
       a.cve_check_ref, u.full_name AS approved_by_name, a.approved_on,
       a.authority_ref,
       (a.authority_ref IS NOT NULL) AS signed_under_delegation,
       (SELECT count(*) FROM das_minor_approval_release r WHERE r.approval_id = a.id)
                                                                   AS releases
  FROM das_minor_approval a
  JOIN das_change_classification cl ON cl.id = a.classification_id
  JOIN das_design_change d ON d.id = cl.change_id
  LEFT JOIN das_project p ON p.id = d.project_id
  JOIN app_user u ON u.id = a.approved_by
 ORDER BY a.approved_on DESC;

-- ---------------------------------------------------------------------
-- 8. 配置数据
-- ---------------------------------------------------------------------
-- 批准声明: UG-DAP-08 第 5 步的原文, 一字不改。
INSERT INTO das_approval_statement
    (code, template, placeholder, source_doc, effective_from, note)
VALUES
('DOA_MINOR_APPROVAL',
 '本资料由经CAAC批准的【设计机构许可证编号】号设计机构许可证持有人按照设计机构许可项目单明确的权利范围进行批准。',
 '【设计机构许可证编号】',
 'UG-DAP-08 第 6 章第 5 步', DATE '2000-01-01',
 '原文照录, 不得改写（第 7 章: 批准声明必须使用局方规定的原文）。'
 '占位符须替换为本单位的设计机构许可证编号 —— 该号尚不存在（DOA 在申请中）, '
 '故须附声明的资料暂不能对外发布, 见 das_approval_statement_blocker。'
 '生效日期取系统纪元: 这段话在系统之前就已经是局方规定的原文了')
ON CONFLICT (code) DO NOTHING;

-- 对外发布的资料种类: 第 5 步把两类列得很清楚。
INSERT INTO das_release_doc_kind (code, name_cn, requires_statement, reason, source_doc)
VALUES
('SERVICE_BULLETIN', '服务通告', true,
 '对外发布且代表本单位在权利范围内作出的设计批准', 'UG-DAP-08 第 5 步'),
('AFM_SUPPLEMENT', '飞行手册补充', true,
 '对外发布且代表本单位在权利范围内作出的设计批准', 'UG-DAP-08 第 5 步'),
('ICA', '持续适航文件', true,
 '对外发布且代表本单位在权利范围内作出的设计批准', 'UG-DAP-08 第 5 步'),
('COMPLIANCE_DOC', '符合性文件', false,
 '**不使用此声明**（第 5 步原文）。它是向局方表明符合性的材料, 不是本单位作出的设计批准',
 'UG-DAP-08 第 5 步'),
('CERT_PLAN', '审定计划', false,
 '**不使用此声明**（第 5 步原文）。它是与局方约定的工作计划, 不是设计批准',
 'UG-DAP-08 第 5 步'),
('PRODUCTION_DATA', '生产用设计数据', false,
 '**不使用此声明**（第 5 步原文）。它是交生产用的数据, 批准状态另按 UG-DAP-10 协调',
 'UG-DAP-08 第 5 步')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 9. 登进 0044 的"范围校验尚未施加"清单
-- ---------------------------------------------------------------------
-- 第 1 步条件 c)"批准人在授权范围内"正是判据 I10.PRODUCT_SCOPE 与 I10.FILE_TYPE 管的事,
-- 而本模块只做到"说得出纸面授权依据"。小改批准是 UG-DAM-01-附2 的 5 类适航签署事项之一,
-- 系统判不了某份授权是否覆盖这件事（待澄清项第 1 条）。
-- 只改 VALUES 的行, 不改列集（0039 第 9 节的教训）。
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
    ('小改批准（UG-DAF-04, M5）',
     '"批准人在授权范围内"要判的是 5 类适航签署事项之一, 而授权第三维校验的是文档种类',
     'UG-DAP-08 第 1 步 c) 由 DCMS-INV-111 要求写明纸面授权依据, 不冒充校验; '
     '判据 I10.FILE_TYPE 语义不符, 待澄清项第 1 条'),
    ('制造符合性声明（UG-DAF-12）', '尚未建模（M3 的下一片）',
     '试验前的制造符合性声明按 UG-DAP-07 第 3 步签署, 标的是试验产品')
  ) AS t(path, why_unenforced, note);
