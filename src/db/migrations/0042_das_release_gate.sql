-- ---------------------------------------------------------------------
-- 0042  上线前六项控制验证与投用闸门（判据 N16、N6、N15、B2）
-- ---------------------------------------------------------------------
-- 依据 UG-DAW-005 第 1 章（上线前须验证身份、授权、独立性、冻结、日志和备份恢复
-- 六项控制并保留结果）、第 7 章（备份与恢复）、UG-DAW-001 第 3 章，
-- 设计输入第六之二节 N6／N15／N16、第 8.6 节批次表、判据 B2（分批不得造成控制空档）。
--
-- 【这一张不是检查表】
-- "六项已验证"写成六个勾，谁都签得下去。N16 说的是"验证并保留结果"，而结果要能回到
-- 证据：哪一次 CI、哪个提交、抽查了几份、每一项核对的结论是什么。所以这里的每条验证
-- 记录都必须带提交号与批次号（同 0041 的执行证据），写入后不得改写也不得删除。
--
-- 【绑在构建上，不绑在时间上】
-- 最容易出的错不是漏验，是**验过之后代码又改了**。所以验证记录带 build_ref，
-- 投用闸门要求验证的 build_ref 与批次当前的 build_ref 相同；批次换了构建，
-- 之前的验证一律不算，由 das_release_stale 列出来。否则"上线前已验证"这句话
-- 指向的是另一个版本的系统，而这种偏差没有任何外部迹象。
--
-- 【"冻结"在这套文件里是两个机制】
-- UG-DAW-005 第 1 章只写了"冻结"两个字。但本系统里:
--   ① 版次冻结（判据 N2）: 发现纸质件与电子件不一致时**立即停止使用有疑义的版本**;
--   ② 记录冻结（判据 R5）: 调查、诉讼、争议、局方要求或未关闭事项期间**拒绝销毁**。
-- 两者是不同的表、不同的触发器、不同的失效后果。只测了一个而记"冻结 已验证",
-- 就是一条假符合。所以本表把"冻结"做成容器项, 状态落在两个子项上（同 0041 的
-- 判据 I3／I10 的处理）。**六项的父项编号保持与 UG-DAW-005 一致**, 自评仍可答"六项",
-- 但证据按机制分别给。拆分是本文件的解释, 不是源文件的措辞 —— 在 note 里写明。
--
-- 【发现的缺口: N6 的两个周期根本不在时限引擎里】
-- 0039 种了 16 个时限参数, 没有一条是备份或恢复验证的。而 N6 要的是"服务器备份每日、
-- 可恢复性验证每季度"。备份恢复这一项的验证内容本身就依赖这两个周期, 缺了它们,
-- 这一项只能验"今天跑过一次"。本迁移第 6 节补上, 并按判据 Q2 取较严的每季度
-- （UG-DAW-001 第 3 章写每季度、第 4 章写每半年, 冲突为第九之三节第 12 条, 未关闭）。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 批次
-- ---------------------------------------------------------------------
-- 批次来自设计输入第 8.6 节那张表, 是配置不是枚举: 批次划分会随建设进度调整,
-- 而每次调整都要能看出"投用前提"变过没有。
CREATE TABLE IF NOT EXISTS das_release_batch (
    code         text PRIMARY KEY,          -- BASE / B1 / B2 / B3
    name_cn      text NOT NULL,
    scope_note   text NOT NULL,             -- 本批次包含什么
    precondition text NOT NULL,             -- 投用前提（第 8.6 节原文口径）
    -- 当前待验证／已投用的构建。换了构建, 之前的验证不算（见 das_release_stale）。
    build_ref    text,
    planned_on   date,
    note         text,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE das_release_batch IS
    '上线批次（设计输入第 8.6 节）。build_ref 是本批次当前待验证或已投用的构建，换构建后之前的验证不再计入投用闸门。';

-- ---------------------------------------------------------------------
-- 2. 六项控制（配置）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_release_check_item (
    code         text PRIMARY KEY,
    parent_code  text REFERENCES das_release_check_item (code),
    seq          int  NOT NULL,             -- UG-DAW-005 第 1 章的列举顺序
    name_cn      text NOT NULL,
    requirement  text NOT NULL,             -- 要验的是什么
    verify_content text NOT NULL,           -- 怎样才算验过（可核验的最低内容）
    source_doc   text NOT NULL,
    criteria_ref text,                      -- 对应判据
    -- 容器项: 有子机制、验证结果只落在子项上。显式声明, 不从编码推（0041 的教训:
    -- 把容器编码写进 CHECK 里, 以后谁给别的项加子机制, 那个 CHECK 不会知道）。
    is_container boolean NOT NULL DEFAULT false,
    -- 备份恢复的抽查份数下限。判据 N15: 还原到测试环境**抽查 5 份文件**。
    -- 做成参数而不是写死 5: 法规与体系文件要求的数都可能变（2026-10-01 的决定）,
    -- 但它同样是下限 —— 只能往上调, 往下调要动的是 UG-DAW-005。
    min_sample   int,
    note         text,
    CONSTRAINT ck_drci_sample CHECK (min_sample IS NULL OR min_sample > 0)
);

COMMENT ON TABLE das_release_check_item IS
    'UG-DAW-005 第 1 章的六项上线前控制。「冻结」是容器项：本系统里版次冻结（N2）与记录冻结（R5）是两个机制，只测一个而记「冻结已验证」是假符合。';

-- ---------------------------------------------------------------------
-- 3. 验证记录
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_release_verification (
    id          bigserial PRIMARY KEY,
    batch_code  text NOT NULL REFERENCES das_release_batch (code),
    item_code   text NOT NULL REFERENCES das_release_check_item (code),
    build_ref   text NOT NULL,              -- 验的是哪个构建
    method      text NOT NULL,              -- 怎么验的
    commit_ref  text NOT NULL,              -- 提交号
    batch_ref   text NOT NULL,              -- CI 批次／运行号
    evidence_ref text,                      -- 日志或工件位置
    result      text NOT NULL,              -- PASS / FAIL
    -- 判据 N6／N15: **验证失败即为不符合项**。所以 FAIL 必须挂一条 NCR,
    -- 不能只在这里记一句"失败"然后没有下文 —— 那样失败就只是一行字。
    ncr_id      bigint REFERENCES das_ncr (id),
    -- 备份恢复专用（判据 N15: 抽查 5 份, 逐项核对内容、版次和签署记录完整性）
    sample_count int,
    content_ok   boolean,
    version_ok   boolean,
    signature_ok boolean,
    sample_note  text,
    verified_by text NOT NULL,              -- 执行人（岗位＋姓名，或外部单位名称）
    verified_by_user uuid REFERENCES app_user (id),
    -- 【不填账号必须是一句明话, 不能是一个空格】
    -- verified_by_user 可空是为了容纳外部人员。但假如留空不需要理由,
    -- das_release_self_verified 这张表就形同虚设: 自验自批只要不填账号就看不见,
    -- 而视图还是干净的 —— 和自由文本 product_scope 那个错是同一类:
    -- 一个没人要求填的字段比没有这个字段更糟。所以要么给账号,
    -- 要么明确声明是外部人员 —— 后者是一条可核对的陈述, 不是一个空格。
    verifier_is_external boolean NOT NULL DEFAULT false,
    verified_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_drv_result CHECK (result IN ('PASS', 'FAIL')),
    CONSTRAINT ck_drv_text CHECK (
        length(btrim(method)) > 0 AND length(btrim(commit_ref)) > 0
        AND length(btrim(batch_ref)) > 0 AND length(btrim(verified_by)) > 0),
    -- 失败必须挂不符合项（判据 N6／N15）
    CONSTRAINT ck_drv_fail_ncr CHECK (result <> 'FAIL' OR ncr_id IS NOT NULL),
    CONSTRAINT ck_drv_verifier CHECK (verified_by_user IS NOT NULL OR verifier_is_external)
);

COMMENT ON TABLE das_release_verification IS
    '六项控制的验证结果。带 build_ref：换了构建之前的验证不计入投用闸门。FAIL 必须挂一条 NCR（判据 N6／N15：验证失败即为不符合项）。写入后不得改写或删除。';

-- ---------------------------------------------------------------------
-- 4. 投用
-- ---------------------------------------------------------------------
-- 刻意**不在批次表上放 status 列**（同 M7 的做法）: "可投用"是从验证记录推出来的,
-- 存一份状态迟早与证据不一致, 而不一致的那一次就是拿着过期的绿灯上线。
-- 投用本身不是状态, 是一次有日期有批准人的**动作**, 所以单独一张表。
CREATE TABLE IF NOT EXISTS das_release_commissioning (
    id          bigserial PRIMARY KEY,
    -- 一个批次可以投用多次: 基础能力在构建 7001 上线, 之后 7050 又发一版。
    -- 判据 N16 说的是"每批次上线前执行", 而批次的软件会反复发版 —— 所以主键不能是
    -- batch_code。写成 batch_code 作主键, 加上 DCMS-INV-089 不许改写, 结果是**第一次
    -- 投用之后这个批次的投用记录永远冻住, 后面任何一版都记不进来**,
    -- 而 089 的报错还在让人"另记一条新的构建"。按 (批次, 构建) 唯一。
    batch_code  text NOT NULL REFERENCES das_release_batch (code),
    build_ref   text NOT NULL,
    commissioned_on date NOT NULL,
    approved_by uuid NOT NULL REFERENCES app_user (id),   -- 在任适航管理负责人
    approval_ref text,                                    -- 批准依据（会议纪要／签批单）
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_drc_batch_build UNIQUE (batch_code, build_ref)
);

COMMENT ON TABLE das_release_commissioning IS
    '批次投用的动作记录，一行一次（批次＋构建唯一）。插入时由 DCMS-INV-087 校验六项（按机制展开后的叶子项）在同一构建上均有 PASS 验证。';

-- ---------------------------------------------------------------------
-- 5. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-087: 六项未全部在**本构建**上验过且 PASS, 不得投用。
CREATE OR REPLACE FUNCTION dcms_check_das_release_commissioning() RETURNS trigger AS $$
DECLARE
    v_missing text;
    v_failed  text;
    v_batch_build text;
BEGIN
    SELECT build_ref INTO v_batch_build FROM das_release_batch WHERE code = NEW.batch_code;
    IF v_batch_build IS NULL OR btrim(v_batch_build) = '' THEN
        RAISE EXCEPTION 'DCMS-INV-087: 批次 % 还没有登记构建号(build_ref), 无从判断验证的是哪个版本的系统。'
                        '不写构建号而宣布"上线前已验证", 这句话指向的是哪一版无人知道。', NEW.batch_code;
    END IF;
    IF NEW.build_ref IS DISTINCT FROM v_batch_build THEN
        RAISE EXCEPTION 'DCMS-INV-087: 投用的构建(%) 与批次当前登记的构建(%) 不是同一个。'
                        '要投用别的构建, 先改批次的 build_ref, 然后在那个构建上重新验证六项 —— '
                        '验证不随构建迁移（判据 N16: 每批次上线前执行）。',
                        NEW.build_ref, v_batch_build;
    END IF;

    -- 叶子项（容器项的结果落在子项上）逐项要有本构建的 PASS。
    -- 取"最近一次"而不是"存在一次": 后一次 FAIL 必须能推翻前一次 PASS,
    -- 否则先验过一次 PASS、改坏了再验出 FAIL, 闸门照样放行。
    SELECT string_agg(i.seq || '. ' || i.name_cn, '、' ORDER BY i.seq)
      INTO v_missing
      FROM das_release_check_item i
     WHERE NOT i.is_container
       AND NOT EXISTS (SELECT 1 FROM das_release_verification v
                        WHERE v.batch_code = NEW.batch_code AND v.item_code = i.code
                          AND v.build_ref = v_batch_build);
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-087: 下列控制在构建 % 上还没有验证记录, 本批次不得投用: %。'
                        '（判据 N16: 六项验证是该批次投用的前置条件; 判据 B2: 分批不得造成控制空档）',
                        v_batch_build, v_missing;
    END IF;

    SELECT string_agg(x.name_cn, '、' ORDER BY x.seq) INTO v_failed
      FROM (SELECT DISTINCT ON (i.code) i.code, i.name_cn, i.seq, v.result
              FROM das_release_check_item i
              JOIN das_release_verification v
                ON v.item_code = i.code AND v.batch_code = NEW.batch_code
               AND v.build_ref = v_batch_build
             WHERE NOT i.is_container
             ORDER BY i.code, v.verified_at DESC) x
     WHERE x.result = 'FAIL';
    IF v_failed IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-087: 下列控制在构建 % 上最近一次验证为失败, 本批次不得投用: %。'
                        '失败已按判据 N6／N15 记为不符合项; 整改后在同一构建上重新验证并另记一条。',
                        v_batch_build, v_failed;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_release_commissioning_check ON das_release_commissioning;
CREATE TRIGGER trg_das_release_commissioning_check
    BEFORE INSERT OR UPDATE ON das_release_commissioning
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_release_commissioning();

-- DCMS-INV-088: 验证记录本身的内容校验。
-- 这里拦的是"填满但不真"的几种写法, 每一种都见过:
--   ① 容器项上记结果（"冻结 已验证"而两个机制一个都没测）;
--   ② 备份恢复抽查不足 5 份, 或三项核对不给结论;
--   ③ 三项核对里有一项为否, 结论却写 PASS。
CREATE OR REPLACE FUNCTION dcms_check_das_release_verification() RETURNS trigger AS $$
DECLARE
    v_container boolean;
    v_min  int;
    v_name text;
BEGIN
    SELECT is_container, min_sample, name_cn INTO v_container, v_min, v_name
      FROM das_release_check_item WHERE code = NEW.item_code;

    IF v_container THEN
        RAISE EXCEPTION 'DCMS-INV-088: 「%」是容器项, 结果只能落在它的子机制上。'
                        '本系统里这一项有多个互不相同的实现, 笼统记一条"已验证"等于没验 —— '
                        '证据要按机制分别给（见 das_release_check_item 的 note）。', v_name;
    END IF;

    IF v_min IS NOT NULL THEN
        -- 判据 N15: 还原到测试环境抽查 5 份文件的内容、版次和签署记录完整性并记录结果。
        -- 写成 (sample_count >= v_min AND ...) 放在一个 OR 式子里是不行的:
        -- 任一字段为 NULL 时整式为 NULL, 而 NULL 在这里会被当成"没有触发异常"放过去。
        IF NEW.sample_count IS NULL OR NEW.sample_count < v_min THEN
            RAISE EXCEPTION 'DCMS-INV-088: 「%」须抽查至少 % 份并记明份数（判据 N15）。'
                            '实际填 %。恢复验证不是"能启动", 是抽查若干份逐项核对。',
                            v_name, v_min, coalesce(NEW.sample_count::text, '空');
        END IF;
        IF NEW.content_ok IS NULL OR NEW.version_ok IS NULL OR NEW.signature_ok IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-088: 「%」的内容、版次、签署记录三项须各自给出结论（判据 N15）。'
                            '少填一项就无从判断这次恢复验证到底核对了什么。', v_name;
        END IF;
        IF NEW.result = 'PASS'
           AND NOT (NEW.content_ok AND NEW.version_ok AND NEW.signature_ok) THEN
            RAISE EXCEPTION 'DCMS-INV-088: 「%」三项核对中有一项为否, 结论不得写 PASS。'
                            '抽查发现签署记录不全却判通过, 比不抽查更坏 —— 它留下了一条说已核对过的记录。',
                            v_name;
        END IF;
    ELSE
        IF NEW.sample_count IS NOT NULL OR NEW.content_ok IS NOT NULL
           OR NEW.version_ok IS NOT NULL OR NEW.signature_ok IS NOT NULL THEN
            RAISE EXCEPTION 'DCMS-INV-088: 抽查份数与三项核对只适用于有抽查要求的控制项（现为「%」）。', v_name;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_release_verification_check ON das_release_verification;
CREATE TRIGGER trg_das_release_verification_check
    BEFORE INSERT OR UPDATE ON das_release_verification
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_release_verification();

-- DCMS-INV-089: 验证记录与投用记录只追加, 不得改写或删除。
-- 理由同 0041 的执行证据: 这两张表是上线决定的依据。能改写, 它们就不是证据了。
CREATE OR REPLACE FUNCTION dcms_guard_das_release_append() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'das_release_verification' THEN
        RAISE EXCEPTION 'DCMS-INV-089: 上线前验证记录不得%, 更正请在同一构建上另记一条。'
                        '它是该批次投用决定的依据 —— 能改写就不是证据了。',
                        CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
    END IF;
    RAISE EXCEPTION 'DCMS-INV-089: 投用记录不得%。投用后又要撤回, 应另记一条新的批次与构建, '
                    '不是把上一次投用抹掉。',
                    CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_release_verification_append ON das_release_verification;
CREATE TRIGGER trg_das_release_verification_append
    BEFORE UPDATE OR DELETE ON das_release_verification
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_release_append();

DROP TRIGGER IF EXISTS trg_das_release_commissioning_append ON das_release_commissioning;
CREATE TRIGGER trg_das_release_commissioning_append
    BEFORE UPDATE OR DELETE ON das_release_commissioning
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_release_append();

-- DCMS-INV-090: 控制项不得删除。
-- 六项是 UG-DAW-005 第 1 章定的。删掉一项等于让它从自评里消失, 和 0041 的
-- DCMS-INV-084 同一个道理。不再适用时改 note 写明理由。
CREATE OR REPLACE FUNCTION dcms_guard_das_release_item() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-INV-090: 上线前控制项不得删除（UG-DAW-005 第 1 章定的六项）。'
                    '不再适用时请在 note 写明理由 —— 删掉等于让这一项从自评里消失。';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_release_item_guard ON das_release_check_item;
CREATE TRIGGER trg_das_release_item_guard
    BEFORE DELETE ON das_release_check_item
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_release_item();

-- ---------------------------------------------------------------------
-- 6. 视图
-- ---------------------------------------------------------------------

-- 6.1 就绪情况: 批次 × 叶子控制项 → 本构建上最近一次验证。
CREATE OR REPLACE VIEW das_release_readiness AS
SELECT b.code AS batch_code, b.name_cn AS batch_name, b.build_ref,
       i.seq, i.code AS item_code, i.name_cn AS item_name, i.criteria_ref,
       v.result, v.verified_at, v.verified_by, v.commit_ref, v.batch_ref,
       v.sample_count, v.ncr_id,
       CASE WHEN b.build_ref IS NULL OR btrim(b.build_ref) = '' THEN 'NO_BUILD'
            WHEN v.id IS NULL                                   THEN 'NOT_VERIFIED'
            WHEN v.result = 'FAIL'                              THEN 'FAILED'
            ELSE 'VERIFIED'
       END AS state
  FROM das_release_batch b
 CROSS JOIN das_release_check_item i
  LEFT JOIN LATERAL (
        SELECT * FROM das_release_verification x
         WHERE x.batch_code = b.code AND x.item_code = i.code
           AND x.build_ref = b.build_ref
         ORDER BY x.verified_at DESC LIMIT 1) v ON true
 WHERE NOT i.is_container
 ORDER BY b.code, i.seq;

COMMENT ON VIEW das_release_readiness IS
    '每个批次在其当前构建上的六项（按机制展开）验证情况。state 为 VERIFIED 以外的任一值都意味着该批次不得投用。';

-- 6.2 拦住投用的那几项。闸门报错前先能看见。
CREATE OR REPLACE VIEW das_release_blocker AS
SELECT batch_code, batch_name, build_ref, seq, item_code, item_name, state, ncr_id
  FROM das_release_readiness
 WHERE state <> 'VERIFIED'
 ORDER BY batch_code, seq;

-- 6.3 **验过之后构建又变了的**。
-- 这是本模块最要紧的一张视图: 它列出那些曾经验过、但验的不是当前构建的记录。
-- 没有这张视图, "上线前已验证"会一直是真的 —— 只不过指向上一个版本的系统,
-- 而这种偏差不会自己暴露。
CREATE OR REPLACE VIEW das_release_stale AS
SELECT v.batch_code, b.name_cn AS batch_name, v.item_code, i.name_cn AS item_name,
       v.build_ref AS verified_build, b.build_ref AS current_build,
       v.result, v.verified_at
  FROM das_release_verification v
  JOIN das_release_batch b ON b.code = v.batch_code
  JOIN das_release_check_item i ON i.code = v.item_code
 WHERE b.build_ref IS DISTINCT FROM v.build_ref
 ORDER BY v.batch_code, i.seq, v.verified_at DESC;

COMMENT ON VIEW das_release_stale IS
    '验证记录的构建与批次当前构建不一致的（验过之后代码又改了）。这些记录不计入投用闸门，须在当前构建上重新验证。';

-- 6.4 执行人同时又是投用批准人的（判据 I6 的同形问题）。
-- **这是可见性清单, 不是阻断。** UG-DAW-005 第 1 章没有写"验证人与批准人须为两人",
-- 而本单位实际编制 5～8 人（现实 O1）, 硬性要求两人在某些项上避不开。
-- 但"自己验完自己宣布可以上线"与判据 I6 禁止的"责任部门负责人自行验证并关闭本部门
-- 不符合项"是同一个形状, 所以列出来交独立监督按 UG-DAP-13 核对。
CREATE OR REPLACE VIEW das_release_self_verified AS
SELECT c.batch_code, b.name_cn AS batch_name, v.item_code, i.name_cn AS item_name,
       u.full_name, u.employee_no, c.commissioned_on
  FROM das_release_commissioning c
  JOIN das_release_batch b ON b.code = c.batch_code
  JOIN das_release_verification v
    ON v.batch_code = c.batch_code AND v.build_ref = c.build_ref
  JOIN das_release_check_item i ON i.code = v.item_code
  JOIN app_user u ON u.id = c.approved_by
 WHERE v.verified_by_user = c.approved_by
 ORDER BY c.batch_code, c.commissioned_on DESC, i.seq;

-- 6.4b 外部人员执行的验证。
-- 单列一张是因为这些行在 6.4 里天然看不见（没有账号可比）。
-- 声明为外部是正当做法, 但它也是绕过 6.4 的唯一路径 ——
-- 所以要能一眼看到哪几项走的是这条路, 交独立监督核对外部单位是否属实。
CREATE OR REPLACE VIEW das_release_external_verifier AS
SELECT v.batch_code, v.item_code, i.name_cn AS item_name, v.verified_by,
       v.build_ref, v.result, v.verified_at
  FROM das_release_verification v
  JOIN das_release_check_item i ON i.code = v.item_code
 WHERE v.verifier_is_external
 ORDER BY v.batch_code, i.seq, v.verified_at DESC;

COMMENT ON VIEW das_release_self_verified IS
    '验证执行人同时是投用批准人的组合。可见性清单而非阻断：UG-DAW-005 未要求两人，5～8 人编制下某些项避不开；交独立监督核对（判据 I6 的同形问题）。';

-- 6.5 控制项的层级声明是否与子项一致（同 0041 的做法, 平时应为空）。
CREATE OR REPLACE VIEW das_release_item_mismatch AS
SELECT i.code, i.name_cn, i.is_container,
       (SELECT count(*) FROM das_release_check_item c WHERE c.parent_code = i.code)
                                                                   AS children,
       CASE WHEN i.is_container
                 AND (SELECT count(*) FROM das_release_check_item c
                       WHERE c.parent_code = i.code) = 0
            THEN '声明为容器却没有子机制: 结果无处可落'
            WHEN NOT i.is_container
                 AND (SELECT count(*) FROM das_release_check_item c
                       WHERE c.parent_code = i.code) > 0
            THEN '有子机制却没声明为容器: 会被当成叶子项直接记结果'
       END AS issue
  FROM das_release_check_item i
 WHERE (i.is_container) <> ((SELECT count(*) FROM das_release_check_item c
                              WHERE c.parent_code = i.code) > 0);

-- ---------------------------------------------------------------------
-- 7. 六项控制的内容（配置数据）
-- ---------------------------------------------------------------------
-- 验证内容不是这里编的: 每一项都指回具体判据。"怎样才算验过"写得具体到可以照着做,
-- 否则它会退化成一句"已测试"。
INSERT INTO das_release_check_item
    (code, parent_code, seq, name_cn, requirement, verify_content, source_doc,
     criteria_ref, is_container, min_sample, note)
VALUES
('IDENTITY', NULL, 1, '身份',
 '一自然人一账号，账号与岗位解耦；不得存在绕过独立性约束的超级用户',
 '以反例验证：① 同一工号不得建两个账号（uq_app_user_employee_no）；'
 '② 系统管理员账号尝试签署须被拒（判据 N11）；'
 '③ 三签的"不同自然人"按工号判而非按账号判。'
 '**已知缺口**：employee_no 仍可为空，同一人持一个有工号、一个无工号的账号仍能通过'
 '（判据 A1-2），验证记录须如实写明这一点，不得以"已验证"掩盖。',
 'UG-DAW-005 第 1 章、第 2 章', 'N3、N11、N12、A1-2', false, NULL, NULL),

('AUTHORISATION', NULL, 2, '授权',
 '授权的申请、变更、撤销有规定的办理方式与批准人；授权条目仅可撤销不可删除；'
 '撤销、到期、离岗当日生效',
 '以反例验证：① 为自己授权被拒（判据 I9）；② 授权条目的 DELETE 被拒、只能撤销'
 '（判据 N14）；③ 生效日期未到与已过期的授权不得用于签署（有效期边界用例）；'
 '④ 撤销时间戳与触发事件同日可核验。'
 '**专业与文件类型两维不得计为已验证**：专业维度调用方未传时不施加限制且无反例用例；'
 '文件类型校验的是 DCMS 文档种类而非 5 类适航签署事项（语义不符，判据 I10.FILE_TYPE）。',
 'UG-DAW-005 第 4 章、第 6 章', 'N4、N14、I9、I10', false, NULL, NULL),

('INDEPENDENCE', NULL, 3, '独立性',
 '判据 I1～I10 的独立性约束均有实现、有反例用例、且在本构建上执行过',
 '直接取独立性规则登记册（迁移 0041）：① das_release 验证时 '
 'das_independence_gap 的 DEFECT 类必须为空（对象缺失或上次跑失败）；'
 '② das_independence_object_missing 与 das_independence_container_mismatch 为空；'
 '③ 其余缺口逐条抄进本次验证记录的证据位置，**并按判据 I10 不得给出"几条已实现"的总数**。'
 '本项的验证不等于"全部已实现"——登记册里 PARTIAL 与 NOT_IMPLEMENTED 的项'
 '由各自的人工控制承接，验证要确认那些人工控制确实写明了。',
 'UG-DAW-005 第 1 章', 'I1～I10、I-总、E2', false, NULL, NULL),

('FREEZE', NULL, 4, '冻结',
 '发现问题时能立即阻止继续使用或继续处置',
 '本项为容器：证据按机制分别给（见两个子项）。',
 'UG-DAW-005 第 1 章', 'N2、R5', true, NULL,
 'UG-DAW-005 只写"冻结"两个字，但本系统里这是两个不同的机制：版次冻结（N2，'
 '停止使用有疑义的版本）与记录冻结（R5，冻结期间拒绝销毁）。不同的表、不同的触发器、'
 '不同的失效后果。只测一个而记"冻结已验证"是一条假符合。**拆分是本文件的解释，'
 '不是源文件的措辞**；父项编号保持与 UG-DAW-005 一致，自评仍可答"六项"。'),

('FREEZE.VERSION', 'FREEZE', 4, '冻结·版次',
 '纸质件与电子件不一致时，能一键登记不符合项并冻结该版次，立即停止使用',
 '以反例验证：冻结后的版次不得被取用、不得进入发放；冻结动作与 NCR 登记同一事务，'
 '不得出现"登了 NCR 但版次还在用"或"冻了版次但没有 NCR"。',
 'UG-DAW-005 第 9 章', 'N2', false, NULL, NULL),

('FREEZE.RECORD', 'FREEZE', 4, '冻结·记录',
 '调查、诉讼、争议、局方要求或未关闭事项期间，受管记录不得销毁',
 '以反例验证：① 冻结期间执行销毁被**拒绝**而不是被跳过（判据 R5）；'
 '② 冻结须写明解除条件，解除只有在任适航管理负责人能做；'
 '③ 关联期限未到（retention_until 为 NULL）时销毁被拒 —— 算不出来不等于可以销毁。',
 'UG-DAW-004 第 1 章通则', 'R2、R5', false, NULL, NULL),

('LOGGING', NULL, 5, '日志',
 '审计日志记录操作人、时间、动作与新旧值；写入后不得修改或删除；'
 '更正记录保留原值、修改人、时间和复核轨迹，禁止覆盖已签内容',
 '以反例验证：① UPDATE 与 DELETE 审计日志均被拒（trg_audit_log_immutable → '
 'dcms_block_write）；② 一次被拒的操作也要留下记录（登录失败走自治事务，'
 '请求事务回滚不会把它一并撤销）；③ 签署内容摘要在签署后不可改，只能升版（判据 N7）。'
 '**已知缺口**：判据 N8 要求的"更正记录关联关系"尚未实现，验证记录须如实写明。',
 'UG-DAW-005 第 1 章、手册 0.4.3', 'N7、N8', false, NULL, NULL),

('BACKUP_RESTORE', NULL, 6, '备份恢复',
 '备份覆盖数据库、上传文件、签署记录和日志四项；恢复验证须还原到测试环境'
 '抽查文件并逐项核对内容、版次和签署记录完整性；验证失败即为不符合项',
 '恢复验证**不是"能启动"**：须实际还原到测试环境，抽查不少于 min_sample 份文件，'
 '对每份核对内容、版次、签署记录三项并记录结论。备份范围四项缺一即为不符合项。'
 '备份每日、可恢复性验证每季度（判据 Q2 取较严，见 das_deadline_param 的 '
 'N6.BACKUP_CYCLE 与 N6.RESTORE_VERIFY_CYCLE）。备份与验证记录保存 5 年'
 '（UG-DAW-004 类别 P01.05）。',
 'UG-DAW-005 第 7 章、UG-DAP-01 第 14 步', 'N6、N15', false, 5, NULL)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 8. 批次（设计输入第 8.6 节）
-- ---------------------------------------------------------------------
-- build_ref 一律留空: 构建号要由实际发布时填, 预先编一个进去等于伪造验证对象。
INSERT INTO das_release_batch (code, name_cn, scope_note, precondition, note)
VALUES
('BASE', '基础能力',
 '时限与周期引擎（五个正交属性＋T1-2 八字段）、权限与独立性校验、签署、'
 '留存与保管期限、审计日志',
 '与第一批同时具备；按判据 N16 完成上线前六项验证并留结果',
 NULL),
('B1', '第一批',
 '符合性检查单（主数据）→ M1 → M6 → M7 → M2',
 '基础能力就绪；**顺序不可颠倒**——检查单是锚点，它的数据结构决定其余模块怎么往上挂；'
 'M1 紧随其后，因为 M6／M7／M2 的每一条记录都要落到具体的授权岗位上',
 NULL),
('B2', '第二批', 'M3（含项目实体）→ M4 → M5',
 '第一批已投用，且**经过一次独立监督覆盖**', NULL),
('B3', '第三批', 'M8、M9、M10、M11', '—', NULL)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 10. 一处取舍: 验证失败挂的那条不符合项算哪个来源
-- ---------------------------------------------------------------------
-- 判据 N6／N15 要求验证失败即为不符合项, 所以 FAIL 必须挂 das_ncr。但 das_ncr 的
-- 来源取值（0037 的 ck_das_ncr_source）是 CAAC／INTERNAL_AUDIT／SUPERVISION／
-- INSPECTION／SUPPLIER／OCCURRENCE 六类, **没有"上线前验证"这一类**。
-- 本模块取 INSPECTION（检查）: 上线前六项验证是一次计划内的检查, 六类里它最贴近。
--
-- 没有从这里去改 0037 的那条 CHECK, 理由有两条:
--   ① 来源分类影响的是 M7 的统计与趋势分析口径, 动它要连着 UG-DAP-14 的记录一起看,
--      不是本模块能单方面决定的;
--   ② 跨迁移改动前面迁移建的对象, 在备份恢复的往返流程里要格外小心（0039 第 9 节
--      已经栽过一次: 替换视图时改了列集, 恢复一份新 schema 的备份再从 0001 重放,
--      失败点在改动之前）。CHECK 不同于视图, 但同类风险要先看清再动。
-- 如果日后认为"上线前验证"应当自成一类, 那是 0037 的修订, 并且要同时说明历史记录
-- 怎么归类 —— 把已经记成 INSPECTION 的那些留在原处而新记录换类, 统计就断了。
--
-- ---------------------------------------------------------------------
-- 9. 补上 N6 的两个周期参数
-- ---------------------------------------------------------------------
-- 0039 种了 16 个时限参数, 没有一条是备份或恢复验证的 —— 而"备份恢复"这一项的
-- 验证内容本身依赖这两个周期。缺了它们, 这一项只能验"今天跑过一次",
-- 验不了"按规定的频次在跑"。
INSERT INTO das_deadline_param
    (code, name_cn, module_code, constraint_source, trigger_mode, start_point,
     calendar_basis, change_authority, source_doc, note)
VALUES
('N6.BACKUP_CYCLE', '服务器备份频次', 'BASE', 'INTERNAL', 'PERIODIC',
 '上一次备份完成之日', 'CALENDAR', 'UG-DAF-09 分类闸门（不得超过上限）',
 'UG-DAW-005 第 7 章',
 '判据 N6: 服务器备份每日。备份范围四项（数据库、上传文件、签署记录、日志）'
 '缺一即为不符合项, 范围由判据 N15 管, 不在本参数里'),
('N6.RESTORE_VERIFY_CYCLE', '可恢复性验证频次', 'BASE', 'INTERNAL', 'PERIODIC',
 '上一次恢复验证完成之日', 'CALENDAR', 'UG-DAF-09 分类闸门（不得超过上限）',
 'UG-DAW-005 第 7 章、UG-DAP-01 步骤 14',
 '**源文件频次冲突**: UG-DAW-001 第 3 章（UG-DAP-01 步骤 14）写"每季度", '
 '同文件第 4 章写"每半年"。按判据 Q2, 冲突关闭前一律取较严的每季度; '
 '冲突为第九之三节第 12 条, 未关闭。恢复验证不是"能启动"（判据 N15）')
ON CONFLICT (code) DO NOTHING;

-- 初值。同 0039 的口径: 生效日期取系统纪元, 不取体系文件基线日 ——
-- 这两个频次在系统之前就已经成立, 填成起草日会让那之前的记录取不到值。
INSERT INTO das_deadline_value
    (param_code, value_num, value_unit, effective_from, is_baseline, baseline_source,
     activated_at)
SELECT b.code, b.num, b.unit, DATE '2000-01-01', true, b.src, now()
  FROM (VALUES
    ('N6.BACKUP_CYCLE', 1, 'DAY',
     'UG-DAW-005 第 7 章「服务器备份每日」'),
    ('N6.RESTORE_VERIFY_CYCLE', 3, 'MONTH',
     'UG-DAW-001 第 3 章「每季度」（判据 Q2 取较严; 第 4 章的"每半年"冲突未关闭）')
  ) AS b(code, num, unit, src)
 WHERE NOT EXISTS (SELECT 1 FROM das_deadline_value x WHERE x.param_code = b.code);
