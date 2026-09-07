param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$startNative = Join-Path $SourceRoot 'windows\start-native.ps1'
$systemApi = Join-Path $SourceRoot 'backend\app\api\system.py'
$configPy = Join-Path $SourceRoot 'backend\app\config.py'

foreach($required in @($provision,$startNative,$systemApi,$configPy)){
  if(-not (Test-Path $required)){ throw ('required source file missing: ' + $required) }
}

# Prove the backend contract from source before changing installer expectations.
$systemText = Get-Content $systemApi -Raw -Encoding UTF8
$configText = Get-Content $configPy -Raw -Encoding UTF8
if(-not $systemText.Contains('@router.get("/health")')){ throw 'backend health route is not /health' }
if(-not $configText.Contains('api_prefix: str = "/api/v1"')){ throw 'backend API prefix is not /api/v1' }

# Installer previously checked /api/v1/system/health, but the actual FastAPI route is
# api_prefix(/api/v1) + /health => /api/v1/health. Align installer to the backend.
$p = Get-Content $provision -Raw -Encoding UTF8
if($p.Contains('/api/v1/system/health')){
  $p = $p.Replace('/api/v1/system/health','/api/v1/health')
}
if(-not $p.Contains('/api/v1/health')){ throw 'installer health check path patch missing' }
if($p.Contains('/api/v1/system/health')){ throw 'stale installer health path remains' }

# Windows PowerShell 5.1 requires a UTF-8 BOM to reliably parse scripts containing
# non-ASCII text. Keep exactly one BOM: zero BOM corrupts Chinese source under 5.1,
# while the original start-native.ps1 contains two BOMs and exposes the second as ?param.
$utf8Bom = New-Object Text.UTF8Encoding($true)
[IO.File]::WriteAllText($provision,$p,$utf8Bom)

$startText = [IO.File]::ReadAllText($startNative)
while($startText.Length -gt 0 -and $startText[0] -eq [char]0xFEFF){
  $startText = $startText.Substring(1)
}
if(-not $startText.StartsWith('param(')){ throw 'start-native.ps1 does not start with param after BOM normalization' }
[IO.File]::WriteAllText($startNative,$startText,$utf8Bom)

# Parse both modified PowerShell files under Windows PowerShell 5.1.
foreach($file in @($provision,$startNative)){
  $tokens=$null
  $errors=$null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file).Path,[ref]$tokens,[ref]$errors) | Out-Null
  if($errors.Count -gt 0){
    foreach($e in $errors){ Write-Host ($file + ': ' + $e.Message + ' at line ' + $e.Extent.StartLineNumber + ', column ' + $e.Extent.StartColumnNumber) }
    throw ('PowerShell parse failure: ' + $file)
  }
}

# Require exactly one UTF-8 BOM on start-native.ps1.
$bytes=[IO.File]::ReadAllBytes($startNative)
if($bytes.Length -lt 6){ throw 'start-native.ps1 unexpectedly short' }
$firstBom = ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$secondBom = ($bytes[3] -eq 0xEF -and $bytes[4] -eq 0xBB -and $bytes[5] -eq 0xBF)
if(-not $firstBom){ throw 'start-native.ps1 is missing required UTF-8 BOM for Windows PowerShell 5.1' }
if($secondBom){ throw 'start-native.ps1 still contains a double UTF-8 BOM' }

# Apply the .env ACL upgrade fix in the same cheap preflight patch phase. This makes
# legacy rc2.10 installations (Administrators=Read only) writable by an elevated
# installer before it attempts Set-Content, while keeping normal users excluded.
$envAclPatch = Join-Path (Split-Path -Parent $PSCommandPath) 'patch-env-acl.ps1'
if(-not(Test-Path $envAclPatch)){ throw 'patch-env-acl.ps1 missing' }
& $envAclPatch -SourceRoot $SourceRoot

$finalProvision = Get-Content $provision -Raw -Encoding UTF8
if(-not $finalProvision.Contains('/api/v1/health')){ throw 'health contract lost after .env ACL patch' }
if(-not $finalProvision.Contains("'*S-1-5-32-544:(F)'")){ throw '.env ACL hardening missing after patch chain' }

# Application uninstall must not stop or unregister the dedicated PostgreSQL service.
# The database service and cluster are intentionally retained so data stays online and
# a later reinstall can reuse the same database without recovery gymnastics.
$uninstallDbPattern = '(?m)^[ \t]*Stop-Service ''\$pgService'' -Force\r?\n[ \t]*& ''\$pgBin\\pg_ctl\.exe'' unregister -N ''\$pgService''\r?\n'
if([regex]::IsMatch($finalProvision,$uninstallDbPattern)){
  $finalProvision = [regex]::Replace($finalProvision,$uninstallDbPattern,"# Dedicated PostgreSQL service intentionally preserved during app uninstall.`r`n",1)
} elseif($finalProvision.Contains('Stop-Service ''$pgService'' -Force') -or $finalProvision.Contains('& ''$pgBin\pg_ctl.exe'' unregister -N ''$pgService''')) {
  throw 'PostgreSQL uninstall removal commands found but expected pair was not patchable'
}
if($finalProvision.Contains('Stop-Service ''$pgService'' -Force')){ throw 'uninstall still stops dedicated PostgreSQL service' }
if($finalProvision.Contains('& ''$pgBin\pg_ctl.exe'' unregister -N ''$pgService''')){ throw 'uninstall still unregisters dedicated PostgreSQL service' }
if(-not $finalProvision.Contains('Dedicated PostgreSQL service intentionally preserved during app uninstall')){ throw 'database-service preservation marker missing' }
[IO.File]::WriteAllText($provision,$finalProvision,$utf8Bom)
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $provision).Path,[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count -gt 0){
  foreach($e in $errors){ Write-Host ('Installer after uninstall patch: ' + $e.Message + ' at line ' + $e.Extent.StartLineNumber + ', column ' + $e.Extent.StartColumnNumber) }
  throw 'install-oneclick.ps1 parse failure after PostgreSQL uninstall preservation patch'
}

# Fix the comprehensive E2E harness before any expensive Windows setup. In Windows
# PowerShell, $Args is an automatic variable; using it as a named function parameter
# caused Start-Process to receive a null ArgumentList and made every downstream test a
# false failure. Rename the parameter and every explicit call-site to ArgumentList.
$e2e = Join-Path (Split-Path -Parent $PSCommandPath) 'windows-e2e-validation.ps1'
if(-not(Test-Path $e2e)){ throw 'windows-e2e-validation.ps1 missing' }
$e2eText = Get-Content $e2e -Raw -Encoding UTF8
$e2eText = $e2eText.Replace('function Invoke-ProcessCapture([string]$Exe,[string[]]$Args,[int]$Minutes=12){','function Invoke-ProcessCapture([string]$Exe,[string[]]$ArgumentList,[int]$Minutes=12){')
$e2eText = $e2eText.Replace('-ArgumentList $Args -PassThru','-ArgumentList $ArgumentList -PassThru')
$e2eText = $e2eText.Replace('-Args @(','-ArgumentList @(')
if($e2eText.Contains('[string[]]$Args')){ throw 'E2E harness still uses reserved $Args parameter' }
if($e2eText.Contains('-ArgumentList $Args')){ throw 'E2E harness still forwards reserved $Args variable' }
if($e2eText.Contains('-Args @(')){ throw 'E2E harness still calls obsolete -Args parameter' }
if(-not $e2eText.Contains('[string[]]$ArgumentList')){ throw 'E2E ArgumentList parameter patch missing' }
[IO.File]::WriteAllText($e2e,$e2eText,(New-Object Text.UTF8Encoding($true)))
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $e2e).Path,[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count -gt 0){
  foreach($e in $errors){ Write-Host ('E2E: ' + $e.Message + ' at line ' + $e.Extent.StartLineNumber + ', column ' + $e.Extent.StartColumnNumber) }
  throw 'windows-e2e-validation.ps1 parse failure after ArgumentList patch'
}

Write-Host 'HEALTH CONTRACT PATCH PASS: health path, script encoding, upgrade ACL, E2E binding, and PostgreSQL uninstall preservation verified.'
