-- 旧版本数据库的认证与审计字段兼容修复；全部操作均可重复执行。
CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS failed_login_count integer NOT NULL DEFAULT 0;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS locked_until timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS updated_by uuid;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS last_seen_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS revoked_by uuid;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS revoke_reason text;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS client_ip text;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS user_agent text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS session_id uuid;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS client_ip text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS request_id text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS result text NOT NULL DEFAULT 'SUCCESS';
INSERT INTO system_setting(key,value,value_type,description,is_locked) VALUES
('max_concurrent_accounts','10','INTEGER','同时活动账户上限',true),
('session_idle_minutes','120','INTEGER','会话空闲超时分钟',false),
('session_absolute_hours','12','INTEGER','会话绝对有效期小时',false),
('login_max_failures','5','INTEGER','连续登录失败锁定阈值',false),
('login_lock_minutes','15','INTEGER','登录锁定时长分钟',false)
ON CONFLICT(key) DO NOTHING;
INSERT INTO user_role(user_id,role_code)
SELECT u.id,r.code FROM app_user u JOIN role r ON r.code IN('SYSTEM_ADMIN','DATA_ADMIN')
WHERE lower(u.username)='admin' ON CONFLICT(user_id,role_code) DO NOTHING;
UPDATE app_user SET failed_login_count=0,locked_until=NULL
WHERE lower(username)='admin' AND must_change_password=true;
