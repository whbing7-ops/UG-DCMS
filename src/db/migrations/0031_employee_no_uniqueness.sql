-- 工号唯一性：判据 A1 的**必要条件**部分。
--
-- 依据 UG-RPT-2026-003 G 版第 4.1 节 A1／A1-2、第九之三节第 15 条。
--
-- 【这条迁移做到哪、没做到哪，要说清楚】
--
-- A1 要的是"一个自然人对应一个系统账号", 因为 I3 要求编、审、批为三个不同
-- **自然人**; 现有约束(services/files.py 与迁移 0027)比较的全是账号标识,
-- 同一自然人持两个账号即可通过全部校验。
--
-- 但 A1-2 也写明了: 唯一约束只能拒绝重复工号, **拒绝不了同一个人被登记成
-- E001 和 E002**——两个号都非空、都唯一, 全部约束照过。真正管住一人一号要靠
-- 受控人员清单、专人核实、禁止自由录入、旧号不重用这几条, 那是组织控制,
-- 不是一个数据库约束能替代的。
--
-- 所以本迁移只做必要的那一半:
--   · 加唯一约束, 堵住"同一工号建两个账号"这个纯技术漏洞;
--   · 给一个诊断视图, 让"哪些账号没填工号"可见, 便于清理。
-- 不做 NOT NULL: 现存账号允许为空, 直接加会让既有账户不可用。
-- 待清理完毕、且受控人员清单的规则落地后, 再由另一条迁移收紧。
--
-- 在此之前, I3 的"不同自然人"在符合性自评中**仍应如实写为未由系统实现**。
--
-- 可重复执行; 不改动任何已有数据。

-- 工号非空时必须唯一。空值不参与唯一性判断, 故用部分索引而不是列约束。
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_employee_no
    ON app_user (employee_no) WHERE employee_no IS NOT NULL;

COMMENT ON INDEX uq_app_user_employee_no IS
    '判据 A1 的必要条件: 同一工号不得存在第二个账号。充分条件(同一自然人不得取得第二个工号)靠受控人员清单, 见 A1-2。';

-- 诊断视图: 账号与自然人标识的对齐情况。
-- 判据 A1-2 要求这件事可复核, 所以让它可查, 而不是藏在数据里等审查时才发现。
CREATE OR REPLACE VIEW app_user_identity_gap AS
SELECT u.id, u.username, u.full_name, u.is_active,
       u.employee_no,
       CASE WHEN u.employee_no IS NULL OR btrim(u.employee_no) = ''
            THEN '未填工号: 无法据此判定是否与他人为同一自然人'
       END AS gap
  FROM app_user u
 WHERE u.employee_no IS NULL OR btrim(u.employee_no) = ''
 ORDER BY u.is_active DESC, u.username;

COMMENT ON VIEW app_user_identity_gap IS
    '工号缺失的账号清单。判据 A1 收紧为 NOT NULL 之前, 这些账号无法参与"不同自然人"的判定。';
