-- 独立监督与内部审核（M2）。
--
-- 依据 UG-DAP-13《独立监督和内部审核程序》第 6 章步骤 1～10、第 7 章关键控制点、
-- UG-DAW-013《审核检查表》，AC-21-48 3.6(6)、CCAR-21.137（十四）、AP-21-18 附录 D，
-- UG-RPT-2026-003 J 版判据 L4、L5、L10、M2-1、M2-2、I1、I6、I7。
--
-- 【判据 M2-1: 这是两类活动, 不是一件事】
-- UG-DAP-13 步骤 1 分得很清楚:
--   a) 设计保证系统独立监督 —— 周期 ≤12 个月, 须覆盖 **DOA 符合性检查单的全部适用项**;
--   b) 质量系统内部审核 —— 间隔 ≤12 个月, 覆盖质量系统的所有过程、活动和部门,
--      并明确覆盖 CCAR-21 的适用要求(不得仅按 ISO9001/AS9100 实施)。
-- 两者覆盖的**对象不同**: 一个是检查单的 66 条适用项, 一个是部门与过程。
-- 两类计划可以合并成一份文件, 但周期和覆盖范围须分别标明 —— 所以这里每份计划
-- 只能是其中一类, 合并编制表现为两行共用同一个 document_ref。做成一个"内审"
-- 会把这个区分做丢, 而这正是局方会查的。
--
-- 【判据 L10: 24 个月不是本单位的周期】
-- AP-21-18 4.2 的 24 个月是**局方**对本单位开展计划性监督的周期。它与这里的 12 个月
-- 一旦混用, 内部监督就会晚一年。所以计划周期的上限直接钉在 12 个月: 24 个月**填不进去**,
-- 不是靠提醒, 是约束(DCMS-INV-054)。
--
-- 【判据 M2-2: 独立权限域, admin 也不例外】
-- 本模块的写入权限不挂在账号角色上。挂在角色上, 拥有该角色的人(包括 admin)就能写,
-- 而 I6／I7 要求被监督对象无写权限。所以写入授权来自**岗位任命**:
--   · 排计划、实施审核、出报告 —— 须在任的独立监督负责人(ISM);
--   · 批准计划 —— 须在任的责任经理(AM);
--   · 对独立监督职能自身的审核 —— 由 AM 组织, 实施人**不得**持 ISM 任命。
-- 这一层在服务层判(要查 das_appointment_in_force), 数据库这一层负责挡住
-- 绕过服务层也不该成立的那几条。
--
-- 【步骤 10: 独立监督职能不能自我审核】
-- 这是整份程序里最容易被做成形式的一条。第 7 章原文: "由该职能自行出具的自查结论
-- 不作为符合性证据"。所以对该职能的审核单独立表, 且:
--   · 实施人不得持 ISM 任命(DCMS-INV-060);
--   · 不是外部审核员时, 须有责任经理的指定依据, 且由责任经理**亲自主持**;
--   · 结果直接向责任经理报告。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 审核员资格（步骤 4）
-- ---------------------------------------------------------------------
-- 资格三项出自步骤 4: 审核方法、CCAR-21、本单位体系文件。三项分列而不是一个
-- "已培训"布尔值 —— 只会 ISO 审核方法而没学过 CCAR-21 的人, 审出来的结论
-- 正是第 7 章说的"审核依据不全"。
CREATE TABLE IF NOT EXISTS das_auditor (
    id            bigserial PRIMARY KEY,
    user_id       uuid REFERENCES app_user (id),
    external_name text,                  -- 外单位审核员无系统账号
    external_org  text,
    method_training_ref   text NOT NULL, -- 审核方法培训证据
    ccar21_training_ref   text NOT NULL, -- CCAR-21 培训证据
    manual_training_ref   text NOT NULL, -- 本单位体系文件培训证据
    external_evidence_ref text,          -- 外单位审核员的资格证明(须保存)
    confirmed_by  uuid NOT NULL REFERENCES app_user (id),   -- 适航管理负责人确认
    confirmed_at  timestamptz NOT NULL DEFAULT now(),
    valid_from    date NOT NULL DEFAULT current_date,
    valid_to      date,
    revoked_at    timestamptz,
    revoke_reason text,
    CONSTRAINT ck_das_auditor_who CHECK (
        (user_id IS NOT NULL AND external_name IS NULL)
        OR (user_id IS NULL AND external_name IS NOT NULL AND external_org IS NOT NULL)),
    -- 外单位审核员的资格证明须保存(步骤 4 末句), 没有就不成立。
    CONSTRAINT ck_das_auditor_ext CHECK (
        user_id IS NOT NULL OR length(btrim(coalesce(external_evidence_ref, ''))) > 0),
    CONSTRAINT ck_das_auditor_training CHECK (
        length(btrim(method_training_ref)) > 0 AND length(btrim(ccar21_training_ref)) > 0
        AND length(btrim(manual_training_ref)) > 0),
    CONSTRAINT ck_das_auditor_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_das_auditor_active ON das_auditor (user_id)
    WHERE user_id IS NOT NULL AND revoked_at IS NULL;

COMMENT ON TABLE das_auditor IS
    'UG-DAP-13 步骤 4：审核员资格。三项培训证据分列——只会审核方法而未学 CCAR-21 的人，审出来的结论正是第 7 章所说"审核依据不全"。资格由适航管理负责人确认。';

-- ---------------------------------------------------------------------
-- 2. 计划（UG-DAF-05A，步骤 1～3）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_audit_plan (
    id            bigserial PRIMARY KEY,
    kind          text NOT NULL,         -- DAS_SUPERVISION / QMS_AUDIT
    document_ref  text,                  -- 两类合并编制为一份文件时, 两行共用
    period_from   date NOT NULL,
    period_to     date NOT NULL,
    scope_note    text NOT NULL,         -- 本类的覆盖范围, 须分别标明
    prepared_by   uuid NOT NULL REFERENCES app_user (id),
    prepared_at   timestamptz NOT NULL DEFAULT now(),
    approved_by   uuid REFERENCES app_user (id),      -- 责任经理
    approved_at   timestamptz,
    supersedes    bigint REFERENCES das_audit_plan (id),
    change_reason text,                  -- 计划变更须说明原因并重新批准
    CONSTRAINT ck_das_plan_kind CHECK (kind IN ('DAS_SUPERVISION', 'QMS_AUDIT')),
    CONSTRAINT ck_das_plan_scope CHECK (length(btrim(scope_note)) > 0),
    CONSTRAINT ck_das_plan_order CHECK (period_to > period_from),
    -- 判据 L4／L5: 周期只能更短不能更长, 系统不得允许配置 >12 个月。
    -- AP-21-18 的 24 个月是局方的监督周期(判据 L10), 在这里填不进去。
    -- 同时另有触发器 DCMS-INV-054 说明理由 —— CHECK 的报错只会说"违反约束 xxx",
    -- 而填错的人需要知道 24 个月那个数字是局方的周期, 不是这里的。
    CONSTRAINT ck_das_plan_cycle CHECK (period_to <= period_from + interval '12 months'),
    -- 变更计划须说明原因; 新建的不需要。
    CONSTRAINT ck_das_plan_change CHECK (
        supersedes IS NULL OR length(btrim(coalesce(change_reason, ''))) > 0),
    CONSTRAINT ck_das_plan_approved CHECK (
        (approved_by IS NULL AND approved_at IS NULL)
        OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_das_plan_kind ON das_audit_plan (kind, period_from);

COMMENT ON TABLE das_audit_plan IS
    'UG-DAF-05A。一份计划只能是一类活动（判据 M2-1）；两类合并编制时共用 document_ref，周期与覆盖范围仍分别标明。周期上限 12 个月由约束保证（判据 L4/L5），局方的 24 个月周期填不进来（判据 L10）。';

CREATE TABLE IF NOT EXISTS das_audit_plan_item (
    id           bigserial PRIMARY KEY,
    plan_id      bigint NOT NULL REFERENCES das_audit_plan (id),
    seq          int    NOT NULL,
    scope_kind   text   NOT NULL,   -- DEPARTMENT 部门 / PROCESS 过程 / CHECKLIST 检查单条目 / FUNCTION 职能
    scope_ref    text   NOT NULL,
    scope_owner  uuid REFERENCES app_user (id),   -- 该范围的负责人: 审核员不得是此人
    planned_from date,
    planned_to   date,
    UNIQUE (plan_id, seq),
    CONSTRAINT ck_das_plan_item_kind CHECK (scope_kind IN
        ('DEPARTMENT', 'PROCESS', 'CHECKLIST', 'FUNCTION')),
    CONSTRAINT ck_das_plan_item_ref CHECK (length(btrim(scope_ref)) > 0)
);

-- ---------------------------------------------------------------------
-- 3. 专项审核的触发情形（步骤 2）
-- ---------------------------------------------------------------------
-- 第 7 章: 触发情形发生而未启动的, 为不符合项。所以触发事件本身要登记,
-- 否则"有没有该启动而没启动"根本无从查 —— 只在计划里排审核是查不出来的。
CREATE TABLE IF NOT EXISTS das_special_audit_trigger (
    id           bigserial PRIMARY KEY,
    trigger_kind text NOT NULL,
    occurred_on  date NOT NULL,
    description  text NOT NULL,
    recorded_by  uuid NOT NULL REFERENCES app_user (id),
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_trigger_kind CHECK (trigger_kind IN
        ('SERIOUS_SERVICE_PROBLEM',   -- 严重使用问题
         'MAJOR_CHANGE',              -- 过程或系统的重大更改
         'NEW_CAPABILITY',            -- 新增生产或设计能力
         'SITE_RELOCATION',           -- 场所迁移
         'KEY_PERSONNEL_CHANGE')),    -- 关键人员变更
    CONSTRAINT ck_das_trigger_desc CHECK (length(btrim(description)) > 0)
);

COMMENT ON TABLE das_special_audit_trigger IS
    'UG-DAP-13 步骤 2 的五种触发情形。登记触发事件本身，否则"该启动而未启动"无从查出（第 7 章：触发情形发生而未启动的，为不符合项）。';

-- ---------------------------------------------------------------------
-- 4. 审核的实施（步骤 5、6）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_audit (
    id            bigserial PRIMARY KEY,
    audit_ref     text UNIQUE,
    kind          text NOT NULL,          -- 与计划同一组取值
    plan_item_id  bigint REFERENCES das_audit_plan_item (id),
    trigger_id    bigint REFERENCES das_special_audit_trigger (id),  -- 专项审核
    scope_ref     text NOT NULL,
    scope_owner   uuid REFERENCES app_user (id),

    -- 步骤 5 的审核准则。四项分列而不是一段自由文本: 第 7 章把"仅覆盖
    -- ISO9001/AS9100 而未覆盖 CCAR-21 适用要求"直接定为审核依据不全,
    -- 写成自由文本就判不出来了。
    criteria_ccar21    boolean NOT NULL DEFAULT false,
    criteria_ap2118_d  boolean NOT NULL DEFAULT false,
    criteria_checklist boolean NOT NULL DEFAULT false,
    criteria_manual    boolean NOT NULL DEFAULT false,
    criteria_other     text,               -- ISO9001/AS9100 等, 可有, 但不能只有这个

    lead_auditor  bigint NOT NULL REFERENCES das_auditor (id),
    conducted_from date NOT NULL,
    conducted_to   date NOT NULL,
    closing_meeting_at timestamptz,        -- 结束会议通报
    created_by    uuid NOT NULL REFERENCES app_user (id),
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_das_audit_kind CHECK (kind IN ('DAS_SUPERVISION', 'QMS_AUDIT')),
    CONSTRAINT ck_das_audit_scope CHECK (length(btrim(scope_ref)) > 0),
    CONSTRAINT ck_das_audit_dates CHECK (conducted_to >= conducted_from),
    -- 计划内审核挂计划条目, 专项审核挂触发事件; 二者不得都空 ——
    -- 都空就是一次来路不明的审核, 既不在计划里也没有触发依据。
    CONSTRAINT ck_das_audit_origin CHECK (plan_item_id IS NOT NULL OR trigger_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_das_audit_kind ON das_audit (kind, conducted_to);

COMMENT ON COLUMN das_audit.criteria_ccar21 IS
    'UG-DAP-13 第 7 章：审核内容仅覆盖 ISO9001/AS9100 而未覆盖 CCAR-21 适用要求的，视为审核依据不全。故该项为必须（DCMS-INV-057）。';

CREATE TABLE IF NOT EXISTS das_audit_auditor (
    audit_id   bigint NOT NULL REFERENCES das_audit (id),
    auditor_id bigint NOT NULL REFERENCES das_auditor (id),
    PRIMARY KEY (audit_id, auditor_id)
);

-- ---------------------------------------------------------------------
-- 5. 审核发现（步骤 6）
-- ---------------------------------------------------------------------
-- 步骤 6: 审核记录须如实记载, **包括符合项和不符合项**。所以 verdict 有 CONFORM,
-- 不是只记不符合 —— 只记不符合的审核记录, 看不出审了哪些、审过没审出问题的是哪些,
-- 覆盖率也就无从统计。
CREATE TABLE IF NOT EXISTS das_audit_finding (
    id          bigserial PRIMARY KEY,
    audit_id    bigint NOT NULL REFERENCES das_audit (id),
    clause_ref  text   NOT NULL,          -- 审核准则中的条款/检查项
    verdict     text   NOT NULL,          -- CONFORM / NONCONFORM / OBSERVATION
    fact        text   NOT NULL,
    evidence    text   NOT NULL,
    -- 步骤 6: 发现的不符合**当场**与受审部门确认事实和证据。
    confirmed_with_auditee boolean NOT NULL DEFAULT false,
    auditee_rep text,                     -- 当场确认的受审方人员
    ncr_id      bigint REFERENCES das_ncr (id),   -- 步骤 7: 不符合项发 NCR
    recorded_by uuid NOT NULL REFERENCES app_user (id),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_find_verdict CHECK (verdict IN ('CONFORM', 'NONCONFORM', 'OBSERVATION')),
    CONSTRAINT ck_das_find_text CHECK (length(btrim(clause_ref)) > 0
        AND length(btrim(fact)) > 0 AND length(btrim(evidence)) > 0),
    -- 不符合项须当场确认, 并写明与谁确认(DCMS-INV-058 的表侧一半)。
    CONSTRAINT ck_das_find_confirm CHECK (
        verdict <> 'NONCONFORM'
        OR (confirmed_with_auditee AND length(btrim(coalesce(auditee_rep, ''))) > 0))
);

CREATE INDEX IF NOT EXISTS ix_das_find_audit ON das_audit_finding (audit_id);

-- 5.2 设计保证系统独立监督对符合性检查单的覆盖（步骤 1a、9）
-- 独立监督的覆盖对象是检查单的适用项, 与质量系统审核覆盖部门/过程不是一回事
-- (判据 M2-1)。分表存, 覆盖率才能分别统计。
CREATE TABLE IF NOT EXISTS das_audit_checklist_coverage (
    audit_id          bigint NOT NULL REFERENCES das_audit (id),
    checklist_item_id bigint NOT NULL REFERENCES das_checklist_item (id),
    covered_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (audit_id, checklist_item_id)
);

COMMENT ON TABLE das_audit_checklist_coverage IS
    'AC-21-48 3.6(6)：独立监督周期内须覆盖 DOA 符合性检查单的全部适用项。覆盖对象与质量系统内部审核（部门、过程）不同，故分表统计（判据 M2-1）。';

-- ---------------------------------------------------------------------
-- 6. 审核报告（步骤 7）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_audit_report (
    id            bigserial PRIMARY KEY,
    audit_id      bigint NOT NULL UNIQUE REFERENCES das_audit (id),
    report_ref    text NOT NULL,
    conclusion    text NOT NULL,
    -- 第 7 章: 审核报告须**直接**送责任经理, 不经被审核部门转交。
    issued_to_am      uuid NOT NULL REFERENCES app_user (id),
    issued_to_am_at   timestamptz NOT NULL DEFAULT now(),
    issued_to_action_owner uuid REFERENCES app_user (id),
    issued_by     uuid NOT NULL REFERENCES app_user (id),
    issued_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_report_text CHECK (length(btrim(report_ref)) > 0
        AND length(btrim(conclusion)) > 0)
);

-- ---------------------------------------------------------------------
-- 7. 对独立监督职能自身的审核（步骤 10）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ism_function_audit (
    id            bigserial PRIMARY KEY,
    report_ref    text NOT NULL,
    period_from   date NOT NULL,
    period_to     date NOT NULL,
    organised_by  uuid NOT NULL REFERENCES app_user (id),   -- 责任经理组织

    -- 实施人: 优先外部; 确无外部资源时由责任经理指定, 且责任经理亲自主持。
    auditor_user_id uuid REFERENCES app_user (id),
    external_name   text,
    external_org    text,
    is_external     boolean NOT NULL,
    designation_basis       text,     -- 非外部时: 责任经理的指定依据
    chaired_by_am           boolean NOT NULL DEFAULT false,
    auditor_capability_note text,

    -- 步骤 10 列明的六项内容, 逐项留结论。做成一段自由文本, "审了没审"就看不出来。
    finding_plan_completeness text NOT NULL,   -- 监督计划的完整性和覆盖率
    finding_execution_rate    text NOT NULL,   -- 计划的执行率
    finding_adequacy          text NOT NULL,   -- 发现问题的充分性
    finding_car_closure       text NOT NULL,   -- NCR／CAR 的跟踪闭环质量
    finding_auditor_qual      text NOT NULL,   -- 审核员资格的保持
    finding_independence      text NOT NULL,   -- 独立性的实际保持情况

    reported_to_am_at timestamptz NOT NULL DEFAULT now(),
    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_das_ism_dates CHECK (period_to > period_from),
    CONSTRAINT ck_das_ism_ref CHECK (length(btrim(report_ref)) > 0),
    CONSTRAINT ck_das_ism_who CHECK (
        (is_external AND external_name IS NOT NULL AND external_org IS NOT NULL)
        OR (NOT is_external AND auditor_user_id IS NOT NULL)),
    -- 非外部审核员时: 须有责任经理的指定依据、能力说明, 且责任经理亲自主持。
    CONSTRAINT ck_das_ism_designated CHECK (
        is_external
        OR (length(btrim(coalesce(designation_basis, ''))) > 0
            AND length(btrim(coalesce(auditor_capability_note, ''))) > 0
            AND chaired_by_am)),
    CONSTRAINT ck_das_ism_findings CHECK (
        length(btrim(finding_plan_completeness)) > 0 AND length(btrim(finding_execution_rate)) > 0
        AND length(btrim(finding_adequacy)) > 0 AND length(btrim(finding_car_closure)) > 0
        AND length(btrim(finding_auditor_qual)) > 0 AND length(btrim(finding_independence)) > 0)
);

COMMENT ON TABLE das_ism_function_audit IS
    'UG-DAP-13 步骤 10：独立监督职能不能自我审核。六项内容逐项留结论；非外部审核员时须有责任经理的指定依据并由其亲自主持；实施人不得持独立监督负责人任命（DCMS-INV-060，判据 I7）。';

-- ---------------------------------------------------------------------
-- 8. 不变量
-- ---------------------------------------------------------------------

-- 判据 M2-2: 本模块 append-only。被监督对象不得改写监督记录, 改判只能另记一次。
CREATE OR REPLACE FUNCTION dcms_guard_das_audit_append() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'DCMS-INV-062: % 是监督记录, 不得删除（判据 M2-2: 独立权限域, append-only）',
            TG_TABLE_NAME USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION
        'DCMS-INV-062: % 是监督记录, 不得改写, 更正请另记一次（判据 M2-2）', TG_TABLE_NAME
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['das_audit', 'das_audit_finding', 'das_audit_report',
                             'das_audit_auditor', 'das_audit_checklist_coverage',
                             'das_ism_function_audit', 'das_special_audit_trigger']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_append BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_audit_append()', t, t);
    END LOOP;
END $$;

-- 计划与审核员资格允许受控修改(批准、撤销资格), 但不得删除。
CREATE OR REPLACE FUNCTION dcms_guard_das_audit_no_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-INV-062: % 记录不得删除（判据 M2-2）', TG_TABLE_NAME
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['das_audit_plan', 'das_audit_plan_item', 'das_auditor']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_nodel ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_nodel BEFORE DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_audit_no_delete()', t, t);
    END LOOP;
END $$;

-- DCMS-INV-054: 计划周期不得超过 12 个月（判据 L4／L5）。
-- 另有 CHECK 约束 ck_das_plan_cycle 作后备; 这里出错误消息, 因为填错的人最需要知道的是
-- "24 个月是局方对本单位的监督周期(AP-21-18 4.2), 不是本单位内部监督的周期"。
CREATE OR REPLACE FUNCTION dcms_check_das_audit_plan() RETURNS trigger AS $$
BEGIN
    IF NEW.period_to > (NEW.period_from + interval '12 months')::date THEN
        RAISE EXCEPTION
            'DCMS-INV-054: 审核计划周期不得超过 12 个月（% 至 %, 共 % 天）。设计保证系统独立监督见 AC-21-48 3.6(6), 质量系统内部审核见 CCAR-21.137（十四）; AP-21-18 的 24 个月是局方对本单位开展计划性监督的周期, 不是本单位内部监督的周期, 不得混用。',
            NEW.period_from, NEW.period_to, (NEW.period_to - NEW.period_from)
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_audit_plan_check ON das_audit_plan;
CREATE TRIGGER trg_das_audit_plan_check
    BEFORE INSERT OR UPDATE ON das_audit_plan
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_audit_plan();

-- DCMS-INV-055: 审核须在计划批准后实施（步骤 3）。
-- DCMS-INV-057: 审核准则须覆盖 CCAR-21 适用条款（第 7 章）。
-- DCMS-INV-061: 主审须有有效的审核员资格（步骤 4）。
CREATE OR REPLACE FUNCTION dcms_check_das_audit() RETURNS trigger AS $$
DECLARE v_plan das_audit_plan%ROWTYPE; v_a das_auditor%ROWTYPE;
BEGIN
    IF NOT NEW.criteria_ccar21 THEN
        RAISE EXCEPTION
            'DCMS-INV-057: 审核准则须覆盖 CCAR-21 的适用条款。UG-DAP-13 第 7 章: 仅覆盖 ISO9001/AS9100 而未覆盖 CCAR-21 适用要求的, 视为审核依据不全。'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.kind = 'DAS_SUPERVISION' AND NOT NEW.criteria_checklist THEN
        RAISE EXCEPTION
            'DCMS-INV-057: 设计保证系统独立监督的准则须包含 DOA 符合性检查单（AC-21-48 3.6(6): 须覆盖检查单的全部适用项）'
            USING ERRCODE = '23514';
    END IF;

    SELECT * INTO v_a FROM das_auditor WHERE id = NEW.lead_auditor;
    IF v_a.revoked_at IS NOT NULL
       OR v_a.valid_from > NEW.conducted_from
       OR (v_a.valid_to IS NOT NULL AND v_a.valid_to < NEW.conducted_to) THEN
        RAISE EXCEPTION
            'DCMS-INV-061: 主审在审核实施期间没有有效的审核员资格（UG-DAP-13 步骤 4: 须经培训合格并有资格记录, 由适航管理负责人确认）'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.plan_item_id IS NOT NULL THEN
        SELECT p.* INTO v_plan FROM das_audit_plan p
          JOIN das_audit_plan_item i ON i.plan_id = p.id WHERE i.id = NEW.plan_item_id;
        IF v_plan.approved_at IS NULL THEN
            RAISE EXCEPTION
                'DCMS-INV-055: 年度审核计划尚未经责任经理批准, 不得实施（UG-DAP-13 步骤 3）'
                USING ERRCODE = '23514';
        END IF;
        IF v_plan.kind <> NEW.kind THEN
            RAISE EXCEPTION
                'DCMS-INV-056: 审核类别（%）与所属计划的类别（%）不一致。两类活动的覆盖对象不同, 不得互相充当（判据 M2-1）',
                NEW.kind, v_plan.kind USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_audit_check ON das_audit;
CREATE TRIGGER trg_das_audit_check
    BEFORE INSERT ON das_audit
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_audit();

-- DCMS-INV-059: 审核员不得审核其本人负责或参与的活动（步骤 4、第 7 章: 该次审核结果无效）。
CREATE OR REPLACE FUNCTION dcms_check_das_audit_auditor() RETURNS trigger AS $$
DECLARE v das_audit%ROWTYPE; v_a das_auditor%ROWTYPE; v_item_owner uuid;
BEGIN
    SELECT * INTO v FROM das_audit WHERE id = NEW.audit_id;
    SELECT * INTO v_a FROM das_auditor WHERE id = NEW.auditor_id;
    IF v_a.user_id IS NULL THEN
        RETURN NEW;                      -- 外单位审核员不在本单位任职, 无自审问题
    END IF;
    IF v_a.user_id IS NOT DISTINCT FROM v.scope_owner THEN
        RAISE EXCEPTION
            'DCMS-INV-059: 审核员不得审核其本人负责的活动。UG-DAP-13 第 7 章: 审核员审核自己负责的领域, 该次审核结果无效。'
            USING ERRCODE = '23514';
    END IF;
    IF v.plan_item_id IS NOT NULL THEN
        SELECT scope_owner INTO v_item_owner FROM das_audit_plan_item WHERE id = v.plan_item_id;
        IF v_a.user_id IS NOT DISTINCT FROM v_item_owner THEN
            RAISE EXCEPTION
                'DCMS-INV-059: 审核员是该计划条目所列范围的负责人, 不得审核本人负责的活动（第 7 章: 该次审核结果无效）'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_audit_auditor_check ON das_audit_auditor;
CREATE TRIGGER trg_das_audit_auditor_check
    BEFORE INSERT ON das_audit_auditor
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_audit_auditor();

-- DCMS-INV-058: 审核报告须有发现记录, 且不符合项须当场确认过。
--               审核记录"须如实记载, 包括符合项和不符合项"(步骤 6) ——
--               一条发现都没有的审核, 报告里的结论没有依据。
CREATE OR REPLACE FUNCTION dcms_check_das_audit_report() RETURNS trigger AS $$
DECLARE v_n int; v_bad int;
BEGIN
    -- 第 7 章: 审核报告须**直接**送责任经理, 不经被审核部门转交。只把字段设为必填
    -- 不够 —— 填一个任意的人进去同样满足必填, 所以要核对收件人确实在任责任经理。
    IF NOT EXISTS (SELECT 1 FROM das_appointment_in_force
                    WHERE user_id = NEW.issued_to_am AND position_code = 'AM') THEN
        RAISE EXCEPTION
            'DCMS-INV-058: 审核报告的收件人没有在任的责任经理任命。UG-DAP-13 第 7 章: 审核报告须直接送责任经理, 不经被审核部门转交。'
            USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO v_n FROM das_audit_finding WHERE audit_id = NEW.audit_id;
    IF v_n = 0 THEN
        RAISE EXCEPTION
            'DCMS-INV-058: 该次审核尚无任何发现记录, 不得出报告。UG-DAP-13 步骤 6: 审核记录须如实记载, 包括符合项和不符合项 —— 一条都没有, 报告的结论就没有依据。'
            USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO v_bad FROM das_audit_finding
     WHERE audit_id = NEW.audit_id AND verdict = 'NONCONFORM' AND NOT confirmed_with_auditee;
    IF v_bad > 0 THEN
        RAISE EXCEPTION
            'DCMS-INV-058: 有 % 条不符合发现未与受审部门当场确认事实和证据（UG-DAP-13 步骤 6）',
            v_bad USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_audit_report_check ON das_audit_report;
CREATE TRIGGER trg_das_audit_report_check
    BEFORE INSERT ON das_audit_report
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_audit_report();

-- DCMS-INV-060: 独立监督职能的审核, 实施人不得持独立监督负责人任命（判据 I7）。
--               第 7 章: 由该职能自行出具的自查结论不作为符合性证据。
CREATE OR REPLACE FUNCTION dcms_check_das_ism_function_audit() RETURNS trigger AS $$
BEGIN
    IF NOT NEW.is_external AND EXISTS (
            SELECT 1 FROM das_appointment_in_force
             WHERE user_id = NEW.auditor_user_id AND position_code = 'ISM') THEN
        RAISE EXCEPTION
            'DCMS-INV-060: 独立监督职能不能自我审核。UG-DAP-13 第 7 章: 独立监督负责人不得审核独立监督职能本身, 由该职能自行出具的自查结论不作为符合性证据。'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM das_appointment_in_force
                    WHERE user_id = NEW.organised_by AND position_code = 'AM') THEN
        RAISE EXCEPTION
            'DCMS-INV-060: 对独立监督职能的审核须由责任经理组织（UG-DAP-13 步骤 10）。组织人没有在任的责任经理任命。'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_ism_function_audit_check ON das_ism_function_audit;
CREATE TRIGGER trg_das_ism_function_audit_check
    BEFORE INSERT ON das_ism_function_audit
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_ism_function_audit();

-- ---------------------------------------------------------------------
-- 9. 视图
-- ---------------------------------------------------------------------

-- 9.1 两类活动的周期状态。**分别**算, 不合并（判据 M2-1）。
--     12 个月是两类各自的上限; AP-21-18 的 24 个月是局方的周期, 不在这里出现。
CREATE OR REPLACE VIEW das_audit_cycle_status AS
SELECT k.kind,
       CASE k.kind WHEN 'DAS_SUPERVISION' THEN '设计保证系统独立监督（AC-21-48 3.6(6)）'
                   ELSE '质量系统内部审核（CCAR-21.137（十四））' END          AS kind_cn,
       p.id                                                                   AS current_plan_id,
       p.period_from, p.period_to, p.approved_at IS NOT NULL                  AS plan_approved,
       (SELECT max(a.conducted_to) FROM das_audit a
         WHERE a.kind = k.kind)                                               AS last_audit_on,
       ((SELECT max(a.conducted_to) FROM das_audit a WHERE a.kind = k.kind)
            + interval '12 months')::date                                     AS next_due_on,
       CASE WHEN (SELECT max(a.conducted_to) FROM das_audit a WHERE a.kind = k.kind) IS NULL
              THEN 'NEVER'
            WHEN current_date > ((SELECT max(a.conducted_to) FROM das_audit a
                                   WHERE a.kind = k.kind) + interval '12 months')::date
              THEN 'OVERDUE'
            ELSE 'WITHIN_CYCLE'
       END                                                                    AS cycle_state
  FROM (VALUES ('DAS_SUPERVISION'), ('QMS_AUDIT')) k(kind)
  LEFT JOIN LATERAL (
        SELECT * FROM das_audit_plan
         WHERE kind = k.kind AND period_from <= current_date AND period_to >= current_date
         ORDER BY prepared_at DESC LIMIT 1) p ON true;

COMMENT ON VIEW das_audit_cycle_status IS
    '两类活动的周期状态分别计算（判据 M2-1）。12 个月上限逾期即为不符合项，不得静默顺延；AP-21-18 的 24 个月是局方对本单位的监督周期，与此无关（判据 L10）。';

-- 9.2 独立监督对符合性检查单的覆盖率（步骤 9）。
--     分母是**适用项**: 适用性为"否"的不计入, "部分"仍计入（见 0029 判据 EV5）。
CREATE OR REPLACE VIEW das_audit_checklist_coverage_stat AS
WITH applicable AS (
    SELECT id FROM das_checklist_item
     WHERE applicable_stc <> '否' OR applicable_pma <> '否'
), covered AS (
    SELECT DISTINCT c.checklist_item_id AS id
      FROM das_audit_checklist_coverage c
      JOIN das_audit a ON a.id = c.audit_id
     WHERE a.conducted_to >= current_date - interval '12 months'
)
SELECT (SELECT count(*) FROM applicable)                                   AS applicable_items,
       (SELECT count(*) FROM covered WHERE id IN (SELECT id FROM applicable)) AS covered_items,
       (SELECT count(*) FROM applicable
         WHERE id NOT IN (SELECT id FROM covered))                         AS uncovered_items,
       CASE WHEN (SELECT count(*) FROM applicable) = 0 THEN NULL
            ELSE round(100.0 * (SELECT count(*) FROM covered
                                 WHERE id IN (SELECT id FROM applicable))
                       / (SELECT count(*) FROM applicable), 1)
       END                                                                 AS coverage_pct;

COMMENT ON VIEW das_audit_checklist_coverage_stat IS
    'UG-DAP-13 步骤 9：已审核条款／适用条款。统计窗口为最近 12 个月（AC-21-48 3.6(6) 的一轮周期）。';

-- 9.3 未覆盖的检查单条目: 覆盖率是个数字, 缺的是哪几条才是能动作的。
CREATE OR REPLACE VIEW das_audit_checklist_uncovered AS
SELECT i.id, i.seq, i.req_code, i.req_name, i.req_source,
       i.applicable_stc, i.applicable_pma
  FROM das_checklist_item i
 WHERE (i.applicable_stc <> '否' OR i.applicable_pma <> '否')
   AND NOT EXISTS (
        SELECT 1 FROM das_audit_checklist_coverage c
          JOIN das_audit a ON a.id = c.audit_id
         WHERE c.checklist_item_id = i.id
           AND a.conducted_to >= current_date - interval '12 months')
 ORDER BY i.seq;

-- 9.4 触发了专项审核却没启动的（第 7 章：为不符合项）。
CREATE OR REPLACE VIEW das_special_audit_due AS
SELECT t.id, t.trigger_kind, t.occurred_on, t.description,
       current_date - t.occurred_on AS days_open
  FROM das_special_audit_trigger t
 WHERE NOT EXISTS (SELECT 1 FROM das_audit a WHERE a.trigger_id = t.id)
 ORDER BY t.occurred_on;

-- 9.5 审核员名册与资格有效性。
CREATE OR REPLACE VIEW das_auditor_roster AS
SELECT d.id, COALESCE(u.full_name, d.external_name) AS auditor_name,
       u.username, d.external_org, d.user_id IS NULL AS is_external,
       d.valid_from, d.valid_to, d.revoked_at,
       (d.revoked_at IS NULL AND d.valid_from <= current_date
        AND (d.valid_to IS NULL OR d.valid_to >= current_date))            AS in_force,
       c.full_name AS confirmed_by_name,
       (SELECT count(*) FROM das_audit_auditor x WHERE x.auditor_id = d.id) AS audits
  FROM das_auditor d
  LEFT JOIN app_user u ON u.id = d.user_id
  JOIN app_user c ON c.id = d.confirmed_by
 ORDER BY in_force DESC, auditor_name;

-- 9.6 对独立监督职能的审核是否到期（步骤 10：每 12 个月一次）。
CREATE OR REPLACE VIEW das_ism_function_audit_due AS
SELECT (SELECT max(period_to) FROM das_ism_function_audit)                 AS last_period_to,
       ((SELECT max(period_to) FROM das_ism_function_audit)
            + interval '12 months')::date                                  AS next_due_on,
       CASE WHEN (SELECT count(*) FROM das_ism_function_audit) = 0 THEN 'NEVER'
            WHEN current_date > ((SELECT max(period_to) FROM das_ism_function_audit)
                                 + interval '12 months')::date THEN 'OVERDUE'
            ELSE 'WITHIN_CYCLE'
       END                                                                 AS state,
       (SELECT count(*) FROM das_ism_function_audit WHERE is_external)     AS external_audits,
       (SELECT count(*) FROM das_ism_function_audit WHERE NOT is_external) AS designated_audits;

COMMENT ON VIEW das_ism_function_audit_due IS
    'UG-DAP-13 步骤 10：每 12 个月由责任经理组织一次对独立监督职能的审核。state=NEVER 时该项从未做过——独立监督职能的自查结论不能替代它（第 7 章）。';

-- 9.7 审核台账。
CREATE OR REPLACE VIEW das_audit_register AS
SELECT a.id, a.audit_ref, a.kind, a.scope_ref, a.conducted_from, a.conducted_to,
       a.plan_item_id, a.trigger_id,
       COALESCE(u.full_name, d.external_name)                              AS lead_auditor_name,
       o.full_name                                                         AS scope_owner_name,
       a.criteria_ccar21, a.criteria_ap2118_d, a.criteria_checklist, a.criteria_manual,
       (SELECT count(*) FROM das_audit_finding f WHERE f.audit_id = a.id)  AS findings,
       (SELECT count(*) FROM das_audit_finding f
         WHERE f.audit_id = a.id AND f.verdict = 'NONCONFORM')             AS nonconformities,
       (SELECT count(*) FROM das_audit_finding f
         WHERE f.audit_id = a.id AND f.ncr_id IS NOT NULL)                 AS ncr_raised,
       r.report_ref, r.issued_to_am_at,
       (SELECT count(*) FROM das_audit_checklist_coverage c
         WHERE c.audit_id = a.id)                                         AS checklist_items_covered
  FROM das_audit a
  JOIN das_auditor d ON d.id = a.lead_auditor
  LEFT JOIN app_user u ON u.id = d.user_id
  LEFT JOIN app_user o ON o.id = a.scope_owner
  LEFT JOIN das_audit_report r ON r.audit_id = a.id
 ORDER BY a.conducted_to DESC, a.id DESC;
