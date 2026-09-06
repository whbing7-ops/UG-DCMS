param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$build = Join-Path $SourceRoot 'installer\Build-Setup.ps1'
$t = Get-Content $build -Raw -Encoding UTF8
$oldBuild = '(Get-Content $MyInvocation.MyCommand.Path -Raw -Encoding UTF8)'
if($t.Contains($oldBuild)){
  $t = $t.Replace($oldBuild,'(Get-Content $PSCommandPath -Raw -Encoding UTF8)')
  Set-Content $build $t -Encoding UTF8
}

$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$p = Get-Content $provision -Raw -Encoding UTF8

# Keep transcript output separate from install.log so Write-Status can always append.
$oldTranscript = 'try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}'
if($p.Contains($oldTranscript)){
  $newTranscript = '$TranscriptFile = Join-Path $LogDir ''powershell-transcript.log''' + "`r`n" + 'try { Start-Transcript -Path $TranscriptFile -Append -Force | Out-Null } catch {}'
  $p = $p.Replace($oldTranscript,$newTranscript)
}

# Windows PowerShell 5.1 can expose ExitCode late after a timed WaitForExit.
# Always perform a final WaitForExit/Refresh before reading it.
if(-not $p.Contains('$exitCode = [int]$proc.ExitCode')){
  $processPattern = '^(?<indent>[^\S\r\n]*)if\s*\(\s*\$proc\.ExitCode\s*-ne\s*0\s*\)\s*\{\s*throw\s*"[^"\r\n]*"\s*\}[^\S\r\n]*$'
  $m = [regex]::Match($p,$processPattern,[System.Text.RegularExpressions.RegexOptions]::Multiline)
  if(-not $m.Success){ throw 'Process exit-code block not found' }
  $indent = $m.Groups['indent'].Value
  $lines = @(
    '$proc.WaitForExit()',
    '$proc.Refresh()',
    'if(-not $proc.HasExited){ throw (''Process did not exit cleanly for step: '' + $Step) }',
    '$exitCode = [int]$proc.ExitCode',
    'if($exitCode -ne 0){ throw (''Step failed: '' + $Step + ''; exit code: '' + [string]$exitCode) }'
  )
  $replacement = ($lines | ForEach-Object { $indent + $_ }) -join "`r`n"
  $p = $p.Substring(0,$m.Index) + $replacement + $p.Substring($m.Index + $m.Length)
}
Set-Content $provision $p -Encoding UTF8

# Do not keep patching the tiny migration script with fragile text replacements.
# Replace it deterministically with a bounded, non-interactive implementation.
$migrate = Join-Path $SourceRoot 'windows\migrate-native.ps1'
$hardenedMigrator = @'
param(
  [string]$InstallDir = "$env:ProgramData\UG-DCMS",
  [string]$PgBin = "C:\Program Files\PostgreSQL\16\bin",
  [string]$PgHost = "127.0.0.1", [int]$PgPort = 5432,
  [string]$PgUser = "dcms", [string]$PgDatabase = "dcms"
)
$ErrorActionPreference='Stop'
$psql = Join-Path $PgBin 'psql.exe'
if(-not (Test-Path $psql)){ throw "psql.exe not found: $psql" }

$env:PGHOST=$PgHost
$env:PGPORT=[string]$PgPort
$env:PGUSER=$PgUser
$env:PGDATABASE=$PgDatabase
$env:PGCLIENTENCODING='UTF8'
$env:PGCONNECT_TIMEOUT='5'
$env:PGOPTIONS='-c statement_timeout=25000 -c lock_timeout=10000'

$LogDir = Join-Path $env:ProgramData 'UG-DCMS\logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$MigrationLog = Join-Path $LogDir 'migration.log'
$WorkDir = Join-Path $LogDir 'migration-work'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

function Write-MigrationLog([string]$Message){
  $line='[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] ' + $Message
  Write-Host $line
  Add-Content -Path $MigrationLog -Value $line -Encoding UTF8
}
function Write-Utf8NoBom([string]$Path,[string]$Text){
  [IO.File]::WriteAllText($Path,$Text,(New-Object Text.UTF8Encoding($false)))
}
function Read-Text([string]$Path){
  if(Test-Path $Path){ return (Get-Content $Path -Raw -ErrorAction SilentlyContinue) }
  return ''
}
function Invoke-PsqlFile {
  param(
    [string]$SqlFile,
    [string]$Step,
    [int]$TimeoutSeconds=30,
    [switch]$SingleTransaction,
    [switch]$TupleOnly
  )
  $safe = ($Step -replace '[^A-Za-z0-9_.-]','_')
  $outFile = Join-Path $WorkDir ($safe + '.out.txt')
  $errFile = Join-Path $WorkDir ($safe + '.err.txt')
  Remove-Item $outFile,$errFile -Force -ErrorAction SilentlyContinue
  $argList=@('-w','-X','-v','ON_ERROR_STOP=1','-q')
  if($SingleTransaction){ $argList += '-1' }
  if($TupleOnly){ $argList += @('-t','-A') }
  $argList += @('-f',$SqlFile)

  Write-MigrationLog ('PSQL START step=' + $Step + ' timeout=' + $TimeoutSeconds + 's file=' + $SqlFile)
  $proc = Start-Process -FilePath $psql -ArgumentList $argList -PassThru -NoNewWindow -RedirectStandardOutput $outFile -RedirectStandardError $errFile
  if(-not $proc.WaitForExit($TimeoutSeconds * 1000)){
    Write-MigrationLog ('PSQL TIMEOUT step=' + $Step + ' pid=' + $proc.Id)
    try { taskkill /PID $proc.Id /T /F | Out-Null } catch {}
    throw ('psql timeout for step: ' + $Step)
  }
  $proc.WaitForExit()
  $proc.Refresh()
  if(-not $proc.HasExited){ throw ('psql did not exit cleanly for step: ' + $Step) }
  $code=[int]$proc.ExitCode
  $stdout=(Read-Text $outFile).Trim()
  $stderr=(Read-Text $errFile).Trim()
  Write-MigrationLog ('PSQL EXIT step=' + $Step + ' code=' + $code)
  if($stdout){ Write-MigrationLog ('STDOUT step=' + $Step + ' ' + $stdout) }
  if($stderr){ Write-MigrationLog ('STDERR step=' + $Step + ' ' + $stderr) }
  if($code -ne 0){ throw ('psql failed for step ' + $Step + ' with exit code ' + $code) }
  return $stdout
}

Write-MigrationLog ('MIGRATOR START pid=' + $PID + ' installDir=' + $InstallDir + ' pg=' + $PgHost + ':' + $PgPort + ' db=' + $PgDatabase)

$createSql = Join-Path $WorkDir 'create_schema_migration.sql'
Write-Utf8NoBom $createSql "CREATE TABLE IF NOT EXISTS schema_migration (version text PRIMARY KEY, filename text NOT NULL, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now(), applied_by text NOT NULL DEFAULT current_user, duration_ms integer);"
Invoke-PsqlFile -SqlFile $createSql -Step 'create_schema_migration' -TimeoutSeconds 15 | Out-Null

$migrations = @(Get-ChildItem (Join-Path $InstallDir 'db\migrations\*.sql') | Sort-Object Name)
if($migrations.Count -ne 12){ throw ('Expected 12 migration files, found ' + $migrations.Count) }

foreach($file in $migrations){
  $v=$file.BaseName
  $escapedV=$v.Replace("'","''")
  $stateSql=Join-Path $WorkDir ('state_' + $v + '.sql')
  Write-Utf8NoBom $stateSql ("SELECT EXISTS(SELECT 1 FROM schema_migration WHERE version='" + $escapedV + "');")
  $state=(Invoke-PsqlFile -SqlFile $stateSql -Step ('state_' + $v) -TimeoutSeconds 15 -TupleOnly).Trim()
  if($state -eq 't'){
    Write-MigrationLog ('SKIP ' + $v)
    continue
  }
  if($state -ne 'f'){ throw ('Unexpected migration state for ' + $v + ': ' + $state) }

  Write-MigrationLog ('MIGRATION START ' + $v + ' file=' + $file.Name)
  $hash=(Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  $sw=[Diagnostics.Stopwatch]::StartNew()
  Invoke-PsqlFile -SqlFile $file.FullName -Step ('apply_' + $v) -TimeoutSeconds 30 -SingleTransaction | Out-Null
  $sw.Stop()

  $escapedName=$file.Name.Replace("'","''")
  $recordSql=Join-Path $WorkDir ('record_' + $v + '.sql')
  $insert="INSERT INTO schema_migration(version,filename,sha256,duration_ms) VALUES ('$escapedV','$escapedName','$hash',$($sw.ElapsedMilliseconds));"
  Write-Utf8NoBom $recordSql $insert
  Invoke-PsqlFile -SqlFile $recordSql -Step ('record_' + $v) -TimeoutSeconds 15 | Out-Null
  Write-MigrationLog ('PASS ' + $v + ' ms=' + $sw.ElapsedMilliseconds)
}

$countSql=Join-Path $WorkDir 'verify_count.sql'
Write-Utf8NoBom $countSql 'SELECT count(*) FROM schema_migration;'
$countText=(Invoke-PsqlFile -SqlFile $countSql -Step 'verify_count' -TimeoutSeconds 15 -TupleOnly).Trim()
if([int]$countText -ne 12){ throw ('Migration count mismatch. Expected 12, actual ' + $countText) }
Write-MigrationLog 'ALL MIGRATIONS PASS 12/12'
Write-Host 'All database migrations completed successfully.' -ForegroundColor Green
'@
Set-Content -Path $migrate -Value $hardenedMigrator -Encoding UTF8

# Parse every modified PowerShell file before the expensive Windows build/install path.
foreach($file in @($provision,$migrate)){
  $tokens=$null
  $parseErrors=$null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file).Path,[ref]$tokens,[ref]$parseErrors) | Out-Null
  if($parseErrors.Count -gt 0){
    $sourceLines=Get-Content $file
    foreach($e in $parseErrors){
      Write-Host ($e.Message + ' at line ' + $e.Extent.StartLineNumber + ', column ' + $e.Extent.StartColumnNumber)
      $n=$e.Extent.StartLineNumber
      if($n -ge 1 -and $n -le $sourceLines.Count){ Write-Host ('SOURCE: ' + $sourceLines[$n-1]) }
    }
    throw ('PowerShell parse failure: ' + $file)
  }
}

$check=Get-Content $provision -Raw -Encoding UTF8
$mgCheck=Get-Content $migrate -Raw -Encoding UTF8
if($check.Contains('Start-Transcript -Path $LogFile')){ throw 'Transcript still locks install.log' }
if(-not $check.Contains('$proc.WaitForExit()')){ throw 'Final WaitForExit patch missing' }
if(-not $check.Contains('$exitCode = [int]$proc.ExitCode')){ throw 'Typed exit-code patch missing' }
if(-not $check.Contains('-InstallDir $newRelease')){ throw 'Migration invocation must use new release' }
if(-not $mgCheck.Contains("PGCONNECT_TIMEOUT='5'")){ throw 'psql connect timeout missing' }
if(-not $mgCheck.Contains('statement_timeout=25000')){ throw 'psql statement timeout missing' }
if(-not $mgCheck.Contains("'-w'")){ throw 'psql noninteractive mode missing' }
if(-not $mgCheck.Contains('PSQL TIMEOUT')){ throw 'per-psql hard timeout missing' }
if(-not $mgCheck.Contains('ALL MIGRATIONS PASS 12/12')){ throw 'migration completion gate missing' }
Write-Host 'Installer reliability patch PASS: deterministic bounded migrator installed and syntax validated.'
