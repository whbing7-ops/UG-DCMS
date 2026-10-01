-- ---------------------------------------------------------------------
-- 0043  M3 项目实体与三条审定路径（判据 M3-1）
-- ---------------------------------------------------------------------
-- 依据 UG-DAP-09《设计批准取证管理程序》第 6 章（9 步）、UG-DAP-04《设计控制程序》
-- 第 6 章（11 步）、UG-DAP-07《符合性验证和核查程序》第 6 章（13 步）、
-- UG-DAW-002《文件编码规则》第 5 章（项目编号）、设计输入第 8.2／8.3 节。
--
-- 【判据 M3-1: 状态机按项目类型参数化, 第三类不得复用"出符合性声明"分支】
-- 三条路径不是两条。UG-DAP-09 里类型分支是明写的: 第 3 步"仅 PMA 项目"、
-- 第 4 步"STC 要点核对"; 第三类来自 UG-DAP-07 第 12 步 ——
-- **本单位不对委托方的产品作设计批准, 只提供经核查的符合性验证资料**（AP-21-18 7.1(5)）。
--
-- 所以这里不写死状态机: 33 个步骤连同"适用于哪几类项目"一起作配置, 由
-- das_project_step.applies_to 决定。把分支写进代码的后果是第三类迟早会沿着
-- STC 的分支走到"签符合性声明", 而那一签就是对委托方产品作了设计批准 ——
-- 这是越权, 不是流程瑕疵。
--
-- **适用性只收紧原文明确收紧的那几步。** 原文没有限定类型的步骤一律三类通用 ——
-- 凭印象替原文加限制, 会把本该做的步骤判成"不适用", 而"不适用"是不会有人去补的。
-- 两处属推断, 已在 note 里写明理由（DAP-07 第 10 步、DAP-09 第 1 步）。
--
-- 【UG-DAP-09 第 2 步不属于任何产品项目】
-- 它是"首次申请 DOA"本身 —— 申请设计机构许可证, 一次性的组织活动, 延续归 M10
-- （UG-DAP-15 与局方联络、报备和证件维持）。applies_to 为空数组, 由
-- das_project_step_unassigned 列出来并写明归属。把它塞进产品项目的话,
-- 每个项目都要"首次申请 DOA"一次, 这个步骤就永远完不成。
--
-- 【一处源文件排版缺陷】
-- UG-DAP-09 第 6 章的表格里, 第 3 步（PMA 质量系统确认）排在第 9 步**之后**。
-- 序号是对的, 顺序是乱的。按序号取, 不按表格行序取。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 项目类型（配置）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_project_type (
    code         text PRIMARY KEY,          -- STC / PMA / SUP
    name_cn      text NOT NULL,
    -- UG-DAW-002 第 5 章: 项目编号 = 前缀 + 两位序号 + 版本字母 + 两位年份
    code_prefix  text NOT NULL,             -- UG-STC / UG-PMA / UG-SUP
    -- 本类项目是否产出设计批准。第三类为 false —— 判据 M3-1 的根。
    yields_design_approval boolean NOT NULL,
    -- 本类项目是否签符合性声明（表-21-160 / UG-DAF-03）。
    signs_compliance_statement boolean NOT NULL,
    path_note    text NOT NULL,             -- 路径要点（设计输入第 8.2 节）
    source_doc   text NOT NULL,
    note         text,
    CONSTRAINT ck_dpt_prefix CHECK (code_prefix ~ '^UG-[A-Z]{3}$'),
    -- 不签符合性声明却产出设计批准, 或反之, 都说明类型定义本身错了。
    -- 两者在本体系里是同一件事的两面: 设计批准的前提是符合性声明（UG-DAP-09 第 8 步
    -- "汇总符合性文件、声明"）。
    CONSTRAINT ck_dpt_consistent CHECK
        (yields_design_approval = signs_compliance_statement)
);

COMMENT ON TABLE das_project_type IS
    '三条审定路径（设计输入第 8.2 节）。yields_design_approval=false 的第三类不得复用前两类的「出符合性声明」分支——判据 M3-1。';

-- ---------------------------------------------------------------------
-- 2. 步骤目录（33 步，按类型适用）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_project_step (
    code         text PRIMARY KEY,          -- DAP09.1 / DAP04.3 / DAP07.11
    procedure_ref text NOT NULL,            -- UG-DAP-04 / 07 / 09
    step_no      int  NOT NULL,
    name_cn      text NOT NULL,
    owner_position text NOT NULL,           -- 责任人（原文"责任人"栏）
    output_record text,                     -- 输入→输出/记录（原文）
    -- 适用于哪几类项目。空数组 = 不属于任何产品项目（见文件头关于第 2 步的说明）。
    applies_to   text[] NOT NULL,
    -- 本步骤完成的前置步骤（同一项目内）。UG-DAP-09 第 3 步 → 第 5 步那条是原文:
    -- "未确认生产质量系统已具备的, 不得提交 PMA 申请"。
    requires_step text REFERENCES das_project_step (code),
    -- 本步骤的实质证据存在哪张表。
    -- 【为什么要有这一列】
    -- "步骤已登记完成"和"该步骤的实质证据存在"是两件事。PMA 的第 3 步要确认四件事,
    -- 这四件事落在 das_pma_quality_confirm 里; 只在 das_project_step_done 里记一行
    -- "第 3 步完成了", 四件事一件都没有。而第 3 步是第 5 步（提交 PMA 申请）的闸门 ——
    -- 原文: 未确认生产质量系统已具备的, 不得提交 PMA 申请。
    -- 闸门一半在配置、一半在代码里写死步骤编号, 下一个有实质证据表的步骤就会漏掉,
    -- 所以把"证据在哪"也作配置。
    evidence_table text,
    is_inferred  boolean NOT NULL DEFAULT false,  -- 适用性属推断而非原文明定
    note         text,
    CONSTRAINT uq_dps_proc_no UNIQUE (procedure_ref, step_no),
    CONSTRAINT ck_dps_applies CHECK (
        applies_to <@ ARRAY['STC', 'PMA', 'SUP']),
    -- 推断来的适用性必须写明理由。不写就没人知道哪几条是原文、哪几条是我们判的,
    -- 而符合性自评里这两者的分量完全不同。
    CONSTRAINT ck_dps_inferred CHECK (
        NOT is_inferred OR length(btrim(coalesce(note, ''))) > 0),
    -- 表名会进 format(%I), 这里再收一道: 只允许 das_ 前缀的小写标识符。
    CONSTRAINT ck_dps_evidence CHECK (
        evidence_table IS NULL OR evidence_table ~ '^das_[a-z0-9_]{1,54}$')
);

COMMENT ON TABLE das_project_step IS
    'UG-DAP-04（11 步）＋UG-DAP-07（13 步）＋UG-DAP-09（9 步）＝33 步，连同适用于哪几类项目一起作配置（判据 M3-1：按类型参数化，不写死状态机）。';

-- ---------------------------------------------------------------------
-- 3. 项目
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_project (
    id           bigserial PRIMARY KEY,
    -- UG-DAW-002 第 5 章: 由资料管理负责人在立项时分配并登记。
    project_no   text NOT NULL UNIQUE,
    type_code    text NOT NULL REFERENCES das_project_type (code),
    name_cn      text NOT NULL,
    -- 局方受理编号**另行记录, 不替代本单位项目编号**（UG-DAW-002 第 5 章原文）,
    -- 例如 UG-PMA08A26 对应局方项目号 NAPMA-2025-125-XN, 二者在台账中并列登记。
    caac_project_no text,
    aircraft_type text,                     -- 航空器型号
    product_desc text,                      -- 产品/拟更改内容
    -- 【这里刻意没有"产品范围"的结构化字段】
    -- 判据 I10.PRODUCT_SCOPE 要的"具体产品／型号／件号范围"须由项目实体与件号的
    -- 结构化关联来表达; 自由文本 product_scope 已于提交 1f39b89 从授权表移除,
    -- 理由见设计输入第九之二节: 一个不被任何校验读取的字段比没有这个字段更糟。
    -- 本迁移先立项目实体, **件号关联属下一片**, 所以独立性登记册里
    -- I10.PRODUCT_SCOPE 仍如实记为未实现 —— 不因为建了项目表就改成"已实现"。
    certification_basis text,               -- 现行审定基础
    -- UG-DAP-09 第 1 步: 责任经理批准立项。未批准不得登记后续步骤（DCMS-INV-099）。
    initiated_on date NOT NULL DEFAULT current_date,
    approved_by  uuid REFERENCES app_user (id),
    approved_on  date,
    approval_ref text,
    closed_on    date,                      -- 结项（转段三项齐备, DCMS-INV-097）
    created_by   uuid REFERENCES app_user (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dp_approved CHECK (
        (approved_by IS NULL) = (approved_on IS NULL)),
    CONSTRAINT ck_dp_closed CHECK (closed_on IS NULL OR approved_on IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_das_project_type ON das_project (type_code, closed_on);

COMMENT ON COLUMN das_project.caac_project_no IS
    '局方受理编号。另行记录，不替代本单位项目编号（UG-DAW-002 第 5 章），二者在台账中并列登记。';

-- ---------------------------------------------------------------------
-- 4. 步骤完成记录
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_project_step_done (
    id          bigserial PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES das_project (id),
    step_code   text NOT NULL REFERENCES das_project_step (code),
    completed_on date NOT NULL,
    record_ref  text NOT NULL,              -- 对应记录/表单编号
    note        text,
    recorded_by uuid REFERENCES app_user (id),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dpsd UNIQUE (project_id, step_code),
    CONSTRAINT ck_dpsd_ref CHECK (length(btrim(record_ref)) > 0)
);

-- ---------------------------------------------------------------------
-- 5. PMA 生产质量系统确认（UG-DAP-09 第 3 步）
-- ---------------------------------------------------------------------
-- 单独一张表而不是一条布尔: 原文要确认四件事, 布尔存不下, 而"确认过了"这句话
-- 在局方面前要拿出质量手册版次与受控状态证明。
-- 该质量系统**不属于本设计保证系统的范围**（CCAR-21.307 由生产单位另行建立），
-- 本表记的是本单位对它的确认, 不是对它的管理。
CREATE TABLE IF NOT EXISTS das_pma_quality_confirm (
    project_id  bigint PRIMARY KEY REFERENCES das_project (id),
    manual_ref  text NOT NULL,              -- 质量手册编号与版次
    established boolean NOT NULL,           -- a) 已建立并处于受控状态
    covers_project boolean NOT NULL,        -- b) 覆盖本项目的零部件类别
    materials_submitted boolean NOT NULL,   -- c) 质量手册及相关资料已一并提交
    interface_ref text NOT NULL,            -- d) 设计与生产接口（UG-DAP-10）依据
    confirmed_by uuid NOT NULL REFERENCES app_user (id),
    confirmed_on date NOT NULL,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dpqc_text CHECK (
        length(btrim(manual_ref)) > 0 AND length(btrim(interface_ref)) > 0)
);

COMMENT ON TABLE das_pma_quality_confirm IS
    'UG-DAP-09 第 3 步（仅 PMA）。四项须全部为真才算确认具备；未确认不得提交 PMA 申请（DCMS-INV-093）。该质量系统不在本设计保证系统范围内，本表记的是本单位对它的确认。';

-- ---------------------------------------------------------------------
-- 6. 符合性声明（UG-DAP-07 第 11 步，表-21-160 / UG-DAF-03）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_compliance_statement (
    id          bigserial PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES das_project (id),
    -- UG-DAW-002: 设计机构许可证编号-SM-年份-流水号。
    -- **没有对许可证编号部分做校验**: 本单位的 DOA 尚在申请中, 许可证编号还不存在,
    -- 而按一个不存在的编号去校验, 只会逼人编一个填进去。待取证后另行收紧。
    statement_no text NOT NULL UNIQUE,
    signed_by   uuid NOT NULL REFERENCES app_user (id),
    signed_on   date NOT NULL,
    -- "由责任经理**或其授权人员**"签署。签署人不是在任责任经理时, 须给出纸面
    -- 授权依据 —— 系统此刻**判不了**该授权是否覆盖"符合性声明"这件事:
    -- 授权的第三维（可签署文件类型）校验的是 DCMS 文档种类, 不是 UG-DAM-01-附2 的
    -- 5 类适航签署事项（判据 I10.FILE_TYPE 语义不符, 待澄清项第 1 条）。
    -- 所以这里做成"必须写明依据"＋可见性清单, 交独立监督核对, 不冒充校验。
    signed_under_authority_ref text,
    completion_confirm_ref text NOT NULL,   -- 第 10 步的完成确认书（UG-DAF-17）
    verification_docs_ref text NOT NULL,    -- 所引用验证文件及其版次
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dcs_no CHECK (statement_no ~ '-SM-'),
    CONSTRAINT ck_dcs_text CHECK (
        length(btrim(completion_confirm_ref)) > 0
        AND length(btrim(verification_docs_ref)) > 0)
);

COMMENT ON TABLE das_compliance_statement IS
    'UG-DAP-07 第 11 步的符合性声明（表-21-160／UG-DAF-03）。设计供应商受托项目不得签（DCMS-INV-094，判据 M3-1）。';

-- ---------------------------------------------------------------------
-- 7. 设计批准（设计输入第 8.3 节（一））
-- ---------------------------------------------------------------------
-- 项目做到"转段"为止, 证后活动挂在**证件**上而不是项目上。理由是数据归属:
-- 一个产品可能有多个 STC; ICA 修订、服务通告、适航指令建议、21.99 信息、
-- 权益转让（转的是证件）、撤销召回 —— 都按证件走。
-- 项目有结束时间, 证件长期有效, PMA 项目单还要每 2 年续（归 M10）。
CREATE TABLE IF NOT EXISTS das_design_approval (
    id          bigserial PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES das_project (id),
    approval_kind text NOT NULL,            -- STC / PMA
    certificate_no text NOT NULL UNIQUE,    -- 证件号/项目单号
    issued_on   date NOT NULL,
    -- PMA 项目单每 2 年延续（设计输入第 8.3 节、AP-21-18）。STC 长期有效,
    -- 故可为空; 但 PMA 为空就等于把一个两年后失效的东西记成长期有效。
    renew_due_on date,
    product_scope_ref text NOT NULL,        -- 批准覆盖的产品/型号/件号（纸面依据）
    statement_id bigint REFERENCES das_compliance_statement (id),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dda_kind CHECK (approval_kind IN ('STC', 'PMA')),
    -- PMA 必须有延续日期。写成 (kind='PMA' AND renew IS NOT NULL) OR (...) 不行:
    -- 任一侧为 NULL 时整式为 NULL, 而 CHECK 在 NULL 时**放行**。用 CASE。
    CONSTRAINT ck_dda_renew CHECK (
        CASE WHEN approval_kind = 'PMA'
             THEN renew_due_on IS NOT NULL AND renew_due_on > issued_on
             ELSE true
        END),
    CONSTRAINT ck_dda_scope CHECK (length(btrim(product_scope_ref)) > 0)
);

COMMENT ON TABLE das_design_approval IS
    '设计批准对象（设计输入第 8.3 节）。证后活动（M8）挂在这里而不是项目上：项目有结束时间，证件长期有效。设计供应商受托项目不得产生（DCMS-INV-095）。';

-- ---------------------------------------------------------------------
-- 8. 转段清单（UG-DAP-09 第 9 步）
-- ---------------------------------------------------------------------
-- 三项: 移交持续适航（UG-DAP-11）、生产协调（UG-DAP-10）、归档（UG-DAP-01）。
-- 做成三行而不是三个布尔列, 因为每一项都要有自己的移交记录与接收人 ——
-- 三个布尔勾完, 没有一项说得出移交给了谁。
CREATE TABLE IF NOT EXISTS das_project_handover (
    id          bigserial PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES das_project (id),
    item_code   text NOT NULL,              -- AIRWORTHINESS / PRODUCTION / ARCHIVE
    target_ref  text NOT NULL,              -- 移交依据程序与记录编号
    received_by uuid NOT NULL REFERENCES app_user (id),
    handed_on   date NOT NULL,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dph UNIQUE (project_id, item_code),
    CONSTRAINT ck_dph_item CHECK
        (item_code IN ('AIRWORTHINESS', 'PRODUCTION', 'ARCHIVE')),
    CONSTRAINT ck_dph_ref CHECK (length(btrim(target_ref)) > 0)
);

-- ---------------------------------------------------------------------
-- 9. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-091: 项目编号前缀须与类型一致（UG-DAW-002 第 5 章）。
-- DCMS-INV-099: 立项未经责任经理批准, 不得登记后续步骤（UG-DAP-09 第 1 步）。
CREATE OR REPLACE FUNCTION dcms_check_das_project() RETURNS trigger AS $$
DECLARE
    v_prefix text;
    v_name   text;
BEGIN
    SELECT code_prefix, name_cn INTO v_prefix, v_name
      FROM das_project_type WHERE code = NEW.type_code;
    IF NEW.project_no NOT LIKE v_prefix || '%' THEN
        RAISE EXCEPTION 'DCMS-INV-091: % 项目的编号须以 % 开头（UG-DAW-002 第 5 章: % + 两位序号 + 版本字母 + 两位年份, 如 %07A25）, 实际为 %。编号前缀是类型的一部分, 前缀对不上的项目在台账里会被归错类。',
                        v_name, v_prefix, v_prefix, v_prefix, NEW.project_no;
    END IF;
    IF NEW.project_no !~ ('^' || v_prefix || '[0-9]{2}[A-Z][0-9]{2}$') THEN
        RAISE EXCEPTION 'DCMS-INV-091: 项目编号 % 不合 UG-DAW-002 第 5 章的格式（% + 两位序号 + 版本字母 + 两位年份, 如 %07A25）。',
                        NEW.project_no, v_prefix, v_prefix;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_project_check ON das_project;
CREATE TRIGGER trg_das_project_check
    BEFORE INSERT OR UPDATE ON das_project
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_project();

-- DCMS-INV-092: 不适用于本项目类型的步骤不得登记完成（判据 M3-1 的落点）。
-- DCMS-INV-093: PMA 未确认生产质量系统不得登记"申请"（UG-DAP-09 第 3 步原文）。
-- DCMS-INV-099: 立项未批准不得登记后续步骤。
CREATE OR REPLACE FUNCTION dcms_check_das_project_step_done() RETURNS trigger AS $$
DECLARE
    v_type    text;
    v_type_cn text;
    v_approved date;
    v_applies text[];
    v_name    text;
    v_requires text;
    v_req_name text;
    v_initiation text;
    v_evidence text;
    v_has     boolean;
BEGIN
    SELECT p.type_code, t.name_cn, p.approved_on
      INTO v_type, v_type_cn, v_approved
      FROM das_project p JOIN das_project_type t ON t.code = p.type_code
     WHERE p.id = NEW.project_id;
    SELECT applies_to, name_cn, requires_step, evidence_table
      INTO v_applies, v_name, v_requires, v_evidence
      FROM das_project_step WHERE code = NEW.step_code;

    IF cardinality(v_applies) = 0 THEN
        RAISE EXCEPTION 'DCMS-INV-092: 步骤「%」(%) 不属于任何产品项目 —— 它是首次申请设计机构许可证本身, 延续与维持归 M10（UG-DAP-15）。登记在产品项目下, 这个步骤对每个项目都完不成。',
                        v_name, NEW.step_code;
    END IF;
    IF NOT (v_type = ANY (v_applies)) THEN
        RAISE EXCEPTION 'DCMS-INV-092: 步骤「%」(%) 不适用于%（适用: %）。判据 M3-1: 状态机按项目类型参数化, 第三类不得复用前两类的分支 —— 沿着别的类型的分支走下去, 迟早会走到"签符合性声明", 而那一签就是对委托方的产品作了设计批准。',
                        v_name, NEW.step_code, v_type_cn, array_to_string(v_applies, '、');
    END IF;

    -- 立项这一步本身不受"立项已批准"约束, 否则第一步永远登不上。
    SELECT code INTO v_initiation FROM das_project_step
     WHERE procedure_ref = 'UG-DAP-09' AND step_no = 1;
    IF NEW.step_code <> v_initiation AND v_approved IS NULL THEN
        RAISE EXCEPTION 'DCMS-INV-099: 项目尚未经责任经理批准立项, 不得登记步骤「%」。UG-DAP-09 第 1 步: 评估资源, 责任经理批准立项。',
                        v_name;
    END IF;

    -- 本步骤自己的实质证据。登记"完成"而证据表里没有对应行, 等于只记了个勾。
    IF v_evidence IS NOT NULL THEN
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE project_id = $1)',
                       v_evidence)
           INTO v_has USING NEW.project_id;
        IF NOT v_has THEN
            RAISE EXCEPTION 'DCMS-INV-093: 步骤「%」的实质记录（%）还没有建立, 不得登记完成。只在步骤台账上记一行"已完成", 该步骤要确认的事情一件都没有。',
                            v_name, v_evidence;
        END IF;
    END IF;

    -- 前置步骤。这里查的不只是前置步骤登记过, 还要查它自己的实质证据 ——
    -- 否则"第 3 步已登记完成"但确认记录不存在时, 第 5 步照样放行,
    -- 而原文拦的正是这一条: 未确认生产质量系统已具备的, 不得提交 PMA 申请。
    IF v_requires IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM das_project_step_done d
                        WHERE d.project_id = NEW.project_id
                          AND d.step_code = v_requires) THEN
            SELECT name_cn INTO v_req_name FROM das_project_step WHERE code = v_requires;
            RAISE EXCEPTION 'DCMS-INV-093: 步骤「%」的前置步骤「%」尚未完成。UG-DAP-09 第 3 步: 未确认生产质量系统已具备的, 不得提交 PMA 申请。',
                            v_name, v_req_name;
        END IF;
        SELECT evidence_table, name_cn INTO v_evidence, v_req_name
          FROM das_project_step WHERE code = v_requires;
        IF v_evidence IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE project_id = $1)',
                           v_evidence)
               INTO v_has USING NEW.project_id;
            IF NOT v_has THEN
                RAISE EXCEPTION 'DCMS-INV-093: 前置步骤「%」登记为完成, 但它的实质记录（%）不存在。UG-DAP-09 第 3 步: 未确认生产质量系统已具备的, 不得提交 PMA 申请 —— 拦的是"没确认", 不是"没登记"。',
                                v_req_name, v_evidence;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_project_step_done_check ON das_project_step_done;
CREATE TRIGGER trg_das_project_step_done_check
    BEFORE INSERT OR UPDATE ON das_project_step_done
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_project_step_done();

-- DCMS-INV-093（另一侧）: 四项未全为真不算确认具备。
CREATE OR REPLACE FUNCTION dcms_check_das_pma_quality() RETURNS trigger AS $$
DECLARE
    v_type text;
BEGIN
    SELECT type_code INTO v_type FROM das_project WHERE id = NEW.project_id;
    IF v_type <> 'PMA' THEN
        RAISE EXCEPTION 'DCMS-INV-093: 生产质量系统确认只适用于 PMA 项目（UG-DAP-09 第 3 步: 仅 PMA 项目）, 本项目类型为 %。', v_type;
    END IF;
    IF NOT (NEW.established AND NEW.covers_project AND NEW.materials_submitted) THEN
        RAISE EXCEPTION 'DCMS-INV-093: a) 已建立并受控、b) 覆盖本项目零部件类别、c) 资料已一并提交, 三项须全部为真才算确认具备（UG-DAP-09 第 3 步）。有一项为否就登记成确认记录, 等于把"不具备"写成了"已确认" —— 而下一步就是提交 PMA 申请。';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_pma_quality_check ON das_pma_quality_confirm;
CREATE TRIGGER trg_das_pma_quality_check
    BEFORE INSERT OR UPDATE ON das_pma_quality_confirm
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_pma_quality();

-- DCMS-INV-094: 不签符合性声明的项目类型不得签（判据 M3-1）。
CREATE OR REPLACE FUNCTION dcms_check_das_compliance_statement() RETURNS trigger AS $$
DECLARE
    v_signs boolean;
    v_type_cn text;
    v_holds_am boolean;
BEGIN
    SELECT t.signs_compliance_statement, t.name_cn INTO v_signs, v_type_cn
      FROM das_project p JOIN das_project_type t ON t.code = p.type_code
     WHERE p.id = NEW.project_id;
    IF NOT v_signs THEN
        RAISE EXCEPTION 'DCMS-INV-094: %不得签符合性声明。UG-DAP-07 第 12 步 d): 本单位不对委托方的产品作设计批准, 只提供经核查的符合性验证资料（AP-21-18 7.1(5)）。签了就是越权作了设计批准, 不是流程瑕疵。',
                        v_type_cn;
    END IF;
    -- "由责任经理或其授权人员"签署: 不是在任责任经理, 就必须写明授权依据。
    -- 系统判不了该授权是否覆盖"符合性声明"这件事（判据 I10.FILE_TYPE 语义不符）,
    -- 所以要的是"说得出依据", 并由 das_compliance_statement_delegated 交独立监督核对。
    v_holds_am := EXISTS (SELECT 1 FROM das_appointment_in_force
                           WHERE user_id = NEW.signed_by AND position_code = 'AM');
    IF NOT v_holds_am
       AND length(btrim(coalesce(NEW.signed_under_authority_ref, ''))) = 0 THEN
        RAISE EXCEPTION 'DCMS-INV-094: 签署人不是在任责任经理时, 须写明授权依据（UG-DAP-07 第 11 步: 由责任经理**或其授权人员**签署）。系统此刻判不了该授权是否覆盖符合性声明这件事 —— 授权的第三维校验的是文档种类, 不是 UG-DAM-01-附2 的 5 类适航签署事项（待澄清项第 1 条）。所以这里要的是说得出依据, 由独立监督核对。';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_compliance_statement_check ON das_compliance_statement;
CREATE TRIGGER trg_das_compliance_statement_check
    BEFORE INSERT OR UPDATE ON das_compliance_statement
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_compliance_statement();

-- DCMS-INV-095: 不产出设计批准的类型不得有设计批准（判据 M3-1）。
-- DCMS-INV-096: 没有符合性声明不得登记设计批准（UG-DAP-09 第 8 步: 汇总符合性文件、声明）。
CREATE OR REPLACE FUNCTION dcms_check_das_design_approval() RETURNS trigger AS $$
DECLARE
    v_yields boolean;
    v_type   text;
    v_type_cn text;
    v_stmt   bigint;
BEGIN
    SELECT t.yields_design_approval, p.type_code, t.name_cn
      INTO v_yields, v_type, v_type_cn
      FROM das_project p JOIN das_project_type t ON t.code = p.type_code
     WHERE p.id = NEW.project_id;
    IF NOT v_yields THEN
        RAISE EXCEPTION 'DCMS-INV-095: %不产出设计批准。UG-DAP-07 第 12 步 d): 本单位不对委托方的产品作设计批准。证件属于委托方, 不属于本单位。',
                        v_type_cn;
    END IF;
    IF NEW.approval_kind <> v_type THEN
        RAISE EXCEPTION 'DCMS-INV-095: 设计批准类别(%) 与项目类型(%) 不一致。',
                        NEW.approval_kind, v_type;
    END IF;
    SELECT id INTO v_stmt FROM das_compliance_statement
     WHERE project_id = NEW.project_id ORDER BY signed_on DESC LIMIT 1;
    IF v_stmt IS NULL THEN
        RAISE EXCEPTION 'DCMS-INV-096: 本项目还没有已签署的符合性声明, 不得登记设计批准。UG-DAP-09 第 8 步: 取证准备与提交要汇总符合性文件、声明、构型状态。没有声明的设计批准, 取证顺序是倒的。';
    END IF;
    IF NEW.statement_id IS NULL THEN
        NEW.statement_id := v_stmt;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_design_approval_check ON das_design_approval;
CREATE TRIGGER trg_das_design_approval_check
    BEFORE INSERT OR UPDATE ON das_design_approval
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_design_approval();

-- DCMS-INV-097: 转段三项未齐不得结项（UG-DAP-09 第 9 步）。
CREATE OR REPLACE FUNCTION dcms_check_das_project_close() RETURNS trigger AS $$
DECLARE
    v_missing text;
    v_type    text;
BEGIN
    IF NEW.closed_on IS NULL OR OLD.closed_on IS NOT NULL THEN
        RETURN NEW;
    END IF;
    SELECT type_code INTO v_type FROM das_project WHERE id = NEW.id;
    SELECT string_agg(x.cn, '、') INTO v_missing
      FROM (VALUES ('AIRWORTHINESS', '移交持续适航（UG-DAP-11）'),
                   ('PRODUCTION',    '生产协调（UG-DAP-10）'),
                   ('ARCHIVE',       '归档（UG-DAP-01）')) AS x(code, cn)
     WHERE NOT EXISTS (SELECT 1 FROM das_project_handover h
                        WHERE h.project_id = NEW.id AND h.item_code = x.code);
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-097: 转段清单还差: %。UG-DAP-09 第 9 步"批准后转段"要移交持续适航、生产协调、归档三项 —— 项目做到转段为止, 没移交就结项等于证后活动没有承接人。',
                        v_missing;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_project_close_check ON das_project;
CREATE TRIGGER trg_das_project_close_check
    BEFORE UPDATE ON das_project
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_project_close();

-- DCMS-INV-098: 步骤完成记录、符合性声明、设计批准、转段记录均不得删除或改写。
-- 它们是取证材料。符合性声明更是签了名的东西 —— 能改写的签署不是签署。
CREATE OR REPLACE FUNCTION dcms_guard_das_project_append() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-INV-098: % 不得%。这些是取证材料, 更正请另记一条并写明理由; 符合性声明签了名, 能改写的签署不是签署。',
                    TG_TABLE_NAME,
                    CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_step_done_append ON das_project_step_done;
CREATE TRIGGER trg_das_step_done_append
    BEFORE UPDATE OR DELETE ON das_project_step_done
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_project_append();

DROP TRIGGER IF EXISTS trg_das_statement_append ON das_compliance_statement;
CREATE TRIGGER trg_das_statement_append
    BEFORE UPDATE OR DELETE ON das_compliance_statement
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_project_append();

DROP TRIGGER IF EXISTS trg_das_approval_append ON das_design_approval;
CREATE TRIGGER trg_das_approval_append
    BEFORE DELETE ON das_design_approval
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_project_append();

DROP TRIGGER IF EXISTS trg_das_handover_append ON das_project_handover;
CREATE TRIGGER trg_das_handover_append
    BEFORE UPDATE OR DELETE ON das_project_handover
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_project_append();

-- ---------------------------------------------------------------------
-- 10. 视图
-- ---------------------------------------------------------------------

-- 10.1 项目进展: 每个项目 × 其类型适用的步骤。
CREATE OR REPLACE VIEW das_project_progress AS
SELECT p.id AS project_id, p.project_no, p.type_code, p.name_cn AS project_name,
       s.procedure_ref, s.step_no, s.code AS step_code, s.name_cn AS step_name,
       s.owner_position, s.requires_step,
       d.completed_on, d.record_ref,
       (d.id IS NOT NULL) AS done
  FROM das_project p
  JOIN das_project_step s ON p.type_code = ANY (s.applies_to)
  LEFT JOIN das_project_step_done d
         ON d.project_id = p.id AND d.step_code = s.code
 ORDER BY p.project_no, s.procedure_ref, s.step_no;

-- 10.2 各类型的步骤数。判据 M3-1 的可见性: 三类的步骤集**必须不同**,
--      一样就说明参数化没起作用。
CREATE OR REPLACE VIEW das_project_step_coverage AS
SELECT t.code, t.name_cn, t.yields_design_approval,
       count(s.code) AS steps,
       count(*) FILTER (WHERE s.procedure_ref = 'UG-DAP-04') AS dap04_steps,
       count(*) FILTER (WHERE s.procedure_ref = 'UG-DAP-07') AS dap07_steps,
       count(*) FILTER (WHERE s.procedure_ref = 'UG-DAP-09') AS dap09_steps
  FROM das_project_type t
  LEFT JOIN das_project_step s ON t.code = ANY (s.applies_to)
 GROUP BY t.code, t.name_cn, t.yields_design_approval
 ORDER BY t.code;

-- 10.2b 各类型**独有**的步骤。
-- 【为什么光看条数不够】
-- STC 与 PMA 都是 30 步, 条数一样。判据 M3-1 要的不是"条数不同", 是**步骤集不同**:
-- STC 独有第 4 步（要点核对）, PMA 独有第 3 步（质量系统确认）, 两者恰好各一条,
-- 条数就抵平了。拿条数去证明"参数化起作用了", 这两类会显示为完全一样。
CREATE OR REPLACE VIEW das_project_type_exclusive AS
SELECT t.code, t.name_cn,
       (SELECT coalesce(string_agg(s.code || '（' || s.name_cn || '）', '、'
                                   ORDER BY s.code), '')
          FROM das_project_step s
         WHERE t.code = ANY (s.applies_to)
           AND NOT EXISTS (SELECT 1 FROM das_project_type o
                            WHERE o.code <> t.code AND o.code = ANY (s.applies_to)))
                                                                   AS exclusive_steps,
       (SELECT coalesce(string_agg(s.code, '、' ORDER BY s.code), '')
          FROM das_project_step s
         WHERE cardinality(s.applies_to) > 0
           AND NOT (t.code = ANY (s.applies_to)))                  AS excluded_steps
  FROM das_project_type t
 ORDER BY t.code;

COMMENT ON VIEW das_project_type_exclusive IS
    '每类项目独有的步骤与不适用的步骤。判据 M3-1 的直接证据：三类的步骤集必须不同——STC 与 PMA 条数相同(30)，只有按集合比才看得出差别。';

-- 10.3 不属于任何产品项目类型的步骤。
--      平时只应有 UG-DAP-09 第 2 步（首次申请 DOA 本身, 归 M10）。
--      它一有别的行, 说明有步骤被漏掉了适用性 —— 而"不适用"是不会有人去补的。
CREATE OR REPLACE VIEW das_project_step_unassigned AS
SELECT code, procedure_ref, step_no, name_cn, note
  FROM das_project_step
 WHERE cardinality(applies_to) = 0
 ORDER BY procedure_ref, step_no;

-- 10.4 适用性属推断而非原文明定的步骤。自评要分得清哪几条是原文、哪几条是我们判的。
CREATE OR REPLACE VIEW das_project_step_inferred AS
SELECT code, procedure_ref, step_no, name_cn,
       array_to_string(applies_to, '、') AS applies_to, note
  FROM das_project_step
 WHERE is_inferred
 ORDER BY procedure_ref, step_no;

-- 10.5 由"授权人员"而非在任责任经理签署的符合性声明。
--      **可见性清单, 不是违规清单。** UG-DAP-07 第 11 步明文允许"或其授权人员",
--      但系统判不了该授权是否覆盖这件事（判据 I10.FILE_TYPE 语义不符,
--      待澄清项第 1 条）。交独立监督按 UG-DAP-13 核对纸面授权书的签署事项范围。
CREATE OR REPLACE VIEW das_compliance_statement_delegated AS
SELECT c.id, c.statement_no, p.project_no, u.full_name, u.employee_no,
       c.signed_on, c.signed_under_authority_ref
  FROM das_compliance_statement c
  JOIN das_project p ON p.id = c.project_id
  JOIN app_user u ON u.id = c.signed_by
 WHERE NOT EXISTS (SELECT 1 FROM das_appointment_in_force a
                    WHERE a.user_id = c.signed_by AND a.position_code = 'AM')
 ORDER BY c.signed_on DESC;

-- 10.6 设计批准的延续到期。PMA 项目单每 2 年续（归 M10）。
CREATE OR REPLACE VIEW das_design_approval_renewal AS
SELECT a.id, a.certificate_no, a.approval_kind, p.project_no,
       a.issued_on, a.renew_due_on,
       (a.renew_due_on - current_date) AS days_left
  FROM das_design_approval a
  JOIN das_project p ON p.id = a.project_id
 WHERE a.renew_due_on IS NOT NULL
 ORDER BY a.renew_due_on;

-- 10.7 可以结项但转段未齐的项目。
CREATE OR REPLACE VIEW das_project_handover_pending AS
SELECT p.id AS project_id, p.project_no, p.name_cn,
       (SELECT count(*) FROM das_project_handover h WHERE h.project_id = p.id)
                                                                   AS handed,
       3 - (SELECT count(*) FROM das_project_handover h WHERE h.project_id = p.id)
                                                                   AS pending
  FROM das_project p
 WHERE p.closed_on IS NULL
   AND EXISTS (SELECT 1 FROM das_design_approval a WHERE a.project_id = p.id)
   AND (SELECT count(*) FROM das_project_handover h WHERE h.project_id = p.id) < 3
 ORDER BY p.project_no;

-- ---------------------------------------------------------------------
-- 11. 三条路径（配置）
-- ---------------------------------------------------------------------
INSERT INTO das_project_type
    (code, name_cn, code_prefix, yields_design_approval,
     signs_compliance_statement, path_note, source_doc, note)
VALUES
('STC', 'STC 项目', 'UG-STC', true, true,
 '立项 → 要点核对（现行审定基础、对适航环保噪声排放的影响、专用条件、'
 '对 ICA/AFM/构型的影响、试验件与大纲、交付资料清单）→ 申请 → 审定计划 → '
 '设计与验证 → 符合性声明（表-21-160）→ 取证 → 转段',
 'UG-DAP-09 第 2～9 步、设计输入第 8.2 节', NULL),
('PMA', 'PMA 项目', 'UG-PMA', true, true,
 '立项 → **生产质量系统确认**（CCAR-21.303／21.137，另建体系，未确认不得提交申请）'
 '→ 申请（图纸规范、尺寸材料工艺、试验计算报告或相似性论证）→ 验证 → 符合性声明 '
 '→ 取证 → 转段（项目单 2 年延续归 M10）',
 'UG-DAP-09 第 3 步、设计输入第 8.2 节',
 'CCAR-21.307 的生产质量系统不在本设计保证系统范围内，由生产单位另行建立并保持'),
('SUP', '设计供应商受托项目', 'UG-SUP', false, false,
 '接洽 → 合同／技术协议评审 → 立项 → 接收委托方设计输入并确认充分性 → 验证 → '
 '**本单位 CVE 独立核查** → 授权人员签署 → 交付委托方。'
 '**不出符合性声明，不对委托方产品作设计批准**',
 'UG-DAP-07 第 12 步、AP-21-18 7.1(5)、设计输入第 8.2 节',
 '判据 M3-1：第三类不得复用前两类的「出符合性声明」分支。工作范围限于本单位设计机构'
 '许可项目单载明的附录 F 专业领域；与委托方手册的附加要求不一致时取较严者并书面确认')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 12. 33 个步骤
-- ---------------------------------------------------------------------
-- 适用性只收紧原文明确收紧的那几步。两处属推断, is_inferred 置真并写明理由。
INSERT INTO das_project_step
    (code, procedure_ref, step_no, name_cn, owner_position, output_record,
     applies_to, is_inferred, note)
VALUES
-- UG-DAP-04 设计控制程序（11 步）: 三类通用 —— 受托项目同样要做设计控制。
('DAP04.1',  'UG-DAP-04',  1, '需求接洽与技术可行性评估', 'PM',
 '客户/市场需求 → 可行性评估记录', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP04.2',  'UG-DAP-04',  2, '合同／技术协议评审', 'PM',
 '合同/技术协议 → 会签记录（技术、适航、质量）', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP04.3',  'UG-DAP-04',  3, '设计输入的识别与确认', 'PM',
 '各方要求 → 书面确认的设计输入清单', ARRAY['STC','PMA','SUP'], false,
 '受托项目在这一步接收委托方设计输入并确认其充分性（设计输入第 8.2 节）'),
('DAP04.4',  'UG-DAP-04',  4, '设计策划', 'PM',
 '设计输入 → 设计计划（分工、接口、评审节点、输出清单）', ARRAY['STC','PMA','SUP'],
 false, NULL),
('DAP04.5',  'UG-DAP-04',  5, '设计输出', 'DE',
 '设计计划 → 图纸、数模及 UG-DAW-008 规定的各类设计输出', ARRAY['STC','PMA','SUP'],
 false, NULL),
('DAP04.6',  'UG-DAP-04',  6, '选材、标准件和外购件选用', 'DE',
 '规范与来源证明 → 选用记录', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP04.7',  'UG-DAP-04',  7, '设计工具和软件的确认', 'DE',
 '首次使用/版本升级 → 确认记录', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP04.8',  'UG-DAP-04',  8, '设计评审（三级：方案、详细设计、符合性）', 'PM',
 '设计输出 → UG-DAW-007 评审记录与意见闭环', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP04.9',  'UG-DAP-04',  9, '设计验证与确认', 'DE',
 '设计输出 → 验证/确认记录', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP04.10', 'UG-DAP-04', 10, '设计定型与发放', 'PM',
 '评审通过、意见闭环、四性核查 → 按 UG-DAP-01 完成签署并发放',
 ARRAY['STC','PMA','SUP'], false, NULL),
('DAP04.11', 'UG-DAP-04', 11, '设计资料的对外交付', 'PM',
 '交付物清单、版次、适用范围 → 交付与接收记录', ARRAY['STC','PMA','SUP'], false,
 '受托项目在这一步交付委托方（UG-DAP-07 第 12 步 e: 保存委托合同、交付清单和接收确认）'),

-- UG-DAP-07 符合性验证和核查程序（13 步）
('DAP07.1',  'UG-DAP-07',  1, '确定适用要求与符合性方法', 'AWM',
 '审定基础 → 适用条款与符合性方法清单', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP07.2',  'UG-DAP-07',  2, '验证计划／试验大纲', 'PM',
 '适用要求 → 大纲（构型、校准、判据、人员、局方保留项目）', ARRAY['STC','PMA','SUP'],
 false, NULL),
('DAP07.3',  'UG-DAP-07',  3, '试验前的制造符合性声明', 'AWM',
 '质量证明/检查记录 → UG-DAF-12', ARRAY['STC','PMA','SUP'], false,
 '所有符合性验证试验（地面和实验室）前均须签署；影响符合性的偏离未关闭不得开展试验'),
('DAP07.4',  'UG-DAP-07',  4, '局方选定项目的制造符合性声明', 'AWM',
 '局方选定项目 → 另行签署的 UG-DAF-12', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP07.5',  'UG-DAP-07',  5, '声明签署后的更改', 'AWM',
 '更改请求 → 重新确认记录', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP07.6',  'UG-DAP-07',  6, '实施验证', 'TE',
 '大纲 → 试验记录、构型核对记录、偏差评估', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP07.7',  'UG-DAP-07',  7, '编制验证文件', 'DE',
 '试验/分析数据 → 验证报告（依据条款、方法、数据、结论、构型状态）',
 ARRAY['STC','PMA','SUP'], false, NULL),
('DAP07.8',  'UG-DAP-07',  8, '独立核查（CVE）', 'AWM',
 '验证文件 → CVE 核查记录', ARRAY['STC','PMA','SUP'], false,
 '受托项目同样要做: UG-DAP-07 第 12 步 c) 验证资料仍须经本单位 CVE 独立核查'),
('DAP07.9',  'UG-DAP-07',  9, '没有不安全特征的确认', 'DE',
 '设计/更改分析 → 结论记录', ARRAY['STC','PMA','SUP'], false, NULL),
('DAP07.10', 'UG-DAP-07', 10, '符合性验证完成确认', 'AWM',
 '核查通过的文件 → UG-DAF-17 完成确认书', ARRAY['STC','PMA'], true,
 '**适用性属推断**: 原文未按类型限定, 但本步骤的产出(完成确认书)是向"责任经理或其'
 '授权签署符合性声明的人员"表明取证所需验证活动已完成, 唯一下游是第 11 步符合性声明。'
 '受托项目不签声明, 故本步骤对其无下游。若日后认为受托项目也应出完成确认书'
 '（例如交付委托方时作为核查完成的证明）, 改 applies_to 即可, 不必动代码'),
('DAP07.11', 'UG-DAP-07', 11, '符合性声明（表-21-160／UG-DAF-03）', 'AM',
 '完成确认书、核查通过的文件 → UG-DAF-03', ARRAY['STC','PMA'], false,
 '**原文明定的类型分支**: UG-DAP-07 第 12 步 d) 本单位不对委托方的产品作设计批准, '
 '只提供经核查的符合性验证资料（AP-21-18 7.1(5)）。编号规则: '
 '设计机构许可证编号-SM-年份-流水号（UG-DAW-002）'),
('DAP07.12', 'UG-DAP-07', 12, '作为设计供应商承接的验证工作', 'PM',
 '委托要求 → 技术协议、经核查的验证资料、交付记录', ARRAY['SUP'], false,
 '**原文明定仅此类**。工作范围限于本单位许可项目单载明的附录 F 专业领域; '
 '与委托方手册的附加要求不一致时取较严者并书面确认'),
('DAP07.13', 'UG-DAP-07', 13, '提交局方与检查', 'AWM',
 'UG-DAF-03、文件 → 局方意见及关闭记录', ARRAY['STC','PMA','SUP'], false,
 '原文明确含受托情形: 本单位作为其他设计机构的设计供应商时, 同样接受局方到本单位'
 '开展的审查。本单位不开展验证试飞'),

-- UG-DAP-09 设计批准取证管理程序（9 步）
('DAP09.1',  'UG-DAP-09',  1, '立项', 'PM',
 '客户需求 → 立项单（责任经理批准）', ARRAY['STC','PMA','SUP'], true,
 '**适用性属推断**: 原文写"批准类型（STC 或 PMA）", 字面只含前两类; 但设计输入'
 '第 8.2 节的第三类路径里明确有"立项"一环, 且 UG-DAW-002 第 5 章给受托项目规定了'
 '项目编号（UG-SUP）—— 有编号就意味着有立项。受托项目的"批准类型"栏不适用'),
('DAP09.2',  'UG-DAP-09',  2, '申请资料的完整性核对（首次申请 DOA）', 'AWM',
 '资料 → 申请包、受理通知书', ARRAY[]::text[], false,
 '**不属于任何产品项目**: 这一步是申请设计机构许可证本身（表-21-164），'
 '一次性的组织活动，延续与维持归 M10（UG-DAP-15）。局方收到后 5 个工作日内完成评审'
 '并一次性书面告知需补正的全部内容。登记在产品项目下的话，每个项目都要"首次申请 DOA"'
 '一次，这个步骤对每个项目都完不成'),
('DAP09.3',  'UG-DAP-09',  3, 'PMA 生产质量系统的确认', 'AWM',
 '质量手册及受控状态证明 → 确认记录', ARRAY['PMA'], false,
 '**原文明定仅 PMA 项目**。CCAR-21.303 要求 PMA 申请人表明具有符合 21.137 的质量系统；'
 '该系统不属于本设计保证系统范围，由本单位另行建立并单独受控。'
 '未确认生产质量系统已具备的，不得提交 PMA 申请'),
('DAP09.4',  'UG-DAP-09',  4, 'STC 要点核对', 'PM',
 '立项单 → STC 要点清单', ARRAY['STC'], false,
 '**原文明定仅 STC**。七项: 拟更改的型号合格证产品及其现行审定基础；对适航、环保、'
 '噪声、排放的影响；适用的补充要求与专用条件；符合性验证计划；对持续适航文件、'
 '飞行手册、构型的影响；试验件、设备、试验大纲；交付资料清单'),
('DAP09.5',  'UG-DAP-09',  5, '申请', 'AWM',
 '申请书 → 局方受理记录', ARRAY['STC','PMA'], false,
 '受托项目不向局方申请设计批准（证件属于委托方）'),
('DAP09.6',  'UG-DAP-09',  6, '审定计划', 'PM',
 '审定计划 → 局方确认', ARRAY['STC','PMA'], false, NULL),
('DAP09.7',  'UG-DAP-09',  7, '设计与验证', 'PM',
 '各类资料 → 局方意见闭环记录', ARRAY['STC','PMA'], false,
 '设计更改按 UG-DAP-06 分类（M4 被 M3 调用，见设计输入第 8.3 节（二））；'
 '验证按 UG-DAP-07；重大更改资料提交局方审查'),
('DAP09.8',  'UG-DAP-09',  8, '取证准备与提交', 'AWM',
 '资料包 → 取证申请', ARRAY['STC','PMA'], false,
 '汇总符合性文件、声明、构型状态、持续适航文件初稿、飞行手册补充'),
('DAP09.9',  'UG-DAP-09',  9, '批准后转段', 'PM',
 '证件 → 转段清单（三项）', ARRAY['STC','PMA'], false,
 '移交持续适航（UG-DAP-11）、生产协调（UG-DAP-10）、归档（UG-DAP-01）。'
 'PMA 涉及的生产制造和质量体系不在本设计保证系统内')
ON CONFLICT (code) DO NOTHING;

-- 前置依赖: PMA 的"申请"要在"生产质量系统确认"之后（UG-DAP-09 第 3 步原文）。
-- 单独 UPDATE 而不在 INSERT 里写, 是因为被引用的行要先存在。
UPDATE das_project_step SET requires_step = 'DAP09.3' WHERE code = 'DAP09.5';
-- 第 3 步的实质证据在 das_pma_quality_confirm（四项确认）。
UPDATE das_project_step SET evidence_table = 'das_pma_quality_confirm'
 WHERE code = 'DAP09.3';
-- 第 11 步的实质证据是签署的符合性声明本身。
UPDATE das_project_step SET evidence_table = 'das_compliance_statement'
 WHERE code = 'DAP07.11';
