-- 时限与周期引擎（基础能力）。
--
-- 依据 UG-RPT-2026-003 第二节（五个正交属性）、判据 T0、T1、T1-2、T2、T3、T4，
-- 第四之二节判据 P1、P2、P2-1、P5，第一节 L1～L10，手册 0.4.1 末段的硬边界，
-- 以及 2026-10-01 的业务决策：**法规要求的时限也可能变，全部做成可设置的参数**。
--
-- 【判据 T1-2: 八个字段, 缺一不可】
--   ① 约束来源  法规／内部规定／外部指定       → 决定走**哪一道**闸门
--   ② 触发方式  周期／事件触发／到期前触发／前置条件 → 决定任务怎么产生
--   ③ 时长或条件 数值＋单位 或 条件表达式
--   ④ 起算点    具体事件的具体时刻
--   ⑤ 日历口径  日历日／工作日
--   ⑥ 上限      规章规定的那个数
--   ⑦ 变更权限
--   ⑧ 本次取值的生效日期
-- 缺"起算点"就无法判超期; 缺"生效日期"就无法按判据 P4 评价历史记录。
--
-- 【2026-10-01 决策: 法规时限也做成参数, 但闸门不同】
-- 原设计把法规硬时限做成不可配置: 值写在代码里, 本表只登记它在哪儿实现。那样一旦
-- CCAR-21 把 48 小时改成别的数, 就得改代码发版本 —— 正是"三级文件体系在系统里塌成
-- 一级"的反面。
--
-- 改成可配置之后, 防护从"做不到"降成了"要走手续", 所以闸门必须比内部参数更严:
--
--   · **规章规定的那个数（上限）**: 只能由**规章修订**驱动。改它必须在同一事务里登记
--     一条 das_deadline_statutory_amendment, 写明条款出处、修订文号、规章生效日期和
--     证据 —— 没有这条记录, 数据库拒绝(DCMS-INV-073)。内部决定改不了它。
--   · **本单位实际执行值**: 走普通的 UG-DAF-09 分类闸门, 但**不得超过上限**
--     (DCMS-INV-065) —— 分类为非重大也不例外(手册 0.4.1 末段)。只能更严。
--   · **约束来源**: 不可改(DCMS-INV-064)。把法规项改成内部项不是修订, 是剥夺防护。
--   · **日历口径**: 判据 L-总 说口径不可由配置改变。它仍然不可由配置改变, 但规章修订
--     可以改它 —— 48 日历小时若被改为工作小时, 那是规章的事, 同样走修订闸门。
--
-- 于是"法规硬时限不得通过任何路线降低"仍然成立: 对内部决定路线是关的, 对规章是开的。
-- 这与原表述的差别已写入设计输入 K 版。
--
-- 【判据 P2-1: 参数变更入口必须内建分类闸门】
-- 改一个参数的最小流程: 填 UG-DAF-09 → 适航管理负责人分类 → 非重大则内部批准后生效;
-- 重大则报 DPI, **认可到手前新值不得生效**。所以取值行有 approved_at 与 activated_at
-- 两个时刻 —— "已批准但未生效"是必须能表达的中间状态。
--
-- 反向也要成立(第四之二节第②个验收示例): 分类为**非重大**的变更**不应**被要求提供
-- 认可证据而卡住。把所有变更都当重大处理是另一种错误, 会让人绕开系统改。所以认可
-- 证据只在 MAJOR 上是启用前提。
--
-- 【初值不是一次变更】
-- 体系文件 00 草案里已有的取值是**初值**, 不是变更: 它没有 UG-DAF-09 单据, 也没有
-- 分类人。硬凑一个签署人进去等于伪造签署。所以 is_baseline 的行允许不填这两项,
-- 但只能是该控制项的**第一行**(DCMS-INV-074), 之后一律走闸门。
--
-- 【判据 T3: 事件触发期限不得由周期调度器生成】
-- 按周期生成会两头错: 没有 NCR 的时候每 21 个工作日凭空冒出一条"NCR 整改"任务;
-- 真实事件发生时又不会自动起算。
--
-- 【判据 T2: 前置条件实现为阻断, 不是提醒】
-- 前置条件类没有数值时长, 不生成倒计时, 不进到期视图 —— 进了就变成"提醒"了。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 控制项（判据 T0：一条体系要求含多个独立控制的，拆成多个控制项）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_deadline_param (
    code          text PRIMARY KEY,          -- 如 M6.REGISTER_LAG
    name_cn       text NOT NULL,
    module_code   text,                      -- M1..M11 / BASE
    -- ① 约束来源：决定走哪一道闸门
    constraint_source text NOT NULL,         -- STATUTORY / INTERNAL / EXTERNAL
    statutory_basis   text,                  -- 法规项：条款出处
    -- ② 触发方式
    trigger_mode  text NOT NULL,             -- PERIODIC / EVENT / PRE_EXPIRY / PRECONDITION
    -- ③ 条件（数值在 das_deadline_value；前置条件类填表达式）
    condition_expr text,
    -- ④ 起算点
    start_point   text NOT NULL,
    -- ⑤ 日历口径（不可由配置改变；规章修订可改，走修订闸门）
    calendar_basis text NOT NULL,            -- CALENDAR / WORKING / NONE
    -- ⑥ 上限：规章规定的那个数。法规项必有；内部项可有（源文件给了上限时）
    ceiling_value numeric(10,2),
    ceiling_unit  text,
    ceiling_basis text,
    -- ⑦ 变更权限
    change_authority text NOT NULL,
    -- 外部指定（判据 T4）
    external_basis  text,
    external_source text,
    source_doc    text NOT NULL,
    implemented_in text,                     -- 哪些视图/函数消费它, 供追溯
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_ddp_source CHECK (constraint_source IN ('STATUTORY','INTERNAL','EXTERNAL')),
    CONSTRAINT ck_ddp_trigger CHECK (trigger_mode IN
        ('PERIODIC','EVENT','PRE_EXPIRY','PRECONDITION')),
    CONSTRAINT ck_ddp_calendar CHECK (calendar_basis IN ('CALENDAR','WORKING','NONE')),
    CONSTRAINT ck_ddp_text CHECK (length(btrim(name_cn)) > 0
        AND length(btrim(start_point)) > 0 AND length(btrim(change_authority)) > 0
        AND length(btrim(source_doc)) > 0),
    -- 法规项须写明条款出处和上限: 没有条款出处, "法规"二字就没有依据;
    -- 没有上限, 就无从判断新值有没有把它放宽。
    CONSTRAINT ck_ddp_statutory CHECK (
        constraint_source <> 'STATUTORY'
        OR (length(btrim(coalesce(statutory_basis,''))) > 0 AND ceiling_value IS NOT NULL)),
    CONSTRAINT ck_ddp_external CHECK (
        constraint_source <> 'EXTERNAL'
        OR (length(btrim(coalesce(external_basis,''))) > 0
            AND length(btrim(coalesce(external_source,''))) > 0)),
    -- 判据 T2／③: 前置条件类没有数值, 填条件表达式; 其余类必须有日历口径。
    CONSTRAINT ck_ddp_condition CHECK (
        CASE WHEN trigger_mode = 'PRECONDITION'
             THEN length(btrim(coalesce(condition_expr,''))) > 0 AND calendar_basis = 'NONE'
             ELSE calendar_basis <> 'NONE'
        END),
    CONSTRAINT ck_ddp_ceiling CHECK (
        (ceiling_value IS NULL AND ceiling_unit IS NULL)
        OR (ceiling_value IS NOT NULL AND ceiling_unit IS NOT NULL
            AND length(btrim(coalesce(ceiling_basis,''))) > 0)),
    CONSTRAINT ck_ddp_unit CHECK (ceiling_unit IS NULL
        OR ceiling_unit IN ('HOUR','DAY','MONTH','YEAR'))
);

COMMENT ON TABLE das_deadline_param IS
    '时限控制项。判据 T1-2 的八个字段缺一不可。法规项的上限（规章规定的那个数）可随规章修订变更，但只能由 das_deadline_statutory_amendment 驱动（DCMS-INV-073）。';
COMMENT ON COLUMN das_deadline_param.ceiling_value IS
    '规章规定的那个数。2026-10-01 决策：法规时限也可能变，所以它是参数；但改它须引用规章修订（DCMS-INV-073），内部决定改不了。';

-- ---------------------------------------------------------------------
-- 2. 规章修订记录（改动上限与日历口径的唯一合法途径）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_deadline_statutory_amendment (
    id            bigserial PRIMARY KEY,
    param_code    text NOT NULL REFERENCES das_deadline_param (code),
    field_changed text NOT NULL,             -- CEILING / CALENDAR_BASIS
    -- 新旧值用**带类型的列**存, 不存渲染好的文本。
    -- 第一版是一个 new_value text, 触发器拿它和 ceiling_value::text 比 —— numeric(10,2)
    -- 渲染成 '72.00', 而人写的是 '72 HOUR', 于是登记了修订仍然改不动上限,
    -- 报错还说"须先登记规章修订"。那种失败方式最坏: 该做的都做了却说没做,
    -- 接下来人就会去猜格式。数值比数值, 不比字符串。
    old_ceiling_value numeric(10,2),
    old_ceiling_unit  text,
    new_ceiling_value numeric(10,2),
    new_ceiling_unit  text,
    old_calendar_basis text,
    new_calendar_basis text,
    regulation_ref          text NOT NULL,   -- 条款出处, 如 CCAR-21.5（六）
    regulation_revision_ref text NOT NULL,   -- 修订文号／局方发布文件号
    regulation_effective_from date NOT NULL, -- 规章本身的生效日期
    evidence_ref  text NOT NULL,             -- 规章原文/局方通知的留存位置
    recorded_by   uuid NOT NULL REFERENCES app_user (id),
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dsa_field CHECK (field_changed IN ('CEILING','CALENDAR_BASIS')),
    CONSTRAINT ck_dsa_text CHECK (length(btrim(regulation_ref)) > 0
        AND length(btrim(regulation_revision_ref)) > 0 AND length(btrim(evidence_ref)) > 0),
    CONSTRAINT ck_dsa_payload CHECK (
        CASE field_changed
          WHEN 'CEILING' THEN new_ceiling_value IS NOT NULL
                              AND new_ceiling_unit IN ('HOUR','DAY','MONTH','YEAR')
          ELSE new_calendar_basis IN ('CALENDAR','WORKING')
        END)
);

CREATE INDEX IF NOT EXISTS ix_dsa_param ON das_deadline_statutory_amendment (param_code);

COMMENT ON TABLE das_deadline_statutory_amendment IS
    '规章修订记录。法规时限的那个数可以改，但只能这么改：引用条款出处、修订文号、规章生效日期和证据留存位置。没有这条记录，数据库拒绝改动上限或日历口径（DCMS-INV-073）。';

-- ---------------------------------------------------------------------
-- 3. 取值（字段 ⑧ 生效日期 + 判据 P2-1 的分类闸门）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_deadline_value (
    id            bigserial PRIMARY KEY,
    param_code    text NOT NULL REFERENCES das_deadline_param (code),
    value_num     numeric(10,2) NOT NULL,
    value_unit    text NOT NULL,
    effective_from date NOT NULL,
    effective_to   date,

    -- 初值: 体系文件 00 草案里已有的取值, 不是一次变更, 没有 UG-DAF-09 也没有分类人。
    is_baseline   boolean NOT NULL DEFAULT false,
    baseline_source text,                   -- 初值的出处（程序步骤／作业文件章节）

    -- 判据 P2-1 的最小流程（变更时必填）
    change_request_ref text,                -- UG-DAF-09 单据号
    classification     text,                -- MAJOR / MINOR
    classified_by      uuid REFERENCES app_user (id),   -- 适航管理负责人
    classified_at      timestamptz,
    caac_ack_ref       text,
    caac_ack_at        timestamptz,
    approved_by        uuid REFERENCES app_user (id),
    approved_at        timestamptz,
    activated_at       timestamptz,

    created_by    uuid REFERENCES app_user (id),
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_ddv_unit CHECK (value_unit IN ('HOUR','DAY','MONTH','YEAR')),
    CONSTRAINT ck_ddv_class CHECK (classification IS NULL
        OR classification IN ('MAJOR','MINOR')),
    CONSTRAINT ck_ddv_positive CHECK (value_num > 0),
    CONSTRAINT ck_ddv_dates CHECK (effective_to IS NULL OR effective_to >= effective_from),
    -- 初值须写出处; 变更须有单据号、分类结论和分类人（判据 P2-1）。
    CONSTRAINT ck_ddv_origin CHECK (
        CASE WHEN is_baseline
             THEN length(btrim(coalesce(baseline_source,''))) > 0
                  AND change_request_ref IS NULL AND classification IS NULL
             ELSE length(btrim(coalesce(change_request_ref,''))) > 0
                  AND classification IS NOT NULL AND classified_by IS NOT NULL
                  AND classified_at IS NOT NULL
        END),
    CONSTRAINT ck_ddv_approved CHECK (
        (approved_by IS NULL AND approved_at IS NULL)
        OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
    CONSTRAINT ck_ddv_ack CHECK (
        (caac_ack_ref IS NULL AND caac_ack_at IS NULL)
        OR (length(btrim(coalesce(caac_ack_ref,''))) > 0 AND caac_ack_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_ddv_param ON das_deadline_value (param_code, effective_from);

COMMENT ON TABLE das_deadline_value IS
    '本单位的实际执行值。approved_at 与 activated_at 分列：判据 P2-1 要求能表达"已批准但未生效"，并在认可证据缺失时拒绝启用。不得超过控制项的上限（DCMS-INV-065），分类为非重大也不例外。';

-- ---------------------------------------------------------------------
-- 4. 任务（判据 T3）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_deadline_task (
    id           bigserial PRIMARY KEY,
    param_code   text NOT NULL REFERENCES das_deadline_param (code),
    period_key   text,
    event_object_type text,
    event_object_id   text,
    started_at   timestamptz NOT NULL,
    due_at       timestamptz NOT NULL,
    completed_at timestamptz,
    completed_by uuid REFERENCES app_user (id),
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- 两类任务的字段互斥。都可空就会出现"既无期间键也无事件"的任务 ——
    -- 那种任务既不知道为什么存在, 也无从判断该不该存在。
    CONSTRAINT ck_ddt_origin CHECK (
        (period_key IS NOT NULL AND event_object_type IS NULL AND event_object_id IS NULL)
        OR (period_key IS NULL AND event_object_type IS NOT NULL
            AND event_object_id IS NOT NULL)),
    CONSTRAINT ck_ddt_due CHECK (due_at >= started_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ddt_periodic
    ON das_deadline_task (param_code, period_key) WHERE period_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ddt_event
    ON das_deadline_task (param_code, event_object_type, event_object_id)
    WHERE event_object_id IS NOT NULL;

COMMENT ON TABLE das_deadline_task IS
    '判据 T3：周期任务按期间生成，事件任务必须指向真实事件实例，两类字段互斥。';

-- ---------------------------------------------------------------------
-- 5. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-064: 约束来源不可改写。
-- DCMS-INV-073: 上限与日历口径只能由规章修订驱动。
CREATE OR REPLACE FUNCTION dcms_guard_das_deadline_param() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-064: 时限控制项不得删除, 停用请另记（追溯矩阵须完整）'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.constraint_source <> OLD.constraint_source THEN
        RAISE EXCEPTION
            'DCMS-INV-064: 约束来源不得改写（% → %）。把法规项改成内部项不是规章修订, 是剥夺它的防护 —— 改完之后新值就只需走内部分类了。',
            OLD.constraint_source, NEW.constraint_source USING ERRCODE = '23514';
    END IF;

    -- 上限变了: 须有同一事务内的规章修订记录。
    IF NEW.ceiling_value IS DISTINCT FROM OLD.ceiling_value
       OR NEW.ceiling_unit IS DISTINCT FROM OLD.ceiling_unit THEN
        IF NOT EXISTS (
                SELECT 1 FROM das_deadline_statutory_amendment a
                 WHERE a.param_code = NEW.code AND a.field_changed = 'CEILING'
                   AND a.new_ceiling_value = NEW.ceiling_value
                   AND a.new_ceiling_unit  = NEW.ceiling_unit) THEN
            RAISE EXCEPTION
                'DCMS-INV-073: 改动规章规定的那个数, 须先登记规章修订（条款出处、修订文号、规章生效日期、证据留存位置）。法规时限确实会变, 但它只能由规章修订驱动, 不能由内部决定改 —— 否则"不得通过任何路线降低"就只剩一句话。'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    -- 日历口径变了: 同样只能由规章修订驱动（判据 L-总：不可由配置改变）。
    IF NEW.calendar_basis <> OLD.calendar_basis THEN
        IF NOT EXISTS (
                SELECT 1 FROM das_deadline_statutory_amendment a
                 WHERE a.param_code = NEW.code AND a.field_changed = 'CALENDAR_BASIS'
                   AND a.new_calendar_basis = NEW.calendar_basis) THEN
            RAISE EXCEPTION
                'DCMS-INV-073: 日历口径不可由配置改变（判据 L-总）。% 现为 % —— 口径从工作日改成日历日会整体改掉一条时限, 而这改动看上去只是换一个下拉框。规章改了口径的, 请先登记规章修订。',
                OLD.code, OLD.calendar_basis USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_deadline_param_guard ON das_deadline_param;
CREATE TRIGGER trg_das_deadline_param_guard
    BEFORE UPDATE OR DELETE ON das_deadline_param
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_deadline_param();

-- DCMS-INV-065: 实际执行值不得超过上限 —— 分类为非重大也不例外。
-- DCMS-INV-066: 变更须经批准方可生效。
-- DCMS-INV-067: 重大更改在认可证据到手前不得生效。
-- DCMS-INV-070: 前置条件类不得有取值行（判据 T2）。
-- DCMS-INV-074: 初值只能是第一行。
CREATE OR REPLACE FUNCTION dcms_check_das_deadline_value() RETURNS trigger AS $$
DECLARE p das_deadline_param%ROWTYPE; v_norm numeric; v_ceil numeric;
BEGIN
    SELECT * INTO p FROM das_deadline_param WHERE code = NEW.param_code;

    IF p.trigger_mode = 'PRECONDITION' THEN
        RAISE EXCEPTION
            'DCMS-INV-070: % 是前置条件类, 没有数值时长（判据 T2: 实现为阻断, 不是提醒）。条件表达式见 das_deadline_param.condition_expr。',
            p.code USING ERRCODE = '23514';
    END IF;

    -- 只在 INSERT 时判。启用新值时要把上一条取值的 effective_to 收口, 那是对已有
    -- 初值行的 UPDATE —— 若在 UPDATE 上也判, 第一次变更就会被这条规则拦住,
    -- 而它本来是防"插一条新的初值行绕过闸门"的。把 is_baseline 改写的路由
    -- dcms_guard_das_deadline_value 堵着, 所以限定 INSERT 不留口子。
    IF TG_OP = 'INSERT' AND NEW.is_baseline AND EXISTS (
            SELECT 1 FROM das_deadline_value WHERE param_code = NEW.param_code
              AND id <> COALESCE(NEW.id, -1)) THEN
        RAISE EXCEPTION
            'DCMS-INV-074: % 已有取值行, 之后的改动一律走变更闸门（UG-DAF-09 → 分类 → 批准）。"初值"只用于体系文件 00 草案里已有的那一个取值, 它没有单据号也没有分类人; 用它来绕过闸门等于让参数可以随便改。',
            p.code USING ERRCODE = '23514';
    END IF;

    -- 上限比较换算到小时。直接比数值会在单位不同时给出错的结论,
    -- 而错的方向恰好是放宽（例如 90 天 vs 上限 3 个月）。
    IF p.ceiling_value IS NOT NULL THEN
        v_norm := NEW.value_num * CASE NEW.value_unit
                     WHEN 'HOUR' THEN 1 WHEN 'DAY' THEN 24
                     WHEN 'MONTH' THEN 24*30 ELSE 24*365 END;
        v_ceil := p.ceiling_value * CASE p.ceiling_unit
                     WHEN 'HOUR' THEN 1 WHEN 'DAY' THEN 24
                     WHEN 'MONTH' THEN 24*30 ELSE 24*365 END;
        IF v_norm > v_ceil THEN
            RAISE EXCEPTION
                'DCMS-INV-065: 新值 % % 超过上限 % %（依据: %）。手册 0.4.1: 法规硬时限不得通过任何路线降低, **包括分类为非重大之后**。规章确实改了上限的, 请先登记规章修订（das_deadline_statutory_amendment）, 不要从执行值这一侧绕。',
                NEW.value_num, NEW.value_unit, p.ceiling_value, p.ceiling_unit,
                p.ceiling_basis USING ERRCODE = '23514';
        END IF;
    END IF;

    -- 判据 P2-1: 启用新值的前提。初值不走这一道（它不是变更）。
    IF NEW.activated_at IS NOT NULL AND NOT NEW.is_baseline THEN
        IF NEW.approved_at IS NULL THEN
            RAISE EXCEPTION
                'DCMS-INV-066: 新值未经批准不得生效（判据 P2-1: 填 UG-DAF-09 → 分类 → 批准 → 生效）'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.classification = 'MAJOR' AND NEW.caac_ack_at IS NULL THEN
            RAISE EXCEPTION
                'DCMS-INV-067: 本次变更已分类为重大更改, 须报 DPI 并取得认可后方可生效。认可证据缺失, 新值不得启用（判据 P2-1、UG-DAP-02 步骤 4）。'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_deadline_value_check ON das_deadline_value;
CREATE TRIGGER trg_das_deadline_value_check
    BEFORE INSERT OR UPDATE ON das_deadline_value
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_deadline_value();

-- DCMS-INV-069: 事件触发期限不得由周期调度器生成, 反之亦然（判据 T3）。
CREATE OR REPLACE FUNCTION dcms_check_das_deadline_task() RETURNS trigger AS $$
DECLARE p das_deadline_param%ROWTYPE;
BEGIN
    SELECT * INTO p FROM das_deadline_param WHERE code = NEW.param_code;
    IF p.trigger_mode = 'PRECONDITION' THEN
        RAISE EXCEPTION
            'DCMS-INV-069: % 是前置条件类, 不生成任务（判据 T2: 只做阻断, 不生成倒计时）',
            p.code USING ERRCODE = '23514';
    END IF;
    IF NEW.period_key IS NOT NULL AND p.trigger_mode = 'EVENT' THEN
        RAISE EXCEPTION
            'DCMS-INV-069: % 是事件触发类, 不得按周期生成任务（判据 T3）。按周期生成会两头错: 没有事件时凭空冒出任务, 真实事件发生时又不会自动起算。',
            p.code USING ERRCODE = '23514';
    END IF;
    IF NEW.event_object_id IS NOT NULL AND p.trigger_mode = 'PERIODIC' THEN
        RAISE EXCEPTION
            'DCMS-INV-069: % 是周期类, 任务须按期间生成, 不挂事件实例（判据 T3）',
            p.code USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_deadline_task_check ON das_deadline_task;
CREATE TRIGGER trg_das_deadline_task_check
    BEFORE INSERT ON das_deadline_task
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_deadline_task();

-- DCMS-INV-072: 取值与修订记录只增不删; 取值与分类结论不可改写。
CREATE OR REPLACE FUNCTION dcms_guard_das_deadline_value() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'DCMS-INV-072: 时限取值不得删除（判据 T1-2 字段⑧: 历史取值是评价历史记录的依据——按当时生效的值判, 不是按现在的值判）'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.value_num <> OLD.value_num OR NEW.value_unit <> OLD.value_unit
       OR NEW.classification IS DISTINCT FROM OLD.classification
       OR NEW.change_request_ref IS DISTINCT FROM OLD.change_request_ref
       OR NEW.classified_by IS DISTINCT FROM OLD.classified_by
       OR NEW.is_baseline <> OLD.is_baseline THEN
        RAISE EXCEPTION
            'DCMS-INV-072: 取值、单位、分类结论、UG-DAF-09 单据号与初值标记不得改写。改值请新增一行, 各自走闸门。'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_deadline_value_guard ON das_deadline_value;
CREATE TRIGGER trg_das_deadline_value_guard
    BEFORE UPDATE OR DELETE ON das_deadline_value
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_deadline_value();

CREATE OR REPLACE FUNCTION dcms_guard_das_deadline_amendment() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-072: 规章修订记录不得删除' USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'DCMS-INV-072: 规章修订记录不得改写, 更正请另记一条'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_deadline_amendment_guard ON das_deadline_statutory_amendment;
CREATE TRIGGER trg_das_deadline_amendment_guard
    BEFORE UPDATE OR DELETE ON das_deadline_statutory_amendment
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_deadline_amendment();

-- ---------------------------------------------------------------------
-- 6. 取值函数（判据 T1：一处取值）
-- ---------------------------------------------------------------------

-- 6.1 某个控制项在某日生效的取值。没有生效取值时返回 NULL,
--     由调用方把"参数未配置"显形 —— 回落到默认常量等于又把值写回了代码里。
CREATE OR REPLACE FUNCTION das_deadline_value_active(p_code text, p_on date DEFAULT NULL)
RETURNS numeric AS $$
    SELECT value_num FROM das_deadline_value
     WHERE param_code = p_code AND activated_at IS NOT NULL
       AND effective_from <= COALESCE(p_on, current_date)
       AND (effective_to IS NULL OR effective_to >= COALESCE(p_on, current_date))
     ORDER BY effective_from DESC, id DESC LIMIT 1;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION das_deadline_unit_active(p_code text, p_on date DEFAULT NULL)
RETURNS text AS $$
    SELECT value_unit FROM das_deadline_value
     WHERE param_code = p_code AND activated_at IS NOT NULL
       AND effective_from <= COALESCE(p_on, current_date)
       AND (effective_to IS NULL OR effective_to >= COALESCE(p_on, current_date))
     ORDER BY effective_from DESC, id DESC LIMIT 1;
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION das_deadline_value_active(text, date) IS
    '判据 T1：内部与法规数值时长一律从这里取。p_on 默认今天；按历史日期查时取当时生效的值（判据 T1-2 字段⑧、判据 P4）。';

-- 6.2 从起算点算到期时刻。日历口径取自控制项的属性⑤, 不由调用方传 ——
--     传进来就等于让调用方决定口径, 那是判据 L-总 禁止的。
CREATE OR REPLACE FUNCTION das_deadline_due_at(p_code text, p_start timestamptz)
RETURNS timestamptz AS $$
DECLARE p das_deadline_param%ROWTYPE; v numeric; u text;
BEGIN
    SELECT * INTO p FROM das_deadline_param WHERE code = p_code;
    IF p.code IS NULL OR p_start IS NULL OR p.trigger_mode = 'PRECONDITION' THEN
        RETURN NULL;
    END IF;
    v := das_deadline_value_active(p_code, p_start::date);
    u := das_deadline_unit_active(p_code, p_start::date);
    IF v IS NULL THEN RETURN NULL; END IF;        -- 未配置: 让它显形, 不回落默认值
    IF p.calendar_basis = 'WORKING' THEN
        -- 工作日口径只对"天"有意义。其余单位配成工作日是配置错误, 返回 NULL 让它暴露,
        -- 而不是按日历日悄悄算一个出来。
        IF u <> 'DAY' THEN RETURN NULL; END IF;
        RETURN das_add_working_days(p_start::date, v::int)::timestamptz;
    END IF;
    RETURN p_start + (v::text || ' ' || lower(u)) :: interval;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION das_deadline_due_at(text, timestamptz) IS
    '判据 T1：一处取值。日历口径取自控制项，不由调用方传入（判据 L-总）。未配置时返回 NULL 让"参数未配置"显形，不回落默认常量。';

-- ---------------------------------------------------------------------
-- 7. 追溯矩阵与缺口视图
-- ---------------------------------------------------------------------

-- 7.1 八个字段的追溯矩阵（判据 T1-2）。判据 T1 的注: 程序里的"见 UG-DAW-001"不是取值,
--     矩阵须把实际取值 join 进来 —— 只写"见 UG-DAW-001"等于没给值。
CREATE OR REPLACE VIEW das_deadline_registry AS
SELECT p.code, p.name_cn, p.module_code,
       p.constraint_source, p.trigger_mode, p.calendar_basis,
       p.start_point, p.change_authority, p.source_doc, p.implemented_in,
       p.statutory_basis, p.condition_expr, p.external_basis, p.external_source,
       p.ceiling_value, p.ceiling_unit, p.ceiling_basis,
       v.value_num, v.value_unit, v.effective_from, v.effective_to,
       v.is_baseline, v.change_request_ref, v.classification, v.activated_at,
       (p.trigger_mode = 'PRECONDITION' OR v.activated_at IS NOT NULL) AS value_settled,
       (SELECT count(*) FROM das_deadline_statutory_amendment a
         WHERE a.param_code = p.code)                                  AS amendments
  FROM das_deadline_param p
  LEFT JOIN LATERAL (
        SELECT * FROM das_deadline_value
         WHERE param_code = p.code AND activated_at IS NOT NULL
           AND effective_from <= current_date
           AND (effective_to IS NULL OR effective_to >= current_date)
         ORDER BY effective_from DESC, id DESC LIMIT 1) v ON true
 ORDER BY p.module_code NULLS LAST, p.code;

COMMENT ON VIEW das_deadline_registry IS
    '时限追溯矩阵：判据 T1-2 的八个字段加当前实际取值。value_settled=false 的尚未配置——判据 T1：未复核的行标注为待判，不得默认按数值时长实现。';

-- 7.2 待配置的控制项。判据 T1 要求"不得默认按数值时长实现",
--     所以缺值要列得出来, 而不是让各调用方各自回落一个默认常量。
CREATE OR REPLACE VIEW das_deadline_unset AS
SELECT p.code, p.name_cn, p.module_code, p.constraint_source, p.trigger_mode,
       p.calendar_basis, p.start_point, p.source_doc, p.note
  FROM das_deadline_param p
 -- 不含外部指定项: 判据 T4 规定它的到期判定以外部来源为准(局方通知／审定计划／合同),
 -- 本来就不存数值时长。把它列成"待配置"会变成一条永远消不掉的待办。
 WHERE p.trigger_mode <> 'PRECONDITION' AND p.constraint_source <> 'EXTERNAL'
   AND NOT EXISTS (SELECT 1 FROM das_deadline_value v
                    WHERE v.param_code = p.code AND v.activated_at IS NOT NULL
                      AND v.effective_from <= current_date
                      AND (v.effective_to IS NULL OR v.effective_to >= current_date))
 ORDER BY p.constraint_source, p.module_code NULLS LAST, p.code;

COMMENT ON VIEW das_deadline_unset IS
    '尚无生效取值的控制项。法规项出现在这里意味着该条法规时限当前算不出来——比算出一个错的好，但要尽快补。';

-- 7.3 已批准但未生效（判据 P2-1 要求能表达的中间状态）。
CREATE OR REPLACE VIEW das_deadline_pending_activation AS
SELECT v.id, v.param_code, p.name_cn, v.value_num, v.value_unit, v.effective_from,
       v.change_request_ref, v.classification, v.approved_at,
       v.caac_ack_ref IS NOT NULL                                  AS has_caac_ack,
       CASE WHEN v.approved_at IS NULL THEN '尚未批准'
            WHEN v.classification = 'MAJOR' AND v.caac_ack_at IS NULL
                 THEN '已分类为重大更改, 认可到手前不得生效'
            ELSE '可启用'
       END                                                         AS blocker
  FROM das_deadline_value v JOIN das_deadline_param p ON p.code = v.param_code
 WHERE v.activated_at IS NULL
 ORDER BY v.created_at;

COMMENT ON VIEW das_deadline_pending_activation IS
    '判据 P2-1：能表达"已批准但未生效"，并在认可证据缺失时拒绝启用。blocker 说明卡在哪一步。';

-- 7.4 到期与超期。前置条件类不进此视图（判据 T2：它只做阻断，不是倒计时）。
CREATE OR REPLACE VIEW das_deadline_task_status AS
SELECT t.id, t.param_code, p.name_cn, p.module_code, p.trigger_mode, p.calendar_basis,
       t.period_key, t.event_object_type, t.event_object_id,
       t.started_at, t.due_at, t.completed_at,
       CASE WHEN t.completed_at IS NOT NULL AND t.completed_at <= t.due_at THEN 'ON_TIME'
            WHEN t.completed_at IS NOT NULL                        THEN 'LATE'
            WHEN now() > t.due_at                                  THEN 'OVERDUE'
            ELSE 'RUNNING'
       END                                                         AS state,
       round(extract(epoch FROM (COALESCE(t.completed_at, now()) - t.started_at))
             / 3600.0, 2)                                          AS elapsed_hours
  FROM das_deadline_task t JOIN das_deadline_param p ON p.code = t.param_code
 ORDER BY t.due_at;

-- 7.5 规章修订史: 哪条时限因为哪次修订变过。
CREATE OR REPLACE VIEW das_deadline_amendment_history AS
SELECT a.id, a.param_code, p.name_cn, a.field_changed,
       CASE a.field_changed
         WHEN 'CEILING' THEN concat_ws(' ', a.old_ceiling_value::text, a.old_ceiling_unit)
         ELSE a.old_calendar_basis END                                AS old_value,
       CASE a.field_changed
         WHEN 'CEILING' THEN concat_ws(' ', a.new_ceiling_value::text, a.new_ceiling_unit)
         ELSE a.new_calendar_basis END                                AS new_value,
       a.regulation_ref, a.regulation_revision_ref, a.regulation_effective_from,
       a.evidence_ref, u.full_name AS recorded_by_name, a.recorded_at
  FROM das_deadline_statutory_amendment a
  JOIN das_deadline_param p ON p.code = a.param_code
  JOIN app_user u ON u.id = a.recorded_by
 ORDER BY a.regulation_effective_from DESC, a.id DESC;

-- ---------------------------------------------------------------------
-- 8. 控制项与初值
-- ---------------------------------------------------------------------
-- 只登记**有出处可引**的取值。UG-DAW-001 第 3 章的逐行取值尚未逐条复核
-- (判据 T1 的"关于行数的说明"), 没有出处的不编一个数填进来 —— 判据 T1 明说未复核的
-- 行标注为待判, **不得默认按数值时长实现**。缺值的由 das_deadline_unset 列出。
INSERT INTO das_deadline_param
    (code, name_cn, module_code, constraint_source, statutory_basis,
     trigger_mode, condition_expr, start_point, calendar_basis,
     ceiling_value, ceiling_unit, ceiling_basis, change_authority, source_doc,
     implemented_in, note)
VALUES
-- ---- 法规项: 上限即规章规定的那个数, 改它须引用规章修订 ----
('L1.OCCURRENCE_REPORT', '故障失效缺陷报局方时限', 'M6', 'STATUTORY', 'CCAR-21.5（六）',
 'EVENT', NULL, '确认故障、失效或缺陷确实存在的时刻', 'CALENDAR',
 48, 'HOUR', 'CCAR-21.5（六）', '规章修订（本单位可设更严的执行值）', 'UG-DAP-12 步骤 5',
 'das_occurrence_deadline（0035）',
 '48 日历小时, 含周末节假日; 起算点不是登记时间（判据 M6-1）'),
('L2.NCR_CLASS1', '局方一类不符合项整改时限', 'M7', 'STATUTORY',
 'UG-DAP-14 步骤 2（法规要求）/ AP-21-18 2.1',
 'EVENT', NULL, '局方发布设计机构审查不符合项记录表之日', 'WORKING',
 21, 'DAY', 'UG-DAP-14 步骤 2（法规要求）', '规章修订', 'UG-DAP-14 步骤 2',
 'das_ncr_deadline（0037）', '21 个工作日, 不得延期; 起算自发布之日, 不是收到之日'),
('L3.NCR_CLASS2', '局方二类不符合项整改时限', 'M7', 'STATUTORY',
 'UG-DAP-14 步骤 2（法规要求）',
 'EVENT', NULL, '局方发布设计机构审查不符合项记录表之日', 'CALENDAR',
 3, 'MONTH', 'UG-DAP-14 步骤 2（法规要求）', '规章修订', 'UG-DAP-14 步骤 2',
 'das_ncr_deadline（0037）', '延期须时限到期前提出并事先获局方同意'),
('L4.DAS_SUPERVISION_CYCLE', '设计保证系统独立监督周期', 'M2', 'STATUTORY', 'AC-21-48 3.6(6)',
 'PERIODIC', NULL, '上一轮监督周期结束之日', 'CALENDAR',
 12, 'MONTH', 'AC-21-48 3.6(6)', '规章修订（本单位可设更短的执行值）', 'UG-DAP-13 步骤 1a',
 'das_audit_plan / DCMS-INV-054（0038）',
 'AP-21-18 4.2 的 24 个月是局方对本单位的监督周期, 不得混用（判据 L10）'),
('L5.QMS_AUDIT_CYCLE', '质量系统内部审核间隔', 'M2', 'STATUTORY', 'CCAR-21.137（十四）',
 'PERIODIC', NULL, '上一次内部审核完成之日', 'CALENDAR',
 12, 'MONTH', 'CCAR-21.137（十四）', '规章修订（本单位可设更短的执行值）', 'UG-DAP-13 步骤 1b',
 'das_audit_plan / DCMS-INV-054（0038）', NULL),
-- ---- 内部规定: 走 UG-DAF-09 分类闸门 ----
('M6.REGISTER_LAG', '事件报告接收后登记时限', 'M6', 'INTERNAL', NULL,
 'EVENT', NULL, '首次接收报告（含 7×24 电话来电）的时刻', 'CALENDAR',
 NULL, NULL, NULL, '适航管理负责人分类后由归口负责人批准', 'UG-DAP-12 步骤 2',
 'das_occurrence_registration_deviation（0035）',
 '程序正文为"最迟 4 小时, 含非工作日"; 时限栏指向 UG-DAW-001'),
('M6.TRIAGE', '事件报告初判时限', 'M6', 'INTERNAL', NULL,
 'EVENT', NULL, '登记完成的时刻', 'CALENDAR', NULL, NULL, NULL,
 '适航管理负责人分类后由归口负责人批准', 'UG-DAP-12 步骤 2', NULL,
 '程序正文为"当日完成"; 具体取值见 UG-DAW-001, 待逐行复核'),
('M7.INVESTIGATION', '一般事件调查时限', 'M7', 'INTERNAL', NULL,
 'EVENT', NULL, 'NCR 登记完成的时刻', 'CALENDAR', NULL, NULL, NULL,
 '适航管理负责人分类后由归口负责人批准', 'UG-DAP-14 步骤 7', NULL,
 '程序时限栏为"见 UG-DAW-001", 取值待逐行复核'),
('M7.TREND_REVIEW', 'NCR 趋势分析频次', 'M7', 'INTERNAL', NULL,
 'PERIODIC', NULL, '上一期趋势分析完成之日', 'CALENDAR', NULL, NULL, NULL,
 '适航管理负责人分类后由归口负责人批准', 'UG-DAP-14 步骤 10',
 'das_ncr_quarterly（0037）', '按判据 Q2 两处源文件冲突时取较严: 每季度'),
('M2.ISM_FUNCTION_AUDIT', '对独立监督职能的审核周期', 'M2', 'INTERNAL', NULL,
 'PERIODIC', NULL, '上一次对该职能的审核完成之日', 'CALENDAR',
 12, 'MONTH', 'UG-DAP-13 步骤 10（每 12 个月）', '责任经理', 'UG-DAP-13 步骤 10',
 'das_ism_function_audit_due（0038）', NULL),
('M1.REGISTER_RECONCILE', '授权人员登记册核对频次', 'M1', 'INTERNAL', NULL,
 'PERIODIC', NULL, '上一次核对完成之日', 'CALENDAR', NULL, NULL, NULL,
 '适航管理负责人分类后由归口负责人批准', 'UG-DAW-005 第 4 章',
 'das_reconciliation_due（0034）', '按判据 Q2 取较严: 每月'),
('M1.RECURRENT_TRAINING', '复训周期', 'M1', 'INTERNAL', NULL,
 'PRE_EXPIRY', NULL, '上一次该课程考核合格之日', 'CALENDAR',
 12, 'MONTH', 'UG-DAW-001 第 4 章（至少每年一次）',
 '适航管理负责人分类后由归口负责人批准', 'UG-DAW-006 第 3 章',
 'das_training_course.valid_months（0033）',
 '现按课程承载; 本控制项提供默认值与上限'),
('M11.CHANGE_CLASSIFY', '参数或系统变更的分类时限', 'BASE', 'INTERNAL', NULL,
 'EVENT', NULL, 'UG-DAF-09 提交的时刻', 'WORKING', NULL, NULL, NULL,
 '适航管理负责人', 'UG-DAP-02 步骤 2', NULL,
 '判据 P2-1: 适航管理负责人分类（3 个工作日）'),
-- ---- 前置条件类: 没有数值, 只做阻断（判据 T2）----
('T2.RECEIPT_BEFORE_USE', '未取得接收确认的资料不得用于改装', 'M8', 'INTERNAL', NULL,
 'PRECONDITION', '存在该资料版次的接收确认记录', '不适用（前置条件不生成倒计时）', 'NONE',
 NULL, NULL, NULL, '适航管理负责人分类后由归口负责人批准', 'UG-DAP-11', NULL,
 '判据 T2: 实现为阻断, 不是提醒'),
('T2.PLAN_APPROVED_BEFORE_AUDIT', '审核计划未批准不得实施', 'M2', 'INTERNAL', NULL,
 'PRECONDITION', '该计划的 approved_at 非空', '不适用（前置条件不生成倒计时）', 'NONE',
 NULL, NULL, NULL, '责任经理', 'UG-DAP-13 步骤 3', 'DCMS-INV-055（0038）',
 '已实现为阻断')
ON CONFLICT (code) DO NOTHING;

-- 外部指定项单独插: ck_ddp_external 在 INSERT 时就要求依据与来源齐备（判据 T4:
-- 不得留空或填 0 充数）, 所以这两列不能留到事后 UPDATE 再补。
INSERT INTO das_deadline_param
    (code, name_cn, module_code, constraint_source, trigger_mode, start_point,
     calendar_basis, change_authority, source_doc, external_basis, external_source, note)
VALUES
('EXT.CAAC_SCHEDULED_SURVEILLANCE', '局方计划性监督周期', 'M10', 'EXTERNAL',
 'PERIODIC', '局方上一轮计划性监督完成之日', 'CALENDAR', '局方（本单位不可调整）',
 'AP-21-18 4.2', '局方的监督计划安排', '局方通知／AP-21-18 4.2',
 '判据 L10: 这是局方的周期, 不是本单位内部监督的周期; 系统记录不主动触发')
ON CONFLICT (code) DO NOTHING;

-- 初值。只填出处明确的那些; 其余由 das_deadline_unset 列为待填。
-- is_baseline 的行没有 UG-DAF-09 单据号也没有分类人 —— 它不是一次变更,
-- 硬凑一个签署人进去等于伪造签署。之后的改动一律走闸门(DCMS-INV-074)。
INSERT INTO das_deadline_value
    (param_code, value_num, value_unit, effective_from, is_baseline, baseline_source,
     activated_at)
SELECT b.code, b.num, b.unit, b.eff, true, b.src, now()
  FROM (VALUES
    -- 初值的生效日期一律取系统纪元, 不取体系文件基线日。
    -- 理由只有一条: 这些值在系统之前就已经成立。CCAR-21.5 的 48 小时在体系文件
    -- 起草之前早就生效了; UG-DAP-12 的"最迟 4 小时"也不是从起草那天才开始算的。
    -- 填成起草日的后果是那之前发生的记录取不到值、期限算成 NULL, 而判据 T1-2
    -- 字段⑧ 要的是"按当时生效的值评价历史记录", 不是"按我们什么时候开始记"。
    -- 各条规章各自的真实生效日期不在本次已核实范围内 —— 编一个出来比用纪元更糟,
    -- 因为编出来的日期会让某一段历史记录按错误的值被评价。待核实后按规章修订补。
    ('L1.OCCURRENCE_REPORT',      48, 'HOUR',  DATE '2000-01-01',
     'CCAR-21.5（六）/ UG-DAP-12 步骤 5（初值生效日取系统纪元, 规章生效日待核实）'),
    ('L2.NCR_CLASS1',             21, 'DAY',   DATE '2000-01-01',
     'UG-DAP-14 步骤 2（法规要求）（同上）'),
    ('L3.NCR_CLASS2',              3, 'MONTH', DATE '2000-01-01',
     'UG-DAP-14 步骤 2（法规要求）（同上）'),
    ('L4.DAS_SUPERVISION_CYCLE',  12, 'MONTH', DATE '2000-01-01',
     'AC-21-48 3.6(6) / UG-DAP-13 步骤 1a（同上）'),
    ('L5.QMS_AUDIT_CYCLE',        12, 'MONTH', DATE '2000-01-01',
     'CCAR-21.137（十四）/ UG-DAP-13 步骤 1b（同上）'),
    ('M6.REGISTER_LAG',            4, 'HOUR',  DATE '2000-01-01',
     'UG-DAP-12 步骤 2 正文「最迟 4 小时, 含非工作日」'),
    ('M2.ISM_FUNCTION_AUDIT',     12, 'MONTH', DATE '2000-01-01',
     'UG-DAP-13 步骤 10 正文「每 12 个月」'),
    ('M1.REGISTER_RECONCILE',      1, 'MONTH', DATE '2000-01-01',
     'UG-DAW-005 第 4 章「每月核对」（判据 Q2 取较严）'),
    ('M1.RECURRENT_TRAINING',     12, 'MONTH', DATE '2000-01-01',
     'UG-DAW-001 第 4 章「至少每年一次」'),
    ('M7.TREND_REVIEW',            3, 'MONTH', DATE '2000-01-01',
     'UG-DAP-14 步骤 10「每季度」（判据 Q2 取较严）'),
    ('M11.CHANGE_CLASSIFY',        3, 'DAY',   DATE '2000-01-01',
     'UG-DAP-02 步骤 2 / 判据 P2-1「3 个工作日」')
  ) AS b(code, num, unit, eff, src)
 -- 可重复执行: 已有取值行的不再插。DCMS-INV-074 只允许初值是第一行,
 -- 用 ON CONFLICT 挡不住(没有对应的唯一约束), 所以在这里先滤掉。
 WHERE NOT EXISTS (SELECT 1 FROM das_deadline_value x WHERE x.param_code = b.code);

-- ---------------------------------------------------------------------
-- 9. 各模块改为从引擎取值（判据 T1：一处取值）
-- ---------------------------------------------------------------------
-- 0035／0037／0038 把数值写在视图与约束里, 并在注释里写着"引擎就绪后应从
-- UG-DAW-001 取值"。引擎到了, 这里接过去。
--
-- 不回去改那三个迁移, 而是在这里 CREATE OR REPLACE —— 一是它们已发布且各自的
-- CI 通过, 二是这正是生产上的真实顺序: 引擎后到, 然后接管。每个迁移的历史因此
-- 如实记着"当时是写死的"。
--
-- 接管后多了一种新状态: **参数未配置时期限算不出来**。这些视图原本总能给出一个
-- 日期, 现在会给 NULL 并在状态里说明。这是有意的 —— 回落到一个默认常量等于又把
-- 值写回了代码里, 而那个常量会在规章改了之后继续悄悄生效。

CREATE OR REPLACE FUNCTION das_deadline_interval(p_code text, p_on date DEFAULT NULL)
RETURNS interval AS $$
DECLARE v numeric; u text;
BEGIN
    v := das_deadline_value_active(p_code, p_on);
    u := das_deadline_unit_active(p_code, p_on);
    IF v IS NULL THEN RETURN NULL; END IF;
    RETURN (v::text || ' ' || lower(u)) :: interval;
END;
$$ LANGUAGE plpgsql STABLE;

-- 9.1 M6：48 小时与 4 小时都从引擎取
CREATE OR REPLACE VIEW das_occurrence_deadline AS
SELECT o.id, o.report_no, o.description, o.triage_conclusion,
       o.received_at, o.registered_at, o.confirmed_at,
       das_deadline_due_at('L1.OCCURRENCE_REPORT', o.confirmed_at)        AS deadline_at,
       s.submitted_at                                                     AS first_submitted_at,
       CASE WHEN o.confirmed_at IS NULL THEN NULL
            WHEN s.submitted_at IS NOT NULL
                 THEN round(extract(epoch FROM (s.submitted_at - o.confirmed_at)) / 3600.0, 2)
            ELSE round(extract(epoch FROM (now() - o.confirmed_at)) / 3600.0, 2)
       END                                                                AS elapsed_hours,
       CASE WHEN o.confirmed_at IS NULL                             THEN 'NO_CLOCK'
            WHEN das_deadline_due_at('L1.OCCURRENCE_REPORT', o.confirmed_at) IS NULL
                                                                    THEN 'NO_PARAM'
            WHEN s.submitted_at IS NULL
                 AND now() > das_deadline_due_at('L1.OCCURRENCE_REPORT', o.confirmed_at)
                                                                    THEN 'OVERDUE'
            WHEN s.submitted_at IS NULL                             THEN 'RUNNING'
            WHEN s.submitted_at > das_deadline_due_at('L1.OCCURRENCE_REPORT', o.confirmed_at)
                                                                    THEN 'LATE'
            ELSE 'ON_TIME'
       END                                                                AS clock_state
  FROM das_occurrence o
  LEFT JOIN LATERAL (
        SELECT min(submitted_at) AS submitted_at FROM das_occurrence_submission
         WHERE occurrence_id = o.id AND NOT is_supplement) s ON true
 WHERE o.triage_conclusion IS DISTINCT FROM 'OUT_OF_SCOPE';

COMMENT ON VIEW das_occurrence_deadline IS
    'CCAR-21.5（六）的报送时限。数值与日历口径取自 das_deadline_param 的 L1.OCCURRENCE_REPORT（判据 T1）；clock_state=NO_PARAM 表示该参数当前无生效取值，期限算不出来——不回落默认常量。';

CREATE OR REPLACE VIEW das_occurrence_registration_deviation AS
SELECT o.id, o.report_no, o.received_at, o.received_via, o.registered_at,
       round(extract(epoch FROM (o.registered_at - o.received_at)) / 3600.0, 2) AS lag_hours,
       das_deadline_value_active('M6.REGISTER_LAG', o.received_at::date)::text
           || ' ' || das_deadline_unit_active('M6.REGISTER_LAG', o.received_at::date)
                                                                                AS threshold
  FROM das_occurrence o
 WHERE das_deadline_interval('M6.REGISTER_LAG', o.received_at::date) IS NOT NULL
   AND o.registered_at > o.received_at
       + das_deadline_interval('M6.REGISTER_LAG', o.received_at::date)
 ORDER BY o.received_at;

COMMENT ON VIEW das_occurrence_registration_deviation IS
    'UG-DAP-12 步骤 2 的登记时限。门限取自 M6.REGISTER_LAG，按当时生效的取值判（判据 T1-2 字段⑧）；参数无取值时本视图为空，由 das_deadline_unset 显形，而不是用一个默认值凑。';

-- 9.2 M7：21 个工作日与 3 个月都从引擎取
CREATE OR REPLACE VIEW das_ncr_deadline AS
SELECT n.id, n.ncr_no, n.source, n.ncr_class, n.issued_on, n.received_on,
       CASE n.ncr_class
         WHEN 'CLASS_1' THEN das_deadline_due_at('L2.NCR_CLASS1',
                                                 n.issued_on::timestamptz)::date
         WHEN 'CLASS_2' THEN COALESCE(
                (SELECT max(e.requested_to) FROM das_ncr_extension e
                  WHERE e.ncr_id = n.id AND e.caac_agreed),
                das_deadline_due_at('L3.NCR_CLASS2', n.issued_on::timestamptz)::date)
       END                                                      AS deadline_on,
       CASE WHEN n.ncr_class = 'CLASS_1'
                 AND das_deadline_due_at('L2.NCR_CLASS1', n.issued_on::timestamptz) IS NULL
            THEN '算不出来: 工作日日历未加载, 或 L2.NCR_CLASS1 无生效取值'
            WHEN n.ncr_class = 'CLASS_2'
                 AND das_deadline_due_at('L3.NCR_CLASS2', n.issued_on::timestamptz) IS NULL
                 AND NOT EXISTS (SELECT 1 FROM das_ncr_extension e
                                  WHERE e.ncr_id = n.id AND e.caac_agreed)
            THEN '算不出来: L3.NCR_CLASS2 无生效取值'
            WHEN n.ncr_class = 'CLASS_2' AND EXISTS (SELECT 1 FROM das_ncr_extension e
                   WHERE e.ncr_id = n.id AND e.caac_agreed)
            THEN '已按局方同意的延期计划顺延'
       END                                                      AS deadline_note,
       n.internal_closed_at, n.caac_closed_at,
       (n.internal_closed_at IS NOT NULL
        AND (n.source <> 'CAAC' OR n.ncr_class = 'OBSERVATION'
             OR n.caac_closed_at IS NOT NULL))                  AS is_closed
  FROM das_ncr n;

COMMENT ON VIEW das_ncr_deadline IS
    'UG-DAP-14 步骤 2 的整改时限。一类与二类的数值和日历口径均取自 das_deadline_param（L2／L3，判据 T1）；一类仍需工作日日历（0036）。';

-- 9.3 M2：12 个月的周期上限从引擎取
-- CHECK 约束不能查别的表, 所以这条上限只能由触发器承担。原来的 CHECK 作为后备
-- 已不成立(上限现在是可变参数), 一并撤掉 —— 留着它会在规章改了上限之后继续按
-- 旧数拦人, 而拦人的理由只会显示一个约束名。
ALTER TABLE das_audit_plan DROP CONSTRAINT IF EXISTS ck_das_plan_cycle;

CREATE OR REPLACE FUNCTION dcms_check_das_audit_plan() RETURNS trigger AS $$
DECLARE v_code text; v_ceil interval; v_num numeric; v_unit text;
BEGIN
    v_code := CASE NEW.kind WHEN 'DAS_SUPERVISION' THEN 'L4.DAS_SUPERVISION_CYCLE'
                            ELSE 'L5.QMS_AUDIT_CYCLE' END;
    v_num  := das_deadline_value_active(v_code, NEW.period_from);
    v_unit := das_deadline_unit_active(v_code, NEW.period_from);
    IF v_num IS NULL THEN
        RAISE EXCEPTION
            'DCMS-INV-054: % 当前没有生效取值, 无法校验周期上限。请先在时限登记册中配置（das_deadline_unset 会列出它）—— 这里不回落到一个默认的 12 个月, 那样规章改了之后旧数会继续悄悄生效。',
            v_code USING ERRCODE = '23514';
    END IF;
    v_ceil := (v_num::text || ' ' || lower(v_unit))::interval;
    IF NEW.period_to > (NEW.period_from + v_ceil)::date THEN
        RAISE EXCEPTION
            'DCMS-INV-054: 审核计划周期不得超过 % %（% 至 %, 共 % 天）。依据见时限控制项 %; AP-21-18 的 24 个月是局方对本单位开展计划性监督的周期, 不是本单位内部监督的周期, 不得混用（判据 L10）。',
            v_num, v_unit, NEW.period_from, NEW.period_to,
            (NEW.period_to - NEW.period_from), v_code USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE VIEW das_audit_cycle_status AS
SELECT k.kind,
       CASE k.kind WHEN 'DAS_SUPERVISION' THEN '设计保证系统独立监督（AC-21-48 3.6(6)）'
                   ELSE '质量系统内部审核（CCAR-21.137（十四））' END          AS kind_cn,
       p.id                                                                   AS current_plan_id,
       p.period_from, p.period_to, p.approved_at IS NOT NULL                  AS plan_approved,
       la.last_audit_on,
       (la.last_audit_on + das_deadline_interval(k.param_code, la.last_audit_on))::date
                                                                              AS next_due_on,
       CASE WHEN la.last_audit_on IS NULL                            THEN 'NEVER'
            WHEN das_deadline_interval(k.param_code, la.last_audit_on) IS NULL
                                                                     THEN 'NO_PARAM'
            WHEN current_date > (la.last_audit_on
                     + das_deadline_interval(k.param_code, la.last_audit_on))::date
                                                                     THEN 'OVERDUE'
            ELSE 'WITHIN_CYCLE'
       END                                                                    AS cycle_state,
       -- 新列只能追加在末尾: CREATE OR REPLACE VIEW 不允许改动已有列的位置和名字。
       k.param_code
  FROM (VALUES ('DAS_SUPERVISION','L4.DAS_SUPERVISION_CYCLE'),
               ('QMS_AUDIT','L5.QMS_AUDIT_CYCLE')) k(kind, param_code)
  LEFT JOIN LATERAL (
        SELECT max(a.conducted_to) AS last_audit_on FROM das_audit a
         WHERE a.kind = k.kind) la ON true
  LEFT JOIN LATERAL (
        SELECT * FROM das_audit_plan
         WHERE kind = k.kind AND period_from <= current_date AND period_to >= current_date
         ORDER BY prepared_at DESC LIMIT 1) p ON true;

COMMENT ON VIEW das_audit_cycle_status IS
    '两类活动的周期状态分别计算（判据 M2-1），周期取自 das_deadline_param（L4／L5，判据 T1）。cycle_state=NO_PARAM 表示该周期当前无生效取值。';

CREATE OR REPLACE VIEW das_ism_function_audit_due AS
SELECT la.last_period_to,
       (la.last_period_to
        + das_deadline_interval('M2.ISM_FUNCTION_AUDIT', la.last_period_to))::date
                                                                           AS next_due_on,
       CASE WHEN la.last_period_to IS NULL THEN 'NEVER'
            WHEN das_deadline_interval('M2.ISM_FUNCTION_AUDIT', la.last_period_to) IS NULL
                 THEN 'NO_PARAM'
            WHEN current_date > (la.last_period_to
                     + das_deadline_interval('M2.ISM_FUNCTION_AUDIT', la.last_period_to))::date
                 THEN 'OVERDUE'
            ELSE 'WITHIN_CYCLE'
       END                                                                 AS state,
       (SELECT count(*) FROM das_ism_function_audit WHERE is_external)     AS external_audits,
       (SELECT count(*) FROM das_ism_function_audit WHERE NOT is_external) AS designated_audits
  FROM (SELECT max(period_to) AS last_period_to FROM das_ism_function_audit) la;

COMMENT ON VIEW das_ism_function_audit_due IS
    'UG-DAP-13 步骤 10：对独立监督职能的审核周期，取自 M2.ISM_FUNCTION_AUDIT（判据 T1）。state=NEVER 时该项从未做过——该职能自己的自查结论不能替代它（第 7 章）。';
