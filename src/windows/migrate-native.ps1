param(
  [string]$InstallDir = "$env:ProgramData\UG-DCMS",
  [string]$PgBin = "C:\Program Files\PostgreSQL\16\bin",
  [string]$PgHost = "127.0.0.1", [int]$PgPort = 5432,
  [string]$PgUser = "dcms", [string]$PgDatabase = "dcms"
)
$ErrorActionPreference="Stop"
$psql=Join-Path $PgBin "psql.exe"
if(-not (Test-Path $psql)){ throw "psql.exe not found: $psql" }

# Migration files are UTF-8. Chinese Windows commonly defaults psql client encoding
# to GBK, which corrupts UTF-8 SQL files unless explicitly overridden.
$env:PGHOST=$PgHost
$env:PGPORT="$PgPort"
$env:PGUSER=$PgUser
$env:PGDATABASE=$PgDatabase
$env:PGCLIENTENCODING='UTF8'

function Invoke-Psql {
  param([string[]]$Args,[string]$Step='psql')
  & $psql @Args
  $code=$LASTEXITCODE
  if($code -ne 0){ throw "$Step failed with exit code $code" }
}

Invoke-Psql @('-X','-v','ON_ERROR_STOP=1','-q','-c',"CREATE TABLE IF NOT EXISTS schema_migration (version text PRIMARY KEY, filename text NOT NULL, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now(), applied_by text NOT NULL DEFAULT current_user, duration_ms integer);") 'create schema_migration'

Get-ChildItem "$InstallDir\db\migrations\*.sql" | Sort-Object Name | ForEach-Object {
  $v=$_.BaseName
  $exists=& $psql -X -v ON_ERROR_STOP=1 -qtAX -c "SELECT EXISTS(SELECT 1 FROM schema_migration WHERE version='$v');"
  if($LASTEXITCODE -ne 0){ throw "query migration state failed for $v" }
  if ($exists.Trim() -eq "t") { Write-Host "Skip $v"; return }

  Write-Host "Apply $v ..."
  $hash=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
  $sw=[Diagnostics.Stopwatch]::StartNew()
  # -1 wraps each migration file in one transaction; ON_ERROR_STOP prevents partial apply.
  & $psql -X -v ON_ERROR_STOP=1 -1 -q -f $_.FullName
  $code=$LASTEXITCODE
  $sw.Stop()
  if($code -ne 0){ throw "Migration $v failed with exit code $code" }

  $insert="INSERT INTO schema_migration(version,filename,sha256,duration_ms) VALUES ('$v','$($_.Name)','$hash',$($sw.ElapsedMilliseconds));"
  Invoke-Psql @('-X','-v','ON_ERROR_STOP=1','-q','-c',$insert) "record migration $v"
}

Write-Host 'All database migrations completed successfully.' -ForegroundColor Green
