-- =====================================================================
-- UG-DCMS 迁移 0010 — 修正位置类受限词的误伤
--
-- 问题: 0008 装载的位置类规则 '^(左|右|前|后|上|下|内|外)' 会把"内径""外径"
--       判为方位词。而 Dash P/N 名称恰恰需要写清与同族其它 Dash 的差异,
--       "内径12" 是最典型的差异描述, 被误拦会让整个 Dash 命名无法使用。
--
-- 修正: 加负向先行断言, 排除"径""螺纹"等尺寸/结构术语构成的复合词。
--       PostgreSQL 的 ARE 与 Python re 都支持 (?!...), 两侧行为一致。
--
-- 说明: 不直接修改 0008 —— 已应用的迁移文件一经改动, db/migrate.sh --verify
--       就会报告文件与应用时不一致, 那是一条应当保持有效的完整性检查。
--
-- 同时补一条项目编号规则: 0008 的项目词规则要求词后紧跟非字母数字, 而实际的
-- 项目编号形如 STC07A25、PMA06A23 —— 词后紧跟的正是数字, 于是整类项目编号
-- 全部逃过检查。补一条"项目前缀 + 数字"的规则。
-- =====================================================================

UPDATE restricted_term
   SET pattern = '^(左|右|前|后|上|下|内|外)(?!径|螺纹|齿|花键)',
       message_cn = 'NAM §7: 以方位词开头的名称默认禁止(尺寸术语如内径/外径除外)'
 WHERE restricted_type = 'LOCATION'
   AND pattern = '^(左|右|前|后|上|下|内|外)';

-- 项目编号: 前缀后直接跟数字, 如 STC07A25 / PMA06A23 / CTSOA01
INSERT INTO restricted_term
    (restricted_type, control_level, pattern, is_regex, example, message_cn)
VALUES ('PROJECT', 'BLOCK', '(^|[^0-9A-Za-z])(STC|PMA|CTSOA|MDA|TSO)[0-9]', true,
        'STC07A25', 'NAM §7: 项目编号禁止进入名称')
ON CONFLICT (restricted_type, pattern) DO NOTHING;

DO $$
DECLARE v int;
BEGIN
    IF NOT ('P形抱箍 STC07A25专用' ~ '(^|[^0-9A-Za-z])(STC|PMA|CTSOA|MDA|TSO)[0-9]') THEN
        RAISE EXCEPTION '项目编号规则未能命中 STC07A25';
    END IF;
    IF 'P形抱箍' ~ '(^|[^0-9A-Za-z])(STC|PMA|CTSOA|MDA|TSO)[0-9]' THEN
        RAISE EXCEPTION '项目编号规则误伤合规名称';
    END IF;
    SELECT count(*) INTO v FROM restricted_term
     WHERE restricted_type = 'LOCATION' AND pattern LIKE '%(?!径%';
    IF v <> 1 THEN
        RAISE EXCEPTION '位置类受限词修正未生效, 命中 % 条', v;
    END IF;

    -- 修正后应放行尺寸术语, 仍拦截真正的方位词
    IF '内径12mm' ~ (SELECT pattern FROM restricted_term
                      WHERE restricted_type='LOCATION' AND pattern LIKE '%(?!径%') THEN
        RAISE EXCEPTION '修正后仍误伤"内径12mm"';
    END IF;
    IF NOT ('左侧支架' ~ (SELECT pattern FROM restricted_term
                          WHERE restricted_type='LOCATION' AND pattern LIKE '%(?!径%')) THEN
        RAISE EXCEPTION '修正后漏放真正的方位词"左侧支架"';
    END IF;

    RAISE NOTICE '受限词已修正: 位置类放行尺寸术语; 新增项目编号规则(STC07A25 等)';
END $$;
