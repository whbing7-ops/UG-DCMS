-- 岗位、任命与互斥规则（M1 人员培训与授权的核心结构）。
--
-- 依据 UG-RPT-2026-003 J 版第四节、第 4.1 节（账号与岗位模型 A1～A7）、
-- 第 4.2 节（出缺承接 O3-1～O3-4），以及 UG-DAM-01 3.1 与 UG-DAP-03 第 6 章。
--
-- 【这个结构要解决的问题】
-- 设计输入从 A 版起就指出: 岗位不能等同于账号角色。5～8 人编制必然一人多岗,
-- 所以需要 岗位 → 任命 → 互斥规则 三层, 让系统在**任命的那一刻**就拒绝冲突组合,
-- 而不是等到某人去签署时才发现他本来就不该同时担任这两个岗位。
--
-- 【一条必须划清的界: 哪些能在任命时拦, 哪些不能】
-- 体系文件里的独立性条款其实是两类, 混在一起实现会两头出错:
--
--   岗位级 —— 与具体文件无关, 任命时即可判定, 本迁移实现:
--     · 适航管理负责人 不得同时担任 独立监督负责人        (手册 3.1)
--     · 独立监督负责人 不得兼任其他运行管理人员, 亦不得承担被监督的运行活动
--     · 事件报告负责人 不得由 独立监督负责人 兼任
--     · 责任经理不兼任其他岗位                          (本单位决定)
--     · 适航管理负责人 专职 / 质量与供应商管理 专人 / 培训与授权管理 专人
--
--   文件级、项目级 —— 取决于"这一份资料是谁编的""这一个项目归谁",
--   任命时无从判断, **不在本迁移**, 由签署与审批环节拦截:
--     · 编制人不得同时担任本文件的核查人或批准人        (approval_step 约束)
--     · CVE 不得核查自己编制的文件                      (signers)
--     · 构型管理员可由设计工程师兼任, 但不得担任本项目设计更改的批准人
--     · 责任部门负责人不得自行验证关闭本部门的不符合项
--
-- 把文件级约束做成岗位互斥会过度限制(一人多岗本来就是常态);
-- 把岗位级约束留到签署时才判会来不及(人已经在那个位子上做了几个月的事)。
--
-- 【判据 A7: 不写死人数】本迁移只描述岗位与约束, 不含任何人数假设:
-- 没有"某岗位只能一人"的约束, 没有编制总数, 统计一律由当期数据算出。
-- 岗位由几人担任、全单位有多少人, 随业务变化, 不是系统该固化的事实。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 岗位字典（来自 UG-DAM-01 3.1）
--    is_exclusive: 手册写明"专任/专职/专人"的岗位, 其持有人不得兼任任何其他岗位。
--    is_ops_mgmt:  运行管理人员, 独立监督负责人不得兼任(手册 3.1)。
--    is_supervised: 被监督的运行活动, 独立监督负责人不得承担。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_position (
    code          text PRIMARY KEY,
    name_cn       text    NOT NULL,
    is_exclusive  boolean NOT NULL DEFAULT false,
    is_ops_mgmt   boolean NOT NULL DEFAULT false,
    is_supervised boolean NOT NULL DEFAULT false,
    basis         text,                       -- 该岗位及其限制的出处
    sort_order    integer NOT NULL DEFAULT 0
);

INSERT INTO das_position (code, name_cn, is_exclusive, is_ops_mgmt, is_supervised, basis, sort_order) VALUES
('AM',   '责任经理',               true,  true,  false, '手册 3.1；本单位决定不兼任其他岗位', 10),
('AWM',  '适航管理负责人',         true,  true,  false, '手册 3.1 资格表：专职（责任经理临时兼任不超过 6 个月）', 20),
('ISM',  '独立监督负责人',         true,  false, false, '手册 3.1：不得兼任适航管理负责人或其他运行管理人员，亦不得承担被监督的运行活动', 30),
('ORM',  '事件报告负责人',         false, true,  false, '手册 3.1：不得由独立监督负责人兼任；可兼任其他非监督岗位', 40),
('RDM',  '研发部负责人',           false, true,  false, '手册 3.1', 50),
('QSM',  '质量与供应商管理负责人', true,  true,  false, '手册 3.1；本单位决定设专人', 60),
('DCM',  '设计资料管理负责人',     false, true,  false, '手册 3.1', 70),
('TAM',  '培训与授权管理',         true,  true,  false, '本单位决定设专人（2026-09-30）。手册 3.1 尚无此岗位，新增须按 UG-DAP-02 分类后修订', 75),
('PM',   '项目负责人',             false, false, true,  '手册 3.1', 80),
('DE',   '设计工程师',             false, false, true,  '手册 3.1', 90),
('TE',   '试验工程师',             false, false, true,  '手册 3.1', 100),
('CFG',  '构型管理员',             false, false, true,  '手册 3.1：可由设计工程师兼任', 110),
('AUD',  '审核员',                 false, false, false, 'UG-DAP-13：独立监督下实施审核', 120),
('SYS',  '系统管理员',             false, false, false, 'UG-DAW-005 第 2 章：无签署权', 130)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. 岗位互斥规则（显式对，超出 is_exclusive 能表达的部分）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_position_exclusion (
    position_a text NOT NULL REFERENCES das_position (code),
    position_b text NOT NULL REFERENCES das_position (code),
    basis      text NOT NULL,
    PRIMARY KEY (position_a, position_b),
    CONSTRAINT ck_das_excl_distinct CHECK (position_a <> position_b)
);

INSERT INTO das_position_exclusion (position_a, position_b, basis) VALUES
('ISM', 'AWM', '手册 3.1：独立监督负责人不得同时担任适航管理负责人'),
('ISM', 'ORM', '手册 3.1：事件报告负责人不得由独立监督负责人兼任'),
('AWM', 'ISM', '手册 3.1：适航管理负责人不得同时担任独立监督负责人')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- 3. 任命
--    kind: FORMAL 正式任命（走 UG-DAP-03 步骤 2～5 全流程）
--          TEMPORARY 临时授权（出缺当日指定，判据 O3-1，必须有截止日）
--    判据 O3-2: 临时授权须关联正式任命流程的单据号, 只发临时而不启动正式的可被查出。
--    判据 O3-3: 正式任命生效时, 对应临时授权自动终止——不靠人记得去撤。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_appointment (
    id              bigserial PRIMARY KEY,
    user_id         uuid        NOT NULL REFERENCES app_user (id),
    position_code   text        NOT NULL REFERENCES das_position (code),
    kind            text        NOT NULL,
    valid_from      date        NOT NULL DEFAULT current_date,
    valid_to        date,
    appointed_by    uuid        NOT NULL REFERENCES app_user (id),
    appointment_ref text,                    -- 任命书/授权书编号
    formal_process_ref text,                 -- 判据 O3-2: 临时授权关联的正式流程单据号
    superseded_by   bigint REFERENCES das_appointment (id),   -- O3-3: 被哪条正式任命接替
    revoked_at      timestamptz,
    revoked_by      uuid REFERENCES app_user (id),
    revoke_reason   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_appt_kind CHECK (kind IN ('FORMAL', 'TEMPORARY')),
    -- 判据 O3-1: 临时授权必须有截止日, 到期自动失效, 不靠人工撤销。
    CONSTRAINT ck_das_appt_temp_end CHECK (kind <> 'TEMPORARY' OR valid_to IS NOT NULL),
    -- 判据 O3-2: 临时授权必须写明关联的正式任命流程, 否则无从查出"只发临时不走正式"。
    CONSTRAINT ck_das_appt_temp_ref CHECK (kind <> 'TEMPORARY' OR formal_process_ref IS NOT NULL),
    CONSTRAINT ck_das_appt_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE INDEX IF NOT EXISTS idx_das_appt_user ON das_appointment (user_id)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_das_appt_position ON das_appointment (position_code)
    WHERE revoked_at IS NULL;

-- 任命记录只能撤销, 不能改写或删除——与授权记录同一规矩(UG-DAW-005 第 4 章)。
CREATE OR REPLACE FUNCTION dcms_guard_das_appointment() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 任命记录不得删除, 只能撤销' USING ERRCODE = '23514';
    END IF;
    -- 允许的改动只有三类: 撤销、被正式任命接替(O3-3)、收紧截止日
    IF OLD.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 已撤销的任命不得再改写' USING ERRCODE = '23514';
    END IF;
    IF NEW.user_id <> OLD.user_id OR NEW.position_code <> OLD.position_code
       OR NEW.kind <> OLD.kind OR NEW.valid_from <> OLD.valid_from THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 任命的人员、岗位、类型和生效日不得修改, 请撤销后重新任命'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_appointment_guard ON das_appointment;
CREATE TRIGGER trg_das_appointment_guard
    BEFORE UPDATE OR DELETE ON das_appointment
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_appointment();

-- ---------------------------------------------------------------------
-- 4. 互斥校验：在任命的那一刻拒绝冲突组合
--    三条规则, 全部是岗位级, 与具体文件无关。
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_check_das_appointment() RETURNS trigger AS $$
DECLARE
    v_new   das_position%ROWTYPE;
    v_other record;
BEGIN
    IF NEW.revoked_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO v_new FROM das_position WHERE code = NEW.position_code;

    FOR v_other IN
        SELECT p.* FROM das_appointment a JOIN das_position p ON p.code = a.position_code
         WHERE a.user_id = NEW.user_id AND a.id <> COALESCE(NEW.id, -1)
           AND a.revoked_at IS NULL AND a.superseded_by IS NULL
           AND (a.valid_to IS NULL OR a.valid_to >= current_date)
    LOOP
        -- 规则一: 专任/专职/专人岗位不得与任何其他岗位并存(两个方向都要判)
        IF v_new.is_exclusive THEN
            RAISE EXCEPTION 'DCMS-INV-031: % 为专任岗位, 不得兼任 %（依据: %）',
                v_new.name_cn, v_other.name_cn, v_new.basis USING ERRCODE = '23514';
        END IF;
        IF v_other.is_exclusive THEN
            RAISE EXCEPTION 'DCMS-INV-031: 该员已任 %（专任岗位）, 不得再兼任 %（依据: %）',
                v_other.name_cn, v_new.name_cn, v_other.basis USING ERRCODE = '23514';
        END IF;

        -- 规则二: 独立监督负责人不得兼任运行管理人员, 也不得承担被监督的运行活动
        IF (NEW.position_code = 'ISM' AND (v_other.is_ops_mgmt OR v_other.is_supervised))
           OR (v_other.code = 'ISM' AND (v_new.is_ops_mgmt OR v_new.is_supervised)) THEN
            RAISE EXCEPTION
                'DCMS-INV-032: 独立监督负责人不得兼任 %（手册 3.1: 不得兼任其他运行管理人员, 亦不得承担被监督的运行活动）',
                CASE WHEN NEW.position_code = 'ISM' THEN v_other.name_cn ELSE v_new.name_cn END
                USING ERRCODE = '23514';
        END IF;

        -- 规则三: 显式互斥对
        IF EXISTS (SELECT 1 FROM das_position_exclusion
                    WHERE (position_a = NEW.position_code AND position_b = v_other.code)
                       OR (position_a = v_other.code AND position_b = NEW.position_code)) THEN
            RAISE EXCEPTION 'DCMS-INV-033: % 与 % 不得由同一人担任（依据: %）',
                v_new.name_cn, v_other.name_cn,
                (SELECT basis FROM das_position_exclusion
                  WHERE (position_a = NEW.position_code AND position_b = v_other.code)
                     OR (position_a = v_other.code AND position_b = NEW.position_code) LIMIT 1)
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_appointment_check ON das_appointment;
CREATE TRIGGER trg_das_appointment_check
    BEFORE INSERT OR UPDATE ON das_appointment
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_appointment();

-- ---------------------------------------------------------------------
-- 5. 视图
-- ---------------------------------------------------------------------

-- 5.1 当期在任的任命。判据 A7: 不含任何人数假设, 有几条算几条。
CREATE OR REPLACE VIEW das_appointment_in_force AS
SELECT a.id, a.user_id, u.full_name, u.username, u.employee_no,
       a.position_code, p.name_cn AS position_name, a.kind,
       a.valid_from, a.valid_to, a.appointment_ref, a.formal_process_ref,
       p.is_exclusive, p.is_ops_mgmt, p.is_supervised
  FROM das_appointment a
  JOIN app_user u ON u.id = a.user_id
  JOIN das_position p ON p.code = a.position_code
 WHERE a.revoked_at IS NULL AND a.superseded_by IS NULL
   AND a.valid_from <= current_date
   AND (a.valid_to IS NULL OR a.valid_to >= current_date);

-- 5.2 判据 O3-2: 只发了临时授权、正式流程迟迟没有结果的。
--     出缺当日发临时授权是对的, 一直挂着临时授权就不是了。
CREATE OR REPLACE VIEW das_appointment_temp_pending AS
SELECT t.id, t.user_id, u.full_name, t.position_code, p.name_cn AS position_name,
       t.valid_from, t.valid_to, t.formal_process_ref,
       current_date - t.valid_from AS days_open
  FROM das_appointment t
  JOIN app_user u ON u.id = t.user_id
  JOIN das_position p ON p.code = t.position_code
 WHERE t.kind = 'TEMPORARY' AND t.revoked_at IS NULL AND t.superseded_by IS NULL
   AND (t.valid_to IS NULL OR t.valid_to >= current_date)
   AND NOT EXISTS (SELECT 1 FROM das_appointment f
                    WHERE f.user_id = t.user_id AND f.position_code = t.position_code
                      AND f.kind = 'FORMAL' AND f.revoked_at IS NULL)
 ORDER BY days_open DESC;

-- 5.3 无人在任的岗位。判据 A7: 这里不判"够不够人", 只列"有没有人"——
--     够不够是管理评审的判断, 不是系统的常量。
CREATE OR REPLACE VIEW das_position_vacant AS
SELECT p.code, p.name_cn, p.is_exclusive, p.basis
  FROM das_position p
 WHERE NOT EXISTS (SELECT 1 FROM das_appointment_in_force f WHERE f.position_code = p.code)
 ORDER BY p.sort_order;
