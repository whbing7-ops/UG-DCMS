-- 记录的保存、冻结与销毁（基础能力）。
--
-- 依据 UG-DAW-004《记录保存期限表》第 1 章通则与第 2 章期限表（97 类记录）、
-- UG-DAP-01 第 6 章步骤 16、CCAR-21.137（十三），
-- UG-RPT-2026-003 第六之三节判据 R1～R8 与 R-总。
--
-- 【判据 R-总: 销毁是带前置条件的受控动作, 不是定时任务】
-- 这是整块最容易做错的地方。把销毁实现成"创建日期 + 固定年限"的定时删除, 会在
-- 冻结期、关联期限未满、缺双人批准的情况下照删不误 —— 那是设计错误, 不是配置问题。
-- 所以这里**没有任何自动删除**。到期只是让记录出现在候选清单里; 销毁要走
-- 三项核查 + 双人批准, 每一步都留痕。
--
-- 【判据 R2: 保留截止日是计算值, 不是记录自身的创建日期加年限】
-- UG-DAW-004 通则: 与在役产品、设计符合性、历史授权签署或持续适航责任相关的记录,
-- 保存至关联项目／产品要求的期限, **以较长者为准**。所以 das_retention_until()
-- 取"本记录自身期限"与"全部关联对象期限"的最大值。只按自身算, 一条关联到在役产品
-- 的 5 年记录会在产品还在飞的时候到期。
--
-- 【期限栏的 8 种表述不是 8 个年限, 是不同的锚点】
-- 从 UG-DAW-004 第 2 章逐行抽出的 97 行里, 期限栏只有 8 种写法, 但它们的**锚点
-- 各不相同**:
--   · 5 年                  → 自创建起算
--   · 授权终止后 5 年       → 自该授权终止起算
--   · 工具停用后 5 年       → 自工具停用起算
--   · 随文件(新版发布后旧版再留 5 年) → 自新版发布起算
--   · 长期                  → 责任存续期间, 没有偏移(判据 R3)
--   · 长期；关联产品记录期限较长的从其规定 → 同上, 且文档自己写出了 R2
--   · 产品/设计全生命周期   → 由关联产品决定
--   · 至产品永久退役        → 由产品退役决定(判据 R8、L9)
-- 把它们统一成"年限"就丢掉了锚点, 而判据 R2 要的恰恰是锚点。
-- 期限栏原文逐字保留在 raw_period_text: 映射对不对, 要能对着原文复核。
--
-- 【锚点未发生时截止日算不出来, 而"算不出来"不等于"可以销毁"】
-- 授权还没终止, "授权终止后 5 年"就没有起算点; 产品还在役, "全生命周期"就没有终点。
-- 这些记录的 retention_until 为 NULL。NULL 一律按**不得销毁**处理(DCMS-INV-078)
-- —— 反过来理解成"没有期限所以随便删"是最危险的一种读法。
--
-- 【判据 R3: "长期"不是"永远", 也不是"无限期不管"】
-- 它指责任存续期间; 责任终止或转让时须**可追溯移交**。所以单独建移交记录表,
-- 没有移交记录的"长期"类记录不得销毁。
--
-- 【判据 R7: 从备份恢复不得让已批准销毁的记录复活】
-- 恢复要重新跑一遍保留与访问条件核验。做不到这一点时, 一次恢复就能把销毁决定
-- 整批撤销, 而没有任何人知道。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 记录类别（UG-DAW-004 第 2 章，97 类）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_retention_class (
    code             text PRIMARY KEY,
    name_cn          text NOT NULL,
    source_procedure text NOT NULL,
    custodian_position text NOT NULL,
    medium           text NOT NULL,          -- ELECTRONIC / BOTH
    -- 锚点: 期限从哪个事件起算（判据 R2 的核心）
    anchor_kind      text NOT NULL,
    offset_years     numeric(4,1),
    -- 期限栏原文逐字保留: 映射对不对要能对着原文复核（沿用 0029 的做法）
    raw_period_text  text NOT NULL,
    -- 判据 R1: 表列期限是**最低内部期限**, 法规要求更长时以法规为准
    is_minimum       boolean NOT NULL DEFAULT true,
    statutory_basis  text,
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_drc_medium CHECK (medium IN ('ELECTRONIC', 'PAPER', 'BOTH')),
    CONSTRAINT ck_drc_anchor CHECK (anchor_kind IN
        ('CREATED',              -- 自创建起算
         'AUTHORISATION_END',    -- 授权终止
         'TOOL_DECOMMISSION',    -- 工具停用
         'NEW_VERSION_RELEASE',  -- 新版发布
         'RESPONSIBILITY',       -- 责任存续期间（长期）
         'PRODUCT_LIFECYCLE',    -- 产品/设计全生命周期
         'PRODUCT_RETIREMENT')), -- 至产品永久退役
    -- 有偏移的锚点必须是"某个事件之后 N 年"那一类; 没有偏移的是由关联对象决定终点。
    CONSTRAINT ck_drc_offset CHECK (
        CASE WHEN anchor_kind IN ('CREATED','AUTHORISATION_END','TOOL_DECOMMISSION',
                                  'NEW_VERSION_RELEASE')
             THEN offset_years IS NOT NULL AND offset_years > 0
             ELSE offset_years IS NULL
        END),
    CONSTRAINT ck_drc_text CHECK (length(btrim(name_cn)) > 0
        AND length(btrim(raw_period_text)) > 0)
);

COMMENT ON TABLE das_retention_class IS
    'UG-DAW-004 第 2 章的 97 类记录。期限栏的 8 种表述对应 7 种锚点——把它们统一成"年限"会丢掉锚点，而判据 R2 要的正是锚点。raw_period_text 保留原文供复核。';
COMMENT ON COLUMN das_retention_class.is_minimum IS
    '判据 R1：表列期限是最低内部期限，同时须满足适用法规；法规要求更长时以法规为准。';

-- ---------------------------------------------------------------------
-- 2. 受管记录与关联
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_retention_record (
    id          bigserial PRIMARY KEY,
    class_code  text NOT NULL REFERENCES das_retention_class (code),
    object_type text NOT NULL,          -- 指向系统内的实体, 如 DAS_OCCURRENCE
    object_id   text NOT NULL,
    label       text,                   -- 清单核对时人看的标识
    created_on  date NOT NULL DEFAULT current_date,
    -- 锚点事件的实际发生时刻。CREATED 类用 created_on; 其余类在事件发生时回填。
    anchor_on   date,
    destroyed_at timestamptz,
    destruction_id bigint,              -- 指向批次, 外键在批次表建好后补
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (object_type, object_id, class_code),
    CONSTRAINT ck_drr_obj CHECK (length(btrim(object_type)) > 0
        AND length(btrim(object_id)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_drr_class ON das_retention_record (class_code);

-- 关联对象: 判据 R2 的"以较长者为准"靠它算。
-- 一条记录可以关联多个对象(项目、产品、授权), 每个对象各自带一个要求的期限。
CREATE TABLE IF NOT EXISTS das_retention_link (
    id          bigserial PRIMARY KEY,
    record_id   bigint NOT NULL REFERENCES das_retention_record (id),
    link_kind   text NOT NULL,          -- PRODUCT / PROJECT / AUTHORISATION / RESPONSIBILITY
    link_ref    text NOT NULL,          -- 该对象的标识
    -- 该关联对象要求保存到哪一天。在役产品、未终止授权时为空 ——
    -- 空不是"没有要求", 是"终点还没到"(见 DCMS-INV-078)。
    required_until date,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_drl_kind CHECK (link_kind IN
        ('PRODUCT','PROJECT','AUTHORISATION','RESPONSIBILITY','TOOL','DOCUMENT')),
    CONSTRAINT ck_drl_ref CHECK (length(btrim(link_ref)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_drl_record ON das_retention_link (record_id);

COMMENT ON TABLE das_retention_link IS
    '判据 R2：与在役产品、设计符合性、历史授权签署或持续适航责任相关的记录，保存至关联对象要求的期限，以较长者为准。required_until 为空表示该对象的终点还没到，不是没有要求。';

-- ---------------------------------------------------------------------
-- 3. 责任移交（判据 R3）
-- ---------------------------------------------------------------------
-- "长期"指责任存续期间, 责任终止或转让时须可追溯移交。没有这张表, "长期"就会
-- 变成"永远不管": 既不会销毁, 也没人知道责任还在不在谁手上。
CREATE TABLE IF NOT EXISTS das_responsibility_handover (
    id            bigserial PRIMARY KEY,
    scope_ref     text NOT NULL,        -- 责任范围（产品、项目、职能）
    event_kind    text NOT NULL,        -- TERMINATED 终止 / TRANSFERRED 转让
    effective_on  date NOT NULL,
    transferred_to text,                -- 转让时的接收方
    handover_ref  text NOT NULL,        -- 移交清单/协议编号
    evidence_ref  text NOT NULL,        -- 移交证据留存位置
    recorded_by   uuid NOT NULL REFERENCES app_user (id),
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_drh_kind CHECK (event_kind IN ('TERMINATED','TRANSFERRED')),
    CONSTRAINT ck_drh_text CHECK (length(btrim(scope_ref)) > 0
        AND length(btrim(handover_ref)) > 0 AND length(btrim(evidence_ref)) > 0),
    -- 转让必须写明接收方: 不写接收方的"转让"等于无人接手, 那是终止不是转让。
    CONSTRAINT ck_drh_to CHECK (event_kind <> 'TRANSFERRED'
        OR length(btrim(coalesce(transferred_to, ''))) > 0)
);

COMMENT ON TABLE das_responsibility_handover IS
    'UG-DAW-004 通则：「长期」指责任存续期间持续保存，终止或转让时安排可追溯移交，不得因停业自动销毁（判据 R3）。';

-- ---------------------------------------------------------------------
-- 4. 冻结（判据 R5）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_retention_freeze (
    id            bigserial PRIMARY KEY,
    -- 冻结可以按单条记录, 也可以按范围(某产品、某项目的全部记录)。
    record_id     bigint REFERENCES das_retention_record (id),
    scope_kind    text,                 -- 范围冻结时: PRODUCT / PROJECT / CLASS
    scope_ref     text,
    freeze_kind   text NOT NULL,        -- INVESTIGATION / LITIGATION / DISPUTE / CAAC_REQUEST / OPEN_ITEM
    reason        text NOT NULL,
    -- 判据 R5: 冻结须记录原因、发起人、发起时间、**解除条件**。
    -- 没有解除条件的冻结会永远挂着, 而"永远挂着"与"没冻结"一样不可管理。
    release_condition text NOT NULL,
    initiated_by  uuid NOT NULL REFERENCES app_user (id),
    initiated_at  timestamptz NOT NULL DEFAULT now(),
    released_at   timestamptz,
    released_by   uuid REFERENCES app_user (id),
    release_note  text,
    CONSTRAINT ck_drf_kind CHECK (freeze_kind IN
        ('INVESTIGATION','LITIGATION','DISPUTE','CAAC_REQUEST','OPEN_ITEM')),
    CONSTRAINT ck_drf_text CHECK (length(btrim(reason)) > 0
        AND length(btrim(release_condition)) > 0),
    CONSTRAINT ck_drf_target CHECK (
        (record_id IS NOT NULL AND scope_kind IS NULL AND scope_ref IS NULL)
        OR (record_id IS NULL AND scope_kind IS NOT NULL AND scope_ref IS NOT NULL)),
    CONSTRAINT ck_drf_scope CHECK (scope_kind IS NULL
        OR scope_kind IN ('PRODUCT','PROJECT','CLASS')),
    CONSTRAINT ck_drf_release CHECK (
        (released_at IS NULL AND released_by IS NULL)
        OR (released_at IS NOT NULL AND released_by IS NOT NULL
            AND length(btrim(coalesce(release_note, ''))) > 0))
);

CREATE INDEX IF NOT EXISTS ix_drf_record ON das_retention_freeze (record_id)
    WHERE released_at IS NULL;

COMMENT ON TABLE das_retention_freeze IS
    '判据 R5：调查、诉讼、争议、局方要求和未关闭事项期间不得销毁。须记录原因、发起人、发起时间与解除条件；冻结期间的销毁动作被拒绝而不是被跳过（DCMS-INV-075）。';

-- ---------------------------------------------------------------------
-- 5. 销毁批次（判据 R4、R6）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_retention_destruction (
    id            bigserial PRIMARY KEY,
    batch_ref     text NOT NULL UNIQUE,
    -- 判据 R4 的三项核查, 每项都要留结果 —— 只记一个"已核查"布尔值等于没核查。
    inventory_check_result       text,
    inventory_checked_by         uuid REFERENCES app_user (id),
    inventory_checked_at         timestamptz,
    related_period_check_result  text,
    related_period_checked_by    uuid REFERENCES app_user (id),
    related_period_checked_at    timestamptz,
    freeze_check_result          text,
    freeze_checked_by            uuid REFERENCES app_user (id),
    freeze_checked_at            timestamptz,
    -- 判据 R6: 资料管理负责人与适航管理负责人**两人**批准, 不得一人兼任两角完成。
    approved_by_dcm uuid REFERENCES app_user (id),
    approved_by_dcm_at timestamptz,
    approved_by_awm uuid REFERENCES app_user (id),
    approved_by_awm_at timestamptz,
    executed_at   timestamptz,
    executed_by   uuid REFERENCES app_user (id),
    created_by    uuid NOT NULL REFERENCES app_user (id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_drd_ref CHECK (length(btrim(batch_ref)) > 0),
    CONSTRAINT ck_drd_inv CHECK (
        (inventory_checked_by IS NULL AND inventory_checked_at IS NULL)
        OR (inventory_checked_by IS NOT NULL AND inventory_checked_at IS NOT NULL
            AND length(btrim(coalesce(inventory_check_result, ''))) > 0)),
    CONSTRAINT ck_drd_rel CHECK (
        (related_period_checked_by IS NULL AND related_period_checked_at IS NULL)
        OR (related_period_checked_by IS NOT NULL AND related_period_checked_at IS NOT NULL
            AND length(btrim(coalesce(related_period_check_result, ''))) > 0)),
    CONSTRAINT ck_drd_frz CHECK (
        (freeze_checked_by IS NULL AND freeze_checked_at IS NULL)
        OR (freeze_checked_by IS NOT NULL AND freeze_checked_at IS NOT NULL
            AND length(btrim(coalesce(freeze_check_result, ''))) > 0)),
    CONSTRAINT ck_drd_dcm CHECK (
        (approved_by_dcm IS NULL AND approved_by_dcm_at IS NULL)
        OR (approved_by_dcm IS NOT NULL AND approved_by_dcm_at IS NOT NULL)),
    CONSTRAINT ck_drd_awm CHECK (
        (approved_by_awm IS NULL AND approved_by_awm_at IS NULL)
        OR (approved_by_awm IS NOT NULL AND approved_by_awm_at IS NOT NULL))
);

COMMENT ON TABLE das_retention_destruction IS
    'UG-DAW-004 通则 / UG-DAP-01 步骤 16：销毁须经清单核对、关联期限核查、冻结状态核查及批准（判据 R4）；两人批准是硬要求（判据 R6）。销毁清单本身按 5 年保存。';

CREATE TABLE IF NOT EXISTS das_retention_destruction_item (
    destruction_id bigint NOT NULL REFERENCES das_retention_destruction (id),
    record_id      bigint NOT NULL REFERENCES das_retention_record (id),
    retention_until_at_approval date,   -- 批准时算出的截止日, 留作对照
    PRIMARY KEY (destruction_id, record_id)
);

ALTER TABLE das_retention_record
    DROP CONSTRAINT IF EXISTS fk_drr_destruction;
ALTER TABLE das_retention_record
    ADD CONSTRAINT fk_drr_destruction FOREIGN KEY (destruction_id)
        REFERENCES das_retention_destruction (id);

-- ---------------------------------------------------------------------
-- 6. 从备份恢复的重新核验（判据 R7）
-- ---------------------------------------------------------------------
-- 共享备份不得为删除单项记录破坏整体可恢复性, 所以备份里仍然有已批准销毁的记录。
-- 恢复时必须重新跑一遍保留与访问条件核验, 并标明哪些是已批准销毁项 ——
-- 做不到这一点, 一次恢复就能把销毁决定整批撤销, 而没有任何人知道。
CREATE TABLE IF NOT EXISTS das_retention_restore_check (
    id            bigserial PRIMARY KEY,
    restore_ref   text NOT NULL,        -- 恢复作业编号
    backup_ref    text NOT NULL,        -- 恢复所用备份
    restored_at   timestamptz NOT NULL DEFAULT now(),
    -- 判据 R7 的两项重新核验, 各留结果
    retention_recheck_result text NOT NULL,
    access_recheck_result    text NOT NULL,
    -- 恢复清单中标明的已批准销毁项及限制
    destroyed_items_noted    int  NOT NULL DEFAULT 0,
    destroyed_items_note     text,
    checked_by    uuid NOT NULL REFERENCES app_user (id),
    CONSTRAINT ck_drrc_text CHECK (length(btrim(restore_ref)) > 0
        AND length(btrim(backup_ref)) > 0
        AND length(btrim(retention_recheck_result)) > 0
        AND length(btrim(access_recheck_result)) > 0),
    -- 清单里有已批准销毁项时, 必须写明限制; 只记个数等于没标明。
    CONSTRAINT ck_drrc_destroyed CHECK (destroyed_items_noted = 0
        OR length(btrim(coalesce(destroyed_items_note, ''))) > 0)
);

COMMENT ON TABLE das_retention_restore_check IS
    '判据 R7：从备份恢复时重新核验保留与访问条件，并在恢复清单中标明已批准销毁项及限制——否则一次恢复就能让已销毁的记录复活且可访问。';

-- ---------------------------------------------------------------------
-- 7. 保留截止日：计算值（判据 R2）
-- ---------------------------------------------------------------------

-- 7.1 本记录自身期限算出的截止日。锚点事件未发生时为 NULL。
CREATE OR REPLACE FUNCTION das_retention_own_until(p_record_id bigint)
RETURNS date AS $$
DECLARE r das_retention_record%ROWTYPE; c das_retention_class%ROWTYPE;
BEGIN
    SELECT * INTO r FROM das_retention_record WHERE id = p_record_id;
    IF r.id IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO c FROM das_retention_class WHERE code = r.class_code;
    IF c.offset_years IS NULL THEN
        -- 长期／全生命周期／至永久退役: 终点由关联对象决定, 自身算不出来。
        RETURN NULL;
    END IF;
    IF c.anchor_kind = 'CREATED' THEN
        RETURN (r.created_on + (c.offset_years::text || ' years')::interval)::date;
    END IF;
    -- 其余锚点要等事件发生并回填 anchor_on。
    IF r.anchor_on IS NULL THEN RETURN NULL; END IF;
    RETURN (r.anchor_on + (c.offset_years::text || ' years')::interval)::date;
END;
$$ LANGUAGE plpgsql STABLE;

-- 7.2 判据 R2: 本记录期限与全部关联对象期限取**最大**。
--     任一关联对象的 required_until 为空(在役产品、未终止授权)时返回 NULL,
--     表示"终点还没到" —— 而 NULL 一律按不得销毁处理。
CREATE OR REPLACE FUNCTION das_retention_until(p_record_id bigint)
RETURNS date AS $$
DECLARE v_own date; v_links int; v_open int; v_max date;
BEGIN
    v_own := das_retention_own_until(p_record_id);
    SELECT count(*), count(*) FILTER (WHERE required_until IS NULL)
      INTO v_links, v_open FROM das_retention_link WHERE record_id = p_record_id;
    IF v_open > 0 THEN
        -- 有关联对象的终点还没到: 整条记录就没有截止日。
        RETURN NULL;
    END IF;
    SELECT max(required_until) INTO v_max FROM das_retention_link
     WHERE record_id = p_record_id;
    IF v_own IS NULL AND v_links = 0 THEN
        -- 自身算不出来又没有关联: 长期类而责任未终止, 同样没有截止日。
        RETURN NULL;
    END IF;
    RETURN GREATEST(COALESCE(v_own, v_max), COALESCE(v_max, v_own));
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION das_retention_until(bigint) IS
    '判据 R2：保留截止日是计算值——本记录期限与全部关联对象期限取最大。任一关联对象终点未到时返回 NULL，而 NULL 按不得销毁处理（DCMS-INV-078）。';

-- 7.3 某条记录当前是否被冻结（单条冻结或范围冻结）。
CREATE OR REPLACE FUNCTION das_retention_frozen(p_record_id bigint)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM das_retention_freeze f
         WHERE f.released_at IS NULL
           AND (f.record_id = p_record_id
                OR (f.scope_kind = 'CLASS' AND f.scope_ref =
                        (SELECT class_code FROM das_retention_record WHERE id = p_record_id))
                OR (f.scope_kind IN ('PRODUCT','PROJECT') AND EXISTS (
                        SELECT 1 FROM das_retention_link l
                         WHERE l.record_id = p_record_id
                           AND l.link_kind = f.scope_kind AND l.link_ref = f.scope_ref))));
$$ LANGUAGE sql STABLE;

-- ---------------------------------------------------------------------
-- 8. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-075: 冻结期间不得销毁（判据 R5：拒绝，不是跳过）。
-- DCMS-INV-076: 三项核查缺一不得销毁（判据 R4）。
-- DCMS-INV-077: 双人批准须为两个不同自然人（判据 R6，按工号判）。
-- DCMS-INV-078: 保留截止日未到或算不出来的不得销毁（判据 R2）。
-- DCMS-INV-079: 「长期」类须先有责任终止／转让的移交记录（判据 R3）。
CREATE OR REPLACE FUNCTION dcms_check_das_destruction_execute() RETURNS trigger AS $$
DECLARE
    d das_retention_destruction%ROWTYPE;
    v_emp_dcm text; v_emp_awm text;
    v_rec record; v_until date;
BEGIN
    IF NEW.executed_at IS NULL THEN
        RETURN NEW;
    END IF;
    d := NEW;

    -- 判据 R4: 三项核查
    IF d.inventory_checked_at IS NULL OR d.related_period_checked_at IS NULL
       OR d.freeze_checked_at IS NULL THEN
        RAISE EXCEPTION
            'DCMS-INV-076: 销毁前的三项核查缺一不得执行（清单核对=%, 关联期限核查=%, 冻结状态核查=%）。UG-DAW-004 通则要求三项都做并留结果 —— 只记一个"已核查"等于没核查。',
            (d.inventory_checked_at IS NOT NULL), (d.related_period_checked_at IS NOT NULL),
            (d.freeze_checked_at IS NOT NULL) USING ERRCODE = '23514';
    END IF;

    -- 判据 R6: 双人批准, 且为两个不同自然人
    IF d.approved_by_dcm IS NULL OR d.approved_by_awm IS NULL THEN
        RAISE EXCEPTION
            'DCMS-INV-077: 销毁须经资料管理负责人与适航管理负责人两人批准（UG-DAW-004、UG-DAP-01 步骤 16）'
            USING ERRCODE = '23514';
    END IF;
    SELECT employee_no INTO v_emp_dcm FROM app_user WHERE id = d.approved_by_dcm;
    SELECT employee_no INTO v_emp_awm FROM app_user WHERE id = d.approved_by_awm;
    IF v_emp_dcm IS NULL OR v_emp_awm IS NULL THEN
        RAISE EXCEPTION
            'DCMS-INV-077: 批准人未填工号。工号是判定"两个不同自然人"的唯一依据（判据 A1）, 未填无从判断双人批准是否成立。'
            USING ERRCODE = '23514';
    END IF;
    IF v_emp_dcm = v_emp_awm THEN
        RAISE EXCEPTION
            'DCMS-INV-077: 双人批准不成立——两个批准人是同一自然人（工号 %）。判据 R6: 不能由一人兼任两个角色完成。',
            v_emp_dcm USING ERRCODE = '23514';
    END IF;

    -- 逐条校验批次内的记录
    FOR v_rec IN SELECT i.record_id, r.class_code, c.anchor_kind
                   FROM das_retention_destruction_item i
                   JOIN das_retention_record r ON r.id = i.record_id
                   JOIN das_retention_class c ON c.code = r.class_code
                  WHERE i.destruction_id = d.id
    LOOP
        -- 判据 R5: 冻结期间不得销毁
        IF das_retention_frozen(v_rec.record_id) THEN
            RAISE EXCEPTION
                'DCMS-INV-075: 记录 % 处于冻结状态, 不得销毁。UG-DAW-004: 调查、诉讼、争议、局方要求或未关闭事项期间冻结销毁 —— 冻结期间的销毁动作是被**拒绝**, 不是被跳过。',
                v_rec.record_id USING ERRCODE = '23514';
        END IF;
        -- 判据 R2 / R8: 截止日未到或算不出来的不得销毁
        v_until := das_retention_until(v_rec.record_id);
        IF v_until IS NULL THEN
            RAISE EXCEPTION
                'DCMS-INV-078: 记录 %（% 类, 锚点 %）当前算不出保留截止日: 锚点事件未发生, 或有关联对象的终点还没到。算不出来**不等于**可以销毁 —— 反过来理解是最危险的一种读法。',
                v_rec.record_id, v_rec.class_code, v_rec.anchor_kind USING ERRCODE = '23514';
        END IF;
        IF v_until > current_date THEN
            RAISE EXCEPTION
                'DCMS-INV-078: 记录 % 的保留截止日为 %, 尚未到期。判据 R2: 截止日是本记录期限与全部关联对象期限的较大者, 不是创建日期加年限。',
                v_rec.record_id, v_until USING ERRCODE = '23514';
        END IF;
        -- 判据 R3: 「长期」类须先有责任终止／转让的移交记录
        IF v_rec.anchor_kind = 'RESPONSIBILITY'
           AND NOT EXISTS (SELECT 1 FROM das_retention_link l
                            JOIN das_responsibility_handover h
                              ON h.scope_ref = l.link_ref
                           WHERE l.record_id = v_rec.record_id
                             AND l.link_kind = 'RESPONSIBILITY'
                             AND h.effective_on <= current_date) THEN
            RAISE EXCEPTION
                'DCMS-INV-079: 记录 % 属「长期」类, 须先有责任终止或转让的可追溯移交记录才能销毁。UG-DAW-004: 「长期」指责任存续期间持续保存, 不得因停业自动销毁（判据 R3）——「长期」不是「永远」, 也不是「无限期不管」。',
                v_rec.record_id USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_destruction_execute ON das_retention_destruction;
CREATE TRIGGER trg_das_destruction_execute
    BEFORE INSERT OR UPDATE ON das_retention_destruction
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_destruction_execute();

-- DCMS-INV-080: 冻结、销毁、移交与恢复核验记录 append-only。
CREATE OR REPLACE FUNCTION dcms_guard_das_retention_append() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-080: % 记录不得删除（销毁清单与冻结记录本身也要保存）',
            TG_TABLE_NAME USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'DCMS-INV-080: % 记录不得改写, 更正请另记一条', TG_TABLE_NAME
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['das_responsibility_handover', 'das_retention_restore_check',
                             'das_retention_destruction_item']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_append BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_retention_append()', t, t);
    END LOOP;
END $$;

-- 冻结记录允许解除(那是 UPDATE), 但不得删除, 也不得改写原因与解除条件。
CREATE OR REPLACE FUNCTION dcms_guard_das_freeze() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-080: 冻结记录不得删除（解除请填解除时间与说明）'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.reason <> OLD.reason OR NEW.release_condition <> OLD.release_condition
       OR NEW.freeze_kind <> OLD.freeze_kind OR NEW.initiated_by <> OLD.initiated_by THEN
        RAISE EXCEPTION
            'DCMS-INV-080: 冻结的原因、类别、发起人与解除条件不得改写。改条件等于事后改变冻结的理由。'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.released_at IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-080: 该冻结已解除, 解除记录不得再改写'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_freeze_guard ON das_retention_freeze;
CREATE TRIGGER trg_das_freeze_guard
    BEFORE UPDATE OR DELETE ON das_retention_freeze
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_freeze();

-- 销毁批次不得删除; 已执行的不得再改。
CREATE OR REPLACE FUNCTION dcms_guard_das_destruction() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-080: 销毁批次不得删除（销毁清单按 UG-DAW-004 保存 5 年）'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.executed_at IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-080: 该批次已执行销毁, 记录不得再改写'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_destruction_guard ON das_retention_destruction;
CREATE TRIGGER trg_das_destruction_guard
    BEFORE UPDATE OR DELETE ON das_retention_destruction
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_destruction();

-- DCMS-INV-081: 记录本身不得物理删除。销毁走批次并留痕, 不是 DELETE。
CREATE OR REPLACE FUNCTION dcms_guard_das_retention_record() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'DCMS-INV-081: 受管记录不得直接删除。销毁是带前置条件的受控动作（判据 R-总）: 走销毁批次, 三项核查 + 双人批准, 并在 das_retention_record.destroyed_at 留痕 —— 物理删除会让"销毁过什么"无从追溯。'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_retention_record_nodel ON das_retention_record;
CREATE TRIGGER trg_das_retention_record_nodel
    BEFORE DELETE ON das_retention_record
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_retention_record();

-- ---------------------------------------------------------------------
-- 9. 视图
-- ---------------------------------------------------------------------

-- 9.1 记录总览: 截止日、冻结状态、挡在哪一条。
CREATE OR REPLACE VIEW das_retention_status AS
SELECT r.id, r.class_code, c.name_cn AS class_name, c.anchor_kind, c.raw_period_text,
       c.custodian_position, r.object_type, r.object_id, r.label,
       r.created_on, r.anchor_on, r.destroyed_at,
       das_retention_own_until(r.id)                                   AS own_until,
       das_retention_until(r.id)                                       AS retention_until,
       das_retention_frozen(r.id)                                      AS frozen,
       (SELECT count(*) FROM das_retention_link l WHERE l.record_id = r.id) AS links,
       (SELECT count(*) FROM das_retention_link l
         WHERE l.record_id = r.id AND l.required_until IS NULL)        AS open_links,
       CASE WHEN r.destroyed_at IS NOT NULL                 THEN 'DESTROYED'
            WHEN das_retention_frozen(r.id)                  THEN 'FROZEN'
            WHEN das_retention_until(r.id) IS NULL           THEN 'NO_END_DATE'
            WHEN das_retention_until(r.id) > current_date    THEN 'RETAINED'
            ELSE 'DUE'
       END                                                             AS state
  FROM das_retention_record r
  JOIN das_retention_class c ON c.code = r.class_code;

COMMENT ON VIEW das_retention_status IS
    'state=DUE 只表示"已过保留截止日"，不表示可以销毁——销毁仍须三项核查与双人批准（判据 R-总：销毁是带前置条件的受控动作，不是定时任务）。NO_END_DATE 一律按不得销毁处理。';

-- 9.2 到期候选清单。**这不是待删除队列** —— 它只是候选, 销毁要人走流程。
CREATE OR REPLACE VIEW das_retention_due AS
SELECT * FROM das_retention_status
 WHERE state = 'DUE'
 ORDER BY retention_until, class_code;

COMMENT ON VIEW das_retention_due IS
    '已过保留截止日的候选记录。判据 R-总：这不是待删除队列，没有任何自动销毁——把它接上定时任务就会在冻结期、关联期限未满、缺双人批准时照删不误。';

-- 9.3 当前冻结。
CREATE OR REPLACE VIEW das_retention_freeze_active AS
SELECT f.id, f.freeze_kind, f.reason, f.release_condition,
       f.record_id, f.scope_kind, f.scope_ref,
       u.full_name AS initiated_by_name, f.initiated_at,
       current_date - f.initiated_at::date AS days_frozen
  FROM das_retention_freeze f JOIN app_user u ON u.id = f.initiated_by
 WHERE f.released_at IS NULL
 ORDER BY f.initiated_at;

-- 9.4 锚点未落地的记录: 期限算不出来, 需要有人去补锚点事件。
--     这类记录既不能销毁, 也不该被当成"永久保存"忘掉。
CREATE OR REPLACE VIEW das_retention_unanchored AS
SELECT r.id, r.class_code, c.name_cn AS class_name, c.anchor_kind, c.raw_period_text,
       r.object_type, r.object_id, r.label, r.created_on,
       CASE WHEN c.offset_years IS NOT NULL AND r.anchor_on IS NULL
                 THEN '锚点事件（' || c.anchor_kind || '）尚未发生或未回填'
            WHEN (SELECT count(*) FROM das_retention_link l
                   WHERE l.record_id = r.id AND l.required_until IS NULL) > 0
                 THEN '有关联对象的终点还没到'
            WHEN c.offset_years IS NULL
                 AND (SELECT count(*) FROM das_retention_link l WHERE l.record_id = r.id) = 0
                 THEN '长期／全生命周期类但尚无关联对象, 无从算终点'
       END                                                             AS reason
  FROM das_retention_record r
  JOIN das_retention_class c ON c.code = r.class_code
 WHERE r.destroyed_at IS NULL AND das_retention_until(r.id) IS NULL
 ORDER BY r.class_code, r.id;

-- 9.5 销毁批次的就绪情况: 缺哪一步看得见。
CREATE OR REPLACE VIEW das_retention_destruction_readiness AS
SELECT d.id, d.batch_ref,
       (SELECT count(*) FROM das_retention_destruction_item i
         WHERE i.destruction_id = d.id)                                AS items,
       d.inventory_checked_at IS NOT NULL      AS inventory_checked,
       d.related_period_checked_at IS NOT NULL AS related_period_checked,
       d.freeze_checked_at IS NOT NULL         AS freeze_checked,
       d.approved_by_dcm IS NOT NULL           AS approved_dcm,
       d.approved_by_awm IS NOT NULL           AS approved_awm,
       d.executed_at,
       array_remove(ARRAY[
         CASE WHEN d.inventory_checked_at IS NULL      THEN '清单核对' END,
         CASE WHEN d.related_period_checked_at IS NULL THEN '关联期限核查' END,
         CASE WHEN d.freeze_checked_at IS NULL         THEN '冻结状态核查' END,
         CASE WHEN d.approved_by_dcm IS NULL           THEN '资料管理负责人批准' END,
         CASE WHEN d.approved_by_awm IS NULL           THEN '适航管理负责人批准' END,
         CASE WHEN EXISTS (SELECT 1 FROM das_retention_destruction_item i
                            WHERE i.destruction_id = d.id
                              AND das_retention_frozen(i.record_id))
              THEN '批次内有冻结记录' END,
         CASE WHEN EXISTS (SELECT 1 FROM das_retention_destruction_item i
                            WHERE i.destruction_id = d.id
                              AND (das_retention_until(i.record_id) IS NULL
                                   OR das_retention_until(i.record_id) > current_date))
              THEN '批次内有未到期或算不出截止日的记录' END
       ]::text[], NULL::text)                                          AS blockers
  FROM das_retention_destruction d
 ORDER BY d.created_at DESC;

COMMENT ON VIEW das_retention_destruction_readiness IS
    'blockers 为空才可执行。UG-DAW-004 通则的三项核查、判据 R6 的双人批准、判据 R5 的冻结、判据 R2 的关联期限，缺哪一条都列出来。';

-- 9.6 类别总览: 按锚点与来源程序看 97 类的分布, 便于与 UG-DAW-004 原文对照。
CREATE OR REPLACE VIEW das_retention_class_summary AS
SELECT c.anchor_kind, c.raw_period_text, count(*) AS classes,
       string_agg(DISTINCT c.source_procedure, '、' ORDER BY c.source_procedure) AS procedures,
       string_agg(DISTINCT c.custodian_position, '、')                           AS custodians
  FROM das_retention_class c
 GROUP BY c.anchor_kind, c.raw_period_text
 ORDER BY count(*) DESC;

-- ---------------------------------------------------------------------
-- 10. UG-DAW-004 第 2 章的 97 类记录
-- ---------------------------------------------------------------------
-- 由 UG-DAW-004 00 草案逐行抽出, 期限栏原文保留在 raw_period_text。
-- 期限栏共 8 种表述, 映射到 7 种锚点; 映射关系在生成脚本里显式写出,
-- 出现新表述会在生成时报错而不是被猜成某一类(沿用 0029 不做模糊匹配的做法)。
INSERT INTO das_retention_class
    (code, name_cn, source_procedure, custodian_position, medium,
     anchor_kind, offset_years, raw_period_text, note)
VALUES
('P01.01', '设计资料和技术资料清单', 'UG-DAP-01', '资料管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P01.02', '文件清单与分发清单', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P01.03', '四性核查记录', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P01.04', '局方批准文件台账', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P01.05', '备份、容灾与权限记录', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P01.06', '作废文件隔离台账', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P01.07', '文件与记录编号台账', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P01.08', '作业文件清单', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P01.09', '表单清单', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P01.10', 'UG-DAF-01 文件修订申请', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'NEW_VERSION_RELEASE', 5, '随文件（新版发布后旧版再留 5 年）', '旧版自新版发布起再留 5 年'),
('P01.11', '记录保存期限表', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P01.12', '销毁清单', 'UG-DAP-01', '资料管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P02.01', 'UG-DAF-09 DAS 变更评估单', 'UG-DAP-02', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P02.02', '报 DPI 的资料及审定信函／认可记录', 'UG-DAP-02', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P02.03', '更新后的 DOA 符合性检查单', 'UG-DAP-02', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P02.04', '非重大更改台账', 'UG-DAP-02', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P03.01', '任命书/授权书', 'UG-DAP-03', '适航管理负责人', 'ELECTRONIC', 'AUTHORISATION_END', 5, '授权终止后 5 年', NULL),
('P03.02', 'UG-DAF-06 授权人员评估表', 'UG-DAP-03', '适航管理负责人', 'ELECTRONIC', 'AUTHORISATION_END', 5, '授权终止后 5 年', NULL),
('P03.03', '培训记录', 'UG-DAP-03', '适航管理负责人', 'ELECTRONIC', 'AUTHORISATION_END', 5, '授权终止后 5 年', NULL),
('P03.04', '授权人员名单', 'UG-DAP-03', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P03.05', '备份安排记录', 'UG-DAP-03', '适航管理负责人', 'ELECTRONIC', 'AUTHORISATION_END', 5, '授权终止后 5 年', NULL),
('P03.06', '履职检查记录', 'UG-DAP-03', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P03.07', '授权撤销通知及影响评估记录', 'UG-DAP-03', '适航管理负责人', 'ELECTRONIC', 'AUTHORISATION_END', 5, '授权终止后 5 年', NULL),
('P04.01', '技术可行性评估报告', 'UG-DAP-04', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P04.02', '合同／技术协议会签记录', 'UG-DAP-04', '研发部负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P04.03', 'UG-DAF-14 设计输入清单', 'UG-DAP-04', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P04.04', '设计计划', 'UG-DAP-04', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P04.05', '设计输出文件', 'UG-DAP-04', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P04.06', 'UG-DAF-15 选用清单', 'UG-DAP-04', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P04.07', 'UG-DAF-16 设计工具和软件确认记录', 'UG-DAP-04', '研发部负责人', 'ELECTRONIC', 'TOOL_DECOMMISSION', 5, '工具停用后 5 年', NULL),
('P04.08', 'UG-DAF-13 设计评审记录及意见闭环表', 'UG-DAP-04', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P04.09', '验证与确认记录', 'UG-DAP-04', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P04.10', '交付和接收记录', 'UG-DAP-04', '研发部负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P05.01', 'UG-DAF-10 构型项目清单', 'UG-DAP-05', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P05.02', '基线记录', 'UG-DAP-05', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P05.03', '构型纪实台账', 'UG-DAP-05', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P05.04', 'UG-DAF-18 构型核查记录', 'UG-DAP-05', '研发部负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P05.05', 'UG-DAF-11 交付构型记录', 'UG-DAP-05', '研发部负责人', 'ELECTRONIC', 'PRODUCT_RETIREMENT', NULL, '至产品永久退役', 'CCAR-21.137（十三）/ 判据 R8、L9'),
('P06.01', 'UG-DAF-02 设计更改分类表', 'UG-DAP-06', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P06.02', '偏离/让步评估记录', 'UG-DAP-06', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.01', '符合性检查单', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.02', 'UG-DAF-12 制造符合性声明及确认记录', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.03', 'UG-DAF-17 符合性验证完成确认书', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.04', '验证计划、试验大纲与记录', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.05', 'CVE 核查记录', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.06', 'UG-DAF-03 符合性声明', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.07', '没有不安全特征的确认记录', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.08', '设计供应商项目的技术协议、经核查的验证资料和交付记录', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P07.09', '局方意见及关闭记录', 'UG-DAP-07', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P08.01', 'UG-DAF-04 小改批准表', 'UG-DAP-08', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P08.02', '发布记录', 'UG-DAP-08', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P08.03', 'CVE 核查记录', 'UG-DAP-08', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P09.01', '立项单、项目计划', 'UG-DAP-09', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P09.02', '审定计划', 'UG-DAP-09', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P09.03', '与局方往来文件', 'UG-DAP-09', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P09.04', 'PMA 生产质量系统确认记录', 'UG-DAP-09', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P09.05', 'STC 要点核对清单和转段清单', 'UG-DAP-09', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P10.01', 'UG-DAF-07 供应商评价表', 'UG-DAP-10', '质量与供应商管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P10.02', '合格供应商清单（受控版本）', 'UG-DAP-10', '质量与供应商管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P10.03', '资料与更改传递台账', 'UG-DAP-10', '质量与供应商管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P10.04', '供应商报告与变更记录', 'UG-DAP-10', '质量与供应商管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P10.05', '核查记录、偏离/让步记录', 'UG-DAP-10', '质量与供应商管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P10.06', '与改装实施单位的技术协议及交付确认', 'UG-DAP-10', '质量与供应商管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P10.07', '改装构型反馈记录', 'UG-DAP-10', '质量与供应商管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P10.08', '采购/分包文件和供应商合作协议', 'UG-DAP-10', '质量与供应商管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P10.09', '供应商满足本体系要求的评估说明', 'UG-DAP-10', '质量与供应商管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P11.01', 'ICA 及修订', 'UG-DAP-11', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P11.02', '发布与确认记录', 'UG-DAP-11', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P11.03', '向局方报送记录', 'UG-DAP-11', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P11.04', '技术支持台账及答复函', 'UG-DAP-11', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P11.05', '首次改装技术支持报告', 'UG-DAP-11', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P11.06', 'UG-DAF-19 在役问题相似性评估记录', 'UG-DAP-11', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P11.07', '止用通知及接收确认', 'UG-DAP-11', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P11.08', '撤销决定和更正资料记录', 'UG-DAP-11', '适航管理负责人', 'BOTH', 'PRODUCT_LIFECYCLE', NULL, '产品/设计全生命周期', NULL),
('P11.09', '同类批准排查记录', 'UG-DAP-11', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P12.01', 'UG-DAF-08 不安全事件报告单', 'UG-DAP-12', '事件报告负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P12.02', '21.5 报告及局方往来', 'UG-DAP-12', '事件报告负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P12.03', '事件台账', 'UG-DAP-12', '事件报告负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P12.04', '事件调查报告', 'UG-DAP-12', '事件报告负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P13.01', 'UG-DAF-05A 独立监督与内部审核计划', 'UG-DAP-13', '独立监督负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P13.02', '审核报告', 'UG-DAP-13', '独立监督负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P13.03', '审核员资格与培训记录', 'UG-DAP-13', '独立监督负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P13.04', '覆盖率统计与年度总结', 'UG-DAP-13', '独立监督负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P13.05', '对独立监督职能的审核报告', 'UG-DAP-13', '独立监督负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P13.06', '审核检查表和审核记录', 'UG-DAP-13', '独立监督负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P14.01', '审定信函及表-21-165', 'UG-DAP-14', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P14.02', '表-21-166 设计机构纠正措施答复', 'UG-DAP-14', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P14.03', 'UG-DAF-05B NCR', 'UG-DAP-14', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P14.04', 'UG-DAF-05C CAR', 'UG-DAP-14', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P14.05', 'NCR/CAR 台账', 'UG-DAP-14', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P14.06', '观察项处理记录', 'UG-DAP-14', '适航管理负责人', 'ELECTRONIC', 'CREATED', 5, '5 年', NULL),
('P15.01', '往来文件登记', 'UG-DAP-15', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P15.02', '报备记录', 'UG-DAP-15', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P15.03', '设计机构许可证与延续材料', 'UG-DAP-15', '适航管理负责人', 'ELECTRONIC', 'RESPONSIBILITY', NULL, '长期', NULL),
('P11.10', 'STC 权益转让／终止协议及通知记录', 'UG-DAP-11', '资料管理负责人', 'BOTH', 'RESPONSIBILITY', NULL, '长期；关联产品记录期限较长的从其规定', 'UG-DAW-004 对本类明确写出了判据 R2：关联产品记录期限较长的从其规定'),
('P11.11', 'STC 书面许可协议及可接受性确认', 'UG-DAP-11', '资料管理负责人', 'BOTH', 'RESPONSIBILITY', NULL, '长期；关联产品记录期限较长的从其规定', 'UG-DAW-004 对本类明确写出了判据 R2：关联产品记录期限较长的从其规定'),
('P15.04', 'PMA 项目单延续记录', 'UG-DAP-15', '资料管理负责人', 'BOTH', 'RESPONSIBILITY', NULL, '长期；关联产品记录期限较长的从其规定', 'UG-DAW-004 对本类明确写出了判据 R2：关联产品记录期限较长的从其规定')
ON CONFLICT (code) DO NOTHING;
