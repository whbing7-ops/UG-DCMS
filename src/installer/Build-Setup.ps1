param(
  [switch]$Offline,
  [switch]$SkipCompile
)
$ErrorActionPreference='Stop'
$buildScriptPath=$PSCommandPath
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root=Split-Path -Parent $PSScriptRoot
$pre=Join-Path $root 'windows\prerequisites'
New-Item -ItemType Directory -Force -Path $pre | Out-Null


function Validate-InstallerSource {
  $expected = '1.0.0-rc2.24'
  $issPath = Join-Path $PSScriptRoot 'UG-DCMS-Setup.iss'
  $cmdPath = Join-Path $PSScriptRoot 'BUILD-SETUP.cmd'
  $provisionPath = Join-Path $root 'windows\install-oneclick.ps1'
  foreach($required in @($issPath,$cmdPath,$provisionPath,(Join-Path $root 'windows\migrate-native.ps1'),(Join-Path $root 'windows\verify-runtime.py'),(Join-Path $root 'backend\requirements.txt'))){
    if(-not (Test-Path $required)){ throw "Installer source missing required file: $required" }
  }
  $issText = Get-Content $issPath -Raw -Encoding UTF8
  $cmdText = Get-Content $cmdPath -Raw
  $provisionText = Get-Content $provisionPath -Raw -Encoding UTF8
  if($issText -notmatch [regex]::Escape($expected)){ throw "UG-DCMS-Setup.iss version is not $expected" }
  if($cmdText -notmatch [regex]::Escape($expected)){ throw "BUILD-SETUP.cmd output version is not $expected" }
  if($provisionText -notmatch [regex]::Escape("Version=$expected")){ throw "install-oneclick.ps1 status version is not $expected" }
  if($issText -match 'LoadStringFromFile\s*\('){ throw 'Unsafe Inno API usage detected: LoadStringFromFile requires AnsiString. Use LoadStringsFromFile for UTF-8 logs.' }
  if($issText -notmatch 'LoadStringsFromFile\s*\('){ throw 'Expected UTF-8-safe LoadStringsFromFile error-log reader is missing.' }
  if($provisionText -notmatch "sys\.version_info\[:2\] == \(3,12\)"){ throw 'Runtime pin check for Python 3.12 is missing.' }
  if((Get-Content $buildScriptPath -Raw -Encoding UTF8) -notmatch '--python-version 3\.12'){ throw 'Wheelhouse is not explicitly targeted to Python 3.12.' }
  if($provisionText -match 'if\(Test-Path \"\$InstallDir\\venv\"\)\{ Remove-Item'){ throw 'Unsafe pre-install deletion of the legacy runtime detected.' }
  if($provisionText -notmatch 'CURRENT-RUNTIME\.txt'){ throw 'Versioned runtime pointer logic is missing.' }
  if($provisionText -notmatch 'CURRENT-RELEASE\.txt'){ throw 'Versioned release pointer logic is missing.' }
  if($provisionText -notmatch 'app-1\.0\.0-rc2\.24-'){ throw 'Versioned release naming is missing.' }
  if($issText -match 'DestDir: "\{app\}\\backend"'){ throw 'Unsafe in-place backend deployment detected. Payload must be staged.' }
  if($issText -notmatch 'DestDir: "\{app\}\\payload\\backend"'){ throw 'Expected staged backend payload is missing.' }
  if($provisionText -notmatch '已恢复上一版本 Release/Runtime 指针'){ throw 'Upgrade rollback pointer logic is missing.' }
  if($provisionText -notmatch 'venv-1\.0\.0-rc2\.24-'){ throw 'Versioned runtime naming is missing.' }
  if($provisionText -notmatch 'verify-runtime\.py' -or $provisionText -notmatch 'RUNTIME-VERIFIED\.txt'){
    throw 'New runtime import self-check is missing.'
  }
  if($provisionText -notmatch '数据库迁移完整性检查通过：16/16'){ throw 'Database migration completeness check is missing.' }
  if($provisionText -match '兼容 health 路径差异'){ throw 'Weak TCP-only health fallback must not be present.' }
  if($provisionText -notmatch 'HTTP 健康检查通过'){ throw 'Strict HTTP health check is missing.' }
  if($provisionText -notmatch '/api/v1/health' -or $provisionText -match '/api/v1/system/health'){
    throw 'Installer health endpoint must match the backend route /api/v1/health.'
  }
  if($provisionText -notmatch "'\*S-1-5-32-544:\(F\)'" -or $provisionText -notmatch 'attrib\.exe -R'){
    throw 'Upgrade-safe .env ACL repair is missing.'
  }
  $migrations = @(Get-ChildItem (Join-Path $root 'db\migrations\*.sql') -File | Sort-Object Name)
  if($migrations.Count -ne 16){ throw "Expected 16 DB migrations, found $($migrations.Count)." }
  for($i=1; $i -le 16; $i++){
    $prefix = ('{0:D4}_' -f $i)
    if(-not $migrations[$i-1].Name.StartsWith($prefix)){ throw "Migration sequence broken at $prefix" }
  }
  Write-Host 'Installer source preflight passed.' -ForegroundColor Green
}

function Download([string]$Url,[string]$Out){
  Write-Host "Downloading $Url" -ForegroundColor Cyan
  Invoke-WebRequest $Url -OutFile $Out -UseBasicParsing
}

function Resolve-Iscc {
  # 1) Current PATH
  $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if($cmd){ return $cmd.Source }

  # 2) Common install locations. Newer Inno Setup may be 64-bit.
  # Force the filtered result to remain an array even when exactly one path matches.
  # Without the outer @(...), PowerShell collapses a single result to a String, and
  # $candidates[0] would incorrectly return only the first character (for example 'C').
  $candidates = @( @(
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
  ) | Where-Object { $_ -and (Test-Path $_) } )
  if($candidates.Count -gt 0){ return [string]$candidates[0] }

  # 3) Registry uninstall entries (machine/user, 32/64 bit views).
  $regRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
  )
  foreach($rootKey in $regRoots){
    $entries = Get-ItemProperty $rootKey -ErrorAction SilentlyContinue | Where-Object {
      $_.DisplayName -like 'Inno Setup*'
    }
    foreach($e in $entries){
      if($e.InstallLocation){
        $candidate = Join-Path $e.InstallLocation 'ISCC.exe'
        if(Test-Path $candidate){ return $candidate }
      }
    }
  }

  # 4) Focused fallback search only under Program Files roots.
  foreach($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})){
    if($base -and (Test-Path $base)){
      $hit = Get-ChildItem -Path $base -Filter ISCC.exe -File -Recurse -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -match 'Inno Setup' } |
             Select-Object -First 1
      if($hit){ return $hit.FullName }
    }
  }
  return $null
}


function Resolve-PythonForBuild {
  $candidates = @(
    "$env:ProgramFiles\Python312\python.exe",
    "$env:ProgramFiles\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
  )
  foreach($p in $candidates){ if($p -and (Test-Path $p)){ return $p } }
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if($cmd){ return $cmd.Source }
  return $null
}

function Ensure-Wheelhouse {
  $wheelhouse = Join-Path $pre 'wheels'
  if(Test-Path $wheelhouse){ Remove-Item $wheelhouse -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
  $requirements = Join-Path $root 'backend\requirements.txt'
  if(-not (Test-Path $requirements)){ throw "Missing backend requirements: $requirements" }

  $py = Resolve-PythonForBuild
  if(-not $py){
    $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
    if(-not $wg){ throw 'Python 3.11+ is required to prepare the offline wheelhouse, and winget was not found.' }
    Write-Host 'Python not found on build machine. Installing Python 3.12 for wheel preparation...' -ForegroundColor Cyan
    & $wg.Source install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
    if($LASTEXITCODE -ne 0){ throw "winget failed to install Python. Exit code: $LASTEXITCODE" }
    Start-Sleep -Seconds 2
    $py = Resolve-PythonForBuild
  }
  if(-not $py){ throw 'Python installation completed but python.exe could not be located.' }

  Write-Host "Preparing offline Windows wheels with: $py" -ForegroundColor Cyan
  Write-Host "Wheelhouse: $wheelhouse" -ForegroundColor Cyan
  & $py -m pip download --disable-pip-version-check --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp --abi cp312 --dest $wheelhouse -r $requirements
  if($LASTEXITCODE -ne 0){ throw "pip download failed. Exit code: $LASTEXITCODE" }

  $wheelFiles = @(Get-ChildItem -Path $wheelhouse -Filter *.whl -File -ErrorAction SilentlyContinue | Sort-Object Name)
  $wheelCount = $wheelFiles.Count
  if($wheelCount -lt 1){ throw 'Offline wheelhouse is empty after pip download.' }
  $manifest = Join-Path $wheelhouse 'SHA256SUMS.txt'
  $manifestLines = foreach($wf in $wheelFiles){
    $h=(Get-FileHash $wf.FullName -Algorithm SHA256).Hash.ToLower()
    "$h  $($wf.Name)"
  }
  Set-Content -Path $manifest -Value $manifestLines -Encoding ASCII
  Write-Host "Offline wheelhouse ready: $wheelCount wheel files + SHA256 manifest." -ForegroundColor Green
}

Validate-InstallerSource

# WinSW fixed version: registers Uvicorn as a Windows Service.
$winsw=Join-Path $pre 'WinSW-x64.exe'
if(-not (Test-Path $winsw)){
  Download 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe' $winsw
}

if($Offline){
  Write-Host 'Offline build requested.' -ForegroundColor Cyan
  Write-Host 'Place Python 3.12 x64 installer at windows\prerequisites\python-installer.exe' -ForegroundColor Yellow
  Write-Host 'Place PostgreSQL 16 x64 installer at windows\prerequisites\postgresql-installer.exe' -ForegroundColor Yellow
  if(-not (Test-Path (Join-Path $pre 'python-installer.exe'))){ throw 'Missing python-installer.exe' }
  if(-not (Test-Path (Join-Path $pre 'postgresql-installer.exe'))){ throw 'Missing postgresql-installer.exe' }
}


# Prepare a self-contained Python dependency wheelhouse so Setup.exe does not depend on PyPI during installation.
Ensure-Wheelhouse

if($SkipCompile){ exit 0 }

$iscc = Resolve-Iscc
if(-not $iscc){
  $wg=Get-Command winget.exe -ErrorAction SilentlyContinue
  if(-not $wg){ throw 'Inno Setup 6 and winget were not found. Install Inno Setup 6 and retry.' }
  Write-Host 'Installing Inno Setup 6...' -ForegroundColor Cyan
  & $wg.Source install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements --silent
  if($LASTEXITCODE -ne 0){ throw "winget failed to install Inno Setup. Exit code: $LASTEXITCODE" }
  Start-Sleep -Seconds 2
  $iscc = Resolve-Iscc
}
if(-not $iscc){
  throw 'Inno Setup was installed, but ISCC.exe still could not be located. Checked Program Files, Program Files (x86), LocalAppData, and registry install locations.'
}
Write-Host "Using ISCC: $iscc" -ForegroundColor Green

Push-Location $PSScriptRoot
try {
  & $iscc 'UG-DCMS-Setup.iss'
  if($LASTEXITCODE -ne 0){ throw "ISCC compile failed: $LASTEXITCODE" }
  Write-Host "Setup.exe created: installer\output\UG-DCMS-Setup-1.0.0-rc2.24.exe" -ForegroundColor Green
} finally { Pop-Location }
