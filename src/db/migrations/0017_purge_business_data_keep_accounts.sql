-- 一次性清理此前装载的综合演示业务数据。
-- 保留：账户、账户角色、角色定义、系统设置、受控字典、分类规范和迁移记录。
-- 删除：设计对象、BOM、文件/版次、基线、外部件、软件、审批、质量问题、
--       导入批次、构型适用性、最近访问、会话与审计业务记录。

CREATE TEMP TABLE _ug_account_guard AS
SELECT count(*)::bigint AS account_count FROM app_user;

TRUNCATE TABLE
    design_object,
    design_file,
    change_package,
    approval_request,
    data_quality_issue,
    import_batch,
    recent_access,
    configuration_context,
    applicability_rule,
    audit_log,
    user_session
RESTART IDENTITY CASCADE;

DO $$
DECLARE before_count bigint;
DECLARE after_count bigint;
BEGIN
    SELECT account_count INTO before_count FROM _ug_account_guard;
    SELECT count(*) INTO after_count FROM app_user;
    IF before_count <> after_count THEN
        RAISE EXCEPTION '账户保护校验失败：清理前 %，清理后 %', before_count, after_count;
    END IF;
END $$;

DROP TABLE _ug_account_guard;
