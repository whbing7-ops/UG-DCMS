param(
  [string]$InstallDir = "$env:ProgramData\UG-DCMS",
  [int]$AppPort = 8080,
  [int]$PgPort = 55432,
  [switch]$SkipPrerequisiteInstall
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Persistent installer diagnostics. Never use Inno Setup's transient {tmp} path.
$LogDir = Join-Path $InstallDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir 'install.log'
$LastErrorFile = Join-Path $LogDir 'LAST-ERROR.txt'
Remove-Item $LastErrorFile -Force -ErrorAction SilentlyContinue
try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}

# Upgrade transaction state. Pointers are switched only after the new release/runtime and DB migration succeed.
$previousRuntime = $null
$previousRelease = $null
$newRuntime = $null
$newRelease = $null
$currentRuntimeFile = $null
$currentReleaseFile = $null
$previousEnvContent = $null
$hadCompletedInstall = Test-Path (Join-Path $InstallDir 'INSTALLATION-STATUS.txt')
$previousStartNativeContent = $null
$previousStartNativePath = Join-Path $InstallDir 'start-native.ps1'
if(Test-Path $previousStartNativePath){ $previousStartNativeContent = Get-Content $previousStartNativePath -Raw -ErrorAction SilentlyContinue }

function Write-Status([string]$Text) {
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Write-Host "[$stamp] $Text" -ForegroundColor Cyan
  # Start-Transcript already records console output. A second writer races with
  # the transcript file lock on Windows PowerShell 5.1.
}
function Invoke-ProcessWithTimeout {
  param([string]$FilePath,[string[]]$ArgumentList=@(),[int]$TimeoutSeconds=900,[string]$Step='外部程序')
  Write-Status "$Step：启动 $FilePath"
  # Keep console output attached to the visible provisioning window so long-running tools (especially pip) show real progress.
  $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -PassThru -NoNewWindow
  if(-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
    try { $proc.Kill() } catch {}
    throw "$Step 超过 $TimeoutSeconds 秒仍未完成，已终止。"
  }
  # Windows PowerShell 5.1 may leave ExitCode unpopulated immediately after the
  # timed WaitForExit overload. Complete the wait and refresh the process object;
  # otherwise $null -ne 0 is true and a successful command is reported as failed.
  $proc.WaitForExit()
  $proc.Refresh()
  $exitCode = $proc.ExitCode
  if($null -eq $exitCode){
    # Some Windows 10 / Windows PowerShell 5.1 builds do not expose ExitCode on
    # a Start-Process object even after the process has definitely terminated.
    # Do not turn that PowerShell defect into a false installation failure.
    # Every caller has a concrete downstream verification (runtime executable,
    # pip/module import, installed prerequisite, migration count or HTTP health).
    Write-Status "$Step 已结束；Windows 未提供退出码，继续执行后续结果校验。"
    return
  }
  if($exitCode -ne 0){ throw "$Step 失败，退出码 $exitCode" }
}

function Write-Step([string]$Text) { Write-Status "[UG-DCMS] $Text" }
function Fail([string]$Text) { throw "UG-DCMS 安装失败：$Text" }
function Test-Admin {
  $id=[Security.Principal.WindowsIdentity]::GetCurrent()
  $p=New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function New-RandomPassword([int]$Length=32) {
  $chars='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%_-'
  $bytes=New-Object byte[] ($Length)
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}
function Find-Python {
  $candidates=@(
    "$env:ProgramFiles\Python312\python.exe",
    "$env:ProgramFiles\Python311\python.exe",
    "$env:LocalAppData\Programs\Python\Python312\python.exe",
    "$env:LocalAppData\Programs\Python\Python311\python.exe"
  )
  foreach($p in $candidates){ if(Test-Path $p){ return $p } }
  $cmd=Get-Command python.exe -ErrorAction SilentlyContinue
  if($cmd){ return $cmd.Source }
  return $null
}
function Test-Python([string]$Python) {
  if(-not $Python){ return $false }
  try { return ((& $Python -c "import sys; print(int(sys.version_info[:2] == (3,12)))") -eq '1') } catch { return $false }
}
function Find-PgBin {
  $roots=@(
    "$env:ProgramFiles\PostgreSQL\16\bin",
    "$env:ProgramFiles\PostgreSQL\17\bin"
  )
  foreach($p in $roots){ if(Test-Path (Join-Path $p 'initdb.exe')){ return $p } }
  $cmd=Get-Command initdb.exe -ErrorAction SilentlyContinue
  if($cmd){ return (Split-Path -Parent $cmd.Source) }
  return $null
}
function Invoke-WingetInstall([string]$Id) {
  $wg=Get-Command winget.exe -ErrorAction SilentlyContinue
  if(-not $wg){ Fail "缺少 $Id，且系统没有 winget。请使用包含离线依赖的完整安装包，或先安装 App Installer。" }
  Invoke-ProcessWithTimeout -FilePath $wg.Source -ArgumentList @('install','--id',$Id,'-e','--accept-package-agreements','--accept-source-agreements','--silent','--disable-interactivity') -TimeoutSeconds 1200 -Step "winget 安装 $Id"
}
function Install-PythonIfNeeded {
  $py=Find-Python
  if(Test-Python $py){ return $py }
  if($SkipPrerequisiteInstall){ Fail '未找到 Python 3.12 x64' }
  $offline=Join-Path $PSScriptRoot 'prerequisites\python-installer.exe'
  if(Test-Path $offline){
    Write-Step '安装内置 Python 3.12...'
    Invoke-ProcessWithTimeout -FilePath $offline -ArgumentList @('/quiet','InstallAllUsers=1','PrependPath=1','Include_test=0','Include_launcher=1') -TimeoutSeconds 900 -Step 'Python 安装'
  } else {
    Write-Step '未检测到 Python 3.12，自动安装 Python 3.12...'
    Invoke-WingetInstall 'Python.Python.3.12'
  }
  $py=Find-Python
  if(-not (Test-Python $py)){ Fail 'Python 已执行安装但仍无法检测到 Python 3.12 x64 运行时' }
  return $py
}
function Install-PostgresIfNeeded {
  $pg=Find-PgBin
  if($pg){ return $pg }
  if($SkipPrerequisiteInstall){ Fail '未找到 PostgreSQL 16/17 Windows binaries' }
  $offline=Join-Path $PSScriptRoot 'prerequisites\postgresql-installer.exe'
  if(Test-Path $offline){
    Write-Step '安装内置 PostgreSQL binaries...'
    # 仅需要程序文件；真正的数据集群由 UG-DCMS 自己创建和管理。
    $tempPw=New-RandomPassword 28
    $args="--mode unattended --unattendedmodeui none --superpassword `"$tempPw`" --servicename `"postgresql-x64-16`" --serverport 55439 --disable-components stackbuilder"
    Invoke-ProcessWithTimeout -FilePath $offline -ArgumentList @($args) -TimeoutSeconds 1200 -Step 'PostgreSQL 安装'
  } else {
    Write-Step '未检测到 PostgreSQL 16/17，通过 winget 自动安装 PostgreSQL 16...'
    Invoke-WingetInstall 'PostgreSQL.PostgreSQL.16'
  }
  $pg=Find-PgBin
  if(-not $pg){ Fail 'PostgreSQL 已执行安装但仍找不到 initdb.exe' }
  return $pg
}
function Wait-Port([string]$HostName,[int]$Port,[int]$Seconds=45){
  $until=(Get-Date).AddSeconds($Seconds)
  do {
    try { $c=New-Object Net.Sockets.TcpClient; $c.Connect($HostName,$Port); $c.Close(); return $true } catch { Start-Sleep -Milliseconds 500 }
  } while((Get-Date) -lt $until)
  return $false
}
function Stop-StaleAppProcesses {
  # WinSW 的服务状态可能已经是 Stopped，但旧 powershell/python 子进程仍存活。
  # 只终止命令行明确指向本安装目录的 UG-DCMS 进程，绝不清理其他 Python 服务。
  $escaped=[regex]::Escape($InstallDir)
  $stale=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $escaped -and
    ($_.CommandLine -match 'start-native\.ps1' -or $_.CommandLine -match '-m\s+uvicorn\s+app\.main:app')
  }
  foreach($p in $stale){
    Write-Status "清理旧 UG-DCMS 应用进程 PID=$($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if($stale){ Start-Sleep -Seconds 2 }
}
function Get-PortOwner([int]$Port){
  $c=Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
  if(-not $c){ return $null }
  return Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
}

trap {
  $failedText = "[FAILED] $($_.Exception.Message)`r`n$($_.Exception.ToString())"
  Write-Host "`n[FAILED] $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "安装日志：$LogFile" -ForegroundColor Yellow
  try { Set-Content -Path $LastErrorFile -Value $failedText -Encoding UTF8 } catch {}

  # Transactional rollback for an upgrade: restore the old pointers/config and try to restart
  # the previously healthy application. Database migrations are forward-only, but all current
  # migrations are additive; keeping the old app pointer is still safer than leaving a broken
  # new runtime selected.
  if($hadCompletedInstall){
    try {
      if($currentRuntimeFile -and $previousRuntime){ Set-Content -Path $currentRuntimeFile -Value $previousRuntime -Encoding ASCII }
      if($currentReleaseFile -and $previousRelease){ Set-Content -Path $currentReleaseFile -Value $previousRelease -Encoding ASCII }
      if($previousEnvContent -ne $null){ Set-Content -Path (Join-Path $InstallDir '.env') -Value $previousEnvContent -Encoding UTF8 }
      if($previousStartNativeContent -ne $null){ Set-Content -Path $previousStartNativePath -Value $previousStartNativeContent -Encoding UTF8 }
      $svc = Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue
      if($svc -and $svc.Status -ne 'Running'){
        Start-Service 'UGDCMS-App' -ErrorAction SilentlyContinue
      }
      Write-Status '升级失败后已恢复上一版本 Release/Runtime 指针。'
    } catch { Write-Host "[ROLLBACK-WARN] $($_.Exception.Message)" -ForegroundColor Yellow }
  }
  try { Stop-Transcript | Out-Null } catch {}
  exit 1
}

if(-not (Test-Admin)){ Fail '请以管理员身份运行 Setup.exe' }
if([Environment]::Is64BitOperatingSystem -eq $false){ Fail '仅支持 64 位 Windows' }
$os=[Environment]::OSVersion.Version
Write-Step "Windows $($os.ToString()) / 安装目录 $InstallDir"

# 若由 Inno Setup 安装，PSScriptRoot 位于 InstallDir\windows；若直接运行源码，则 root 为上一级。
$sourceRoot=Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Stop a previously installed application service before touching application files.
# Windows keeps loaded .pyd/.dll modules locked while Python is running.
$existingAppService = Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue
if($existingAppService){
  Write-Step '检测到现有 UG-DCMS 应用服务，先停止服务以安全升级...'
  try { Stop-Service 'UGDCMS-App' -Force -ErrorAction Stop } catch { Write-Status "停止旧服务时收到警告：$($_.Exception.Message)" }
  $waitUntil=(Get-Date).AddSeconds(30)
  do {
    $svc=Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue
    if(-not $svc -or $svc.Status -eq 'Stopped'){ break }
    Start-Sleep -Milliseconds 500
  } while((Get-Date) -lt $waitUntil)
  $svc=Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue
  if($svc -and $svc.Status -ne 'Stopped'){ Fail '旧 UGDCMS-App 服务在 30 秒内未停止。请重启 Windows 后重新运行安装程序。' }
  Stop-StaleAppProcesses
  $portOwner=Get-PortOwner $AppPort
  if($portOwner){
    Fail "端口 $AppPort 已被其他程序占用（PID=$($portOwner.ProcessId)，$($portOwner.Name)）。请关闭该程序或释放端口后重试。"
  }
}
# Application payload is staged by Inno Setup under {app}\payload. When the script is
# run directly from source, use the repository root instead. Never overwrite the currently
# running release in place.
$payloadRoot = Join-Path $InstallDir 'payload'
if(-not (Test-Path (Join-Path $payloadRoot 'backend'))){ $payloadRoot = $sourceRoot }
foreach($name in @('backend','frontend','db')){
  if(-not (Test-Path (Join-Path $payloadRoot $name))){ Fail "安装包缺少 $name payload" }
}
$releaseRoot = Join-Path $InstallDir 'releases'
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$currentReleaseFile = Join-Path $releaseRoot 'CURRENT-RELEASE.txt'
if(Test-Path $currentReleaseFile){
  $previousRelease = (Get-Content $currentReleaseFile -Raw -ErrorAction SilentlyContinue).Trim()
  if($previousRelease -and -not [IO.Path]::IsPathRooted($previousRelease)){ $previousRelease = Join-Path $releaseRoot $previousRelease }
  if($previousRelease -and -not (Test-Path $previousRelease)){ $previousRelease = $null }
}
$releaseName = 'app-1.0.0-rc2.33-' + (Get-Date -Format 'yyyyMMddHHmmss')
$newRelease = Join-Path $releaseRoot $releaseName
if(Test-Path $newRelease){ Fail "目标 Release 已存在：$newRelease" }
New-Item -ItemType Directory -Force -Path $newRelease | Out-Null
foreach($name in @('backend','frontend','db')){
  Copy-Item (Join-Path $payloadRoot $name) (Join-Path $newRelease $name) -Recurse -Force
}
Write-Step "已创建版本化应用 Release：$newRelease"
New-Item -ItemType Directory -Force -Path "$InstallDir\data\files","$InstallDir\logs","$InstallDir\postgres" | Out-Null

$python=Install-PythonIfNeeded
$pgBin=Install-PostgresIfNeeded
Write-Step "Python: $python"
Write-Step "PostgreSQL: $pgBin"

# 创建版本化、不可变 Python Runtime。
# 升级时绝不删除当前正在使用的 Runtime，避免 Windows 对 _bcrypt.pyd、psycopg*.pyd 等已加载二进制模块的文件锁导致安装失败。
Write-Step '创建版本化应用 Python Runtime...'
$runtimeRoot = Join-Path $InstallDir 'runtime'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$currentRuntimeFile = Join-Path $runtimeRoot 'CURRENT-RUNTIME.txt'
if(Test-Path $currentRuntimeFile){
  $previousRuntime = (Get-Content $currentRuntimeFile -Raw -ErrorAction SilentlyContinue).Trim()
  if($previousRuntime -and -not [IO.Path]::IsPathRooted($previousRuntime)){
    $previousRuntime = Join-Path $runtimeRoot $previousRuntime
  }
  if($previousRuntime -and -not (Test-Path $previousRuntime)){ $previousRuntime = $null }
}
$runtimeName = 'venv-1.0.0-rc2.33-' + (Get-Date -Format 'yyyyMMddHHmmss')
$newRuntime = Join-Path $runtimeRoot $runtimeName
if(Test-Path $newRuntime){ Fail "目标 Runtime 已存在：$newRuntime" }
Invoke-ProcessWithTimeout -FilePath $python -ArgumentList @('-m','venv',$newRuntime) -TimeoutSeconds 180 -Step '创建 Python 虚拟环境'
$venvPy=Join-Path $newRuntime 'Scripts\python.exe'
# ensurepip is bundled with CPython and never requires network access.
Invoke-ProcessWithTimeout -FilePath $venvPy -ArgumentList @('-m','ensurepip','--upgrade') -TimeoutSeconds 180 -Step '离线初始化 pip'
$wheelhouse = "$InstallDir\windows\prerequisites\wheels"
if(Test-Path $wheelhouse){
  Write-Step '检测到离线 Python Wheel 包，校验安装包依赖完整性...'
  $wheelManifest = Join-Path $wheelhouse 'SHA256SUMS.txt'
  if(-not (Test-Path $wheelManifest)){ Fail '离线 Wheel 包缺少 SHA256SUMS.txt 完整性清单' }
  foreach($line in Get-Content $wheelManifest){
    if([string]::IsNullOrWhiteSpace($line)){ continue }
    if($line -notmatch '^([0-9a-fA-F]{64})\s{2}(.+)$'){ Fail "Wheel 完整性清单格式错误：$line" }
    $expectedHash=$matches[1].ToLower(); $wheelName=$matches[2]
    $wheelPath=Join-Path $wheelhouse $wheelName
    if(-not (Test-Path $wheelPath)){ Fail "离线 Wheel 缺失：$wheelName" }
    $actualHash=(Get-FileHash $wheelPath -Algorithm SHA256).Hash.ToLower()
    if($actualHash -ne $expectedHash){ Fail "离线 Wheel 校验失败：$wheelName" }
  }
  Write-Step '离线 Wheel 完整性校验通过，使用本地依赖安装（整个依赖阶段无需访问 PyPI）...'
  Invoke-ProcessWithTimeout -FilePath $venvPy -ArgumentList @('-m','pip','install','--disable-pip-version-check','--no-index','--find-links',$wheelhouse,'-r',"$newRelease\backend\requirements.txt") -TimeoutSeconds 900 -Step '安装 UG-DCMS Python 依赖（离线）'
} else {
  Write-Step '安装包未包含离线 Wheels，使用 PyPI 在线安装；将实时显示 pip 下载/解析进度...'
  Invoke-ProcessWithTimeout -FilePath $venvPy -ArgumentList @('-m','pip','install','--disable-pip-version-check','--upgrade','pip') -TimeoutSeconds 600 -Step '在线升级 pip'
  Invoke-ProcessWithTimeout -FilePath $venvPy -ArgumentList @('-m','pip','install','--disable-pip-version-check','--prefer-binary','--only-binary=:all:','--retries','2','--timeout','30','-r',"$newRelease\backend\requirements.txt") -TimeoutSeconds 1200 -Step '安装 UG-DCMS Python 依赖（在线）'
}
# 在切换服务前先验证新 Runtime 能实际导入应用关键依赖。
# 不使用 python -c：Windows PowerShell 5.1 的 Start-Process 会错误拆分多语句参数。
$runtimeVerifyScript = Join-Path $InstallDir 'windows\verify-runtime.py'
$runtimeVerifyMarker = Join-Path $newRuntime 'RUNTIME-VERIFIED.txt'
Remove-Item $runtimeVerifyMarker -Force -ErrorAction SilentlyContinue
Invoke-ProcessWithTimeout -FilePath $venvPy -ArgumentList @($runtimeVerifyScript,(Join-Path $newRelease 'backend'),$runtimeVerifyMarker) -TimeoutSeconds 120 -Step '验证新 Python Runtime 和后端应用导入'
if(-not (Test-Path $runtimeVerifyMarker)){ Fail 'Python Runtime 导入校验未生成成功标记；依赖或后端应用导入失败' }


# 数据库专用集群，不使用/覆盖现有 PostgreSQL 集群。
$pgData="$InstallDir\postgres\data"
$pgAdminPwFile="$InstallDir\postgres\.pg_admin_pw"
$isExistingCluster=Test-Path "$pgData\PG_VERSION"
# 如果上一次首次安装被强制中断，可能只留下未完成的数据集群而没有安装状态/凭据。
# 不直接删除，先改名保留，再重新初始化，避免把不完整状态误当成可升级生产库。
# 只有 INSTALLATION-STATUS.txt 才表示上一轮真正完成了迁移和健康检查。
# 旧 rc2.5/rc2.6 可能在迁移失败后已经写出 .env，因此不能把 .env 当作完整安装标志。
$statusFile = Join-Path $InstallDir 'INSTALLATION-STATUS.txt'
if($isExistingCluster -and -not (Test-Path $statusFile)){
  $orphan="$InstallDir\postgres\data-incomplete-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  Write-Step "检测到未完成的旧安装（无 INSTALLATION-STATUS.txt），保留数据库到 $orphan 并重新初始化..."
  if(Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue){
    try { Stop-Service 'UGDCMS-App' -Force -ErrorAction SilentlyContinue } catch {}
    $oldSvcExe = Join-Path $InstallDir 'UGDCMS-App.exe'
    if(Test-Path $oldSvcExe){ try { & $oldSvcExe uninstall | Out-Null } catch {} }
  }
  if(Get-Service 'UGDCMS-PostgreSQL' -ErrorAction SilentlyContinue){
    try { Stop-Service 'UGDCMS-PostgreSQL' -Force -ErrorAction SilentlyContinue } catch {}
    try { & "$pgBin\pg_ctl.exe" unregister -N 'UGDCMS-PostgreSQL' | Out-Null } catch {}
  }
  Move-Item $pgData $orphan -Force
  Remove-Item (Join-Path $InstallDir '.env') -Force -ErrorAction SilentlyContinue
  Remove-Item $statusFile -Force -ErrorAction SilentlyContinue
  $isExistingCluster=$false
}
$pgAdminPassword=$null
$appDbPassword=$null
if($isExistingCluster -and (Test-Path "$InstallDir\.env")){
  $oldPw=(Get-Content "$InstallDir\.env" | Where-Object { $_ -match '^DCMS_PG_PASSWORD=' } | Select-Object -First 1)
  if($oldPw){ $appDbPassword=$oldPw.Substring('DCMS_PG_PASSWORD='.Length) }
}
if(-not $appDbPassword){ $appDbPassword=New-RandomPassword 36 }
$pgService='UGDCMS-PostgreSQL'
$existingPg=Get-Service -Name $pgService -ErrorAction SilentlyContinue
if($existingPg){
  Write-Step '移除旧 UG-DCMS PostgreSQL 服务以执行干净升级...'
  if($existingPg.Status -ne 'Stopped'){ Stop-Service $pgService -Force -ErrorAction SilentlyContinue }
  & "$pgBin\pg_ctl.exe" unregister -N $pgService | Out-Null
}
if(-not $isExistingCluster){
  Write-Step '初始化 UG-DCMS 专用 PostgreSQL 数据集群...'
  $pgAdminPassword=New-RandomPassword 36
  Set-Content -Path $pgAdminPwFile -Value $pgAdminPassword -Encoding ASCII -NoNewline
  New-Item -ItemType Directory -Force -Path $pgData | Out-Null
  & "$pgBin\initdb.exe" -D $pgData -U postgres -A scram-sha-256 --pwfile=$pgAdminPwFile --encoding=UTF8 --locale=C
  if($LASTEXITCODE -ne 0){ Fail 'initdb 初始化失败' }
  Remove-Item $pgAdminPwFile -Force -ErrorAction SilentlyContinue
}
# 强制本地监听与独立端口
Add-Content "$pgData\postgresql.conf" "`n# UG-DCMS managed settings`nport = $PgPort`nlisten_addresses = '127.0.0.1'`nmax_connections = 60`n"
& "$pgBin\pg_ctl.exe" register -N $pgService -D $pgData -S auto
if($LASTEXITCODE -ne 0){ Fail '注册 UGDCMS-PostgreSQL Windows 服务失败' }
Start-Service $pgService
if(-not (Wait-Port '127.0.0.1' $PgPort 60)){ Fail "PostgreSQL 未在端口 $PgPort 启动" }

# 首次安装创建应用数据库；升级时直接复用原有 dcms 账户密码。
$psql="$pgBin\psql.exe"
# Force UTF-8 for all psql sessions; SQL migration files are UTF-8.
$env:PGCLIENTENCODING='UTF8'
if(-not $isExistingCluster){
  Write-Step '创建 UG-DCMS 数据库和应用账户...'
  $env:PGPASSWORD=$pgAdminPassword
  $escapedAppPw=$appDbPassword.Replace("'","''")
  & $psql -X -h 127.0.0.1 -p $PgPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE dcms LOGIN PASSWORD '$escapedAppPw';"
  if($LASTEXITCODE -ne 0){ Fail '创建 dcms 数据库账户失败' }
  & $psql -X -h 127.0.0.1 -p $PgPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE dcms OWNER dcms;'
  if($LASTEXITCODE -ne 0){ Fail '创建 dcms 数据库失败' }
} else {
  Write-Step '检测到现有 UG-DCMS 数据库，保留原数据并执行升级迁移...'
  $env:PGPASSWORD=$appDbPassword
  & $psql -h 127.0.0.1 -p $PgPort -U dcms -d dcms -qtAX -c 'SELECT 1;' | Out-Null
  if($LASTEXITCODE -ne 0){ Fail '现有数据库存在，但无法用已保存的 UG-DCMS 数据库凭据连接。为保护数据，安装已停止。' }
}

# 运行迁移
$env:PGPASSWORD=$appDbPassword
$env:PGHOST='127.0.0.1'; $env:PGPORT="$PgPort"; $env:PGUSER='dcms'; $env:PGDATABASE='dcms'
Write-Step '执行数据库迁移...'
$migrateScript = Join-Path $InstallDir 'windows\migrate-native.ps1'
Write-Step "迁移脚本：$migrateScript"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $migrateScript -InstallDir $newRelease -PgBin $pgBin -PgHost '127.0.0.1' -PgPort $PgPort -PgUser 'dcms' -PgDatabase 'dcms'
$migrateExit = $LASTEXITCODE
if($migrateExit -ne 0){ Fail "数据库迁移失败（退出码 $migrateExit）。请查看 $LogFile" }
# 迁移完成后必须验证所有迁移均已登记，避免脚本异常提前退出却误判成功。
$env:PGPASSWORD=$appDbPassword
$migrationCount = (& $psql -X -h 127.0.0.1 -p $PgPort -U dcms -d dcms -qtAX -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM schema_migration;').Trim()
if($LASTEXITCODE -ne 0){ Fail '无法验证数据库迁移状态' }
if([int]$migrationCount -ne 19){ Fail "数据库迁移数量异常：期望 19，实际 $migrationCount" }
Write-Step '数据库迁移完整性检查通过：19/19（综合演示业务数据已清理，仅保留账户与基础配置）'

# 应用配置。密码只允许 SYSTEM/Administrators 读取。
$envText=@"
DCMS_PG_HOST=127.0.0.1
DCMS_PG_BIN=$pgBin
DCMS_PG_PORT=$PgPort
DCMS_PG_USER=dcms
DCMS_PG_PASSWORD=$appDbPassword
DCMS_PG_DATABASE=dcms
DCMS_BACKUP_ROOT=$InstallDir\backups
DCMS_STORAGE_ROOT=$InstallDir\data\files
DCMS_FRONTEND_ROOT=$newRelease\frontend
DCMS_ENVIRONMENT=PROD
"@
$envPath="$InstallDir\.env"
if(Test-Path $envPath){
  # rc2.15 and earlier restricted Administrators to read-only access. Restore
  # upgrade-safe permissions before reading/replacing the protected config.
  & attrib.exe -R $envPath 2>$null
  & icacls $envPath /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
  if($LASTEXITCODE -ne 0){ Fail "无法修复现有配置文件权限：$envPath" }
  $previousEnvContent = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
}
Set-Content -Path $envPath -Value $envText -Encoding UTF8
& icacls $envPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
if($LASTEXITCODE -ne 0){ Fail "无法保护应用配置文件：$envPath" }

# 启动脚本 + WinSW 服务配置
# 只有新 Runtime 已安装、依赖验证和数据库迁移全部成功后，才原子切换 CURRENT-RUNTIME。
$tmpRuntimeFile = "$currentRuntimeFile.new"
$tmpReleaseFile = "$currentReleaseFile.new"
Set-Content -Path $tmpRuntimeFile -Value $newRuntime -Encoding ASCII
Set-Content -Path $tmpReleaseFile -Value $newRelease -Encoding ASCII
Move-Item -Path $tmpRuntimeFile -Destination $currentRuntimeFile -Force
Move-Item -Path $tmpReleaseFile -Destination $currentReleaseFile -Force
Write-Step "切换当前 Python Runtime：$newRuntime"
Write-Step "切换当前应用 Release：$newRelease"
Copy-Item "$InstallDir\windows\start-native.ps1" "$InstallDir\start-native.ps1" -Force
$serviceExe="$InstallDir\UGDCMS-App.exe"
$bundledWinSW="$InstallDir\windows\prerequisites\WinSW-x64.exe"
if(-not (Test-Path $bundledWinSW)){
  $bundledWinSW="$PSScriptRoot\prerequisites\WinSW-x64.exe"
}
if(-not (Test-Path $bundledWinSW)){
  if($SkipPrerequisiteInstall){ Fail '缺少 WinSW-x64.exe' }
  Write-Step '下载 Windows Service Wrapper...'
  try { Invoke-WebRequest 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe' -OutFile $serviceExe -UseBasicParsing }
  catch { Fail '无法下载 WinSW。请使用带离线 prerequisites 的完整安装包。' }
}else{ Copy-Item $bundledWinSW $serviceExe -Force }
$serviceXml=@"
<service>
  <id>UGDCMS-App</id>
  <name>UG-DCMS Application Service</name>
  <description>UG-DCMS 设计构型管理系统 Web/API 服务</description>
  <executable>powershell.exe</executable>
  <arguments>-NoProfile -ExecutionPolicy Bypass -File &quot;$InstallDir\start-native.ps1&quot; -Port $AppPort</arguments>
  <workingdirectory>$InstallDir</workingdirectory>
  <startmode>Automatic</startmode>
  <depend>UGDCMS-PostgreSQL</depend>
  <onfailure action="restart" delay="3 sec" />
  <onfailure action="restart" delay="10 sec" />
  <resetfailure>1 hour</resetfailure>
  <logpath>$InstallDir\logs</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
Set-Content "$InstallDir\UGDCMS-App.xml" $serviceXml -Encoding UTF8
if(Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue){
  & $serviceExe stop | Out-Null
  & $serviceExe uninstall | Out-Null
}
Write-Step '注册 UG-DCMS 应用 Windows 服务...'
& $serviceExe install
if($LASTEXITCODE -ne 0){ Fail 'UGDCMS-App 服务注册失败' }
& $serviceExe start
if($LASTEXITCODE -ne 0){ Fail 'UGDCMS-App 服务启动命令失败' }

# 防火墙：仅 Domain/Private 网络开放局域网访问。
Write-Step "配置 Windows 防火墙 TCP/$AppPort..."
Get-NetFirewallRule -DisplayName 'UG-DCMS Web Access' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'UG-DCMS Web Access' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $AppPort -Profile Domain,Private | Out-Null

# 桌面快捷方式：打开本机 Web UI。
Write-Step '创建桌面快捷方式...'
$desktop=[Environment]::GetFolderPath('CommonDesktopDirectory')
$ws=New-Object -ComObject WScript.Shell
$lnk=$ws.CreateShortcut((Join-Path $desktop 'UG-DCMS.lnk'))
$lnk.TargetPath="$env:SystemRoot\System32\cmd.exe"
$lnk.Arguments="/c start `"`" http://localhost:$AppPort"
$lnk.WorkingDirectory=$InstallDir
$lnk.Description='打开 UG-DCMS 设计构型管理系统'
$lnk.Save()

# 写卸载辅助脚本
$uninstall=@"
`$ErrorActionPreference='SilentlyContinue'
if(Test-Path '$serviceExe'){ & '$serviceExe' stop; & '$serviceExe' uninstall }
Stop-Service '$pgService' -Force
& '$pgBin\pg_ctl.exe' unregister -N '$pgService'
Get-NetFirewallRule -DisplayName 'UG-DCMS Web Access' | Remove-NetFirewallRule
Remove-Item '$desktop\UG-DCMS.lnk' -Force
Write-Host 'UG-DCMS 服务和防火墙规则已移除。数据库数据保留在：$pgData' -ForegroundColor Yellow
"@
Set-Content "$InstallDir\uninstall-services.ps1" $uninstall -Encoding UTF8

# 健康检查
Write-Step '执行安装后健康检查...'
$deadline=(Get-Date).AddSeconds(60); $healthy=$false
while((Get-Date) -lt $deadline){
  try {
    $r=Invoke-WebRequest "http://127.0.0.1:$AppPort/api/v1/health" -UseBasicParsing -TimeoutSec 3
    if($r.StatusCode -eq 200){ $healthy=$true; break }
  } catch { Start-Sleep 1 }
}
if(-not $healthy){
  $svc=Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue
  $details="服务状态=$($svc.Status)"
  foreach($name in @('UGDCMS-App.err.log','UGDCMS-App.out.log','UGDCMS-App.wrapper.log')){
    $path=Join-Path $LogDir $name
    if(Test-Path $path){
      $tail=(Get-Content $path -Tail 30 -ErrorAction SilentlyContinue) -join ' | '
      if($tail){ $details += "；$name：$tail" }
    }
  }
  Fail "UGDCMS-App 未通过 HTTP 健康检查 /api/v1/health（$details）。安装不会被标记为完成。"
}
Write-Step 'HTTP 健康检查通过'

# 健康检查成功后，才尝试清理旧 Release。清理失败仅记录警告。
if($previousRelease -and ($previousRelease -ne $newRelease) -and (Test-Path $previousRelease)){
  try {
    Write-Step "清理旧应用 Release：$previousRelease"
    Remove-Item $previousRelease -Recurse -Force -ErrorAction Stop
  } catch {
    Write-Status "旧 Release 暂时无法删除，已保留，不影响本次安装：$($_.Exception.Message)"
  }
}

# 健康检查成功后，才尝试清理旧 Runtime。清理失败仅记录警告，绝不让安装失败。
if($previousRuntime -and ($previousRuntime -ne $newRuntime) -and (Test-Path $previousRuntime)){
  try {
    Write-Step "清理旧 Python Runtime：$previousRuntime"
    Remove-Item $previousRuntime -Recurse -Force -ErrorAction Stop
  } catch {
    Write-Status "旧 Runtime 暂时无法删除，已保留，不影响本次安装：$($_.Exception.Message)"
  }
}
# 额外清理历史 rc2.9 及更早版本使用的固定 venv；文件被锁时只记录，不阻断。
$legacyVenv = Join-Path $InstallDir 'venv'
if(Test-Path $legacyVenv){
  try { Remove-Item $legacyVenv -Recurse -Force -ErrorAction Stop }
  catch { Write-Status "旧版固定 venv 暂时无法删除，已保留：$($_.Exception.Message)" }
}

# 安装状态
$status=@"
InstalledAt=$(Get-Date -Format o)
Version=1.0.0-rc2.33
AppPort=$AppPort
DatabasePort=$PgPort
AppService=UGDCMS-App
DatabaseService=UGDCMS-PostgreSQL
Runtime=$newRuntime
Release=$newRelease
URL=http://localhost:$AppPort
"@
Set-Content "$InstallDir\INSTALLATION-STATUS.txt" $status -Encoding UTF8
Write-Host "`nUG-DCMS 安装完成。" -ForegroundColor Green
Write-Host "本机：http://localhost:$AppPort" -ForegroundColor Green
Write-Host "局域网：http://本机IP:$AppPort" -ForegroundColor Green
Write-Host "安装日志：$LogFile" -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch {}
