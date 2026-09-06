param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$p = Get-Content $provision -Raw -Encoding UTF8

$old = @'
$envPath="$InstallDir\.env"
if(Test-Path $envPath){ $previousEnvContent = Get-Content $envPath -Raw -ErrorAction SilentlyContinue }
Set-Content -Path $envPath -Value $envText -Encoding UTF8
& icacls $envPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(R)' | Out-Null
'@

$new = @'
$envPath="$InstallDir\.env"
if(Test-Path $envPath){
  $previousEnvContent = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
  # rc2.10 originally reduced Administrators to read-only, which makes the next
  # elevated upgrade unable to overwrite .env. Repair legacy ACL before writing.
  Write-Step '修复旧版本 .env ACL 以允许管理员安全升级...'
  & takeown.exe /F $envPath /A | Out-Null
  if($LASTEXITCODE -ne 0){ Fail '无法取得现有 .env 的管理员所有权' }
  & icacls.exe $envPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
  if($LASTEXITCODE -ne 0){ Fail '无法修复现有 .env 的访问控制列表' }
}
Set-Content -Path $envPath -Value $envText -Encoding UTF8
# Secrets stay hidden from normal users; LocalSystem may read them and local
# Administrators retain full control so future elevated upgrades can replace them.
& icacls.exe $envPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
if($LASTEXITCODE -ne 0){ Fail '无法设置 .env 的安全访问控制列表' }
'@

if($p.Contains($old)){
  $p = $p.Replace($old,$new)
} elseif(-not $p.Contains("'*S-1-5-32-544:(F)'")) {
  throw 'Expected .env ACL block not found and hardened ACL is absent'
}

Set-Content -Path $provision -Value $p -Encoding UTF8

$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $provision).Path,[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count -gt 0){
  foreach($e in $errors){ Write-Host ($e.Message + ' at line ' + $e.Extent.StartLineNumber) }
  throw 'install-oneclick.ps1 parse failure after .env ACL patch'
}

$check = Get-Content $provision -Raw -Encoding UTF8
if($check.Contains("'*S-1-5-32-544:(R)'")){ throw 'Administrators are still read-only on .env' }
if(-not $check.Contains('& takeown.exe /F $envPath /A')){ throw 'legacy .env ownership repair missing' }
if(-not $check.Contains("'*S-1-5-18:(R)' '*S-1-5-32-544:(F)'")){ throw 'hardened .env ACL missing' }
Write-Host 'ENV ACL PATCH PASS: SYSTEM=Read, Administrators=FullControl, legacy upgrade repair enabled.'
