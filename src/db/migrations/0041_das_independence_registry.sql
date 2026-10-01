-- 独立性约束的实现状态登记（基础能力）。
--
-- 依据 UG-RPT-2026-003 第四节判据 I1～I10 与 I-总、第 5.1 节（授权校验的实现状态，
-- 逐维核验）、判据 E2。
--
-- 【这张表要解决的问题】
-- 判据 I1～I10 的实现分散在六个迁移、四个服务模块里: 岗位互斥在 0032 的触发器,
-- 三签在 0034, 不符合项关闭的独立性在 0037, 审核员自审在 0038, 自授权与备份在
-- signers.py。于是"某条独立性约束在哪实现、测过没有、现在到底算不算实现"没有一处
-- 能回答 —— 而这恰好是符合性自评要逐条写的东西。
--
-- 【判据 E2: 三件事必须分三栏, 不能并成一件】
-- 第 5.1 节的说明写得很直接: "用例已定义"不等于"已执行通过"; C 版把"有用例"写成
-- "运行验证有", 是把三件事合并成了一件。所以这里分三栏:
--   ① source_state   源码状态   —— 代码/约束在不在
--   ② test_state     用例状态   —— 反例用例定义了没有（判据 I-总: 必须是反例）
--   ③ 执行证据       另建表     —— 提交号 + 批次 + 结果, 由 CI 或人工登记
-- 本迁移**不预填任何执行证据**: 预填等于自己给自己发合格证。
--
-- 【判据 I10: 不得笼统称已实现, 也不得笼统计数】
-- I10 的四个维度状态各不相同（有效期/专业/可签署文件类型/产品范围）, 原文明确禁止
-- 笼统。所以 I10 作为父项**不许自己带状态**(DCMS-INV-086), 状态只能落在四个子维度上。
-- 一行"I10 已实现"写不进这张表。
--
-- 【声称数据库实现的, 当场核对系统目录】
-- 声称"由触发器 trg_xxx 保证"而那个触发器其实已被改名或删掉, 是最难发现的一类
-- 假覆盖 —— 登记册看上去是满的。所以写入时就查 pg_trigger / pg_constraint /
-- pg_proc, 对不上直接拒绝(DCMS-INV-083); 另有视图持续核对, 日后有人删了触发器,
-- 对应那一行会变红。
--
-- 【source_state 为什么有 MISMATCHED 这个取值】
-- 第 5.1 节的"可签署文件类型"一维: 代码在、校验也跑, 但它校验的是 DCMS 的文档种类
-- (DWG/SPEC/QTP), 不是 UG-DAM-01-附2 的 5 类适航签署事项。这种情况既不是"有"
-- 也不是"无" —— 记成"有"会让自评说谎, 记成"无"会丢掉"代码确实在跑"这个事实。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 规则登记
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_independence_rule (
    code         text PRIMARY KEY,          -- I1..I10, 或 I10.VALIDITY 这类子维度
    parent_code  text REFERENCES das_independence_rule (code),
    requirement  text NOT NULL,             -- 要求原文
    source_doc   text NOT NULL,             -- 手册 3.1 / UG-DAP-01 等
    -- 容器项（有子维度、状态只落在子项上）**显式声明**, 不从编码推。
    -- 原先把容器编码写进 CHECK 里(code IN ('I10'))漏了 I3; 而且以后谁给 I5 加了
    -- 子维度, 那个 CHECK 也不会知道。声明与实际子项是否一致, 由
    -- das_independence_container_mismatch 持续核对。
    is_container boolean NOT NULL DEFAULT false,

    -- ① 源码状态
    source_state text,                      -- PRESENT / PARTIAL / MISMATCHED / ABSENT
    enforcement_kind text,                  -- DB_TRIGGER / DB_CONSTRAINT / DB_INDEX /
                                            -- DB_FUNCTION / SERVICE_CHECK / NONE / PAPER_ONLY
    enforcement_object text,                -- 触发器/约束/函数名, 或服务层函数
    invariant_code text,                    -- DCMS-INV-NNN

    -- ② 用例状态（判据 I-总：必须是"尝试违反并确认被拒绝"的反例用例）
    test_state   text,                      -- DEFINED / ABSENT / NOT_APPLICABLE
    test_ref     text,                      -- 用例文件与用例描述

    -- 未实现或部分实现时的人工控制（符合性自评须如实写明）
    manual_control text,
    gap_note     text,
    updated_at   timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_dir_source CHECK (source_state IS NULL
        OR source_state IN ('PRESENT', 'PARTIAL', 'MISMATCHED', 'ABSENT')),
    CONSTRAINT ck_dir_kind CHECK (enforcement_kind IS NULL OR enforcement_kind IN
        ('DB_TRIGGER', 'DB_CONSTRAINT', 'DB_INDEX', 'DB_FUNCTION',
         'SERVICE_CHECK', 'NONE', 'PAPER_ONLY')),
    CONSTRAINT ck_dir_test CHECK (test_state IS NULL
        OR test_state IN ('DEFINED', 'ABSENT', 'NOT_APPLICABLE')),
    CONSTRAINT ck_dir_text CHECK (length(btrim(requirement)) > 0
        AND length(btrim(source_doc)) > 0),
    -- 父项是容器, 不带自己的状态（判据 I10）。叶子项必须带三栏里的前两栏。
    CONSTRAINT ck_dir_leaf CHECK (
        CASE WHEN is_container
             THEN source_state IS NULL AND test_state IS NULL
                  AND enforcement_kind IS NULL AND manual_control IS NULL
             ELSE source_state IS NOT NULL AND test_state IS NOT NULL
                  AND enforcement_kind IS NOT NULL
        END),
    -- 声称有实现对象的, 必须写出对象名; 声称没有的, 不许写对象名。
    CONSTRAINT ck_dir_object CHECK (
        CASE WHEN enforcement_kind IN ('DB_TRIGGER','DB_CONSTRAINT','DB_INDEX',
                                       'DB_FUNCTION','SERVICE_CHECK')
             THEN length(btrim(coalesce(enforcement_object, ''))) > 0
             ELSE enforcement_object IS NULL
        END),
    -- 用例已定义的, 必须写得出是哪个用例（判据 I-总）。
    CONSTRAINT ck_dir_testref CHECK (
        test_state <> 'DEFINED' OR length(btrim(coalesce(test_ref, ''))) > 0),
    -- 未实现／部分实现／语义不符的, 必须写明人工控制 ——
    -- 否则自评里就会出现一个没有任何控制的缺口而不自知。
    -- 实现不齐备就得说清在齐备前靠什么把关。**两种不齐备都算**:
    --   a) 实现本身有缺口（ABSENT／PARTIAL／MISMATCHED）；
    --   b) 实现在、但从没有人验证过它真拦得住（test_state = 'ABSENT'）。
    -- 原先只管 a。b 漏在外面是不对的: 判据 I-总 的要求是每条独立性约束都有自己的
    -- 反例用例, "有触发器"不等于"验证过"; 在用例补齐之前, 自评里必须写明这条靠
    -- 什么发现违规（如 I8 的任命台账月度核对）, 否则这一格就是空着的承诺。
    -- 注意不含 NOT_EXECUTED（用例已写、还没在 CI 里跑过）: 那个的补救是去跑,
    -- 不是补一条线下控制, das_independence_unexecuted 已把它单列。
    CONSTRAINT ck_dir_manual CHECK (
        source_state IS NULL OR
        (source_state = 'PRESENT' AND test_state <> 'ABSENT')
        OR length(btrim(coalesce(manual_control, ''))) > 0)
);

COMMENT ON TABLE das_independence_rule IS
    '判据 I1～I10 的实现状态登记。判据 E2：源码状态、用例状态、执行证据分三栏，不得并成一件（第 5.1 节："用例已定义"不等于"已执行通过"）。判据 I10：父项不带状态，不得笼统称已实现。';
COMMENT ON COLUMN das_independence_rule.source_state IS
    'MISMATCHED 指代码在跑但校验的不是这条要求要的对象（如可签署文件类型一维校验的是 DCMS 文档种类，不是 5 类适航签署事项）——记成 PRESENT 会让自评说谎。';

-- ---------------------------------------------------------------------
-- 2. 执行证据（判据 E2 的第三栏）
-- ---------------------------------------------------------------------
-- 第 5.1 节说明 1: 验收时须分三栏记录, 第三栏是**执行证据（提交号＋批次＋结果）**。
-- 单独建表而不是在规则上加一列, 因为同一条规则会被反复执行, 每次都是一条证据;
-- 压成一列只会留下最后一次, 而"上一次通过、这一次失败"正是最要紧的信息。
CREATE TABLE IF NOT EXISTS das_independence_execution (
    id          bigserial PRIMARY KEY,
    rule_code   text NOT NULL REFERENCES das_independence_rule (code),
    commit_ref  text NOT NULL,              -- 提交号
    batch_ref   text NOT NULL,              -- CI 批次/运行号
    result      text NOT NULL,              -- PASS / FAIL
    evidence_ref text,                      -- 日志或工件位置
    executed_at timestamptz NOT NULL DEFAULT now(),
    recorded_by uuid REFERENCES app_user (id),
    CONSTRAINT ck_die_result CHECK (result IN ('PASS', 'FAIL')),
    CONSTRAINT ck_die_text CHECK (length(btrim(commit_ref)) > 0
        AND length(btrim(batch_ref)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_die_rule ON das_independence_execution (rule_code, executed_at);

COMMENT ON TABLE das_independence_execution IS
    '判据 E2 的第三栏：执行证据（提交号＋批次＋结果）。本迁移不预填任何证据——预填等于自己给自己发合格证。';

-- ---------------------------------------------------------------------
-- 3. 状态变更留痕
-- ---------------------------------------------------------------------
-- 规则状态会随实现进展变好, 所以允许改; 但"什么时候从未实现变成已实现、依据是什么"
-- 本身就是自评材料, 不能改完就没了。
CREATE TABLE IF NOT EXISTS das_independence_rule_change (
    id          bigserial PRIMARY KEY,
    rule_code   text NOT NULL REFERENCES das_independence_rule (code),
    old_source_state text,
    new_source_state text,
    old_test_state   text,
    new_test_state   text,
    old_object  text,
    new_object  text,
    changed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION dcms_log_das_independence_change() RETURNS trigger AS $$
BEGIN
    IF NEW.source_state IS DISTINCT FROM OLD.source_state
       OR NEW.test_state IS DISTINCT FROM OLD.test_state
       OR NEW.enforcement_object IS DISTINCT FROM OLD.enforcement_object THEN
        INSERT INTO das_independence_rule_change
               (rule_code, old_source_state, new_source_state,
                old_test_state, new_test_state, old_object, new_object)
        VALUES (OLD.code, OLD.source_state, NEW.source_state,
                OLD.test_state, NEW.test_state, OLD.enforcement_object,
                NEW.enforcement_object);
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_independence_change ON das_independence_rule;
CREATE TRIGGER trg_das_independence_change
    BEFORE UPDATE ON das_independence_rule
    FOR EACH ROW EXECUTE FUNCTION dcms_log_das_independence_change();

-- ---------------------------------------------------------------------
-- 4. 对系统目录核对实现对象
-- ---------------------------------------------------------------------
-- 声称"由触发器 trg_xxx 保证"而那个触发器已被改名或删掉, 是最难发现的一类假覆盖:
-- 登记册看上去是满的。所以这里直接查系统目录。
CREATE OR REPLACE FUNCTION das_independence_object_present(p_kind text, p_object text)
RETURNS boolean AS $$
BEGIN
    IF p_object IS NULL THEN RETURN NULL; END IF;
    RETURN CASE p_kind
      WHEN 'DB_TRIGGER' THEN EXISTS (SELECT 1 FROM pg_trigger
                                      WHERE tgname = p_object AND NOT tgisinternal)
      WHEN 'DB_CONSTRAINT' THEN EXISTS (SELECT 1 FROM pg_constraint WHERE conname = p_object)
      WHEN 'DB_INDEX' THEN EXISTS (SELECT 1 FROM pg_class
                                    WHERE relname = p_object AND relkind = 'i')
      WHEN 'DB_FUNCTION' THEN EXISTS (SELECT 1 FROM pg_proc WHERE proname = p_object)
      -- 服务层与纸面控制不在数据库目录里, 核不了, 返回 NULL 而不是 false ——
      -- false 会让它看起来像"对象缺失", 那是另一回事。
      ELSE NULL
    END;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION das_independence_object_present(text, text) IS
    '声称由数据库对象保证的，当场查系统目录核对。服务层与纸面控制返回 NULL（核不了，不等于缺失）。';

-- DCMS-INV-083: 声称由数据库对象保证的, 写入时该对象必须真的存在。
-- DCMS-INV-086: 有子维度的父项不得自己带状态（判据 I10：不得笼统称已实现）。
CREATE OR REPLACE FUNCTION dcms_check_das_independence_rule() RETURNS trigger AS $$
DECLARE v_present boolean;
BEGIN
    IF NEW.enforcement_kind IN ('DB_TRIGGER','DB_CONSTRAINT','DB_INDEX','DB_FUNCTION') THEN
        v_present := das_independence_object_present(NEW.enforcement_kind,
                                                     NEW.enforcement_object);
        IF NOT v_present THEN
            RAISE EXCEPTION
                'DCMS-INV-083: % 声称由 % 「%」保证, 但系统目录里没有这个对象。声称有实现而对象不存在, 是最难发现的一类假覆盖 —— 登记册看上去是满的。',
                NEW.code, NEW.enforcement_kind, NEW.enforcement_object
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.parent_code IS NOT NULL AND NEW.parent_code = NEW.code THEN
        RAISE EXCEPTION 'DCMS-INV-086: 规则不得以自己为父项' USING ERRCODE = '23514';
    END IF;
    -- 有子维度的规则不得自己带状态: 状态只能落在叶子上。
    IF NEW.source_state IS NOT NULL AND EXISTS (
            SELECT 1 FROM das_independence_rule WHERE parent_code = NEW.code) THEN
        RAISE EXCEPTION
            'DCMS-INV-086: % 有子维度, 不得自己带实现状态。判据 I10: 四维状态各不相同, **不得笼统称已实现, 也不得笼统计数为"几维已实现"** —— 状态只能落在子维度上。',
            NEW.code USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_independence_rule_check ON das_independence_rule;
CREATE TRIGGER trg_das_independence_rule_check
    BEFORE INSERT OR UPDATE ON das_independence_rule
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_independence_rule();

-- DCMS-INV-084: 执行证据与状态变更只增不删。
CREATE OR REPLACE FUNCTION dcms_guard_das_independence_append() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-084: % 不得删除（自评材料）', TG_TABLE_NAME
            USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'DCMS-INV-084: % 不得改写, 更正请另记一条', TG_TABLE_NAME
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['das_independence_execution', 'das_independence_rule_change']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_append BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_independence_append()',
                       t, t);
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION dcms_guard_das_independence_rule() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'DCMS-INV-084: 独立性规则登记不得删除。规则不再适用时请改状态并写明理由 —— 删掉等于让这条要求从自评里消失。'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_independence_rule_nodel ON das_independence_rule;
CREATE TRIGGER trg_das_independence_rule_nodel
    BEFORE DELETE ON das_independence_rule
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_independence_rule();

-- ---------------------------------------------------------------------
-- 5. 视图
-- ---------------------------------------------------------------------

-- 5.1 三栏矩阵。这是符合性自评要逐条抄的那张表。
CREATE OR REPLACE VIEW das_independence_matrix AS
SELECT r.code, r.parent_code, r.requirement, r.source_doc,
       r.source_state, r.enforcement_kind, r.enforcement_object, r.invariant_code,
       das_independence_object_present(r.enforcement_kind, r.enforcement_object)
                                                                   AS object_present,
       r.test_state, r.test_ref,
       e.executions, e.last_result, e.last_batch_ref, e.last_executed_at,
       r.manual_control, r.gap_note,
       -- 综合判定。刻意不叫"已实现": 三栏齐备才叫 VERIFIED, 少一栏就说少哪一栏。
       CASE WHEN r.source_state IS NULL                     THEN 'CONTAINER'
            WHEN r.source_state = 'ABSENT'                   THEN 'NOT_IMPLEMENTED'
            WHEN r.source_state = 'MISMATCHED'               THEN 'SEMANTIC_MISMATCH'
            WHEN r.enforcement_kind IN ('DB_TRIGGER','DB_CONSTRAINT','DB_INDEX','DB_FUNCTION')
                 AND NOT das_independence_object_present(r.enforcement_kind,
                                                         r.enforcement_object)
                                                             THEN 'OBJECT_MISSING'
            WHEN r.source_state = 'PARTIAL'                  THEN 'PARTIAL'
            WHEN r.test_state <> 'DEFINED'                   THEN 'NO_COUNTER_EXAMPLE'
            WHEN COALESCE(e.executions, 0) = 0               THEN 'NOT_EXECUTED'
            WHEN e.last_result = 'FAIL'                      THEN 'LAST_RUN_FAILED'
            ELSE 'VERIFIED'
       END                                                          AS verdict
  FROM das_independence_rule r
  LEFT JOIN LATERAL (
        SELECT count(*) AS executions,
               (array_agg(result ORDER BY executed_at DESC))[1]     AS last_result,
               (array_agg(batch_ref ORDER BY executed_at DESC))[1]  AS last_batch_ref,
               max(executed_at)                                     AS last_executed_at
          FROM das_independence_execution WHERE rule_code = r.code) e ON true
 ORDER BY r.code;

COMMENT ON VIEW das_independence_matrix IS
    'verdict 刻意不叫"已实现"：三栏齐备才是 VERIFIED，少一栏就说少哪一栏（NO_COUNTER_EXAMPLE／NOT_EXECUTED／OBJECT_MISSING／SEMANTIC_MISMATCH）。判据 E2：三件事不得并成一件。';

-- 5.2 对象缺失: 声称由数据库对象保证, 而目录里找不到那个对象。
--     这张视图平时应当是空的; 它一有行, 说明有人删/改了触发器而登记册还写着它。
CREATE OR REPLACE VIEW das_independence_object_missing AS
SELECT code, requirement, enforcement_kind, enforcement_object, invariant_code
  FROM das_independence_rule
 WHERE enforcement_kind IN ('DB_TRIGGER','DB_CONSTRAINT','DB_INDEX','DB_FUNCTION')
   AND NOT das_independence_object_present(enforcement_kind, enforcement_object)
 ORDER BY code;

COMMENT ON VIEW das_independence_object_missing IS
    '平时应为空。一有行就说明登记册声称的数据库对象已不存在——声称有实现而对象不在，是最难发现的一类假覆盖。';

-- 5.3 缺口清单: 自评里要如实写明的那些, 连人工控制一起列出。
CREATE OR REPLACE VIEW das_independence_gap AS
SELECT m.code, m.requirement, m.source_doc, m.verdict,
       m.source_state, m.test_state, m.manual_control, m.gap_note,
       -- 缺口分三类, **处置方式完全不同**, 所以必须分开标。混在一张表里会让人
       -- 把"去跑一下就行"当成"得写补偿措施", 更糟的是把回归当成已知缺口接受下来。
       --   IMPLEMENTATION 实现或验证本身有缺口（未实现／语义不符／部分／无反例用例）。
       --                  自评里要如实写明未由系统实现或未验证, 并写明在补齐前靠
       --                  什么人工控制把关 —— 这几种 verdict 下 ck_dir_manual
       --                  保证 manual_control 非空。
       --   EVIDENCE       实现齐备、反例用例也写了, 只是这一版还没跑过。补救是去跑,
       --                  不是补线下控制, 所以这类 manual_control 为空是正常的。
       --   DEFECT         登记册与系统**对不上**, 或上一次跑**失败**了。
       --                  这不是"已知缺口", 是当场要查的问题: 要么对象被后续迁移
       --                  删了（OBJECT_MISSING）, 要么原来拦得住的规则现在拦不住了
       --                  （LAST_RUN_FAILED）。这两种下 manual_control 可以为空 ——
       --                  因为本来不该有缺口, 不能靠"补一条线下控制"把它消化掉。
       CASE WHEN m.verdict = 'NOT_EXECUTED'                       THEN 'EVIDENCE'
            WHEN m.verdict IN ('OBJECT_MISSING', 'LAST_RUN_FAILED') THEN 'DEFECT'
            ELSE 'IMPLEMENTATION'
       END                                                          AS gap_kind
  FROM das_independence_matrix m
 WHERE m.verdict NOT IN ('VERIFIED', 'CONTAINER')
 ORDER BY CASE m.verdict
            WHEN 'NOT_IMPLEMENTED'    THEN 1
            WHEN 'SEMANTIC_MISMATCH'  THEN 2
            WHEN 'OBJECT_MISSING'     THEN 3
            WHEN 'LAST_RUN_FAILED'    THEN 4
            WHEN 'PARTIAL'            THEN 5
            WHEN 'NO_COUNTER_EXAMPLE' THEN 6
            ELSE 7
          END, m.code;

-- 5.4 用例已定义但从未执行（判据 E2：两者不是一件事）。
CREATE OR REPLACE VIEW das_independence_unexecuted AS
SELECT code, requirement, test_ref
  FROM das_independence_matrix
 WHERE test_state = 'DEFINED' AND executions = 0
 ORDER BY code;

-- 5.5 按 verdict 汇总。**刻意不给"已实现几条"这个数** ——
--     判据 I10 禁止笼统计数, 给一个总数就是把八种不同的状态压成一个数字。
CREATE OR REPLACE VIEW das_independence_summary AS
SELECT verdict, count(*) AS rules,
       string_agg(code, '、' ORDER BY code) AS codes
  FROM das_independence_matrix
 GROUP BY verdict
 ORDER BY count(*) DESC;

COMMENT ON VIEW das_independence_summary IS
    '按 verdict 分组列出，不给"已实现几条"的总数——判据 I10 禁止笼统计数，一个总数会把八种不同状态压成一个数字。';

-- 5.5b 容器声明与实际子项不一致: 声明为容器却没有子维度, 或有子维度却没声明。
--      平时应为空。它一有行, 说明登记册的层级与状态落点对不上 ——
--      判据 I10 禁止的"笼统称已实现"正是从这种错位开始的。
CREATE OR REPLACE VIEW das_independence_container_mismatch AS
SELECT r.code, r.is_container,
       (SELECT count(*) FROM das_independence_rule c WHERE c.parent_code = r.code)
                                                                   AS children,
       CASE WHEN r.is_container THEN '声明为容器但没有子维度'
            ELSE '有子维度却未声明为容器（状态不该落在它自己身上）' END AS issue
  FROM das_independence_rule r
 WHERE r.is_container <> EXISTS (SELECT 1 FROM das_independence_rule c
                                  WHERE c.parent_code = r.code)
 ORDER BY r.code;

-- 5.6 互为备份（判据 I5 的可见性, 不是阻断）。
--     I5 原文是"不得互为备份**并**相互核查对方编制的资料" —— 禁的是合取。
--     5～8 人编制下互为备份常常避不开, 硬禁前半截会过度约束; 后半截"相互核查
--     对方编制的资料"系统判不了(它不知道某份资料是谁编的)。所以这里只让它可见,
--     由独立监督去核对后半截。登记册上 I5 如实记为 PARTIAL。
CREATE OR REPLACE VIEW das_independence_mutual_backup AS
SELECT a.user_id AS user_a, ua.full_name AS name_a, ua.employee_no AS emp_a,
       b.user_id AS user_b, ub.full_name AS name_b, ub.employee_no AS emp_b,
       a.level, a.file_type_code, a.discipline_code
  FROM signer_authorization a
  JOIN signer_authorization b
    ON b.user_id = a.backup_user_id AND b.backup_user_id = a.user_id
  JOIN app_user ua ON ua.id = a.user_id
  JOIN app_user ub ON ub.id = b.user_id
 WHERE a.revoked_at IS NULL AND b.revoked_at IS NULL
   AND a.user_id < b.user_id
 ORDER BY ua.full_name;

COMMENT ON VIEW das_independence_mutual_backup IS
    '判据 I5 的可见性视图，不是阻断。I5 禁的是"互为备份**并**相互核查对方编制的资料"这个合取；5～8 人编制下互为备份常避不开，而"相互核查对方编制的资料"系统判不了——它不知道某份资料是谁编的。由独立监督核对后半截。';

-- ---------------------------------------------------------------------
-- 6. 判据 I1～I10 的当前状态
-- ---------------------------------------------------------------------
-- 按**当前实际**填, 不照抄设计输入第 5.1 节的旧快照 —— 那张表写于 0028 时期,
-- 此后 0031（工号唯一）、0032（岗位互斥）、0034（三签按工号）、0037（关闭独立性）、
-- 0038（审核员自审）陆续落地, 多条已从"无"变成"有"。
-- 对象名逐个对着系统目录核过; 对不上的会被 DCMS-INV-083 当场拒绝。
--
-- 执行证据一栏**一律留空**: 本迁移不预填, 由 CI 或人工按提交号＋批次＋结果登记
-- (判据 E2 第三栏)。因此下面多数行的 verdict 会是 NOT_EXECUTED —— 这是如实状态,
-- 不是遗漏。
-- 容器项先插: 它们是子维度的父项, 状态一律落在子项上（判据 I10）。
INSERT INTO das_independence_rule
    (code, requirement, source_doc, is_container, gap_note)
VALUES
('I3', '编、审、批必须三个不同自然人（体系文件、程序、表单、设计文件）',
 'UG-DAP-01', true, '四类对象的实现状态不同，状态落在子项上'),
('I10', '越权签署无效；签署时校验有效期、专业、可签署文件类型、具体产品范围',
 'UG-DAP-03 第 8 节', true,
 '四维状态各不相同，不得笼统称已实现，也不得笼统计数为「几维已实现」；状态落在四个子维度上')
ON CONFLICT (code) DO NOTHING;

INSERT INTO das_independence_rule
    (code, parent_code, requirement, source_doc, source_state, enforcement_kind,
     enforcement_object, invariant_code, test_state, test_ref, manual_control, gap_note)
VALUES
('I1', NULL,
 '独立监督负责人不得由适航管理负责人或其他运行管理人员担任',
 '手册 3.1', 'PRESENT', 'DB_TRIGGER', 'trg_das_appointment_check',
 'DCMS-INV-032', 'DEFINED',
 'ci/test-das-appointments.py：「已任被监督运行岗者不得再任独立监督负责人」「两个方向都判」',
 NULL, NULL),

('I2', NULL,
 'CVE 不得核查本人编制或技术审批过的资料',
 '手册 3.2、UG-DAP-01', 'PARTIAL', 'SERVICE_CHECK',
 'das_qualification.assess（indep_no_self_check 必须勾选）', NULL, 'DEFINED',
 'ci/test-das-qualification.py：「步骤4 同意授权而独立性两项未全勾被拒」',
 'UG-DAP-03 步骤 4 线下逐份核对编制人与核查人，结果记入 UG-DAF-06',
 '勾选是**声明**不是校验：系统不知道某份资料是谁编的，无法在核查时判断核查人是否正是编制人。'
 '要做成校验，须先有资料的编制人字段与 CVE 核查对象的关联（属 M3 项目实体）。'),

('I3.QUALIFICATION_FORM', 'I3',
 'UG-DAF-06 授权人员评估表的编制、审核、批准须为三个不同自然人',
 'UG-DAP-01、UG-DAP-03', 'PRESENT', 'DB_TRIGGER', 'trg_das_qualification_check',
 'DCMS-INV-036', 'DEFINED',
 'ci/test-das-qualification.py：「审核与批准为同一人被拒」「被评估人不得签本人评估表」',
 NULL,
 '按**工号**判自然人，不是按账号（迁移 0031 的 uq_app_user_employee_no 使之成立）'),

('I3.DOCUMENT_SIGNOFF', 'I3',
 '三级签署的编制、审核、批准须为三个不同自然人',
 'UG-DAP-01', 'PARTIAL', 'DB_TRIGGER', 'trg_approval_step_people_order',
 NULL, 'DEFINED',
 'ci/test-signoff.py：「编制人不在候选人里」「同一人不得指派两步」「审核未过不得处理批准步」；'
 'ci/test-signer-boundaries.py 判据 A1-2：「同一人持两个不同工号时三签照样成立」（已知缺口用例）',
 '一自然人一账号由线下人员管理保证；UG-DAM-01-附2 的授权人员名单为受控依据',
 '比较的是**账号**（requester_id／assignee_user_id／decided_by），不是自然人。'
 'employee_no 已唯一（0031）但仍可为空，所以同一人持一个有工号、一个无工号的账号仍能通过。'
 '彻底闭合须 employee_no 非空，而存量账号补齐工号属数据治理工作'),

('I4', NULL,
 'CVE 兼任授权人员时，编制人与核查／签署人仍必须不同（至少两人）',
 'UG-DAP-01', 'PARTIAL', 'DB_TRIGGER', 'trg_approval_step_people_order',
 NULL, 'DEFINED',
 'ci/test-signoff.py：「同一人不得指派两步」',
 '同 I3.DOCUMENT_SIGNOFF：由受控人员名单与线下核对保证',
 '与 I3.DOCUMENT_SIGNOFF 同一实现，同一缺口：比的是账号不是自然人'),

('I5', NULL,
 '两名人员不得互为备份并相互核查对方编制的资料',
 'UG-DAP-03', 'PARTIAL', 'DB_CONSTRAINT', 'ck_signer_backup_not_self',
 NULL, 'DEFINED',
 'ci/test-signer-boundaries.py：「备份人不得是本人」；判据 CV1「撤销他人的备份人被拒」',
 '互为备份的组合由 das_independence_mutual_backup 视图列出，交独立监督按 UG-DAP-13 核对'
 '其是否同时相互核查对方编制的资料',
 '原文禁的是"互为备份**并**相互核查对方编制的资料"这个**合取**。'
 '现只拦住了"自己做自己的备份"。硬禁互为备份会过度约束——5～8 人编制下常常避不开；'
 '而后半截系统判不了，它不知道某份资料是谁编的。故做成可见性而非阻断，I5 如实记为 PARTIAL'),

('I6', NULL,
 '责任部门负责人不得自行验证并关闭本部门的不符合项',
 '手册 3.1、UG-DAP-14', 'PRESENT', 'DB_TRIGGER', 'trg_das_ncr_closure_check',
 'DCMS-INV-047', 'DEFINED',
 'ci/test-das-ncr.py：「验证人不得是整改责任人」（含责任部门负责人本人一例）',
 NULL,
 '验证人与三栏措施责任人、责任部门负责人逐一比对；涉及独立监督职能自身的另需'
 '责任经理指定记录（DCMS-INV-049）'),

('I7', NULL,
 '独立监督负责人不得审核独立监督职能本身',
 'UG-DAP-13', 'PRESENT', 'DB_TRIGGER', 'trg_das_ism_function_audit_check',
 'DCMS-INV-060', 'DEFINED',
 'ci/test-das-audit.py：「实施人持独立监督负责人任命被拒」「组织人须在任责任经理」',
 NULL,
 '另按 UG-DAP-13 步骤 10：非外部审核员时须有责任经理指定依据且亲自主持'),

('I8', NULL,
 '事件报告负责人不得由独立监督负责人兼任',
 '手册 3.1', 'PRESENT', 'DB_TRIGGER', 'trg_das_appointment_check',
 'DCMS-INV-033', 'ABSENT', NULL,
 '任命时由 das_position_exclusion 的 ISM-ORM 互斥对拦截；在补齐用例前，'
 '该组合是否出现由任命台账月度核对发现',
 '互斥对数据在 0032 已种入（ISM-ORM），触发器也会拦，但**没有针对这一对的反例用例**。'
 '判据 I-总 要求每条独立性约束都有自己的反例用例——有实现不等于有用例'),

('I9', NULL,
 '任何人不得为自己授权',
 'UG-DAP-03', 'PRESENT', 'SERVICE_CHECK',
 'signers.grant（user_id = actor 时拒绝）', NULL, 'DEFINED',
 'ci/test-signoff.py：为自己授权被拒（400）',
 NULL,
 '服务层校验。绕过服务层直写数据库不受此限，但签署授权的唯一写入路径是该服务'),

('I10.VALIDITY', 'I10',
 '签署时校验授权的有效期与撤销状态',
 'UG-DAP-03 第 8 节', 'PRESENT', 'SERVICE_CHECK',
 'signers.is_authorized（valid_from／valid_to／revoked_at）', NULL, 'DEFINED',
 'ci/test-signer-boundaries.py：「生效日期未到」「昨天到期」「起止日均为今天」'
 '「过期后重新授权」「生效日期未到的授权同样阻挡重复授权」；'
 'ci/test-signoff.py 撤销相关 5 条',
 NULL,
 '过期与生效日期边界用例已补齐（原第 5.1 节记为"仍缺"）'),

('I10.DISCIPLINE', 'I10',
 '签署时校验专业（D01～D16）',
 'UG-DAP-03 第 8 节', 'PARTIAL', 'SERVICE_CHECK',
 'signers.is_authorized（discipline_code）', NULL, 'ABSENT', NULL,
 '调用方未传专业的路径由线下按纸面授权书的专业范围把关；'
 '专业维度在补齐用例前**不得计为已实现**',
 '调用方未传专业时不施加专业限制（SQL 的 %%s::text IS NULL 分支）。'
 '该行为既无反例用例，也未逐条排查哪些调用方未传专业'),

('I10.FILE_TYPE', 'I10',
 '签署时校验可签署文件类型（UG-DAM-01-附2 的 5 类适航签署事项）',
 'UG-DAP-03 第 8 节、UG-DAM-01-附2', 'MISMATCHED', 'SERVICE_CHECK',
 'signers.is_authorized（file_type_code）', NULL, 'NOT_APPLICABLE', NULL,
 '适航签署事项由纸面授权书界定；界面不得以文档种类下拉框冒充签署事项',
 '代码在跑, 但校验的是 DCMS 的**文档种类**（DWG／SPEC／QTP……），'
 '不是附2 的 5 类**适航签署事项**（更改分类／符合性声明／制造符合性声明／小改批准／CVE 核查）。'
 '语义不符，测了也不证明签署事项受控；两者的映射属待澄清项第 1 条，待 M3／M4'),

('I10.PRODUCT_SCOPE', 'I10',
 '签署时校验具体产品／型号／件号范围及限制',
 'UG-DAP-03 第 8 节、手册 3.2', 'ABSENT', 'NONE', NULL, NULL, 'ABSENT', NULL,
 '手册 3.2「未列入的产品类别不得签署」**完全由纸面授权书把关**；'
 '符合性自评须如实写明未由系统实现',
 '自由文本 product_scope 已于提交 1f39b89 移除（理由见设计输入第九之二节：'
 '一个不被任何校验读取的字段比没有这个字段更糟——它让人以为范围受控）')
ON CONFLICT (code) DO NOTHING;
