-- 工作日日历（基础能力的一部分，M7 的前置）。
--
-- 依据 UG-DAP-14 第 6 章步骤 2 与第 7 章关键控制点、AP-21-18 2.1，
-- UG-RPT-2026-003 J 版判据 L1、T1。
--
-- 【为什么必须单独建这一层】
-- 体系里有两套互不相同的计时口径, 混用任何一次都是实质错误:
--
--   · CCAR-21.5（六）的 48 小时是**日历小时**, 含周末节假日, 不得顺延(判据 L1)。
--     这一套不需要日历, 直接加 interval 即可, 已在 0035 实现。
--   · UG-DAP-14 步骤 2 的一类问题 21 天是**工作日**, 而且"不得延期"。
--     这一套必须知道哪天是工作日。
--
-- 把工作日近似成"周一到周五"是不行的: 中国的法定节假日集中成假, 还有调休——
-- 某个周六被定为上班日。两头都会错, 而错的是一条**法规时限**。
--
-- 【日历没加载时, 系统说"算不出来", 不给一个错的日期】
-- 本迁移只建表与函数, **不种入任何节假日**。节假日逐年由国务院办公厅通知发布,
-- 没有权威来源时编一份出来, 会让系统给出一个看着精确、实际错误的法定期限 ——
-- 那比算不出来危险得多。
--
-- 周末是结构性的(周六周日默认非工作日), 不需要逐条录入; 需要录入的只有例外:
-- 法定节假日(本该上班却不上班)和调休上班日(本该休息却上班)。
--
-- 但"表里没有例外"与"没人录过例外"看着一样。所以另设 das_work_calendar_year:
-- 某一年的节假日通知加载过, 才有那一行。函数遇到未声明覆盖的年份返回 NULL,
-- 由调用方把"日历缺数据"显形, 而不是按"该年无节假日"算下去。
--
-- 可重复执行; 不改动任何已有数据。

-- ---------------------------------------------------------------------
-- 1. 例外日
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_work_calendar (
    day        date PRIMARY KEY,
    kind       text NOT NULL,     -- HOLIDAY 法定节假日(不上班) / MAKEUP 调休上班日(上班)
    name_cn    text,              -- 节假日名称, 如"国庆节"
    source_ref text NOT NULL,     -- 依据: 国务院办公厅通知文号等
    loaded_by  uuid REFERENCES app_user (id),
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_cal_kind CHECK (kind IN ('HOLIDAY', 'MAKEUP')),
    CONSTRAINT ck_das_cal_src  CHECK (length(btrim(source_ref)) > 0)
);

COMMENT ON TABLE das_work_calendar IS
    '工作日日历的例外日。周末为结构性非工作日无需录入；只录法定节假日与调休上班日。每条须写明依据文号（判据 P1：参数是受控配置）。';

-- ---------------------------------------------------------------------
-- 2. 年度覆盖声明
-- ---------------------------------------------------------------------
-- 没有这一层时, "空表"既可能是"该年没有节假日"(不可能), 也可能是"没人录"。
-- 法规时限不能建立在这种歧义上。
CREATE TABLE IF NOT EXISTS das_work_calendar_year (
    calendar_year int PRIMARY KEY,
    source_ref    text NOT NULL,
    declared_by   uuid REFERENCES app_user (id),
    declared_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_cal_year_range CHECK (calendar_year BETWEEN 2000 AND 2199),
    CONSTRAINT ck_das_cal_year_src   CHECK (length(btrim(source_ref)) > 0)
);

COMMENT ON TABLE das_work_calendar_year IS
    '声明某一年的节假日安排已按权威来源加载完整。未声明的年份，工作日函数返回 NULL——法规时限宁可算不出来，不可算错。';

-- 例外日所属年份必须已声明覆盖, 否则会出现"录了几天却不算加载过"的半成品日历。
CREATE OR REPLACE FUNCTION dcms_check_das_work_calendar() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM das_work_calendar_year
                    WHERE calendar_year = extract(year FROM NEW.day)::int) THEN
        RAISE EXCEPTION
            'DCMS-INV-043: % 年的节假日安排尚未声明加载, 请先登记 das_work_calendar_year（依据文号）后再录入例外日',
            extract(year FROM NEW.day)::int USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_work_calendar_check ON das_work_calendar;
CREATE TRIGGER trg_das_work_calendar_check
    BEFORE INSERT OR UPDATE ON das_work_calendar
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_work_calendar();

-- ---------------------------------------------------------------------
-- 3. 函数
-- ---------------------------------------------------------------------

-- 3.1 某天是不是工作日。周末默认不是, 调休上班日是, 法定节假日不是。
CREATE OR REPLACE FUNCTION das_is_working_day(p_day date) RETURNS boolean AS $$
DECLARE v_kind text;
BEGIN
    SELECT kind INTO v_kind FROM das_work_calendar WHERE day = p_day;
    IF v_kind = 'MAKEUP'  THEN RETURN true;  END IF;
    IF v_kind = 'HOLIDAY' THEN RETURN false; END IF;
    RETURN extract(isodow FROM p_day) < 6;      -- 1..5 为周一至周五
END;
$$ LANGUAGE plpgsql STABLE;

-- 3.2 自 p_from 起的第 n 个工作日。
--
-- 口径: p_from 当天**不计入**, 从次日开始数。UG-DAP-14 步骤 2 的时限"自局方发布
-- 记录表之日起算", 发布当日不是用于整改的工作日 —— 这个口径必须与主管监察员
-- 核实一次(见待澄清项), 若局方按含当日计算, 改这一个函数即可, 不必动调用方。
--
-- 跨越的每一年都要已声明覆盖, 否则返回 NULL。
CREATE OR REPLACE FUNCTION das_add_working_days(p_from date, p_days int)
RETURNS date AS $$
DECLARE
    v_day   date := p_from;
    v_left  int  := p_days;
    v_guard int  := 0;
BEGIN
    IF p_from IS NULL OR p_days IS NULL OR p_days < 0 THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM das_work_calendar_year
                    WHERE calendar_year = extract(year FROM p_from)::int) THEN
        RETURN NULL;
    END IF;
    WHILE v_left > 0 LOOP
        v_day   := v_day + 1;
        v_guard := v_guard + 1;
        -- 每跨一年都要已声明覆盖: 21 个工作日跨年是常事(例如 12 月开具的不符合项)。
        IF extract(year FROM v_day) <> extract(year FROM v_day - 1) THEN
            IF NOT EXISTS (SELECT 1 FROM das_work_calendar_year
                            WHERE calendar_year = extract(year FROM v_day)::int) THEN
                RETURN NULL;
            END IF;
        END IF;
        IF das_is_working_day(v_day) THEN
            v_left := v_left - 1;
        END IF;
        -- 兜底: 日历录成全年都是节假日时, 上面的循环不会结束。
        IF v_guard > p_days * 10 + 400 THEN
            RETURN NULL;
        END IF;
    END LOOP;
    RETURN v_day;
END;
$$ LANGUAGE plpgsql STABLE;

-- 3.3 两个日期之间的工作日数（含终日, 不含起日; 与 3.2 口径一致）。
CREATE OR REPLACE FUNCTION das_working_days_between(p_from date, p_to date)
RETURNS int AS $$
DECLARE v_n int := 0; v_day date := p_from;
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RETURN NULL;
    END IF;
    -- 区间跨越的**每一年**都要已声明覆盖, 少一年就算不准。
    IF EXISTS (SELECT 1 FROM generate_series(extract(year FROM p_from)::int,
                                             extract(year FROM p_to)::int) AS y
                WHERE NOT EXISTS (SELECT 1 FROM das_work_calendar_year c
                                   WHERE c.calendar_year = y)) THEN
        RETURN NULL;
    END IF;
    WHILE v_day < p_to LOOP
        v_day := v_day + 1;
        IF das_is_working_day(v_day) THEN v_n := v_n + 1; END IF;
    END LOOP;
    RETURN v_n;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION das_add_working_days(date, int) IS
    'UG-DAP-14 步骤 2：一类问题 21 个工作日。起算日当天不计入；跨越的任一年份未声明加载节假日时返回 NULL，由调用方把"日历缺数据"显形。';

-- ---------------------------------------------------------------------
-- 4. 日历覆盖缺口
-- ---------------------------------------------------------------------
-- 让"今年和明年的节假日通知有没有加载"成为一件看得见的待办, 而不是等到
-- 某条一类不符合项算不出期限时才发现。
CREATE OR REPLACE VIEW das_work_calendar_gap AS
SELECT y AS calendar_year,
       EXISTS (SELECT 1 FROM das_work_calendar_year c WHERE c.calendar_year = y) AS declared,
       (SELECT count(*) FROM das_work_calendar w
         WHERE extract(year FROM w.day)::int = y AND w.kind = 'HOLIDAY')          AS holidays,
       (SELECT count(*) FROM das_work_calendar w
         WHERE extract(year FROM w.day)::int = y AND w.kind = 'MAKEUP')           AS makeup_days
  FROM generate_series(extract(year FROM current_date)::int - 1,
                       extract(year FROM current_date)::int + 1) AS y
 ORDER BY y;

COMMENT ON VIEW das_work_calendar_gap IS
    '前后三年的工作日日历加载情况。declared=false 的年份无法计算工作日时限（UG-DAP-14 的一类 21 个工作日）。';
