-- ---------------------------------------------------------------------
-- 0047  线下审批的证据上传（判据 B1 的落点）
-- ---------------------------------------------------------------------
-- 依据 2026-10-02 的业务决策：**系统实现不了的走线下审批, 证据资料上传系统即可。**
-- 以及设计输入第 8.6 节判据 B1（人工替代流程须逐项登记六项）、
-- 第六之二节判据 N-总（允许线下发生, 但线下发生的事必须补进系统）。
--
-- 【这个决定把什么从系统里搬走了, 又把什么留下了】
-- 搬走的是**判定过程**: 比如 UG-DAW-010 第 3 章的"逐条判据打勾并写明依据", 9 项判据
-- 一条条录进系统, 对 5～8 人的编制是过重的负担。
-- 留下的是**结论与证据**: 判据 N-总 原文"允许线下发生, 但线下发生的事必须补进系统"。
-- 所以体系文件的要求没有变松, 只是改了承接方式 —— 而承接方式一改,
-- 符合性自评要指向的东西就从"系统里那 9 行"变成"系统里那份扫描件"。
-- **没有上传的证据, 自评里那一格就是空的。** 这是本迁移存在的理由。
--
-- 【为什么不是给每张表加一个 attachment 列】
-- 线下审批不是一个文件, 是一件事: 谁批的、哪天批的、用的哪张表单、编号是什么、
-- 对应哪条体系文件要求。只挂一个文件, 三个月后没人说得清那份扫描件是什么。
-- 所以一条线下审批记录带着这些字段, 文件挂在它下面。
--
-- 【判据 B1 的六项在这里**不是可选的**】
-- 原文: 每个尚未上线的模块, 其对应的体系要求须由已批准的人工替代流程承接,
-- 逐项登记下列六项, **缺一不得开展对应业务**: 线下责任人、表单与台账、核对频率、
-- 补录规则、退出条件、符合性自评表述。
-- 所以 das_manual_process 的这六列全部 NOT NULL —— 做成可空就会出现一堆
-- 只填了"线下责任人"的登记, 而那种登记在局方面前等于没有替代流程。
-- 待澄清项第 10 条（第一批各模块的人工替代流程未编制）就是这张表要填的东西。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 人工替代流程（判据 B1 的六项）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_manual_process (
    code         text PRIMARY KEY,          -- 如 M4.CRITERIA / M5.CONFIG_AUDIT
    scope_note   text NOT NULL,             -- 承接哪一条体系要求
    requirement_ref text NOT NULL,          -- 体系文件条款（文件编号＋条款号）
    -- 判据 B1 的六项, 一列一项, 全部必填。
    offline_owner text NOT NULL,            -- 线下责任人: 具体岗位, 不写部门
    forms_and_ledger text NOT NULL,         -- 用哪张 UG-DAF 表单、台账存放位置、保管岗位
    reconcile_frequency text NOT NULL,      -- 多久核对一次线下台账与系统的一致性
    backfill_rule text NOT NULL,            -- 何时补入系统、补录后由谁复核
    exit_condition text NOT NULL,           -- 该模块上线并通过验证后何时停用、历史数据怎么迁
    self_assessment_text text NOT NULL,     -- 在 UG-DAM-01-附3 中如实写明的那句话
    approved_by  uuid REFERENCES app_user (id),   -- 责任经理批准（判据 B1）
    approved_on  date,
    retired_on   date,                      -- 对应模块上线后停用之日
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- 六项缺一不可。写成可空就会出现一堆只填了线下责任人的登记,
    -- 而那种登记在局方面前等于没有替代流程。
    CONSTRAINT ck_dmp_six CHECK (
        length(btrim(offline_owner)) > 0 AND length(btrim(forms_and_ledger)) > 0
        AND length(btrim(reconcile_frequency)) > 0 AND length(btrim(backfill_rule)) > 0
        AND length(btrim(exit_condition)) > 0
        AND length(btrim(self_assessment_text)) > 0),
    -- 线下责任人要写具体岗位而不是部门（判据 B1 原文）。
    -- 这一条校验不了"是不是岗位", 但拦得住最常见的那几种写法。
    CONSTRAINT ck_dmp_owner CHECK (offline_owner NOT LIKE '%部%'
                                   OR offline_owner LIKE '%负责人%'
                                   OR offline_owner LIKE '%员%'
                                   OR offline_owner LIKE '%经理%'),
    CONSTRAINT ck_dmp_approved CHECK ((approved_by IS NULL) = (approved_on IS NULL)),
    CONSTRAINT ck_dmp_retired CHECK (retired_on IS NULL OR approved_on IS NOT NULL)
);

COMMENT ON TABLE das_manual_process IS
    '判据 B1 的人工替代流程登记。六项全部必填——原文「逐项登记下列六项，缺一不得开展对应业务」。待澄清项第 10 条要填的就是这张表。';

-- ---------------------------------------------------------------------
-- 2. 线下审批记录
-- ---------------------------------------------------------------------
-- 一条记录 = 一次线下审批。文件挂在它下面（第 3 节）。
CREATE TABLE IF NOT EXISTS das_offline_approval (
    id           bigserial PRIMARY KEY,
    -- 挂在哪条系统记录上。用 (对象类型, 对象键) 而不是给每张表加外键:
    -- 线下审批会挂到很多种对象上, 每种加一个可空外键会让这张表长出十几个空列,
    -- 而那十几个列里永远只有一个非空。
    object_type  text NOT NULL,             -- DAS_DESIGN_CHANGE / DAS_MINOR_APPROVAL / ...
    object_key   text NOT NULL,             -- 该对象的业务编号（更改单号、批准单号……）
    manual_process_code text REFERENCES das_manual_process (code),
    -- 线下审批本身: 谁批的、哪天、用的哪张表单、编号。
    subject      text NOT NULL,             -- 批的是什么事
    approver     text NOT NULL,             -- 线下批准人（岗位＋姓名, 可能是外部）
    approved_on  date NOT NULL,
    form_ref     text NOT NULL,             -- 表单编号与版次（如 UG-DAF-02-2026-0007）
    conclusion   text NOT NULL,             -- 线下结论
    note         text,
    -- 补录进系统的人与时间。判据 N1: 补录须同时保留实际发生时间与补录时间。
    recorded_by  uuid NOT NULL REFERENCES app_user (id),
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    -- 判据 N-总: 补录数据在完成复核并发布之前不具有权威性。
    -- 所以复核是单独的两列, 不是默认已复核。
    reviewed_by  uuid REFERENCES app_user (id),
    reviewed_on  date,
    CONSTRAINT ck_doa_text CHECK (
        length(btrim(object_type)) > 0 AND length(btrim(object_key)) > 0
        AND length(btrim(subject)) > 0 AND length(btrim(approver)) > 0
        AND length(btrim(form_ref)) > 0 AND length(btrim(conclusion)) > 0),
    CONSTRAINT ck_doa_reviewed CHECK ((reviewed_by IS NULL) = (reviewed_on IS NULL)),
    -- 补录时间不得早于实际审批日期。倒过来说明有人把补录时间当成了审批日期。
    CONSTRAINT ck_doa_order CHECK (recorded_at::date >= approved_on)
);

CREATE INDEX IF NOT EXISTS idx_das_offline_approval_object
    ON das_offline_approval (object_type, object_key);

COMMENT ON TABLE das_offline_approval IS
    '一次线下审批的记录：批的是什么、谁批的、哪天、哪张表单、什么结论。证据文件挂在 das_offline_evidence。补录时间与实际审批日期分开保存（判据 N1）。';

-- ---------------------------------------------------------------------
-- 3. 证据文件
-- ---------------------------------------------------------------------
-- 复用现有的存储层（app/storage.py 的 storage_key ＋ sha256）, 不另建一套。
CREATE TABLE IF NOT EXISTS das_offline_evidence (
    id           bigserial PRIMARY KEY,
    approval_id  bigint NOT NULL REFERENCES das_offline_approval (id),
    filename     text NOT NULL,
    storage_key  text NOT NULL UNIQUE,
    mime_type    text NOT NULL,
    size_bytes   bigint NOT NULL,
    sha256       text NOT NULL,
    -- 判据 N1: 纸质签署的文件**扫描件与原件一并归档**。
    -- 所以要记原件在哪 —— 只有扫描件而不知道原件在哪, 局方要看原件时拿不出来。
    original_location text NOT NULL,
    uploaded_by  uuid NOT NULL REFERENCES app_user (id),
    uploaded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_doe_text CHECK (
        length(btrim(filename)) > 0 AND length(btrim(storage_key)) > 0
        AND length(btrim(sha256)) = 64
        AND length(btrim(original_location)) > 0),
    CONSTRAINT ck_doe_size CHECK (size_bytes > 0)
);

CREATE INDEX IF NOT EXISTS idx_das_offline_evidence_approval
    ON das_offline_evidence (approval_id);

COMMENT ON COLUMN das_offline_evidence.original_location IS
    '纸质原件的存放位置。判据 N1：扫描件与原件一并归档——只有扫描件而不知道原件在哪，局方要看原件时拿不出来。';

-- ---------------------------------------------------------------------
-- 4. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-115: 线下审批记录必须有证据文件。
-- 这是整个决定的落点: "证据资料上传系统即可" —— 那么没有上传的证据就不成立。
-- 一条只有结论没有扫描件的线下审批记录, 在自评里和没有记录是一回事,
-- 而它比没有记录更坏: 台账上看起来有。
-- 做成 AFTER 触发器并延迟到事务末尾, 否则插记录与挂文件就必须在同一条语句里。
CREATE OR REPLACE FUNCTION dcms_check_das_offline_evidence() RETURNS trigger AS $$
DECLARE
    v_n int;
BEGIN
    SELECT count(*) INTO v_n FROM das_offline_evidence WHERE approval_id = NEW.id;
    IF v_n = 0 THEN
        RAISE EXCEPTION 'DCMS-INV-115: 线下审批记录「%」还没有上传证据文件。'
                        '2026-10-02 的决定是"系统实现不了的走线下审批, **证据资料上传系统**" —— '
                        '没有上传的证据就不成立。只有结论没有扫描件的记录比没有记录更坏: 台账上看起来有。',
                        NEW.subject;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_offline_evidence_check ON das_offline_approval;
CREATE CONSTRAINT TRIGGER trg_das_offline_evidence_check
    AFTER INSERT ON das_offline_approval
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_offline_evidence();

-- DCMS-INV-116: 线下审批记录与证据文件不得删除或改写。
-- 它们替代的是系统里的审批, 所以要和系统里的审批一样不可抹改。
CREATE OR REPLACE FUNCTION dcms_guard_das_offline_append() RETURNS trigger AS $$
BEGIN
    -- 【表名要在外层判, 不能和字段比较写在同一个条件里】
    -- 本函数挂在两张表上, 而 das_offline_evidence 没有 reviewed_by 列。
    -- 把 TG_TABLE_NAME 和 OLD.reviewed_by 写进同一个 IF 的话, PL/pgSQL 要把整个
    -- 布尔表达式编成一条 SQL, 所有字段引用都得解析得出 —— 于是在证据表上触发时报
    -- 「record "old" has no field "reviewed_by"」, 而本该报的是 INV-116。
    -- 表名不是短路条件, 它决定了下面那些字段存不存在, 所以必须先判。
    IF TG_TABLE_NAME = 'das_offline_approval' AND TG_OP = 'UPDATE' THEN
        -- 复核两列允许从空填上（判据 N-总: 补录数据复核后才具有权威性）, 其余一律不许改。
        IF OLD.reviewed_by IS NULL AND NEW.reviewed_by IS NOT NULL
           AND NEW.object_type = OLD.object_type AND NEW.object_key = OLD.object_key
           AND NEW.subject = OLD.subject AND NEW.approver = OLD.approver
           AND NEW.approved_on = OLD.approved_on AND NEW.form_ref = OLD.form_ref
           AND NEW.conclusion = OLD.conclusion
           AND NEW.recorded_by = OLD.recorded_by
           AND NEW.recorded_at = OLD.recorded_at THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'DCMS-INV-116: % 不得%。它替代的是系统里的审批, 所以和系统里的审批一样不可抹改; '
                    '只有"复核人／复核日期"两列可以从空填上（判据 N-总: 补录数据复核后才具有权威性）。'
                    '更正请另记一条并写明理由。',
                    TG_TABLE_NAME,
                    CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_offline_approval_append ON das_offline_approval;
CREATE TRIGGER trg_das_offline_approval_append
    BEFORE UPDATE OR DELETE ON das_offline_approval
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_offline_append();

DROP TRIGGER IF EXISTS trg_das_offline_evidence_append ON das_offline_evidence;
CREATE TRIGGER trg_das_offline_evidence_append
    BEFORE UPDATE OR DELETE ON das_offline_evidence
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_offline_append();

-- DCMS-INV-117: 人工替代流程未经责任经理批准, 不得据以开展业务。
-- 判据 B1 原文: 须由**已批准的**人工替代流程承接。
-- 所以未批准的登记不能被线下审批记录引用。
CREATE OR REPLACE FUNCTION dcms_check_das_offline_process() RETURNS trigger AS $$
DECLARE
    v_approved date;
    v_retired  date;
BEGIN
    IF NEW.manual_process_code IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT approved_on, retired_on INTO v_approved, v_retired
      FROM das_manual_process WHERE code = NEW.manual_process_code;
    IF v_approved IS NULL THEN
        RAISE EXCEPTION 'DCMS-INV-117: 人工替代流程「%」尚未经责任经理批准。'
                        '判据 B1: 须由**已批准的**人工替代流程承接 —— 未批准就照着做, '
                        '等于那条体系要求此刻既不在系统里、也不在一个受控的线下流程里。',
                        NEW.manual_process_code;
    END IF;
    IF v_retired IS NOT NULL AND NEW.approved_on > v_retired THEN
        RAISE EXCEPTION 'DCMS-INV-117: 人工替代流程「%」已于 % 停用（对应模块已上线）, '
                        '此后的审批应走系统。判据 B1 第六项: 退出条件。',
                        NEW.manual_process_code, v_retired;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_offline_process_check ON das_offline_approval;
CREATE TRIGGER trg_das_offline_process_check
    BEFORE INSERT ON das_offline_approval
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_offline_process();

-- ---------------------------------------------------------------------
-- 5. 视图
-- ---------------------------------------------------------------------

-- 5.1 线下审批台账（带证据份数与复核状态）。
CREATE OR REPLACE VIEW das_offline_approval_register AS
SELECT a.id, a.object_type, a.object_key, a.subject, a.approver, a.approved_on,
       a.form_ref, a.conclusion, a.manual_process_code,
       (SELECT count(*) FROM das_offline_evidence e WHERE e.approval_id = a.id)
                                                                   AS evidence_files,
       u.full_name AS recorded_by_name, a.recorded_at,
       (a.recorded_at::date - a.approved_on)                       AS backfill_lag_days,
       (a.reviewed_by IS NOT NULL)                                AS reviewed,
       r.full_name AS reviewed_by_name, a.reviewed_on
  FROM das_offline_approval a
  JOIN app_user u ON u.id = a.recorded_by
  LEFT JOIN app_user r ON r.id = a.reviewed_by
 ORDER BY a.approved_on DESC;

-- 5.2 **补录了但还没复核的。**
-- 判据 N-总: 补录数据在完成复核并发布之前**不具有权威性**。
-- 所以这一档不是"待办", 是"这些线下结论现在还不能当依据用"。
CREATE OR REPLACE VIEW das_offline_approval_unreviewed AS
SELECT * FROM das_offline_approval_register WHERE NOT reviewed;

COMMENT ON VIEW das_offline_approval_unreviewed IS
    '补录了但还没复核的线下审批。判据 N-总：补录数据在完成复核并发布之前不具有权威性——所以这不是「待办」，是「这些结论现在还不能当依据用」。';

-- 5.3 人工替代流程的登记情况。
-- 六项齐备由 CHECK 保证, 这里看的是**批准**与**停用**两件事:
-- 未批准的流程按判据 B1 不得据以开展业务; 对应模块上线后要停用, 否则会出现
-- 系统和线下两套都在走、而两套说法不一致的局面。
CREATE OR REPLACE VIEW das_manual_process_status AS
SELECT p.code, p.scope_note, p.requirement_ref, p.offline_owner,
       p.reconcile_frequency, p.approved_on, p.retired_on,
       (SELECT count(*) FROM das_offline_approval a
         WHERE a.manual_process_code = p.code)                     AS used_by,
       CASE WHEN p.approved_on IS NULL THEN 'DRAFT'
            WHEN p.retired_on IS NOT NULL THEN 'RETIRED'
            ELSE 'IN_FORCE'
       END                                                         AS state
  FROM das_manual_process p
 ORDER BY p.code;

-- 5.4 **停用之后还在用的线下流程。**
-- 对应模块上线了、流程停用了, 却还有新的线下审批挂在它上面 —— 说明系统和线下两套在并行,
-- 而判据 B1 第六项（退出条件）要的正是一刀切清。
CREATE OR REPLACE VIEW das_manual_process_overrun AS
SELECT p.code, p.retired_on, a.id AS approval_id, a.subject, a.approved_on
  FROM das_manual_process p
  JOIN das_offline_approval a ON a.manual_process_code = p.code
 WHERE p.retired_on IS NOT NULL AND a.approved_on > p.retired_on
 ORDER BY a.approved_on DESC;

-- 5.5 按对象类型汇总: 哪几类业务现在靠线下承接。
-- 这是符合性自评要抄的那一栏: 哪些要求由线下流程执行、系统未实现。
CREATE OR REPLACE VIEW das_offline_coverage AS
SELECT a.object_type,
       count(*)                                                    AS approvals,
       count(DISTINCT a.object_key)                                AS objects,
       count(*) FILTER (WHERE a.reviewed_by IS NULL)               AS unreviewed,
       min(a.approved_on)                                          AS earliest,
       max(a.approved_on)                                          AS latest,
       coalesce(string_agg(DISTINCT a.manual_process_code, '、'), '(未挂替代流程)')
                                                                   AS manual_processes
  FROM das_offline_approval a
 GROUP BY a.object_type
 ORDER BY a.object_type;
