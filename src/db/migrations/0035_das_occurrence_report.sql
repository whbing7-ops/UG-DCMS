-- 故障、失效、缺陷和不安全事件报告（M6）。
--
-- 依据 UG-DAP-12《故障失效缺陷和不安全事件报告程序》第 6 章工作步骤 1～10、
-- 第 7 章关键控制点、附表 CCAR-21.5（二）13 种应报告情形，
-- 以及 UG-RPT-2026-003 J 版判据 L1、M6-1、M6-2、M6-3、第 8.7 节的时间字段表。
--
-- 【这个模块的全部难点是一个时间点】
-- CCAR-21.5（六）的 48 小时是**日历小时**，起算点是"确认故障、失效或缺陷确实存在"
-- 的时刻，不是登记时刻，也不是接收时刻。UG-DAP-12 第 7 章把该说的都说尽了:
-- 不因调查未完成、不因责任人休假、也不因内部登记或初判未完成而推迟。
--
-- 所以六个时间点必须**各存一列**，不得合并成一个"报告时间"(第 8.7 节):
--   首次接收 → 登记 → 初判 → **确认存在** → 报送 → 补录
-- 合并任意两个，都等于把起算点挪到了法规没说的地方。
--
-- 【两个一碰就塌的方向】
--
-- 其一: **晚登记不得重置计时**(判据 M6-1)。起算点取 confirmed_at，与 registered_at
-- 无关; 否则拖延登记就能拖延法定时限。反过来，登记晚于接收超过 4 小时(步骤 2)
-- 本身是一条应记录的偏离，由 das_occurrence_registration_deviation 列出。
--
-- 其二: **不确认也不能绕过计时**。起算点是"确认存在"，于是"迟迟不填确认存在时间"
-- 就成了另一条绕路——记录看上去永远没到期。das_occurrence_clock_pending 把这类
-- 记录单独列出: 已判定属 21.5 范围、却还没有确认存在时间的，是待办，不是合规。
--
-- 【判据 M6-2: 系统不得因字段未填齐而阻止报送】
-- 报告内容要含序列号、型别、件号、故障性质、时间地点、初步原因分析六项，
-- 但"信息不全的先报已知部分"。把六项做成 NOT NULL 会直接造成超 48 小时——
-- 那是拿数据完整性去换法规超期。六项全部可空，齐备度由视图算出来**标记**，不拦。
--
-- 【判据 M6-3: 免于报告不是默认值】
-- UG-DAP-12 步骤 3 的三种免报情形，须有理由、证据和适航管理负责人签署。
-- 做成 das_occurrence 上的一个布尔字段就会变成"不填即免报"，所以单独立表:
-- 没有那一行，就不存在免报判定。
--
-- 【保密】
-- 步骤 1 允许匿名或保密提交，步骤 9 要求报告人信息保密。匿名件没有报告人,
-- 保密件有报告人但不得在常规界面显示——两者不是一回事, 用 disclosure 区分。
--
-- 【与 M7 的边界】
-- 步骤 8 的纠正预防措施(CAR)属 UG-DAP-14，是 M7 的实体。这里只留 car_ref 文本,
-- M7 落地后改为外键。现在就建一张半成品 CAR 表会与 M7 冲突。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. CCAR-21.5（二）应当报告的情形（配置，非代码）
-- ---------------------------------------------------------------------
-- 13 种情形出自规章附表，经 UG-DAP-12 附表转录。规章改版时在系统内维护,
-- 不改代码(判据 P1)。seq 即规章附表序号, 对外答复时要能对上号。
CREATE TABLE IF NOT EXISTS das_occurrence_category (
    seq         int  PRIMARY KEY,
    description text NOT NULL,
    basis       text NOT NULL DEFAULT 'CCAR-21.5（二）',
    is_active   boolean NOT NULL DEFAULT true
);

INSERT INTO das_occurrence_category (seq, description) VALUES
(1,  '由于航空器系统或者设备的故障、失效或者缺陷而引起着火'),
(2,  '由于发动机排气系统的故障、失效或者缺陷而使发动机或者相邻的航空器结构、设备或者部件损伤'),
(3,  '驾驶舱或者客舱内出现有毒或者有害气体'),
(4,  '螺旋桨操纵系统出现故障、失效或者缺陷'),
(5,  '螺旋桨、旋翼桨毂或者桨叶结构发生损坏'),
(6,  '在正常点火源附近，有易燃液体渗漏'),
(7,  '使用期间由于结构或者材料损坏而引起刹车系统失效'),
(8,  '任何自发情况（如疲劳、腐蚀、强度不够等）引起的航空器主要结构的严重缺陷或者损坏'),
(9,  '由于结构或者系统的故障、失效或者缺陷而引起的任何异常振动或者抖振'),
(10, '发动机失效'),
(11, '干扰航空器的正常操纵并降低飞行品质的任何结构或者飞行操纵系统的故障、失效或者缺陷'),
(12, '在航空器规定的一次运行期间内，一套或者一套以上的发电系统或者液压系统完全失效'),
(13, '在航空器规定的一次运行期间内，一个以上的空速仪表、姿态仪表或者高度仪表出现故障或者失效')
ON CONFLICT (seq) DO NOTHING;

COMMENT ON TABLE das_occurrence_category IS
    'CCAR-21.5（二）应当报告的 13 种情形，经 UG-DAP-12 附表转录。初判须逐项核对（判据 M6-1 所依赖的步骤 2）。';

-- ---------------------------------------------------------------------
-- 2. 事件报告单（UG-DAF-08）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_occurrence (
    id             bigserial PRIMARY KEY,
    report_no      text UNIQUE,                 -- UG-DAF-08 单据号

    -- 报告人。步骤 1: 可匿名或保密提交; 步骤 9: 报告人信息保密。
    -- ANONYMOUS 没有报告人可记; CONFIDENTIAL 有人但不得常规显示。两者不同。
    reporter_user_id   uuid REFERENCES app_user (id),
    reporter_name_text text,                    -- 外部报告人(使用人、供应商)无系统账号
    disclosure     text NOT NULL DEFAULT 'NAMED',

    -- ---- 六个时间点，各存一列，不合并（第 8.7 节）----
    received_at    timestamptz NOT NULL,        -- 首次接收(含 7×24 电话来电时间)
    received_via   text NOT NULL DEFAULT 'WRITTEN',   -- PHONE 先电话后补单 / WRITTEN
    registered_at  timestamptz NOT NULL DEFAULT now(),
    triaged_at     timestamptz,                 -- 初判完成
    confirmed_at   timestamptz,                 -- **确认存在: 48 小时唯一起算点**
    reported_at    timestamptz,                 -- 向局方报出(同时记在报送行上)
    backfilled_at  timestamptz,                 -- 系统不可用期间线下办理后补入(判据 N1)

    -- ---- 报告内容六项（判据 M6-2：全部可空，不齐备不得阻止报送）----
    occurrence_at    timestamptz,
    occurrence_place text,
    aircraft_serial  text,
    product_model    text,
    part_marking     text,                      -- 涉及部件标志，含件号
    failure_nature   text,
    preliminary_cause text,
    description      text NOT NULL,             -- 事件描述: 唯一必填, 否则这条记录没有内容

    -- ---- 初判结论（步骤 2）----
    -- UNDETERMINED 不是终态: 第 7 章"有疑问时按应报告处理"。
    triage_conclusion   text,
    triage_by           uuid REFERENCES app_user (id),
    containment_needed  boolean,                -- 步骤 2 同时判断是否需要立即遏制
    containment_note    text,

    car_ref        text,                        -- 步骤 8: 纠正措施单据号, M7 落地后改外键
    closed_at      timestamptz,
    closed_by      uuid REFERENCES app_user (id),

    created_by     uuid NOT NULL REFERENCES app_user (id),
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_das_occ_disclosure
        CHECK (disclosure IN ('NAMED', 'CONFIDENTIAL', 'ANONYMOUS')),
    -- 匿名件不得留下报告人; 具名/保密件必须有报告人(账号或外部姓名之一)。
    CONSTRAINT ck_das_occ_anonymous
        CHECK ((disclosure = 'ANONYMOUS'
                AND reporter_user_id IS NULL AND reporter_name_text IS NULL)
            OR (disclosure <> 'ANONYMOUS'
                AND (reporter_user_id IS NOT NULL OR reporter_name_text IS NOT NULL))),
    CONSTRAINT ck_das_occ_via CHECK (received_via IN ('PHONE', 'WRITTEN', 'EMAIL', 'OTHER')),
    CONSTRAINT ck_das_occ_triage
        CHECK (triage_conclusion IS NULL
               OR triage_conclusion IN ('REPORTABLE', 'EXEMPT', 'OUT_OF_SCOPE', 'UNDETERMINED')),
    -- 初判有结论就必须有判定人和判定时间, 三者同进同出。
    CONSTRAINT ck_das_occ_triage_who
        CHECK ((triage_conclusion IS NULL AND triage_by IS NULL AND triaged_at IS NULL)
            OR (triage_conclusion IS NOT NULL AND triage_by IS NOT NULL AND triaged_at IS NOT NULL)),
    -- 时序: 确认、登记、初判都不可能早于接收。
    CONSTRAINT ck_das_occ_order_reg   CHECK (registered_at >= received_at),
    CONSTRAINT ck_das_occ_order_triage CHECK (triaged_at IS NULL OR triaged_at >= received_at),
    CONSTRAINT ck_das_occ_order_conf  CHECK (confirmed_at IS NULL OR confirmed_at >= received_at),
    CONSTRAINT ck_das_occ_desc CHECK (length(btrim(description)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_das_occ_confirmed ON das_occurrence (confirmed_at)
    WHERE reported_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_das_occ_triage ON das_occurrence (triage_conclusion);

COMMENT ON TABLE das_occurrence IS
    'UG-DAF-08 故障失效缺陷和不安全事件报告单。六个时间点分列不合并；报告内容六项允许为空（判据 M6-2：不得因字段未齐阻止报送）。';
COMMENT ON COLUMN das_occurrence.confirmed_at IS
    'CCAR-21.5（六）48 日历小时的唯一起算点。与登记时间无关——晚登记不得重置计时（判据 M6-1）。';

-- ---------------------------------------------------------------------
-- 3. 初判：13 种情形逐项核对（步骤 2）
-- ---------------------------------------------------------------------
-- 为什么要逐项存: 步骤 2 要求"对照 13 种应报告情形逐项判断"。只存一个结论,
-- 看不出是逐项核对过还是凭印象下的; 局方问"第 8 种你们怎么判的"时答不上来。
CREATE TABLE IF NOT EXISTS das_occurrence_triage_item (
    occurrence_id bigint NOT NULL REFERENCES das_occurrence (id),
    category_seq  int    NOT NULL REFERENCES das_occurrence_category (seq),
    verdict       text   NOT NULL,              -- YES 符合 / NO 不符合 / UNSURE 无法确定
    note          text,
    assessed_by   uuid NOT NULL REFERENCES app_user (id),
    assessed_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (occurrence_id, category_seq),
    CONSTRAINT ck_das_occ_item_verdict CHECK (verdict IN ('YES', 'NO', 'UNSURE')),
    -- 判为"符合"或"无法确定"的，须写明理由: 这两种都会推向应报告，依据要留下。
    CONSTRAINT ck_das_occ_item_note
        CHECK (verdict = 'NO' OR length(btrim(coalesce(note, ''))) > 0)
);

COMMENT ON TABLE das_occurrence_triage_item IS
    'UG-DAP-12 步骤 2：对照 CCAR-21.5（二）13 种情形逐项判断的结果。缺项即未完成初判。';

-- ---------------------------------------------------------------------
-- 4. 免于报告的判定（步骤 3，判据 M6-3）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_occurrence_exemption (
    id            bigserial PRIMARY KEY,
    occurrence_id bigint NOT NULL UNIQUE REFERENCES das_occurrence (id),
    ground        text   NOT NULL,   -- IMPROPER_MAINTENANCE / ABNORMAL_USE / ALREADY_REPORTED
    rationale     text   NOT NULL,   -- 判定理由
    evidence      text   NOT NULL,   -- 证据
    signed_by     uuid   NOT NULL REFERENCES app_user (id),   -- 适航管理负责人
    signed_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_occ_exempt_ground
        CHECK (ground IN ('IMPROPER_MAINTENANCE', 'ABNORMAL_USE', 'ALREADY_REPORTED')),
    CONSTRAINT ck_das_occ_exempt_text
        CHECK (length(btrim(rationale)) > 0 AND length(btrim(evidence)) > 0)
);

COMMENT ON TABLE das_occurrence_exemption IS
    'UG-DAP-12 步骤 3 免于报告的判定。单独立表而非 das_occurrence 上的布尔字段——字段会变成"不填即免报"（判据 M6-3：不得作为默认值或静默跳过）。';

-- ---------------------------------------------------------------------
-- 5. 向局方报送（步骤 5）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_occurrence_submission (
    id            bigserial PRIMARY KEY,
    occurrence_id bigint NOT NULL REFERENCES das_occurrence (id),
    channel       text   NOT NULL,       -- AMOS / 与主管监察员约定的其他方式
    channel_note  text,                  -- AMOS 不可用时, 写明实际途径
    submitted_at  timestamptz NOT NULL,
    submitted_by  uuid NOT NULL REFERENCES app_user (id),
    reference_no  text,                  -- 局方受理编号
    receipt_ref   text,                  -- 回执
    is_supplement boolean NOT NULL DEFAULT false,   -- 先报已知部分后的补充报送
    content_gaps  text,                  -- 本次报送时仍缺的内容, 对应"注明后续补充"
    CONSTRAINT ck_das_occ_sub_channel CHECK (length(btrim(channel)) > 0),
    -- AMOS 以外的途径必须写明实际途径, 否则"报了"这件事无从核对。
    CONSTRAINT ck_das_occ_sub_other
        CHECK (channel = 'AMOS' OR length(btrim(coalesce(channel_note, ''))) > 0)
);

CREATE INDEX IF NOT EXISTS ix_das_occ_sub ON das_occurrence_submission (occurrence_id);

COMMENT ON TABLE das_occurrence_submission IS
    'UG-DAP-12 步骤 5 的报送记录。可多条：先报已知部分、后续补充各算一次，每次都留途径、时间和编号。';

-- ---------------------------------------------------------------------
-- 6. 调查与反馈（步骤 7、9）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_occurrence_investigation (
    id            bigserial PRIMARY KEY,
    occurrence_id bigint NOT NULL REFERENCES das_occurrence (id),
    findings      text   NOT NULL,
    root_cause    text   NOT NULL,
    report_ref    text,
    organised_by  uuid NOT NULL REFERENCES app_user (id),   -- 事件报告负责人组织
    completed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_occ_inv_text
        CHECK (length(btrim(findings)) > 0 AND length(btrim(root_cause)) > 0)
);

CREATE TABLE IF NOT EXISTS das_occurrence_feedback (
    id            bigserial PRIMARY KEY,
    occurrence_id bigint NOT NULL REFERENCES das_occurrence (id),
    content       text   NOT NULL,
    given_by      uuid NOT NULL REFERENCES app_user (id),
    given_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_occ_fb_text CHECK (length(btrim(content)) > 0)
);

COMMENT ON TABLE das_occurrence_feedback IS
    'UG-DAP-12 步骤 9 向报告人反馈。匿名件无反馈对象，由视图 das_occurrence_feedback_due 排除。';

-- ---------------------------------------------------------------------
-- 7. 时间点变更留痕
-- ---------------------------------------------------------------------
-- confirmed_at 一旦可以随意改写, 把它往后挪就能让一条已超期的记录重新"未到期",
-- 48 小时就形同虚设。但一律不许改也不对: 填错了就永远错, 记录从此说假话。
-- 折中是**改得了, 但改不掉痕迹**: 改 confirmed_at 必须先写一条变更行说明理由,
-- 触发器核对本次事务里有没有这条行, 没有就拒绝。
-- 注意: 变更行与 UPDATE 必须在**同一个事务**里 —— 分开提交时, 若 UPDATE 被其他
-- 约束拒绝, 变更行会留下来, 记录一次没发生的改动。服务层走 TransactionalRoute,
-- 两者同生同死; 手工改库时必须自己开事务。
CREATE TABLE IF NOT EXISTS das_occurrence_time_change (
    id            bigserial PRIMARY KEY,
    occurrence_id bigint NOT NULL REFERENCES das_occurrence (id),
    field_name    text   NOT NULL,
    old_value     timestamptz,
    new_value     timestamptz,
    reason        text   NOT NULL,
    changed_by    uuid NOT NULL REFERENCES app_user (id),
    changed_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_occ_tc_field CHECK (field_name IN ('confirmed_at', 'received_at')),
    CONSTRAINT ck_das_occ_tc_reason CHECK (length(btrim(reason)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_das_occ_tc ON das_occurrence_time_change (occurrence_id);

-- ---------------------------------------------------------------------
-- 8. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-038: 所有报告均登记, 不得删除（第 7 章末条, 含判定不需上报的）。
CREATE OR REPLACE FUNCTION dcms_guard_das_occurrence_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'DCMS-INV-038: 事件报告记录不得删除（UG-DAP-12 第 7 章: 所有报告含判定不需上报的均登记，不得删除）'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_occurrence_no_delete ON das_occurrence;
CREATE TRIGGER trg_das_occurrence_no_delete
    BEFORE DELETE ON das_occurrence
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_occurrence_delete();

-- 初判、免报判定、报送、调查、反馈都是已发生的事实, 只增不改不删。
CREATE OR REPLACE FUNCTION dcms_guard_das_occurrence_append() RETURNS trigger AS $$
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
    FOREACH t IN ARRAY ARRAY['das_occurrence_triage_item', 'das_occurrence_exemption',
                             'das_occurrence_submission', 'das_occurrence_investigation',
                             'das_occurrence_feedback', 'das_occurrence_time_change']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_append BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_occurrence_append()', t, t);
    END LOOP;
END $$;

-- DCMS-INV-039: 确认存在时间的改写必须留痕。
-- DCMS-INV-040: 初判定为"不属 21.5 范围"或"免于报告"前, 13 种情形须逐项判完。
-- DCMS-INV-041: 判为应报告/无法确定的情形存在时, 结论不得是"不属范围"。
-- DCMS-INV-042: 免于报告的记录须与初判结论一致。
CREATE OR REPLACE FUNCTION dcms_check_das_occurrence() RETURNS trigger AS $$
DECLARE
    v_items   int;
    v_active  int;
    v_pushing int;
BEGIN
    -- 判据 M6-1 的防线: confirmed_at 改写必须有本次事务内的变更说明。
    IF TG_OP = 'UPDATE' AND NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
       AND OLD.confirmed_at IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM das_occurrence_time_change c
                        WHERE c.occurrence_id = NEW.id AND c.field_name = 'confirmed_at'
                          AND c.new_value IS NOT DISTINCT FROM NEW.confirmed_at
                          AND c.old_value IS NOT DISTINCT FROM OLD.confirmed_at) THEN
            RAISE EXCEPTION
                'DCMS-INV-039: 确认存在时间是 48 小时法定时限的起算点, 改写须先登记变更理由（das_occurrence_time_change）'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.triage_conclusion IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT count(*) INTO v_active FROM das_occurrence_category WHERE is_active;
    SELECT count(*) INTO v_items FROM das_occurrence_triage_item i
      JOIN das_occurrence_category c ON c.seq = i.category_seq AND c.is_active
     WHERE i.occurrence_id = NEW.id;

    -- "逐项判断"不是一句话。少判一项就下结论, 等于把那一项当成了"不符合"。
    IF v_items < v_active THEN
        RAISE EXCEPTION
            'DCMS-INV-040: 初判须对 CCAR-21.5（二）% 种情形逐项判断, 现已判 % 项（UG-DAP-12 步骤 2）',
            v_active, v_items USING ERRCODE = '23514';
    END IF;

    SELECT count(*) INTO v_pushing FROM das_occurrence_triage_item
     WHERE occurrence_id = NEW.id AND verdict IN ('YES', 'UNSURE');

    -- 第 7 章: 有疑问时按应报告处理。有任一项判为符合或无法确定, 却结论为
    -- "不属范围", 就是把疑问当成了否定。
    IF v_pushing > 0 AND NEW.triage_conclusion = 'OUT_OF_SCOPE' THEN
        RAISE EXCEPTION
            'DCMS-INV-041: 有 % 项判为"符合"或"无法确定", 不得结论为不属 21.5 范围（UG-DAP-12 第 7 章: 有疑问时按应报告处理）',
            v_pushing USING ERRCODE = '23514';
    END IF;

    IF NEW.triage_conclusion = 'EXEMPT'
       AND NOT EXISTS (SELECT 1 FROM das_occurrence_exemption WHERE occurrence_id = NEW.id) THEN
        RAISE EXCEPTION
            'DCMS-INV-042: 结论为免于报告的, 须先登记免报判定（理由、证据、适航管理负责人签署；判据 M6-3）'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_occurrence_check ON das_occurrence;
CREATE TRIGGER trg_das_occurrence_check
    BEFORE INSERT OR UPDATE ON das_occurrence
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_occurrence();

-- ---------------------------------------------------------------------
-- 9. 视图
-- ---------------------------------------------------------------------

-- 9.1 48 小时时限。日历小时, 不折算工作日(判据 L1)。
--     deadline_at 由 confirmed_at 算出, 与登记时间无关。
CREATE OR REPLACE VIEW das_occurrence_deadline AS
SELECT o.id, o.report_no, o.description, o.triage_conclusion,
       o.received_at, o.registered_at, o.confirmed_at,
       o.confirmed_at + interval '48 hours'                      AS deadline_at,
       s.submitted_at                                            AS first_submitted_at,
       CASE WHEN o.confirmed_at IS NULL THEN NULL
            WHEN s.submitted_at IS NOT NULL
                 THEN round(extract(epoch FROM (s.submitted_at - o.confirmed_at)) / 3600.0, 2)
            ELSE round(extract(epoch FROM (now() - o.confirmed_at)) / 3600.0, 2)
       END                                                        AS elapsed_hours,
       CASE WHEN o.confirmed_at IS NULL                      THEN 'NO_CLOCK'
            WHEN s.submitted_at IS NULL
                 AND now() > o.confirmed_at + interval '48 hours' THEN 'OVERDUE'
            WHEN s.submitted_at IS NULL                           THEN 'RUNNING'
            WHEN s.submitted_at > o.confirmed_at + interval '48 hours' THEN 'LATE'
            ELSE 'ON_TIME'
       END                                                        AS clock_state
  FROM das_occurrence o
  LEFT JOIN LATERAL (
        SELECT min(submitted_at) AS submitted_at FROM das_occurrence_submission
         WHERE occurrence_id = o.id AND NOT is_supplement) s ON true
 WHERE o.triage_conclusion IS DISTINCT FROM 'OUT_OF_SCOPE';

COMMENT ON VIEW das_occurrence_deadline IS
    'CCAR-21.5（六）48 日历小时。起算点为确认存在时间，与登记时间无关（判据 M6-1）；首次报送计时，补充报送不重算。';

-- 9.2 计时还没开始的。这是绕过 48 小时的另一条路: 迟迟不填确认存在时间,
--     记录看上去永远没到期。列出来, 让它变成待办而不是合规。
CREATE OR REPLACE VIEW das_occurrence_clock_pending AS
SELECT o.id, o.report_no, o.description, o.received_at, o.registered_at,
       o.triage_conclusion,
       round(extract(epoch FROM (now() - o.received_at)) / 3600.0, 2) AS hours_since_received
  FROM das_occurrence o
 WHERE o.confirmed_at IS NULL
   AND o.triage_conclusion IN ('REPORTABLE', 'UNDETERMINED')
 ORDER BY o.received_at;

COMMENT ON VIEW das_occurrence_clock_pending IS
    '已判定属 21.5 范围（或尚无法确定）却还没有确认存在时间的报告。48 小时从确认存在起算，所以"不确认"等于计时不开始——这些是待办，不是合规。';

-- 9.3 登记晚于接收超过 4 小时（UG-DAP-12 步骤 2，含非工作日）。
--     门限写在视图里是临时安排: 4 小时是内部时限, 引擎就绪后应从 UG-DAW-001 取值
--     (判据 T1: 内部时限从一处取值)。
CREATE OR REPLACE VIEW das_occurrence_registration_deviation AS
SELECT o.id, o.report_no, o.received_at, o.received_via, o.registered_at,
       round(extract(epoch FROM (o.registered_at - o.received_at)) / 3600.0, 2) AS lag_hours
  FROM das_occurrence o
 WHERE o.registered_at > o.received_at + interval '4 hours'
 ORDER BY o.received_at;

COMMENT ON VIEW das_occurrence_registration_deviation IS
    'UG-DAP-12 步骤 2 要求收到后最迟 4 小时内登记（含非工作日）。超过即为一条应记录的偏离——它不影响 48 小时起算，但本身要被看见。';

-- 9.4 初判进度：13 项判完了几项。
CREATE OR REPLACE VIEW das_occurrence_triage_progress AS
SELECT o.id, o.report_no, o.triage_conclusion, o.triaged_at,
       (SELECT count(*) FROM das_occurrence_category WHERE is_active)   AS categories,
       (SELECT count(*) FROM das_occurrence_triage_item i
          JOIN das_occurrence_category c ON c.seq = i.category_seq AND c.is_active
         WHERE i.occurrence_id = o.id)                                  AS assessed,
       (SELECT count(*) FROM das_occurrence_triage_item
         WHERE occurrence_id = o.id AND verdict = 'YES')                AS matched,
       (SELECT count(*) FROM das_occurrence_triage_item
         WHERE occurrence_id = o.id AND verdict = 'UNSURE')             AS unsure
  FROM das_occurrence o;

-- 9.5 报告内容齐备度（判据 M6-2）。**只标不拦**——缺项不得阻止报送,
--     否则拿数据完整性换超 48 小时。缺哪几项列出来, 供补充报送时跟踪。
CREATE OR REPLACE VIEW das_occurrence_content_gaps AS
SELECT o.id, o.report_no,
       array_remove(ARRAY[
           CASE WHEN o.aircraft_serial  IS NULL THEN '航空器序列号' END,
           CASE WHEN o.product_model    IS NULL THEN '产品型别' END,
           CASE WHEN o.part_marking     IS NULL THEN '涉及部件标志（含件号）' END,
           CASE WHEN o.failure_nature   IS NULL THEN '故障性质' END,
           CASE WHEN o.occurrence_at    IS NULL THEN '发生时间' END,
           CASE WHEN o.occurrence_place IS NULL THEN '发生地点' END,
           CASE WHEN o.preliminary_cause IS NULL THEN '初步原因分析' END
       ]::text[], NULL::text) AS missing,
       EXISTS (SELECT 1 FROM das_occurrence_submission WHERE occurrence_id = o.id) AS submitted
  FROM das_occurrence o;

COMMENT ON VIEW das_occurrence_content_gaps IS
    'UG-DAP-12 第 7 章列为"报告不完整"的各项。只标记不阻止报送（判据 M6-2：信息不全的先报已知部分；阻止报送会直接造成超 48 小时）。';

-- 9.6 应向报告人反馈而未反馈的（步骤 9）。匿名件无反馈对象, 排除。
CREATE OR REPLACE VIEW das_occurrence_feedback_due AS
SELECT o.id, o.report_no, o.disclosure, o.received_at, o.triage_conclusion
  FROM das_occurrence o
 WHERE o.disclosure <> 'ANONYMOUS'
   AND o.triage_conclusion IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM das_occurrence_feedback WHERE occurrence_id = o.id)
 ORDER BY o.received_at;

-- 9.7 台账。保密件不在此视图暴露报告人（步骤 9：报告人信息保密）。
CREATE OR REPLACE VIEW das_occurrence_register AS
SELECT o.id, o.report_no, o.description, o.received_at, o.received_via, o.registered_at,
       o.triaged_at, o.confirmed_at, o.triage_conclusion, o.containment_needed,
       o.car_ref, o.closed_at,
       CASE WHEN o.disclosure = 'NAMED'
            THEN coalesce(u.full_name, o.reporter_name_text) END        AS reporter_display,
       o.disclosure,
       d.deadline_at, d.elapsed_hours, d.clock_state,
       (SELECT count(*) FROM das_occurrence_submission WHERE occurrence_id = o.id) AS submissions
  FROM das_occurrence o
  LEFT JOIN app_user u ON u.id = o.reporter_user_id
  LEFT JOIN das_occurrence_deadline d ON d.id = o.id
 ORDER BY o.received_at DESC, o.id DESC;

COMMENT ON VIEW das_occurrence_register IS
    'UG-DAP-12 第 8 章事件台账。保密件与匿名件不在此显示报告人（步骤 9：报告人信息保密）；需要时另行按权限查原表。';
