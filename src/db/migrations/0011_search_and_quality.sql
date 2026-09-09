-- =====================================================================
-- UG-DCMS 迁移 0011 — 检索索引与数据质量支撑
-- 依据: SRS-SRH-001~003 / SRS-DQ-001 / AC-SEARCH-01 / AC-PERF-02 / AT-008
--
-- 【中文检索方案说明 — 验收时请注意】
-- PostgreSQL 内置的分词器不切分中文: to_tsvector('simple', '双耳支架') 只会产出
-- 一个整串 token, 搜"支架"匹配不到。因此本系统的中文检索不用 tsvector, 改用
-- pg_trgm 三元组索引 + ILIKE 子串匹配 + similarity 排序 —— 这是不引入外部分词
-- 扩展前提下唯一可靠的中文子串检索手段。
--
-- 代价与边界:
--   * 能做: 子串匹配("支架"命中"双耳支架")、模糊匹配(错字容忍)、相似度排序
--   * 不能做: 语义分词("电源控制板组件"不会被拆成"电源/控制/板组件"这样的词单元)
--   * 若日后确需真正的中文分词, 需安装 zhparser 或 pg_jieba 扩展并重建索引;
--     这属于部署环境变更, 应走基准变更流程, 不要在应用层拼凑替代方案。
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------
-- 1 设计对象检索
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_design_object_code_trgm
    ON design_object USING gin (object_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_design_object_name_trgm
    ON design_object USING gin (display_name gin_trgm_ops);

-- ---------------------------------------------------------------------
-- 2 P/N 与设计族检索
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_part_number_full_trgm
    ON part_number USING gin (full_part_number gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_part_number_name_trgm
    ON part_number USING gin (formal_name_cn gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_family_number_trgm
    ON basic_drawing_family USING gin (basic_drawing_number gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_family_name_trgm
    ON basic_drawing_family USING gin (family_name_cn gin_trgm_ops);

-- ---------------------------------------------------------------------
-- 3 外部件与交叉引用 — AT-008 的关键路径
--   历史件号 ZD850-3 / ZD8503 / 0ZD8503 必须都能定位到当前对象,
--   归一值上的等值索引比模糊匹配快得多, 且结果确定。
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_external_part_number_trgm
    ON external_part USING gin (external_part_number gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_cross_reference_value_trgm
    ON cross_reference USING gin (reference_value gin_trgm_ops);
-- normalized_value 的等值索引在 0003 已建 (idx_cross_reference_norm)

-- ---------------------------------------------------------------------
-- 4 设计文件检索
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_design_file_number_trgm
    ON design_file USING gin (file_number gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_design_file_title_trgm
    ON design_file USING gin (title_cn gin_trgm_ops);

-- ---------------------------------------------------------------------
-- 5 词典别名检索 — 支持用别名/历史叫法找到正式词条
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_term_alias_trgm
    ON term_alias USING gin (alias_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_core_term_name_trgm
    ON naming_core_term USING gin (name_cn gin_trgm_ops);

-- ---------------------------------------------------------------------
-- 6 最近访问 — UI §3 首页"最近访问"
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_recent_access_user_time
    ON recent_access (user_id, accessed_at DESC);

-- ---------------------------------------------------------------------
-- 7 数据质量问题的处置字段
--   0005 建表时只有 status/waiver_reason, 补齐处置人与豁免有效期,
--   使"谁豁免的、豁免到什么时候"可追溯 —— 无期限的豁免等于取消该规则。
-- ---------------------------------------------------------------------
ALTER TABLE data_quality_issue
    ADD COLUMN IF NOT EXISTS waived_by uuid REFERENCES app_user (id),
    ADD COLUMN IF NOT EXISTS waived_at timestamptz,
    ADD COLUMN IF NOT EXISTS waiver_expires_at timestamptz;

-- ADD CONSTRAINT 没有 IF NOT EXISTS, 重跑会报"约束已存在"。迁移执行器虽然按版本
-- 跳过已应用的文件, 但排障时手工重跑单个文件是常有的事, 保持幂等更省心。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_dq_issue_waiver') THEN
        ALTER TABLE data_quality_issue
            ADD CONSTRAINT ck_dq_issue_waiver CHECK (
                status <> 'WAIVED'
                OR (waiver_reason IS NOT NULL AND waived_by IS NOT NULL
                    AND waiver_expires_at IS NOT NULL));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_dq_issue_open
    ON data_quality_issue (rule_code, detected_at DESC) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS idx_dq_issue_object
    ON data_quality_issue (object_type, object_id) WHERE status = 'OPEN';

-- 豁免过期后自动回到 OPEN: 用视图表达, 不改动原始记录
CREATE OR REPLACE VIEW v_data_quality_effective AS
SELECT i.*,
       CASE WHEN i.status = 'WAIVED' AND i.waiver_expires_at <= now()
            THEN 'OPEN' ELSE i.status END AS effective_status
  FROM data_quality_issue i;

COMMENT ON VIEW v_data_quality_effective IS
    '数据质量问题的有效状态: 豁免过期后自动视为 OPEN。原始记录不改动, 保留豁免历史。';

-- ---------------------------------------------------------------------
-- 8 检索性能基线记录 — AC-PERF-02 要求 95% 请求 ≤3s
--   把索引清单登记下来, 使日后有人删索引时能被发现。
-- ---------------------------------------------------------------------
INSERT INTO invariant_registry (code, statement_cn, enforced_by, enforcement_ref, test_ref)
VALUES
('SRH-001', '中文检索采用 pg_trgm 子串匹配, 不依赖 tsvector 分词', 'DB_STRUCTURE',
 '迁移 0011 建立的 gin_trgm_ops 索引; PostgreSQL 内置分词器不切分中文', 'test_search_chinese'),
('SRH-002', '历史件号经归一后可定位当前对象', 'DB_AND_APP',
 'dcms_normalize_identifier + idx_cross_reference_norm + SearchService', 'test_at008'),
('DQ-001', '数据质量豁免必须有理由、处置人与有效期', 'DB_CONSTRAINT',
 'ck_dq_issue_waiver + v_data_quality_effective', 'test_waiver_requires_expiry')
ON CONFLICT (code) DO NOTHING;

DO $$
DECLARE v int;
BEGIN
    SELECT count(*) INTO v FROM pg_indexes
     WHERE indexname LIKE 'idx_%_trgm';
    IF v < 12 THEN
        RAISE EXCEPTION '三元组索引应至少 12 个, 实际 %', v;
    END IF;
    RAISE NOTICE '检索索引建立完成: % 个 pg_trgm 索引', v;
END $$;
