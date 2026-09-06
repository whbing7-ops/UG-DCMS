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

$oldTranscript = 'try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}'
if($p.Contains($oldTranscript)){
  $newTranscript = '$TranscriptFile = Join-Path $LogDir ''powershell-transcript.log''' + "`r`n" + 'try { Start-Transcript -Path $TranscriptFile -Append -Force | Out-Null } catch {}'
  $p = $p.Replace($oldTranscript,$newTranscript)
}

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

$migrate = Join-Path $SourceRoot 'windows\migrate-native.ps1'
$mg = Get-Content $migrate -Raw -Encoding UTF8

$anchor = '$env:PGCLIENTENCODING=''UTF8'''
if(-not $mg.Contains('$env:PGCONNECT_TIMEOUT=''5''')){
  if(-not $mg.Contains($anchor)){ throw 'Migration environment anchor not found' }
  $extra = $anchor + "`r`n" +
    '$env:PGCONNECT_TIMEOUT=''5''' + "`r`n" +
    '$env:PGOPTIONS=''-c statement_timeout=120000 -c lock_timeout=10000''' + "`r`n" +
    '$MigrationLog = Join-Path $env:ProgramData ''UG-DCMS\logs\migration.log''' + "`r`n" +
    'function Write-MigrationLog { param([string]$Message) $line = ''['' + (Get-Date -Format ''yyyy-MM-dd HH:mm:ss'') + ''] '' + $Message; Write-Host $line; Add-Content -Path $MigrationLog -Value $line -Encoding UTF8 }'
  $mg = $mg.Replace($anchor,$extra)
}

# Instrument the original call sites first, then harden any remaining psql calls.
$oldState = @'
  $exists=& $psql -X -v ON_ERROR_STOP=1 -qtAX -c "SELECT EXISTS(SELECT 1 FROM schema_migration WHERE version='$v');"
  if($LASTEXITCODE -ne 0){ throw "query migration state failed for $v" }
  if ($exists.Trim() -eq "t") { Write-Host "Skip $v"; return }
'@
$newState = @'
  Write-MigrationLog ("STATE " + $v + " START")
  $stateOutput = @(& $psql -w -X -v ON_ERROR_STOP=1 -qtAX -c "SELECT EXISTS(SELECT 1 FROM schema_migration WHERE version='$v');" 2>&1)
  $stateCode = $LASTEXITCODE
  $stateText = (($stateOutput | ForEach-Object { [string]$_ }) -join "`n").Trim()
  Write-MigrationLog ("STATE " + $v + " EXIT=" + $stateCode + " OUTPUT=" + $stateText)
  if($stateCode -ne 0){ throw "query migration state failed for $v with exit code $stateCode" }
  if($stateText -eq "t"){ Write-MigrationLog ("SKIP " + $v); return }
  if($stateText -ne "f"){ throw ("unexpected migration state output for " + $v + ": '" + $stateText + "'") }
'@
if($mg.Contains($oldState)){
  $mg = $mg.Replace($oldState,$newState)
}
elseif(-not $mg.Contains('STATE " + $v + " START')){
  throw 'Migration state block not found'
}

$oldApply = @'
  Write-Host "Apply $v ..."
  $hash=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
  $sw=[Diagnostics.Stopwatch]::StartNew()
  # -1 wraps each migration file in one transaction; ON_ERROR_STOP prevents partial apply.
  & $psql -X -v ON_ERROR_STOP=1 -1 -q -f $_.FullName
  $code=$LASTEXITCODE
  $sw.Stop()
  if($code -ne 0){ throw "Migration $v failed with exit code $code" }
'@
$newApply = @'
  Write-MigrationLog ("START " + $v + " FILE=" + $_.Name)
  $hash=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
  $sw=[Diagnostics.Stopwatch]::StartNew()
  # -1 wraps each migration file in one transaction; ON_ERROR_STOP prevents partial apply.
  $applyOutput = @(& $psql -w -X -v ON_ERROR_STOP=1 -1 -q -f $_.FullName 2>&1)
  $code=$LASTEXITCODE
  $sw.Stop()
  if($applyOutput.Count -gt 0){
    $applyText = (($applyOutput | ForEach-Object { [string]$_ }) -join "`n")
    Write-MigrationLog ("OUTPUT " + $v + " " + $applyText)
  }
  Write-MigrationLog ("EXIT " + $v + " CODE=" + $code + " MS=" + $sw.ElapsedMilliseconds)
  if($code -ne 0){ throw "Migration $v failed with exit code $code" }
'@
if($mg.Contains($oldApply)){
  $mg = $mg.Replace($oldApply,$newApply)
}
elseif(-not $mg.Contains('START " + $v + " FILE=')){
  throw 'Migration apply block not found'
}

$oldRecord = '  Invoke-Psql @(''-X'',''-v'',''ON_ERROR_STOP=1'',''-q'',''-c'',$insert) "record migration $v"'
$newRecord = $oldRecord + "`r`n" + '  Write-MigrationLog ("PASS " + $v + " MS=" + $sw.ElapsedMilliseconds)'
if($mg.Contains($oldRecord) -and -not $mg.Contains('PASS " + $v + " MS=')){
  $mg = $mg.Replace($oldRecord,$newRecord)
}

$oldDone = "Write-Host 'All database migrations completed successfully.' -ForegroundColor Green"
$newDone = "Write-MigrationLog 'ALL MIGRATIONS PASS'`r`n" + $oldDone
if($mg.Contains($oldDone) -and -not $mg.Contains('ALL MIGRATIONS PASS')){
  $mg = $mg.Replace($oldDone,$newDone)
}

# Harden all remaining psql calls after instrumentation.
$mg = $mg.Replace('& $psql @Args','& $psql -w @Args')
$mg = $mg.Replace('& $psql -X -v ON_ERROR_STOP=1 -qtAX -c','& $psql -w -X -v ON_ERROR_STOP=1 -qtAX -c')
$mg = $mg.Replace('& $psql -X -v ON_ERROR_STOP=1 -1 -q -f','& $psql -w -X -v ON_ERROR_STOP=1 -1 -q -f')
Set-Content $migrate $mg -Encoding UTF8

foreach($file in @($provision,$migrate)){
  $tokens = $null
  $parseErrors = $null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file).Path,[ref]$tokens,[ref]$parseErrors) | Out-Null
  if($parseErrors.Count -gt 0){
    $sourceLines = Get-Content $file
    foreach($e in $parseErrors){
      Write-Host ($e.Message + ' at line ' + $e.Extent.StartLineNumber + ', column ' + $e.Extent.StartColumnNumber)
      $n = $e.Extent.StartLineNumber
      if($n -ge 1 -and $n -le $sourceLines.Count){ Write-Host ('SOURCE: ' + $sourceLines[$n-1]) }
    }
    throw ('PowerShell parse failure: ' + $file)
  }
}

$check = Get-Content $provision -Raw -Encoding UTF8
$mgCheck = Get-Content $migrate -Raw -Encoding UTF8
if($check.Contains('Start-Transcript -Path $LogFile')){ throw 'Transcript still locks install.log' }
if(-not $check.Contains('$proc.WaitForExit()')){ throw 'Final WaitForExit patch missing' }
if(-not $check.Contains('$exitCode = [int]$proc.ExitCode')){ throw 'Typed exit-code patch missing' }
if(-not $check.Contains('-InstallDir $newRelease')){ throw 'Direct migration invocation must use the new release' }
if(-not $mgCheck.Contains('$env:PGCONNECT_TIMEOUT=''5''')){ throw 'psql connect timeout missing' }
if(-not $mgCheck.Contains('$env:PGOPTIONS=''-c statement_timeout=120000 -c lock_timeout=10000''')){ throw 'psql statement/lock timeout missing' }
if(-not $mgCheck.Contains('& $psql -w')){ throw 'psql noninteractive mode missing' }
if(-not $mgCheck.Contains('ALL MIGRATIONS PASS')){ throw 'migration instrumentation missing' }
if(-not $mgCheck.Contains('$stateText')){ throw 'null-safe migration state query missing' }
Write-Host 'Installer reliability patches applied, migrations instrumented, and syntax validated.'
