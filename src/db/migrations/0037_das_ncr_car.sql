-- 不符合项与纠正措施（M7）。
--
-- 依据 UG-DAP-14《不符合项和纠正措施程序》第 6 章步骤 1～10、第 7 章关键控制点、
-- AP-21-18 2.1 一类问题定义，UG-RPT-2026-003 J 版判据 M7-1、M7-2、I6、I7。
--
-- 【两套计时口径不能混】
-- M6 的 48 小时是日历小时, 这里的一类问题 21 天是**工作日**, 而且"不得延期"。
-- 工作日要算准必须有节假日与调休日历, 见 0036 —— 日历未加载时期限为 NULL,
-- 由视图把"算不出来"显形, 而不是悄悄给一个错的法定期限。
--
-- 还有一处极易写错: 时限"自局方**发布**记录表之日起算, 不是收到之日"(第 7 章)。
-- 所以 issued_on 与 received_on 必须分列, 起算只认 issued_on。按收到之日算会把
-- 期限整体往后挪, 而挪动的是一条法规时限。
--
-- 【判据 M7-1: 内部关闭 ≠ 局方关闭】
-- 这里**没有一个叫 status 的字段**, 也没有"已关闭"这个可以被谁一键推进的状态。
-- 局方开具的不符合项要求"局方评估其合理性并验证后方可关闭"(步骤 8), 所以关闭由
-- 两条独立记录推导: internal_closed_at(内部验证完成)与 caac_closed_at(局方确认)。
-- 内部动作写不到后者 —— 不是靠校验挡住, 是结构上没有那支笔。
--
-- 【判据 M7-2: 仅有"已整改"声明不得关闭】
-- 关闭记录里证据和有效性结论都是 NOT NULL, 且验证人不得是整改责任人(DCMS-INV-049)。
-- 涉及独立监督职能自身的, 还要有责任经理的指定依据、验证人资格和独立性说明
-- —— 这是 I6／I7 在本模块的具体形态。
--
-- 【根本原因不止于"人为失误"】
-- 步骤 6 要求追查到管理或程序层面。文字内容系统判不了, 但"落在哪一层"是可以
-- 结构化的: level 取值里 HUMAN_ERROR_ONLY 单列出来, 并且只有它时不得关闭。
-- 这样"人为失误"仍可记录(它常常是事实的一部分), 但不能成为唯一答案。
--
-- 【"内容雷同"只能机械地判一部分】
-- 第 7 章把"预防措施与纠正措施内容雷同"视为未采取预防措施。完全相同、互为子串
-- 这类可以判(DCMS-INV-050); 语义上近似要人看, 由 das_car_preventive_review 列出
-- 供独立验证人判断并留结论。系统不假装自己能判断雷同。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 不符合项（UG-DAF-05B NCR / 表-21-165）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ncr (
    id            bigserial PRIMARY KEY,
    ncr_no        text UNIQUE,
    source        text NOT NULL,     -- CAAC 局方开具 / 其余为内部来源
    ncr_class     text,              -- 仅局方开具项分类: CLASS_1 一类 / CLASS_2 二类 / OBSERVATION 观察项

    -- 时限起算只认 issued_on（第 7 章: 自局方发布记录表之日起算, 不是收到之日）
    issued_on     date,
    received_on   date,
    registered_at timestamptz NOT NULL DEFAULT now(),

    fact          text NOT NULL,     -- 事实
    basis_clause  text NOT NULL,     -- 依据条款
    evidence      text NOT NULL,     -- 证据
    source_ref    text,              -- 审定信函号／审核报告号／表-21-165 编号

    -- 来自事件调查的不符合项（UG-DAP-12 步骤 8 转入）
    occurrence_id bigint REFERENCES das_occurrence (id),

    responsible_user uuid REFERENCES app_user (id),
    responsible_dept text,

    -- 涉及独立监督职能自身: 关闭时须由责任经理另行指定验证人（I6／I7）
    concerns_ism  boolean NOT NULL DEFAULT false,
    -- 12 个月内重复发生的, 须升级为系统性纠正措施并重新分析根本原因（第 7 章）
    systemic      boolean NOT NULL DEFAULT false,
    -- 二类未按要求提交或落实而上升为一类时, 指向原记录（第 7 章）
    escalated_from bigint REFERENCES das_ncr (id),

    internal_closed_at timestamptz,
    caac_closed_at     timestamptz,
    caac_closed_ref    text,

    created_by    uuid NOT NULL REFERENCES app_user (id),
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_das_ncr_source CHECK (source IN
        ('CAAC', 'INTERNAL_AUDIT', 'SUPERVISION', 'INSPECTION', 'SUPPLIER', 'OCCURRENCE')),
    -- 局方开具的必须有分类和发布日期: 没有分类就定不出 21 工作日还是 3 个月,
    -- 没有发布日期就无从起算。内部来源不套用局方的分类。
    -- 写成 (source='CAAC' AND ... AND ncr_class IN (...)) OR (...) 是不行的:
    -- ncr_class 为 NULL 时 IN 返回 NULL 而不是 false, 整式成为 NULL OR false = NULL,
    -- 而 CHECK 约束在结果为 NULL 时**放行** —— 一条没有分类的局方不符合项就这么进了库,
    -- 它的整改时限会被静默算成 NULL。用 CASE 把两个分支都写成确定的布尔值。
    CONSTRAINT ck_das_ncr_caac CHECK (
        CASE WHEN source = 'CAAC'
             THEN issued_on IS NOT NULL AND ncr_class IS NOT NULL
                  AND ncr_class IN ('CLASS_1', 'CLASS_2', 'OBSERVATION')
             ELSE ncr_class IS NULL
        END),
    CONSTRAINT ck_das_ncr_dates CHECK (received_on IS NULL OR issued_on IS NULL
                                       OR received_on >= issued_on),
    -- 局方关闭只存在于局方开具项, 且必须有局方的确认依据。
    CONSTRAINT ck_das_ncr_caac_close CHECK (
        caac_closed_at IS NULL
        OR (source = 'CAAC' AND length(btrim(coalesce(caac_closed_ref, ''))) > 0)),
    CONSTRAINT ck_das_ncr_text CHECK (length(btrim(fact)) > 0
        AND length(btrim(basis_clause)) > 0 AND length(btrim(evidence)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_das_ncr_basis ON das_ncr (basis_clause);
CREATE INDEX IF NOT EXISTS ix_das_ncr_open  ON das_ncr (issued_on)
    WHERE internal_closed_at IS NULL;

COMMENT ON TABLE das_ncr IS
    'UG-DAF-05B 不符合项 / 表-21-165。没有 status 字段：关闭由 internal_closed_at 与 caac_closed_at 两条独立记录推导（判据 M7-1：内部动作不得把局方开具项推进到已关闭）。';
COMMENT ON COLUMN das_ncr.issued_on IS
    'UG-DAP-14 第 7 章：整改时限自局方发布记录表之日起算，不是收到之日。';

-- ---------------------------------------------------------------------
-- 2. 遏制（步骤 5）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ncr_containment (
    id          bigserial PRIMARY KEY,
    ncr_id      bigint NOT NULL REFERENCES das_ncr (id),
    measures    text   NOT NULL,     -- 暂停签署/发放/使用、隔离受影响项目
    issued_docs_impact text,         -- 已发出资料的影响评估
    decided_by  uuid NOT NULL REFERENCES app_user (id),
    decided_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_ncr_cont_text CHECK (length(btrim(measures)) > 0)
);

-- ---------------------------------------------------------------------
-- 3. 根本原因分析（步骤 6）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ncr_root_cause (
    id          bigserial PRIMARY KEY,
    ncr_id      bigint NOT NULL REFERENCES das_ncr (id),
    level       text   NOT NULL,
    analysis    text   NOT NULL,
    analysed_by uuid NOT NULL REFERENCES app_user (id),
    analysed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_rc_level CHECK (level IN
        ('PROCEDURE', 'MANAGEMENT', 'TRAINING', 'RESOURCE', 'SUPPLIER', 'HUMAN_ERROR_ONLY')),
    CONSTRAINT ck_das_rc_text CHECK (length(btrim(analysis)) > 0)
);

COMMENT ON TABLE das_ncr_root_cause IS
    'UG-DAP-14 步骤 6：追查到管理或程序层面，不止于"人为失误"。可记多条；只有 HUMAN_ERROR_ONLY 一条时不得关闭（DCMS-INV-048）。';

-- ---------------------------------------------------------------------
-- 4. 纠正、纠正措施、预防措施（UG-DAF-05C，步骤 7）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_car (
    id          bigserial PRIMARY KEY,
    ncr_id      bigint NOT NULL UNIQUE REFERENCES das_ncr (id),

    correction        text NOT NULL,   -- a) 消除已发生的不符合本身
    corrective_action text NOT NULL,   -- b) 针对根本原因, 使该问题不再发生
    preventive_action text NOT NULL,   -- c) 举一反三, 排查同类产品/过程/部门

    correction_owner  uuid NOT NULL REFERENCES app_user (id),
    corrective_owner  uuid NOT NULL REFERENCES app_user (id),
    preventive_owner  uuid NOT NULL REFERENCES app_user (id),
    correction_due    date NOT NULL,
    corrective_due    date NOT NULL,
    preventive_due    date NOT NULL,

    drafted_by  uuid NOT NULL REFERENCES app_user (id),
    drafted_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_das_car_text CHECK (length(btrim(correction)) > 0
        AND length(btrim(corrective_action)) > 0 AND length(btrim(preventive_action)) > 0)
);

COMMENT ON TABLE das_car IS
    'UG-DAF-05C：纠正／纠正措施／预防措施三者分别填写，内容不得雷同（第 7 章：雷同或只写纠正措施的，视为未采取预防措施）。';

-- ---------------------------------------------------------------------
-- 5. 延期（步骤 2）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ncr_extension (
    id             bigserial PRIMARY KEY,
    ncr_id         bigint NOT NULL REFERENCES das_ncr (id),
    requested_on   date   NOT NULL DEFAULT current_date,
    requested_to   date   NOT NULL,
    reason         text   NOT NULL,
    requested_by   uuid NOT NULL REFERENCES app_user (id),
    caac_agreed    boolean NOT NULL DEFAULT false,
    caac_agreed_on date,
    caac_agreed_ref text,
    CONSTRAINT ck_das_ext_text CHECK (length(btrim(reason)) > 0),
    -- 延期生效的前提是局方事先同意, 且同意要有依据可查（第 7 章）。
    CONSTRAINT ck_das_ext_agreed CHECK (
        NOT caac_agreed
        OR (caac_agreed_on IS NOT NULL AND length(btrim(coalesce(caac_agreed_ref, ''))) > 0))
);

COMMENT ON TABLE das_ncr_extension IS
    'UG-DAP-14 步骤 2：二类问题确有需要的可在时限到期前提出延期，延期计划须提前得到局方同意。一类问题不得延期（DCMS-INV-044）。';

-- ---------------------------------------------------------------------
-- 6. 提交局方答复（表-21-166，步骤 8）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ncr_caac_reply (
    id            bigserial PRIMARY KEY,
    ncr_id        bigint NOT NULL REFERENCES das_ncr (id),
    form_ref      text   NOT NULL,                 -- 表-21-166 编号
    -- 表-21-166 须载明这两个时间, 缺一即为答复不完整。
    correction_completed_on  date NOT NULL,
    corrective_completed_on  date NOT NULL,
    confirmed_by  uuid NOT NULL REFERENCES app_user (id),   -- 责任经理或其授权人
    submitted_on  date NOT NULL DEFAULT current_date,
    submitted_by  uuid NOT NULL REFERENCES app_user (id),
    CONSTRAINT ck_das_reply_ref CHECK (length(btrim(form_ref)) > 0)
);

-- ---------------------------------------------------------------------
-- 7. 内部关闭验证（步骤 9）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ncr_closure (
    id              bigserial PRIMARY KEY,
    ncr_id          bigint NOT NULL UNIQUE REFERENCES das_ncr (id),
    verifier        uuid NOT NULL REFERENCES app_user (id),
    evidence        text NOT NULL,     -- 实施证据
    effectiveness   text NOT NULL,     -- 有效性结论
    -- 涉及独立监督职能自身时, 责任经理另行指定的三项记录（I6／I7）
    designated_by   uuid REFERENCES app_user (id),
    designation_basis      text,
    verifier_qualification text,
    independence_note      text,
    verified_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_clo_text CHECK (length(btrim(evidence)) > 0
        AND length(btrim(effectiveness)) > 0)
);

COMMENT ON TABLE das_ncr_closure IS
    'UG-DAP-14 步骤 9：核对实施证据和有效性，仅有"已整改"声明不得关闭。局方发现项仍须局方确认关闭（判据 M7-1）。';

-- ---------------------------------------------------------------------
-- 8. 观察项处理意见（步骤 3）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_ncr_observation_response (
    id          bigserial PRIMARY KEY,
    ncr_id      bigint NOT NULL UNIQUE REFERENCES das_ncr (id),
    assessment  text NOT NULL,       -- 逐条评估
    disposition text NOT NULL,       -- 处理意见
    to_management_review boolean NOT NULL DEFAULT true,
    responded_by uuid NOT NULL REFERENCES app_user (id),
    responded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_obs_text CHECK (length(btrim(assessment)) > 0
        AND length(btrim(disposition)) > 0)
);

COMMENT ON TABLE das_ncr_observation_response IS
    'UG-DAP-14 步骤 3：观察项逐条评估并给出处理意见，纳入管理评审；不闭环的下次监督中可能升级。';

-- ---------------------------------------------------------------------
-- 9. 不变量
-- ---------------------------------------------------------------------

-- 只增不改不删: 遏制、原因分析、答复、关闭验证、延期、观察项处理都是已发生的事实。
CREATE OR REPLACE FUNCTION dcms_guard_das_ncr_append() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: % 记录不得删除', TG_TABLE_NAME USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'DCMS-AUDIT: % 记录不得修改, 更正请另增一条', TG_TABLE_NAME
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['das_ncr_containment', 'das_ncr_root_cause',
                             'das_ncr_caac_reply', 'das_ncr_closure',
                             'das_ncr_observation_response']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_append BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_ncr_append()', t, t);
    END LOOP;
END $$;

-- NCR 本体与 CAR 允许受控修改(整改过程中会补充), 但不得删除。
CREATE OR REPLACE FUNCTION dcms_guard_das_ncr_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-INV-051: % 记录不得删除（不符合项台账须完整, 第 8 章）',
        TG_TABLE_NAME USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['das_ncr', 'das_car', 'das_ncr_extension']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_no_delete ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_no_delete BEFORE DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_ncr_delete()', t, t);
    END LOOP;
END $$;

-- DCMS-INV-044: 一类问题不得延期。
-- DCMS-INV-045: 延期申请须在原时限到期前提出。
CREATE OR REPLACE FUNCTION dcms_check_das_ncr_extension() RETURNS trigger AS $$
DECLARE v das_ncr%ROWTYPE; v_due date;
BEGIN
    SELECT * INTO v FROM das_ncr WHERE id = NEW.ncr_id;
    IF v.ncr_class = 'CLASS_1' THEN
        RAISE EXCEPTION
            'DCMS-INV-044: 一类问题的 21 个工作日不得延期（UG-DAP-14 第 7 章: 未按要求提交或落实的, 局方可暂停本单位的体系权利）'
            USING ERRCODE = '23514';
    END IF;
    IF v.ncr_class IS DISTINCT FROM 'CLASS_2' THEN
        RAISE EXCEPTION 'DCMS-INV-044: 只有二类问题可以申请延期, 本记录不是二类'
            USING ERRCODE = '23514';
    END IF;
    v_due := (v.issued_on + interval '3 months')::date;
    IF NEW.requested_on > v_due THEN
        RAISE EXCEPTION
            'DCMS-INV-045: 延期须在时限到期前提出（原时限 %, 申请日 %；第 7 章: 延期必须在时限到期前提出并事先获局方同意）',
            v_due, NEW.requested_on USING ERRCODE = '23514';
    END IF;
    IF NEW.requested_to <= v_due THEN
        RAISE EXCEPTION 'DCMS-INV-045: 延期后的日期（%）须晚于原时限（%）', NEW.requested_to, v_due
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_ncr_extension_check ON das_ncr_extension;
CREATE TRIGGER trg_das_ncr_extension_check
    BEFORE INSERT ON das_ncr_extension
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_ncr_extension();

-- DCMS-INV-050: 预防措施不得与纠正措施雷同, 也不得只是它的子串。
CREATE OR REPLACE FUNCTION dcms_check_das_car() RETURNS trigger AS $$
DECLARE a text; b text; c text;
BEGIN
    a := lower(regexp_replace(NEW.correction,        '\s+', '', 'g'));
    b := lower(regexp_replace(NEW.corrective_action, '\s+', '', 'g'));
    c := lower(regexp_replace(NEW.preventive_action, '\s+', '', 'g'));
    -- 先挡空白串: 否则 position('' in b) 恒为 1, 会掉进下面"雷同"那条,
    -- 报出一个与真实原因无关的理由。
    IF a = '' OR b = '' OR c = '' THEN
        RAISE EXCEPTION
            'DCMS-INV-050: 纠正、纠正措施、预防措施三者须分别填写, 不得留空（UG-DAP-14 步骤 7）'
            USING ERRCODE = '23514';
    END IF;
    IF c = b OR position(c in b) > 0 OR position(b in c) > 0 THEN
        RAISE EXCEPTION
            'DCMS-INV-050: 预防措施与纠正措施内容雷同, 视为未采取预防措施（UG-DAP-14 第 7 章）。纠正措施针对根本原因使问题不再发生, 预防措施是举一反三排查同类产品、过程和部门。'
            USING ERRCODE = '23514';
    END IF;
    IF a = b OR a = c THEN
        RAISE EXCEPTION
            'DCMS-INV-050: 纠正与措施内容雷同。纠正只消除已发生的不符合本身, 与针对根本原因的措施不是一回事（UG-DAP-14 步骤 7）。'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_car_check ON das_car;
CREATE TRIGGER trg_das_car_check
    BEFORE INSERT OR UPDATE ON das_car
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_car();

-- DCMS-INV-046: 关闭须有 CAR 与根本原因分析。
-- DCMS-INV-047: 验证人不得是整改责任人（判据 M7-2、I6）。
-- DCMS-INV-048: 根本原因只落在"人为失误"时不得关闭（步骤 6）。
-- DCMS-INV-053: 12 个月内同一依据条款重复发生的, 须先升级为系统性纠正措施。
-- DCMS-INV-049: 涉及独立监督职能自身的, 须有责任经理的指定依据、资格与独立性记录。
CREATE OR REPLACE FUNCTION dcms_check_das_ncr_closure() RETURNS trigger AS $$
DECLARE v das_ncr%ROWTYPE; r das_car%ROWTYPE; v_deep int; v_any int;
BEGIN
    SELECT * INTO v FROM das_ncr WHERE id = NEW.ncr_id;
    SELECT * INTO r FROM das_car WHERE ncr_id = NEW.ncr_id;
    IF r.id IS NULL THEN
        RAISE EXCEPTION
            'DCMS-INV-046: 尚未填写 UG-DAF-05C（纠正／纠正措施／预防措施）, 不得关闭'
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*), count(*) FILTER (WHERE level <> 'HUMAN_ERROR_ONLY')
      INTO v_any, v_deep FROM das_ncr_root_cause WHERE ncr_id = NEW.ncr_id;
    IF v_any = 0 THEN
        RAISE EXCEPTION 'DCMS-INV-046: 尚未做根本原因分析, 不得关闭（UG-DAP-14 步骤 6）'
            USING ERRCODE = '23514';
    END IF;
    IF v_deep = 0 THEN
        RAISE EXCEPTION
            'DCMS-INV-048: 根本原因仅记为"人为失误", 不得关闭。UG-DAP-14 步骤 6 要求追查到管理或程序层面——人为失误可以是事实的一部分, 不能是唯一答案。'
            USING ERRCODE = '23514';
    END IF;

    -- 验证人不得是整改责任人, 也不得是责任部门负责人本人。
    IF NEW.verifier IN (r.correction_owner, r.corrective_owner, r.preventive_owner)
       OR NEW.verifier IS NOT DISTINCT FROM v.responsible_user THEN
        RAISE EXCEPTION
            'DCMS-INV-047: 验证人不得是整改责任人（UG-DAP-14 步骤 9: 涉及验证人参与整改的, 责任经理另行指定独立于整改责任人的合格人员）'
            USING ERRCODE = '23514';
    END IF;

    IF v.concerns_ism AND (NEW.designated_by IS NULL
            OR length(btrim(coalesce(NEW.designation_basis, ''))) = 0
            OR length(btrim(coalesce(NEW.verifier_qualification, ''))) = 0
            OR length(btrim(coalesce(NEW.independence_note, ''))) = 0) THEN
        RAISE EXCEPTION
            'DCMS-INV-049: 涉及独立监督职能自身的不符合项, 须由责任经理另行指定验证人, 并在 UG-DAF-05C 记录指定依据、资格及独立性（判据 I6／I7）'
            USING ERRCODE = '23514';
    END IF;

    -- 12 个月内重复发生的, 须先升级为系统性纠正措施（第 7 章）。
    IF NOT v.systemic AND EXISTS (
            SELECT 1 FROM das_ncr o
             WHERE o.id <> v.id AND o.basis_clause = v.basis_clause
               AND o.registered_at >= v.registered_at - interval '12 months'
               AND o.registered_at <= v.registered_at) THEN
        RAISE EXCEPTION
            'DCMS-INV-053: 同一依据条款（%）在 12 个月内重复发生, 须先升级为系统性纠正措施并重新分析根本原因（第 7 章）',
            v.basis_clause USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_ncr_closure_check ON das_ncr_closure;
CREATE TRIGGER trg_das_ncr_closure_check
    BEFORE INSERT ON das_ncr_closure
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_ncr_closure();

-- DCMS-INV-052: 内部关闭时间只能在有关闭验证记录后写入。
-- 这条配合"没有 status 字段"一起生效: 内部关闭要有验证记录, 局方关闭要有局方依据,
-- 两者各自独立, 谁也推不动对方（判据 M7-1）。
CREATE OR REPLACE FUNCTION dcms_check_das_ncr() RETURNS trigger AS $$
BEGIN
    IF NEW.internal_closed_at IS NULL THEN
        RETURN NEW;
    END IF;
    -- 观察项与不符合项的闭环要求**不是同一件事**, 两条检查必须互斥。
    -- 步骤 3 对观察项只要求逐条评估、给处理意见、纳入管理评审 —— 观察项不构成
    -- 不符合, 没有"整改实施证据"也没有"措施有效性"可验。把关闭验证记录也要求上,
    -- 观察项就永远关不掉。
    IF NEW.ncr_class = 'OBSERVATION' THEN
        IF NOT EXISTS (SELECT 1 FROM das_ncr_observation_response WHERE ncr_id = NEW.id) THEN
            RAISE EXCEPTION
                'DCMS-INV-052: 观察项须先登记评估与处理意见（UG-DAP-14 步骤 3: 逐条评估并给出处理意见, 纳入管理评审）'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NOT EXISTS (SELECT 1 FROM das_ncr_closure WHERE ncr_id = NEW.id) THEN
        RAISE EXCEPTION
            'DCMS-INV-052: 内部关闭须先有关闭验证记录（证据与有效性结论）, 仅有"已整改"声明不得关闭（判据 M7-2）'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_ncr_check ON das_ncr;
CREATE TRIGGER trg_das_ncr_check
    BEFORE INSERT OR UPDATE ON das_ncr
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_ncr();

-- ---------------------------------------------------------------------
-- 10. 视图
-- ---------------------------------------------------------------------

-- 10.1 整改时限。一类走工作日, 二类走日历月 —— 两套口径分开算, 不互相套用。
--      日历未加载时一类的 deadline 为 NULL, 由 deadline_note 说明为什么。
CREATE OR REPLACE VIEW das_ncr_deadline AS
SELECT n.id, n.ncr_no, n.source, n.ncr_class, n.issued_on, n.received_on,
       CASE n.ncr_class
         WHEN 'CLASS_1' THEN das_add_working_days(n.issued_on, 21)
         WHEN 'CLASS_2' THEN COALESCE(
                (SELECT max(e.requested_to) FROM das_ncr_extension e
                  WHERE e.ncr_id = n.id AND e.caac_agreed),
                (n.issued_on + interval '3 months')::date)
       END                                                      AS deadline_on,
       CASE WHEN n.ncr_class = 'CLASS_1' AND das_add_working_days(n.issued_on, 21) IS NULL
            THEN '工作日日历未加载, 无法计算 21 个工作日（见 das_work_calendar_gap）'
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
    'UG-DAP-14 步骤 2 的整改时限。一类为 21 个工作日（需工作日日历），二类为 3 个月或局方同意的延期日期。is_closed 是推导值：局方开具项须内部与局方两边都关闭（判据 M7-1）。';

-- 10.2 逾期未闭环。观察项无法规时限, 单独看 10.5。
CREATE OR REPLACE VIEW das_ncr_overdue AS
SELECT d.*, current_date - d.deadline_on AS days_over
  FROM das_ncr_deadline d
 WHERE d.deadline_on IS NOT NULL AND NOT d.is_closed AND current_date > d.deadline_on
 ORDER BY d.deadline_on;

-- 10.3 二类上升为一类的风险: 未按要求提交或落实措施的（第 7 章）。
CREATE OR REPLACE VIEW das_ncr_escalation_risk AS
SELECT d.id, d.ncr_no, d.ncr_class, d.issued_on, d.deadline_on,
       EXISTS (SELECT 1 FROM das_car c WHERE c.ncr_id = d.id)              AS has_car,
       EXISTS (SELECT 1 FROM das_ncr_caac_reply r WHERE r.ncr_id = d.id)   AS has_reply,
       EXISTS (SELECT 1 FROM das_ncr_extension e
                WHERE e.ncr_id = d.id AND e.caac_agreed)                   AS has_agreed_extension
  FROM das_ncr_deadline d
 WHERE d.ncr_class = 'CLASS_2' AND NOT d.is_closed
   AND (d.deadline_on IS NULL OR d.deadline_on <= current_date + 14)
 ORDER BY d.deadline_on NULLS FIRST;

COMMENT ON VIEW das_ncr_escalation_risk IS
    'UG-DAP-14 第 7 章：二类问题未按要求提交或落实措施的，将上升为一类问题。临近时限且缺 CAR 或缺答复的在此列出。';

-- 10.4 12 个月内同一依据条款重复发生（第 7 章：须升级为系统性纠正措施）。
CREATE OR REPLACE VIEW das_ncr_recurrence AS
SELECT n.basis_clause, count(*) AS occurrences,
       min(n.registered_at) AS first_at, max(n.registered_at) AS latest_at,
       array_agg(n.id ORDER BY n.registered_at)        AS ncr_ids,
       bool_and(n.systemic)                            AS all_marked_systemic
  FROM das_ncr n
 WHERE n.registered_at >= now() - interval '12 months'
 GROUP BY n.basis_clause
HAVING count(*) > 1
 ORDER BY count(*) DESC;

-- 10.5 观察项未给处理意见的（步骤 3：不闭环的下次监督中可能升级）。
CREATE OR REPLACE VIEW das_ncr_observation_open AS
SELECT n.id, n.ncr_no, n.issued_on, n.fact, n.source_ref
  FROM das_ncr n
 WHERE n.ncr_class = 'OBSERVATION'
   AND NOT EXISTS (SELECT 1 FROM das_ncr_observation_response r WHERE r.ncr_id = n.id)
 ORDER BY n.issued_on;

-- 10.6 预防措施待人工复核。系统只判得出完全雷同与互为子串,
--      语义近似要独立验证人看 —— 列出来, 不假装已经判过。
CREATE OR REPLACE VIEW das_car_preventive_review AS
SELECT c.ncr_id, n.ncr_no, c.corrective_action, c.preventive_action,
       length(c.preventive_action)                                   AS preventive_len,
       (length(c.preventive_action) < 20)                            AS suspiciously_short,
       (c.preventive_owner = c.corrective_owner)                     AS same_owner
  FROM das_car c JOIN das_ncr n ON n.id = c.ncr_id
 WHERE n.internal_closed_at IS NULL
 ORDER BY c.ncr_id;

COMMENT ON VIEW das_car_preventive_review IS
    'UG-DAP-14 第 7 章把"预防措施与纠正措施内容雷同"视为未采取预防措施。完全雷同由 DCMS-INV-050 拒绝；语义近似须独立验证人判断，本视图提供线索而不下结论。';

-- 10.7 季度趋势（步骤 10：每季度统计 NCR，识别重复性问题，提交管理评审）。
CREATE OR REPLACE VIEW das_ncr_quarterly AS
SELECT date_trunc('quarter', n.registered_at)::date AS quarter,
       n.source, n.ncr_class, count(*) AS total,
       count(*) FILTER (WHERE n.internal_closed_at IS NOT NULL) AS internally_closed,
       count(*) FILTER (WHERE n.caac_closed_at IS NOT NULL)     AS caac_closed,
       count(*) FILTER (WHERE n.systemic)                       AS systemic
  FROM das_ncr n
 GROUP BY 1, 2, 3
 ORDER BY 1 DESC, 2, 3;

-- 10.8 台账（第 8 章）。
CREATE OR REPLACE VIEW das_ncr_register AS
SELECT n.id, n.ncr_no, n.source, n.ncr_class, n.issued_on, n.received_on, n.registered_at,
       n.basis_clause, n.fact, n.responsible_dept, u.full_name AS responsible_name,
       n.concerns_ism, n.systemic, n.escalated_from, n.occurrence_id,
       d.deadline_on, d.deadline_note, d.is_closed,
       n.internal_closed_at, n.caac_closed_at, n.caac_closed_ref,
       EXISTS (SELECT 1 FROM das_car c WHERE c.ncr_id = n.id)            AS has_car,
       EXISTS (SELECT 1 FROM das_ncr_closure k WHERE k.ncr_id = n.id)    AS has_verification
  FROM das_ncr n
  LEFT JOIN app_user u ON u.id = n.responsible_user
  LEFT JOIN das_ncr_deadline d ON d.id = n.id
 ORDER BY n.registered_at DESC, n.id DESC;
