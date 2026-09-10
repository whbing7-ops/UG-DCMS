-- 单机交付的预置 admin 必须能够完成初始化后的全部管理操作。
-- 审批不相容仍由 INV-025（申请人不得批准自己的申请）强制，不因角色叠加而绕过。
INSERT INTO user_role(user_id,role_code)
SELECT u.id,r.code
  FROM app_user u
 CROSS JOIN role r
 WHERE u.username IN ('admin','demo_sysadmin')
   AND r.code IN ('ENGINEER','CONFIGURATION_MANAGER','APPROVER')
ON CONFLICT DO NOTHING;
