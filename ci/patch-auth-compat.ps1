param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$migDir = Join-Path $SourceRoot 'db\migrations'
$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$verify = Join-Path $SourceRoot 'installer\verify_installer_source.py'
$build = Join-Path $SourceRoot 'installer\Build-Setup.ps1'
foreach($p in @($migDir,$provision,$verify,$build)){
  if(-not(Test-Path $p)){ throw ('required path missing: '+$p) }
}

$mig = Join-Path $migDir '0013_auth_runtime_compat.sql'
$sql = @'
-- =====================================================================
-- UG-DCMS migration 0013 - authentication runtime compatibility repair
-- Purpose: normalize legacy development databases that may already have
-- 0001-0012 recorded but lack columns/settings required by the current
-- authentication/session path. All operations are idempotent.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Core account columns used by app.services.auth.
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS failed_login_count integer NOT NULL DEFAULT 0;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS locked_until timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS updated_by uuid;

-- Session table/columns required by licensing.acquire_slot and current_user.
CREATE TABLE IF NOT EXISTS user_session (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES app_user(id),
    token_hash    text NOT NULL,
    issued_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    revoked_by    uuid,
    revoke_reason text,
    client_ip     text,
    user_agent    text
);
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS last_seen_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS revoked_by uuid;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS revoke_reason text;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS client_ip text;
ALTER TABLE user_session ADD COLUMN IF NOT EXISTS user_agent text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_session_token_compat ON user_session(token_hash);
CREATE INDEX IF NOT EXISTS idx_user_session_active_compat ON user_session(user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_session_expiry_compat ON user_session(expires_at) WHERE revoked_at IS NULL;

-- Audit columns used by successful and denied login events.
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS session_id uuid;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS client_ip text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS request_id text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS result text NOT NULL DEFAULT 'SUCCESS';

-- Required authentication/licensing settings. Preserve any existing configured value.
INSERT INTO system_setting(key,value,value_type,description,is_locked) VALUES
('max_concurrent_accounts','10','INTEGER','同时活动账户上限',true),
('session_idle_minutes','120','INTEGER','会话空闲超时(分钟)',false),
('session_absolute_hours','12','INTEGER','会话绝对有效期(小时)',false),
('login_max_failures','5','INTEGER','连续登录失败锁定阈值',false),
('login_lock_minutes','15','INTEGER','登录锁定时长(分钟)',false)
ON CONFLICT (key) DO NOTHING;

-- Restore built-in administrator role assignments if a legacy database lost them.
INSERT INTO user_role(user_id, role_code)
SELECT u.id, r.code
  FROM app_user u
  JOIN role r ON r.code IN ('SYSTEM_ADMIN','DATA_ADMIN')
 WHERE lower(u.username)='admin'
ON CONFLICT (user_id, role_code) DO NOTHING;

-- A bootstrap administrator that has never completed its mandatory first password
-- change must not remain locked because of earlier installer/login experiments.
UPDATE app_user
   SET failed_login_count=0, locked_until=NULL
 WHERE lower(username)='admin' AND must_change_password=true;

-- Transactional login-write schema probe. The inner exception block intentionally
-- rolls back its own changes, so the migration proves the login DML path without
-- leaving a session or audit record behind.
DO $$
DECLARE
  aid uuid;
  probe_session uuid := gen_random_uuid();
  expected_rollback boolean := false;
BEGIN
  SELECT id INTO aid FROM app_user WHERE lower(username)='admin' LIMIT 1;
  IF aid IS NULL THEN
    RAISE EXCEPTION 'DCMS-AUTH-COMPAT: bootstrap administrator is missing';
  END IF;

  BEGIN
    INSERT INTO user_session(id,user_id,token_hash,issued_at,last_seen_at,expires_at,client_ip,user_agent)
    VALUES(probe_session, aid, encode(gen_random_bytes(32),'hex'), now(), now(), now()+interval '5 minutes', '127.0.0.1', 'migration-auth-probe');

    UPDATE app_user SET last_login_at=last_login_at WHERE id=aid;

    INSERT INTO audit_log(user_id,username,action,object_type,object_id,session_id,client_ip,result)
    VALUES(aid,'admin','LOGIN_PROBE','USER_SESSION',probe_session::text,probe_session,'127.0.0.1','SUCCESS');

    RAISE EXCEPTION 'AUTH_PROBE_ROLLBACK' USING ERRCODE='P0001';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'AUTH_PROBE_ROLLBACK' THEN RAISE; END IF;
    expected_rollback := true;
  END;

  IF NOT expected_rollback THEN
    RAISE EXCEPTION 'DCMS-AUTH-COMPAT: authentication transaction probe did not execute';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM app_user WHERE lower(username)='admin') THEN
    RAISE EXCEPTION 'DCMS-AUTH-COMPAT: administrator row missing after repair';
  END IF;
  IF (SELECT count(*) FROM user_role ur JOIN app_user u ON u.id=ur.user_id WHERE lower(u.username)='admin' AND ur.role_code IN ('SYSTEM_ADMIN','DATA_ADMIN')) < 2 THEN
    RAISE EXCEPTION 'DCMS-AUTH-COMPAT: administrator role assignments incomplete';
  END IF;
END $$;
'@

[IO.File]::WriteAllText($mig,$sql,(New-Object Text.UTF8Encoding($false)))

# Move the installer completeness gate from 12 migrations to 13.
$p = Get-Content $provision -Raw -Encoding UTF8
$p = $p.Replace('if([int]$migrationCount -ne 12){','if([int]$migrationCount -ne 13){')
$p = $p.Replace('期望 12，实际 $migrationCount','期望 13，实际 $migrationCount')
$p = $p.Replace('数据库迁移完整性检查通过：12/12','数据库迁移完整性检查通过：13/13')
if(-not $p.Contains('if([int]$migrationCount -ne 13){')){ throw 'installer migration count was not patched to 13' }
if(-not $p.Contains('数据库迁移完整性检查通过：13/13')){ throw 'installer migration completion text was not patched to 13/13' }
[IO.File]::WriteAllText($provision,$p,(New-Object Text.UTF8Encoding($true)))

# Keep these replacements deliberately simple. Windows PowerShell 5.1 does not use
# backslash to escape a quote inside a double-quoted string, so whole Python source
# line replacements are fragile. Replacing stable tokens is both clearer and safer.
$v = Get-Content $verify -Raw -Encoding UTF8
$v = $v.Replace('len(migs) == 12','len(migs) == 13')
$v = $v.Replace('expected 12 migrations','expected 13 migrations')
$v = $v.Replace('数据库迁移完整性检查通过：12/12','数据库迁移完整性检查通过：13/13')
$v = $v.Replace('{len(migs)}/12','{len(migs)}/13')
if($v.Contains('len(migs) == 12')){ throw 'verify_installer_source.py still checks for 12 migrations' }
if($v.Contains('expected 12 migrations')){ throw 'verify_installer_source.py still contains old migration-count message' }
if($v.Contains('数据库迁移完整性检查通过：12/12')){ throw 'verify_installer_source.py still contains old migration completion marker' }
if($v.Contains('{len(migs)}/12')){ throw 'verify_installer_source.py still prints /12 migration total' }
[IO.File]::WriteAllText($verify,$v,(New-Object Text.UTF8Encoding($false)))

$b = Get-Content $build -Raw -Encoding UTF8
$b = $b.Replace('数据库迁移完整性检查通过：12/12','数据库迁移完整性检查通过：13/13')
if(-not $b.Contains('数据库迁移完整性检查通过：13/13')){ throw 'Build-Setup.ps1 migration gate was not patched' }
[IO.File]::WriteAllText($build,$b,(New-Object Text.UTF8Encoding($true)))

# Parse modified PowerShell files under Windows PowerShell 5.1 parser.
foreach($file in @($provision,$build)){
  $tokens=$null; $errors=$null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file).Path,[ref]$tokens,[ref]$errors) | Out-Null
  if($errors.Count -gt 0){ throw ('PowerShell parse failure after auth compat patch: '+$file) }
}

if((Get-ChildItem $migDir -Filter '*.sql').Count -ne 13){ throw 'expected 13 migration files after auth compatibility patch' }
Write-Host 'AUTH COMPAT PATCH PASS: migration 0013 added, auth runtime schema repaired, migration gates moved to 13/13.'
