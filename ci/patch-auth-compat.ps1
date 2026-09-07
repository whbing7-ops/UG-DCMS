param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$migDir = Join-Path $SourceRoot 'db\migrations'
$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$migrate = Join-Path $SourceRoot 'windows\migrate-native.ps1'
$verify = Join-Path $SourceRoot 'installer\verify_installer_source.py'
$build = Join-Path $SourceRoot 'installer\Build-Setup.ps1'
foreach($p in @($migDir,$provision,$migrate,$verify,$build)){
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

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS failed_login_count integer NOT NULL DEFAULT 0;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS locked_until timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS updated_by uuid;

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

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS session_id uuid;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS client_ip text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS request_id text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS result text NOT NULL DEFAULT 'SUCCESS';

INSERT INTO system_setting(key,value,value_type,description,is_locked) VALUES
('max_concurrent_accounts','10','INTEGER','同时活动账户上限',true),
('session_idle_minutes','120','INTEGER','会话空闲超时(分钟)',false),
('session_absolute_hours','12','INTEGER','会话绝对有效期(小时)',false),
('login_max_failures','5','INTEGER','连续登录失败锁定阈值',false),
('login_lock_minutes','15','INTEGER','登录锁定时长(分钟)',false)
ON CONFLICT (key) DO NOTHING;

INSERT INTO user_role(user_id, role_code)
SELECT u.id, r.code
  FROM app_user u
  JOIN role r ON r.code IN ('SYSTEM_ADMIN','DATA_ADMIN')
 WHERE lower(u.username)='admin'
ON CONFLICT (user_id, role_code) DO NOTHING;

UPDATE app_user
   SET failed_login_count=0, locked_until=NULL
 WHERE lower(username)='admin' AND must_change_password=true;

-- Prove all writes performed by a successful login can execute. The intentional
-- exception rolls the probe records back so this migration leaves no fake session.
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

# install-oneclick.ps1: locale-independent migration-count changes. The old file has
# one 12/12 completion marker; replacing the numeric marker avoids Windows console
# code-page corruption of a Chinese literal inside this patch script.
$p = Get-Content $provision -Raw -Encoding UTF8
$p = $p.Replace('if([int]$migrationCount -ne 12){','if([int]$migrationCount -ne 13){')
$p = $p.Replace('12/12','13/13')
if(-not $p.Contains('if([int]$migrationCount -ne 13){')){ throw 'installer migration count was not patched to 13' }
if(-not $p.Contains('13/13')){ throw 'installer migration completion marker was not patched to 13/13' }
[IO.File]::WriteAllText($provision,$p,(New-Object Text.UTF8Encoding($true)))

# migrate-native.ps1 is generated wholesale by patch-installer.ps1 and therefore
# carries its own 12-file/count gates. Patch every stable ASCII token here.
$m = Get-Content $migrate -Raw -Encoding UTF8
$m = $m.Replace('$migrations.Count -ne 12','$migrations.Count -ne 13')
$m = $m.Replace('Expected 12 migration files','Expected 13 migration files')
$m = $m.Replace('$countText -ne 12','$countText -ne 13')
$m = $m.Replace('Expected 12, actual','Expected 13, actual')
$m = $m.Replace('ALL MIGRATIONS PASS 12/12','ALL MIGRATIONS PASS 13/13')
foreach($old in @('$migrations.Count -ne 12','Expected 12 migration files','$countText -ne 12','Expected 12, actual','ALL MIGRATIONS PASS 12/12')){
  if($m.Contains($old)){ throw ('migrate-native.ps1 still contains old gate: '+$old) }
}
if(-not $m.Contains('ALL MIGRATIONS PASS 13/13')){ throw 'migrate-native.ps1 13/13 completion marker missing' }
[IO.File]::WriteAllText($migrate,$m,(New-Object Text.UTF8Encoding($true)))

# Static source verifier.
$v = Get-Content $verify -Raw -Encoding UTF8
$v = $v.Replace('len(migs) == 12','len(migs) == 13')
$v = $v.Replace('expected 12 migrations','expected 13 migrations')
$v = $v.Replace('12/12','13/13')
$v = $v.Replace('{len(migs)}/12','{len(migs)}/13')
foreach($old in @('len(migs) == 12','expected 12 migrations','{len(migs)}/12')){
  if($v.Contains($old)){ throw ('verify_installer_source.py still contains old gate: '+$old) }
}
[IO.File]::WriteAllText($verify,$v,(New-Object Text.UTF8Encoding($false)))

# Build preflight has both migration-file-count and completion-text assertions.
$b = Get-Content $build -Raw -Encoding UTF8
$b = $b.Replace('$migrations.Count -ne 12','$migrations.Count -ne 13')
$b = $b.Replace('Expected 12 DB migrations','Expected 13 DB migrations')
$b = $b.Replace('12/12','13/13')
foreach($old in @('$migrations.Count -ne 12','Expected 12 DB migrations','12/12')){
  if($b.Contains($old)){ throw ('Build-Setup.ps1 still contains old gate: '+$old) }
}
[IO.File]::WriteAllText($build,$b,(New-Object Text.UTF8Encoding($true)))

foreach($file in @($provision,$migrate,$build)){
  $tokens=$null; $errors=$null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file).Path,[ref]$tokens,[ref]$errors) | Out-Null
  if($errors.Count -gt 0){
    foreach($e in $errors){ Write-Host ($file+': '+$e.Message+' line '+$e.Extent.StartLineNumber) }
    throw ('PowerShell parse failure after auth compat patch: '+$file)
  }
}

if((Get-ChildItem $migDir -Filter '*.sql').Count -ne 13){ throw 'expected 13 migration files after auth compatibility patch' }
Write-Host 'AUTH COMPAT PATCH PASS: 0013 repair added and installer/migrator/build/static gates moved to 13/13.'
