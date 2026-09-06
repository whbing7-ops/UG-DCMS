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
[IO.File]::WriteAllText($provision,$p,(New-Object Text.UTF8Encoding($false)))

# The original archive contains a double UTF-8 BOM in start-native.ps1. PowerShell
# consumes one BOM and exposes the second as a leading character, producing '?param'.
# Strip every leading U+FEFF and write deterministic UTF-8 without BOM.
$startText = [IO.File]::ReadAllText($startNative)
while($startText.Length -gt 0 -and $startText[0] -eq [char]0xFEFF){
  $startText = $startText.Substring(1)
}
if(-not $startText.StartsWith('param(')){ throw 'start-native.ps1 does not start with param after BOM normalization' }
[IO.File]::WriteAllText($startNative,$startText,(New-Object Text.UTF8Encoding($false)))

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

$bytes=[IO.File]::ReadAllBytes($startNative)
if($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF){
  throw 'start-native.ps1 still has UTF-8 BOM'
}

Write-Host 'HEALTH CONTRACT PATCH PASS: /api/v1/health and BOM-free start-native.ps1 verified.'
