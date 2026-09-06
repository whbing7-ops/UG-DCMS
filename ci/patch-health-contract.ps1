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

Write-Host 'HEALTH CONTRACT PATCH PASS: /api/v1/health and single-BOM PowerShell scripts verified.'
