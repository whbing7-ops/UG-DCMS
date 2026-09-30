-- 签署授权按"专业 + 具体产品"限定范围, 并增加 CVE 级别与备份人。
-- 依据 UG-DAP-03《岗位任命与授权人员管理程序》第 8 节: 签署时由系统强制校验签署人在册,
-- 且其授权的有效期、专业、具体产品和文件类型权限与本份资料相符, 任一不符的不得签署。
-- 依据 AC-21-48 3.2.4: 体系文件中应包括当指定的人员不可用时的备份安排。
-- 可重复执行; 不改动任何已有业务数据。已有授权的新增字段一律为 NULL, 含义是"不限",
-- 与升级前的行为完全一致, 不会让任何已签署的文件事后变成越权。

-- ---------------------------------------------------------------------
-- 1. 适航专业字典
--    与 function_domain(功能域, 描述零件"干什么")是两回事: 这里是适航专业分工,
--    对应 AP-21-18 附录F 的工作范围划分和设计保证手册 2.2 的设计能力清单。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_discipline (
    code       text PRIMARY KEY,
    name_cn    text NOT NULL,
    name_en    text NOT NULL,
    definition text,
    status     dict_status,
    sort_order integer NOT NULL DEFAULT 0
);

INSERT INTO das_discipline (code, name_cn, name_en, definition, sort_order) VALUES
('D01', '总体',             'Overall Design',            '总体布置、构型定义、专业间接口协调', 10),
('D02', '结构强度',         'Structural Strength',       '静强度、疲劳、损伤容限与强度符合性', 20),
('D03', '机体结构',         'Airframe Structure',        '机翼、机身、尾翼、舱门等主要结构', 30),
('D04', '起落架',           'Landing Gear',              '起落架及其收放、刹车、转弯', 40),
('D05', '飞行操纵',         'Flight Controls',           '主操纵、辅助操纵与操纵系统', 50),
('D06', '动力装置',         'Powerplant',                '发动机安装、进排气、燃油与操纵', 60),
('D07', '机械系统',         'Mechanical Systems',        '液压、气动、水与废物等机械系统', 70),
('D08', '环境控制',         'Environmental Control',     '空调、增压、通风、防除冰', 80),
('D09', '电气系统',         'Electrical Systems',        '供电、配电、线束、电气负载与搭接', 90),
('D10', '航电系统',         'Avionics',                  '通信、导航、显示、记录与监视', 100),
('D11', '客舱内饰与安全设备','Cabin Interior & Safety',   '内饰、座椅、应急设备与客舱安全', 110),
('D12', '机载软件与电子硬件','Airborne SW & Electronic HW','机载软件与机载电子硬件研制保证', 120),
('D13', '材料标准件与工艺',  'Materials, Standards & Process','材料、标准件选用与工艺规范', 130),
('D14', '重量平衡',         'Weight & Balance',          '重量、重心与平衡符合性', 140),
('D15', '噪声与排放',       'Noise & Emissions',         '环境保护要求的噪声与排放符合性', 150),
('D16', '持续适航',         'Continued Airworthiness',   '持续适航文件、维修任务与适航限制', 160)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. 设计文件归属专业
--    校验需要两侧都有专业: 授权上有, 被签署的资料上也要有, 否则无从比对。
--    已有文件为 NULL, 表示"未标注专业", 第 4 节的校验对这类文件不拦截,
--    以免升级后既有在途审批被卡死; 补标由 UG-DAP-01 的四性核查推动。
-- ---------------------------------------------------------------------
ALTER TABLE design_file ADD COLUMN IF NOT EXISTS discipline_code text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_design_file_discipline') THEN
        ALTER TABLE design_file
            ADD CONSTRAINT fk_design_file_discipline
            FOREIGN KEY (discipline_code) REFERENCES das_discipline (code);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_design_file_discipline ON design_file (discipline_code)
    WHERE discipline_code IS NOT NULL;

-- ---------------------------------------------------------------------
-- 3. 授权范围: 专业、具体产品、备份人; level 增加 CVE
--    产品范围这一维暂不实现: 系统尚无项目实体, "产品"没有可引用的标识,
--    写成自由文本只能显示、无法比对, 等于把手册要求强制执行的控制做成装饰。
--    待项目实体落地后用项目引用一次做对, 见 docs/das-operation-design-input.md 第九之二节。
-- ---------------------------------------------------------------------
ALTER TABLE signer_authorization ADD COLUMN IF NOT EXISTS discipline_code text;
ALTER TABLE signer_authorization ADD COLUMN IF NOT EXISTS backup_user_id  uuid;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_signer_auth_discipline') THEN
        ALTER TABLE signer_authorization
            ADD CONSTRAINT fk_signer_auth_discipline
            FOREIGN KEY (discipline_code) REFERENCES das_discipline (code);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_signer_auth_backup') THEN
        ALTER TABLE signer_authorization
            ADD CONSTRAINT fk_signer_auth_backup
            FOREIGN KEY (backup_user_id) REFERENCES app_user (id);
    END IF;
    -- 备份人不得是本人: 自己给自己做备份等于没有备份
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_signer_backup_not_self') THEN
        ALTER TABLE signer_authorization
            ADD CONSTRAINT ck_signer_backup_not_self
            CHECK (backup_user_id IS NULL OR backup_user_id <> user_id);
    END IF;
END $$;

-- level 增加 CVE(符合性核查工程师): 表明符合性的资料由 CVE 独立核查,
-- 这一级与 REVIEW(设计审核)是两件事, 不能混用同一份授权。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'signer_authorization_level_check'
                  AND conrelid = 'signer_authorization'::regclass) THEN
        ALTER TABLE signer_authorization DROP CONSTRAINT signer_authorization_level_check;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_signer_level') THEN
        ALTER TABLE signer_authorization
            ADD CONSTRAINT ck_signer_level CHECK (level IN ('REVIEW', 'APPROVE', 'CVE'));
    END IF;
END $$;

-- signature_record 的 level 同步放开 CVE, 否则核查签署无处落账
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'signature_record_level_check'
                  AND conrelid = 'signature_record'::regclass) THEN
        ALTER TABLE signature_record DROP CONSTRAINT signature_record_level_check;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_signature_level') THEN
        ALTER TABLE signature_record
            ADD CONSTRAINT ck_signature_level CHECK (level IN ('PREPARE', 'REVIEW', 'APPROVE', 'CVE'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_signer_auth_scope
    ON signer_authorization (level, discipline_code) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------
-- 4. 把新字段纳入"只能撤销、不能改写"的保护
--    0027 的触发器逐列比对, 新列不加进去就会成为漏洞: 授权范围可以被事后悄悄改大。
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_guard_signer_authorization() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 签署授权记录不得删除, 只能撤销' USING ERRCODE = '23514';
    END IF;
    IF OLD.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 已撤销的签署授权不得再改写' USING ERRCODE = '23514';
    END IF;
    IF (NEW.user_id, NEW.level, NEW.file_type_code, NEW.valid_from, NEW.valid_to, NEW.note,
        NEW.granted_by, NEW.granted_at, NEW.discipline_code, NEW.backup_user_id)
       IS DISTINCT FROM
       (OLD.user_id, OLD.level, OLD.file_type_code, OLD.valid_from, OLD.valid_to, OLD.note,
        OLD.granted_by, OLD.granted_at, OLD.discipline_code, OLD.backup_user_id) THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 签署授权除撤销外不得修改' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_signer_authorization_guard ON signer_authorization;
CREATE TRIGGER trg_signer_authorization_guard BEFORE UPDATE OR DELETE ON signer_authorization
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_signer_authorization();

-- ---------------------------------------------------------------------
-- 5. 授权范围视图: 给界面和审核用的可读清单
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW signer_authorization_scope AS
SELECT sa.id,
       sa.user_id,
       u.username,
       u.full_name,
       sa.level,
       sa.file_type_code,
       ft.name_cn                              AS file_type_name,
       sa.discipline_code,
       d.name_cn                               AS discipline_name,
       sa.backup_user_id,
       bu.full_name                            AS backup_full_name,
       sa.valid_from,
       sa.valid_to,
       sa.revoked_at,
       (sa.revoked_at IS NULL
        AND u.is_active
        AND sa.valid_from <= current_date
        AND (sa.valid_to IS NULL OR sa.valid_to >= current_date)) AS is_currently_valid
  FROM signer_authorization sa
  JOIN app_user u        ON u.id = sa.user_id
  LEFT JOIN app_user bu  ON bu.id = sa.backup_user_id
  LEFT JOIN file_type ft ON ft.code = sa.file_type_code
  LEFT JOIN das_discipline d ON d.code = sa.discipline_code;
