param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$p = Get-Content $provision -Raw -Encoding UTF8

$oldExisting = 'if(Test-Path $envPath){ $previousEnvContent = Get-Content $envPath -Raw -ErrorAction SilentlyContinue }'
$newExisting = @'
if(Test-Path $envPath){
  $previousEnvContent = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
  # Legacy rc2.10 made Administrators read-only. An elevated upgrade must first
  # take ownership and restore administrator FullControl before overwriting .env.
  & takeown.exe /F $envPath /A | Out-Null
  if($LASTEXITCODE -ne 0){ Fail '无法取得现有 .env 的管理员所有权' }
  & icacls.exe $envPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
  if($LASTEXITCODE -ne 0){ Fail '无法修复现有 .env 的访问控制列表' }
}
'@

$oldAcl = "& icacls `$envPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(R)' | Out-Null"
$newAcl = @'
& icacls.exe $envPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
if($LASTEXITCODE -ne 0){ Fail '无法设置 .env 的安全访问控制列表' }
'@

if($p.Contains($oldExisting)){
  $p = $p.Replace($oldExisting,$newExisting.TrimEnd("`r","`n"))
} elseif(-not $p.Contains('& takeown.exe /F $envPath /A')) {
  throw 'Existing .env upgrade block not found'
}

if($p.Contains($oldAcl)){
  $p = $p.Replace($oldAcl,$newAcl.TrimEnd("`r","`n"))
} elseif(-not $p.Contains("'*S-1-5-32-544:(F)'")) {
  throw 'Read-only .env ACL line not found'
}

Set-Content -Path $provision -Value $p -Encoding UTF8

$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $provision).Path,[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count -gt 0){
  $sourceLines=Get-Content $provision
  foreach($e in $errors){
    $n=$e.Extent.StartLineNumber
    Write-Host ($e.Message + ' at line ' + $n + ', column ' + $e.Extent.StartColumnNumber)
    foreach($i in ([Math]::Max(1,$n-3)..[Math]::Min($sourceLines.Count,$n+3))){ Write-Host ('SOURCE '+$i+': '+$sourceLines[$i-1]) }
  }
  throw 'install-oneclick.ps1 parse failure after .env ACL patch'
}

$check = Get-Content $provision -Raw -Encoding UTF8
if($check.Contains("'*S-1-5-32-544:(R)'")){ throw 'Administrators are still read-only on .env' }
if(-not $check.Contains('& takeown.exe /F $envPath /A')){ throw 'legacy .env ownership repair missing' }
if(-not $check.Contains("'*S-1-5-18:(R)' '*S-1-5-32-544:(F)'")){ throw 'hardened .env ACL missing' }
Write-Host 'ENV ACL PATCH PASS: SYSTEM=Read, Administrators=FullControl, legacy upgrade repair enabled.'
