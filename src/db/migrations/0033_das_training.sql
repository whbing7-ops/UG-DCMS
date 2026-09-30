-- 培训与考核（M1 的第二块）。
--
-- 依据 UG-DAW-006《培训课程和考核要求》、UG-DAP-03 第 6 章步骤 10、
-- UG-RPT-2026-003 J 版判据 P1（作业文件参数是受控配置）与 P5（不含产品技术参数）。
--
-- 【UG-DAW-006 里带牙齿的三条规则】
-- 这份作业文件不只是"记下来"，它规定了三条有系统后果的规则:
--   1. 初始培训: 新入职、转岗、**首次授权前**必须完成并考核合格;
--   2. 考核不合格的补考一次; **补考仍不合格的不得授权**;
--   3. 已授权人员**复训考核不合格的, 暂停其签署权**直至重新合格。
-- 只把培训做成一张记录表, 这三条就落不了地——它们要在授权和签署路径上生效。
--
-- 【判据 P1: 课程矩阵与合格标准是配置, 不是代码】
-- 课程、学时、考核方式、合格标准、课程与岗位的对应关系, 全部来自第三级作业文件,
-- 由归口负责人按 UG-DAP-02 分类后修订。写死在代码里就等于把作业文件塞进了一级文件。
-- 本迁移把 UG-DAW-006 现行内容作为**初值**种入, 之后由系统内维护。
--
-- 【一处明确的依赖】
-- 复训周期"至少每年一次"出自 UG-DAW-001 第 4 章。在时限引擎(基础能力)落地前,
-- 这里用课程上的 valid_months 承载, 默认 12。引擎就绪后应改为从 UG-DAW-001 取值,
-- 避免同一个周期在两处各写一份(判据 T1: 内部时限必须从一处取值)。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 课程（UG-DAW-006 第 2、3 章）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_training_course (
    code           text PRIMARY KEY,
    name_cn        text    NOT NULL,
    category       text    NOT NULL,   -- 法规类/手册和程序/专业操作类/系统操作/审核技能
    hours          numeric(4,1),       -- 建议学时
    assessment     text,               -- 考核方式
    pass_standard  text,               -- 合格标准
    valid_months   integer NOT NULL DEFAULT 12,   -- 见文件头"一处明确的依赖"
    is_active      boolean NOT NULL DEFAULT true,
    basis          text,
    sort_order     integer NOT NULL DEFAULT 0
);

INSERT INTO das_training_course (code, name_cn, category, hours, assessment, pass_standard, basis, sort_order) VALUES
('C01', '民航法规体系与 CCAR-21 概要',              '法规类',     4, '闭卷笔试',        '80 分以上', 'UG-DAW-006 第 2、3 章', 10),
('C02', 'CCAR-21 第十四章与 AP-21-18',              '法规类',     4, '闭卷笔试',        '80 分以上', 'UG-DAW-006 第 2、3 章', 20),
('C03', '本单位设计保证手册和程序',                  '手册和程序', 6, '笔试＋案例分析',  '80 分以上', 'UG-DAW-006 第 2、3 章', 30),
('C04', '设计更改分类（UG-DAP-06、UG-DAW-010）',     '专业操作类', 8, '案例作业＋实操',  '作业合格且实操无原则性错误', 'UG-DAW-006 第 2、3 章', 40),
('C05', '符合性验证与 CVE 核查（UG-DAP-07、UG-DAW-011）', '专业操作类', 8, '案例作业＋实操', '作业合格且实操无原则性错误', 'UG-DAW-006 第 2、3 章', 50),
('C06', '文件和记录控制（UG-DAP-01、UG-DAW-005/03）', '手册和程序', 6, '笔试＋案例分析',  '80 分以上', 'UG-DAW-006 第 2、3 章', 60),
('C07', '事件报告与 21.5（UG-DAP-12）',              '手册和程序', 6, '笔试＋案例分析',  '80 分以上', 'UG-DAW-006 第 2、3 章', 70),
('C08', '不符合项与纠正预防措施（UG-DAP-14）',        '手册和程序', 6, '笔试＋案例分析',  '80 分以上', 'UG-DAW-006 第 2、3 章', 80),
('C09', '审核技能与审核员资格（UG-DAP-13、UG-DAW-013）', '审核技能', 8, '笔试＋随岗审核', '随岗审核表现合格', 'UG-DAW-006 第 2、3 章', 90),
('C10', '供应商控制（UG-DAP-10、UG-DAW-012）',        '专业操作类', 8, '案例作业＋实操',  '作业合格且实操无原则性错误', 'UG-DAW-006 第 2、3 章', 100),
('C11', '电子系统操作与签署（UG-DAW-005）',          '系统操作',   2, '实操',            '能独立完成签署和查询', 'UG-DAW-006 第 2、3 章', 110)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. 课程与岗位的对应（UG-DAW-006 第 2 章矩阵：√必修 ○选修 —不要求）
--    矩阵按 UG-DAW-006 的 7 个岗位列种入; 本系统的岗位更细(das_position 14 个),
--    未列入矩阵的岗位默认不要求, 由归口负责人按需在系统内补。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_course_requirement (
    course_code   text NOT NULL REFERENCES das_training_course (code),
    position_code text NOT NULL REFERENCES das_position (code),
    level         text NOT NULL,      -- REQUIRED 必修 / OPTIONAL 选修
    PRIMARY KEY (course_code, position_code),
    CONSTRAINT ck_das_req_level CHECK (level IN ('REQUIRED', 'OPTIONAL'))
);

-- √ = REQUIRED。矩阵列 → 本系统岗位: 责任经理 AM、适航负责人 AWM、独立监督 ISM、
-- 授权人员 → 以 CVE 相关岗位承载(此处记在 RDM/PM 之外, 由授权表另行校验)、
-- 设计工程师 DE、资料管理 DCM、质量/采购 QSM。
INSERT INTO das_course_requirement (course_code, position_code, level) VALUES
('C01','AM','REQUIRED'),('C01','AWM','REQUIRED'),('C01','ISM','REQUIRED'),
('C01','DE','REQUIRED'),('C01','DCM','REQUIRED'),('C01','QSM','REQUIRED'),
('C02','AM','REQUIRED'),('C02','AWM','REQUIRED'),('C02','ISM','REQUIRED'),('C02','DE','REQUIRED'),
('C02','DCM','OPTIONAL'),('C02','QSM','OPTIONAL'),
('C03','AM','REQUIRED'),('C03','AWM','REQUIRED'),('C03','ISM','REQUIRED'),
('C03','DE','REQUIRED'),('C03','DCM','REQUIRED'),('C03','QSM','REQUIRED'),
('C04','AWM','REQUIRED'),('C04','ISM','REQUIRED'),('C04','DE','REQUIRED'),
('C04','AM','OPTIONAL'),('C04','QSM','OPTIONAL'),
('C05','AWM','REQUIRED'),('C05','DE','REQUIRED'),('C05','ISM','OPTIONAL'),
('C06','AWM','REQUIRED'),('C06','ISM','REQUIRED'),('C06','DE','REQUIRED'),
('C06','DCM','REQUIRED'),('C06','QSM','REQUIRED'),('C06','AM','OPTIONAL'),
('C07','AM','REQUIRED'),('C07','AWM','REQUIRED'),('C07','ISM','REQUIRED'),
('C07','DE','REQUIRED'),('C07','QSM','REQUIRED'),('C07','DCM','OPTIONAL'),
('C08','AM','REQUIRED'),('C08','AWM','REQUIRED'),('C08','ISM','REQUIRED'),
('C08','QSM','REQUIRED'),('C08','DE','OPTIONAL'),('C08','DCM','OPTIONAL'),
('C09','ISM','REQUIRED'),('C09','AUD','REQUIRED'),('C09','AWM','OPTIONAL'),('C09','QSM','OPTIONAL'),
('C10','QSM','REQUIRED'),('C10','AWM','OPTIONAL'),('C10','ISM','OPTIONAL'),
('C11','AWM','REQUIRED'),('C11','ISM','REQUIRED'),('C11','DE','REQUIRED'),
('C11','DCM','REQUIRED'),('C11','AM','OPTIONAL'),('C11','QSM','OPTIONAL')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- 3. 培训记录（UG-DAW-006 第 5 章）
--    append-only: 考核结果是事实, 改写等于篡改资格依据。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_training_record (
    id             bigserial PRIMARY KEY,
    user_id        uuid        NOT NULL REFERENCES app_user (id),
    course_code    text        NOT NULL REFERENCES das_training_course (code),
    kind           text        NOT NULL,   -- INITIAL 初始 / RECURRENT 复训 / SPECIAL 专项 / MENTORING 在岗带教
    trained_on     date        NOT NULL,
    hours          numeric(4,1),
    result         text        NOT NULL,   -- PASS / FAIL
    is_retake      boolean     NOT NULL DEFAULT false,   -- 补考(UG-DAW-006 第 3 章: 补考一次)
    certificate_ref text,                  -- 外部培训的证书或结业证明
    effectiveness  text,                   -- 第 4 章: 有效性评估结论
    evaluated_by   uuid REFERENCES app_user (id),
    recorded_by    uuid        NOT NULL REFERENCES app_user (id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_tr_kind CHECK (kind IN ('INITIAL', 'RECURRENT', 'SPECIAL', 'MENTORING')),
    CONSTRAINT ck_das_tr_result CHECK (result IN ('PASS', 'FAIL'))
);

CREATE INDEX IF NOT EXISTS idx_das_tr_user ON das_training_record (user_id, course_code, trained_on DESC);

CREATE OR REPLACE FUNCTION dcms_guard_das_training() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 培训记录不得删除' USING ERRCODE = '23514';
    END IF;
    -- 只允许补填有效性评估结论(第 4 章允许上岗后 3 个月内评估), 其余一律不可改。
    IF NEW.user_id <> OLD.user_id OR NEW.course_code <> OLD.course_code
       OR NEW.kind <> OLD.kind OR NEW.trained_on <> OLD.trained_on
       OR NEW.result <> OLD.result OR NEW.is_retake <> OLD.is_retake THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 培训记录的人员、课程、日期和考核结果不得修改'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_training_guard ON das_training_record;
CREATE TRIGGER trg_das_training_guard
    BEFORE UPDATE OR DELETE ON das_training_record
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_training();

-- 补考只允许一次(UG-DAW-006 第 3 章)。第二次补考直接拒绝, 而不是记下来再靠人发现。
CREATE OR REPLACE FUNCTION dcms_check_das_retake() RETURNS trigger AS $$
BEGIN
    IF NEW.is_retake AND EXISTS (
        SELECT 1 FROM das_training_record
         WHERE user_id = NEW.user_id AND course_code = NEW.course_code
           AND kind = NEW.kind AND is_retake AND id <> COALESCE(NEW.id, -1)) THEN
        RAISE EXCEPTION 'DCMS-INV-034: 同一课程的补考只允许一次（UG-DAW-006 第 3 章）'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_retake_check ON das_training_record;
CREATE TRIGGER trg_das_retake_check
    BEFORE INSERT OR UPDATE ON das_training_record
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_retake();

-- ---------------------------------------------------------------------
-- 4. 视图：培训是否达标
-- ---------------------------------------------------------------------

-- 4.1 每人每门必修课的当前状态。
--     OK 合格且在有效期内 / EXPIRED 已过复训期 / MISSING 从未参加 / FAILED 最近一次不合格
CREATE OR REPLACE VIEW das_training_status AS
SELECT f.user_id, f.full_name, f.position_code, f.position_name,
       r.course_code, c.name_cn AS course_name, c.valid_months,
       t.trained_on AS last_trained_on, t.result AS last_result, t.is_retake AS last_was_retake,
       CASE
         WHEN t.id IS NULL                                          THEN 'MISSING'
         WHEN t.result = 'FAIL'                                     THEN 'FAILED'
         WHEN t.trained_on + (c.valid_months || ' months')::interval < current_date
                                                                    THEN 'EXPIRED'
         ELSE 'OK'
       END AS status
  FROM das_appointment_in_force f
  JOIN das_course_requirement r ON r.position_code = f.position_code AND r.level = 'REQUIRED'
  JOIN das_training_course c ON c.code = r.course_code AND c.is_active
  LEFT JOIN LATERAL (
        SELECT * FROM das_training_record x
         WHERE x.user_id = f.user_id AND x.course_code = r.course_code
         ORDER BY x.trained_on DESC, x.id DESC LIMIT 1) t ON true;

COMMENT ON VIEW das_training_status IS
    'UG-DAW-006: 在任人员的必修课程达标情况。MISSING/FAILED/EXPIRED 任一存在即不满足授权前提。';

-- 4.2 应暂停签署权的人。
--     UG-DAW-006 第 3 章: 已授权人员复训考核不合格的, 暂停其签署权直至重新合格。
--     这里只**列出**, 不自动撤销授权——暂停是管理动作, 须有人决定并留痕(UG-DAP-03 步骤 11)。
CREATE OR REPLACE VIEW das_training_suspend_due AS
SELECT DISTINCT s.user_id, s.full_name,
       string_agg(DISTINCT s.course_name || '（' || s.status || '）', '；') AS reasons
  FROM das_training_status s
 WHERE s.status IN ('FAILED', 'EXPIRED')
   AND EXISTS (SELECT 1 FROM signer_authorization sa
                WHERE sa.user_id = s.user_id AND sa.revoked_at IS NULL
                  AND (sa.valid_to IS NULL OR sa.valid_to >= current_date))
 GROUP BY s.user_id, s.full_name;

COMMENT ON VIEW das_training_suspend_due IS
    'UG-DAW-006 第 3 章: 复训不合格或已过期而仍持有有效签署授权的人。只列出不自动撤销——暂停签署权是管理动作, 须有人决定并留痕。';
