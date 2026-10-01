-- 资格评估（UG-DAF-06）与登记册月度核对（UG-DAW-005 第 4 章）。M1 的最后两块。
--
-- 依据 UG-DAP-03 第 6 章步骤 3（资格评估）、步骤 4（独立性核对）、步骤 5（授权），
-- UG-DAW-005 第 4 章（每月核对系统登记册与《授权人员名单》），
-- UG-RPT-2026-003 J 版判据 E1、N12、N13、A1、I3。
--
-- 【判据 E1: 授权的前提不止"未过期"】
-- UG-DAP-03 第 6 章要求同时满足资格评估、培训有效、独立性核对通过、实际生效日期
-- 和适用的认可依据。培训那一半已由 0033 的闸门实现, 这里补资格评估这一半:
-- 没有结论为"同意授权"的 UG-DAF-06, 不得授权。
--
-- 【三签必须是三个不同自然人, 用工号判】
-- UG-DAF-06 有编制、审核/核查、批准三栏。判据 I3 要的是三个不同**自然人**,
-- 而不是三个不同账号——这正是 0031 给 employee_no 加唯一索引的用处:
-- 有了自然人标识, "三个不同的人"才判得出来。没填工号的账号不能出现在这三栏里。
--
-- 【一处诚实的边界】
-- 表单的"授权文件类型"是 4 类**适航签署事项**(更改分类/符合性声明/小改批准/CVE 核查),
-- 与系统里 signer_authorization.level 的 REVIEW/APPROVE/CVE **不是一回事**——
-- 后者是文档签署级别。二者的对应关系是待澄清项第 1 条, 要等 M3／M4 落地。
-- 所以本迁移**记录**表单勾选的签署事项, 但授权闸门只校验"有无同意授权的评估",
-- 不假装两者能自动对上。判据 M1-1 已写明: 三维齐备前不得因为界面上有字段就称已实现。
--
-- 【月度核对: 系统只能提供一侧】
-- UG-DAW-005 第 4 章要核对的是系统登记册与《授权人员名单》(UG-DAM-01-附2, 受控文档)。
-- 系统拿不到那份 Word 文档的内容, **差异比对是人做的**。系统能做的是:
-- 给出自己这一侧的快照、记录核对结果与纠正动作、跟踪下次到期。
-- 把它做成"系统自动核对通过"会是假的。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 资格评估（UG-DAF-06）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_qualification_assessment (
    id                  bigserial PRIMARY KEY,
    user_id             uuid        NOT NULL REFERENCES app_user (id),
    form_ref            text,                       -- 表单编号(UG-DAW-002 的记录编号规则)
    -- 表单字段
    intended_positions  text,                       -- 姓名/岗位
    intended_scope      text,                       -- 拟授权专业与产品类别
    sign_types          text[]      NOT NULL,       -- 更改分类/符合性声明/小改批准/CVE核查
    education_experience text       NOT NULL,       -- 学历与专业经验
    training_evidence   text        NOT NULL,       -- CCAR-21 及 DAS 培训
    prior_work          text,                       -- 过往相关工作证明
    -- 独立性核对(表单的两个勾)。系统另有岗位级互斥校验(0032), 两者互不替代:
    -- 勾选是评估人的判断, 互斥校验是机器能查的那部分。
    indep_no_self_check boolean     NOT NULL DEFAULT false,
    indep_no_ism_conflict boolean   NOT NULL DEFAULT false,
    valid_from          date,
    valid_to            date,
    backup_user_id      uuid REFERENCES app_user (id),
    conclusion          text        NOT NULL,       -- AGREE 同意授权 / REJECT 不同意
    scope_conditions    text,                       -- 授权范围与生效条件
    -- 三签(判据 I3: 三个不同自然人)
    prepared_by         uuid        NOT NULL REFERENCES app_user (id),
    reviewed_by         uuid        NOT NULL REFERENCES app_user (id),
    approved_by         uuid        NOT NULL REFERENCES app_user (id),
    prepared_at         timestamptz NOT NULL DEFAULT now(),
    reviewed_at         timestamptz,
    approved_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_qa_conclusion CHECK (conclusion IN ('AGREE', 'REJECT')),
    CONSTRAINT ck_das_qa_dates CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
    -- 同意授权的, 独立性两项必须都已核对——不勾就同意, 等于跳过步骤 4。
    CONSTRAINT ck_das_qa_indep
        CHECK (conclusion <> 'AGREE' OR (indep_no_self_check AND indep_no_ism_conflict)),
    CONSTRAINT ck_das_qa_backup_not_self CHECK (backup_user_id IS NULL OR backup_user_id <> user_id)
);

CREATE INDEX IF NOT EXISTS idx_das_qa_user ON das_qualification_assessment (user_id, created_at DESC);

-- 三签必须是三个不同自然人。用工号判, 不用账号判(判据 A1、I3)。
CREATE OR REPLACE FUNCTION dcms_check_das_qualification() RETURNS trigger AS $$
DECLARE
    v_emp text[];
    v_missing text;
BEGIN
    SELECT array_agg(DISTINCT btrim(employee_no)) INTO v_emp
      FROM app_user WHERE id IN (NEW.prepared_by, NEW.reviewed_by, NEW.approved_by);

    SELECT string_agg(full_name, '、') INTO v_missing
      FROM app_user
     WHERE id IN (NEW.prepared_by, NEW.reviewed_by, NEW.approved_by)
       AND (employee_no IS NULL OR btrim(employee_no) = '');
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION
            'DCMS-INV-035: % 未填工号。工号是判定"三个不同自然人"的唯一依据（判据 A1），未填不得担任评估表的签署人'
            , v_missing USING ERRCODE = '23514';
    END IF;

    IF array_length(v_emp, 1) <> 3 THEN
        RAISE EXCEPTION
            'DCMS-INV-036: 资格评估表的编制、审核、批准须为三个不同自然人（判据 I3）；按工号判定只有 % 人'
            , array_length(v_emp, 1) USING ERRCODE = '23514';
    END IF;

    -- 不得自己评估自己
    IF NEW.user_id IN (NEW.prepared_by, NEW.reviewed_by, NEW.approved_by) THEN
        RAISE EXCEPTION 'DCMS-INV-037: 被评估人不得担任本人资格评估表的任何签署人'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_qualification_check ON das_qualification_assessment;
CREATE TRIGGER trg_das_qualification_check
    BEFORE INSERT OR UPDATE ON das_qualification_assessment
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_qualification();

-- 评估记录 append-only: 结论与签署是事实, 改判请另评一次。
CREATE OR REPLACE FUNCTION dcms_guard_das_qualification() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 资格评估记录不得删除' USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'DCMS-AUDIT: 资格评估记录不得修改, 改判请新增一次评估'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_qualification_guard ON das_qualification_assessment;
CREATE TRIGGER trg_das_qualification_guard
    BEFORE UPDATE OR DELETE ON das_qualification_assessment
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_qualification();

-- 当前有效的资格评估（授权闸门查这个）
CREATE OR REPLACE VIEW das_qualification_current AS
SELECT DISTINCT ON (q.user_id)
       q.id, q.user_id, u.full_name, q.form_ref, q.sign_types, q.conclusion,
       q.valid_from, q.valid_to, q.scope_conditions, q.created_at,
       p.full_name AS prepared_by_name, r.full_name AS reviewed_by_name,
       a.full_name AS approved_by_name,
       (q.conclusion = 'AGREE'
        AND (q.valid_from IS NULL OR q.valid_from <= current_date)
        AND (q.valid_to IS NULL OR q.valid_to >= current_date)) AS in_force
  FROM das_qualification_assessment q
  JOIN app_user u ON u.id = q.user_id
  JOIN app_user p ON p.id = q.prepared_by
  JOIN app_user r ON r.id = q.reviewed_by
  JOIN app_user a ON a.id = q.approved_by
 ORDER BY q.user_id, q.created_at DESC, q.id DESC;

-- ---------------------------------------------------------------------
-- 2. 登记册月度核对（UG-DAW-005 第 4 章、判据 N13）
--    频次冲突已于 2026-09-30 按判据 Q2 取严关闭: 每月一次。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_register_reconciliation (
    id               bigserial PRIMARY KEY,
    period_month     date        NOT NULL,      -- 核对所属月份(当月 1 日)
    reconciled_on    date        NOT NULL DEFAULT current_date,
    reconciled_by    uuid        NOT NULL REFERENCES app_user (id),
    reviewed_by      uuid REFERENCES app_user (id),   -- 判据 N12: 登记操作由第二人复核
    -- 系统侧快照: 核对当时系统登记册里的有效授权条数。
    -- 只存数字不存明细 —— 明细在 signer_authorization 里, 按时点可回溯, 不必抄一份。
    system_count     integer     NOT NULL,
    roster_ref       text        NOT NULL,      -- 比对的《授权人员名单》版次
    differences      integer     NOT NULL DEFAULT 0,
    difference_note  text,                      -- 差异内容
    corrections      text,                      -- 纠正动作(UG-DAW-005: 不一致的即时纠正并记录)
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_das_recon_month UNIQUE (period_month),
    -- 有差异就必须写清差异和纠正: 记一个数字而不说是什么, 等于没核对。
    CONSTRAINT ck_das_recon_detail
        CHECK (differences = 0 OR (difference_note IS NOT NULL AND corrections IS NOT NULL)),
    -- 判据 N12: 复核人不得是核对人本人。
    CONSTRAINT ck_das_recon_reviewer CHECK (reviewed_by IS NULL OR reviewed_by <> reconciled_by)
);

CREATE OR REPLACE FUNCTION dcms_guard_das_reconciliation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 核对记录不得删除' USING ERRCODE = '23514';
    END IF;
    -- 只允许补填复核人(N12 的第二人复核可能晚于核对本身)
    IF NEW.period_month <> OLD.period_month OR NEW.reconciled_by <> OLD.reconciled_by
       OR NEW.system_count <> OLD.system_count OR NEW.differences <> OLD.differences THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 核对记录的月份、核对人、快照和差异数不得修改'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_recon_guard ON das_register_reconciliation;
CREATE TRIGGER trg_das_recon_guard
    BEFORE UPDATE OR DELETE ON das_register_reconciliation
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_reconciliation();

-- 核对是否按月做了。判据 A7: 这里算的是"哪些月份没有记录", 不含任何人数或条数假设。
CREATE OR REPLACE VIEW das_reconciliation_due AS
SELECT m.period_month,
       r.id IS NOT NULL                   AS done,
       r.reconciled_on, r.differences,
       r.reviewed_by IS NOT NULL          AS second_person_reviewed
  FROM (SELECT generate_series(
                 date_trunc('month', COALESCE(
                     (SELECT min(period_month) FROM das_register_reconciliation),
                     date_trunc('month', current_date) - interval '11 months')),
                 date_trunc('month', current_date), interval '1 month')::date AS period_month) m
  LEFT JOIN das_register_reconciliation r ON r.period_month = m.period_month
 ORDER BY m.period_month DESC;

COMMENT ON VIEW das_reconciliation_due IS
    'UG-DAW-005 第 4 章要求每月核对系统登记册与《授权人员名单》。done=false 的月份即为漏做。';
