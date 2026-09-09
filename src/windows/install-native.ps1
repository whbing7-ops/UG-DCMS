param(
  [string]$InstallDir = "$env:ProgramData\UG-DCMS",
  [string]$PgBin = "C:\Program Files\PostgreSQL\16\bin",
  [string]$PgHost = "127.0.0.1",
  [int]$PgPort = 5432,
  [string]$PgUser = "dcms",
  [string]$PgDatabase = "dcms"
)
$ErrorActionPreference = "Stop"
Write-Host "UG-DCMS Windows Native Setup" -ForegroundColor Cyan
if (-not (Get-Command python.exe -ErrorAction SilentlyContinue)) { throw "未找到 Python 3.11+。请安装 64 位 Python 并勾选 Add to PATH。" }
$pyv = python -c "import sys;print(sys.version_info[:2] >= (3,11))"
if ($pyv -ne "True") { throw "需要 Python 3.11 或更高版本。" }
$psql = Join-Path $PgBin "psql.exe"
if (-not (Test-Path $psql)) { throw "未找到 PostgreSQL 16 psql.exe：$psql" }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$root = Split-Path -Parent $PSScriptRoot
Copy-Item "$root\backend" "$InstallDir\backend" -Recurse -Force
Copy-Item "$root\frontend" "$InstallDir\frontend" -Recurse -Force
Copy-Item "$root\db" "$InstallDir\db" -Recurse -Force
python -m venv "$InstallDir\venv"
& "$InstallDir\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$InstallDir\venv\Scripts\pip.exe" install -r "$InstallDir\backend\requirements.txt"
$pw = Read-Host "请输入 PostgreSQL 用户 $PgUser 的密码" -AsSecureString
$bstr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw); $plain=[Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
$env:PGPASSWORD=$plain; $env:PGHOST=$PgHost; $env:PGPORT="$PgPort"; $env:PGUSER=$PgUser; $env:PGDATABASE=$PgDatabase
& "$PSScriptRoot\migrate-native.ps1" -InstallDir $InstallDir -PgBin $PgBin -PgHost $PgHost -PgPort $PgPort -PgUser $PgUser -PgDatabase $PgDatabase
$envFile = @"
DCMS_PG_HOST=$PgHost
DCMS_PG_PORT=$PgPort
DCMS_PG_USER=$PgUser
DCMS_PG_PASSWORD=$plain
DCMS_PG_DATABASE=$PgDatabase
DCMS_STORAGE_ROOT=$InstallDir\data\files
DCMS_FRONTEND_ROOT=$InstallDir\frontend
DCMS_ENVIRONMENT=PROD
"@
$envFile | Set-Content -Encoding UTF8 "$InstallDir\.env"
New-Item -ItemType Directory -Force -Path "$InstallDir\data\files" | Out-Null
Copy-Item "$PSScriptRoot\start-native.ps1" "$InstallDir\start-native.ps1" -Force
Write-Host "安装完成。运行 $InstallDir\start-native.ps1 后访问 http://本机IP:8080" -ForegroundColor Green
