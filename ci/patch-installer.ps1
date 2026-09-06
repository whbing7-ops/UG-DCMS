param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$build = Join-Path $SourceRoot 'installer\Build-Setup.ps1'
$t = Get-Content $build -Raw -Encoding UTF8
$old = '(Get-Content $MyInvocation.MyCommand.Path -Raw -Encoding UTF8)'
$new = '(Get-Content $PSCommandPath -Raw -Encoding UTF8)'
if($t.Contains($old)){
  $t = $t.Replace($old,$new)
  Set-Content $build $t -Encoding UTF8
}

$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$p = Get-Content $provision -Raw -Encoding UTF8

# Keep the transcript on its own file so install.log can be written independently.
$oldTranscript = 'try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}'
$newTranscript = '$TranscriptFile = Join-Path $LogDir ''powershell-transcript.log''' + "`r`n" + 'try { Start-Transcript -Path $TranscriptFile -Append -Force | Out-Null } catch {}'
if($p.Contains($oldTranscript)){
  $p = $p.Replace($oldTranscript,$newTranscript)
}

# Windows PowerShell 5.1 may not populate ExitCode after the timed WaitForExit overload
# until the parameterless WaitForExit is called. Normalize this once in the helper.
$pattern = '(?m)^(?<indent>[^\S\r\n]*)if\s*\(\s*\$proc\.ExitCode\s*-ne\s*0\s*\)\s*\{\s*throw\s*"[^"\r\n]*"\s*\}[^\S\r\n]*$'
$m = [regex]::Match($p,$pattern)
if($m.Success){
  $indent = $m.Groups['indent'].Value
  $replacement = @'
$proc.WaitForExit()
$proc.Refresh()
if(-not $proc.HasExited){ throw ('Process did not exit cleanly for step: ' + $Step) }
$exitCode = [int]$proc.ExitCode
if($exitCode -ne 0){ throw ('Step failed: ' + $Step + '; exit code: ' + [string]$exitCode) }
'@
  $replacement = (($replacement -split "`r?`n") | ForEach-Object { $indent + $_ }) -join "`r`n"
  $p = $p.Substring(0,$m.Index) + $replacement + $p.Substring($m.Index + $m.Length)
}
elseif(-not $p.Contains('$exitCode = [int]$proc.ExitCode')){
  throw 'Process exit-code handling was not recognized'
}

# Bound the migration child process. A migration must never be allowed to hang the whole installer.
$oldMigration = @'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $migrateScript -InstallDir $newRelease -PgBin $pgBin -PgHost '127.0.0.1' -PgPort $PgPort -PgUser 'dcms' -PgDatabase 'dcms'
$migrateExit = $LASTEXITCODE
if($migrateExit -ne 0){ Fail "数据库迁移失败（退出码 $migrateExit）。请查看 $LogFile" }
'@
$newMigration = @'
$migrateArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$migrateScript,'-InstallDir',$newRelease,'-PgBin',$pgBin,'-PgHost','127.0.0.1','-PgPort',[string]$PgPort,'-PgUser','dcms','-PgDatabase','dcms')
Invoke-ProcessWithTimeout -FilePath 'powershell.exe' -ArgumentList $migrateArgs -TimeoutSeconds 240 -Step 'Database migrations'
'@
if($p.Contains($oldMigration)){
  $p = $p.Replace($oldMigration,$newMigration)
}
elseif(-not $p.Contains("-Step 'Database migrations'")){
  throw 'Migration invocation block not found'
}

Set-Content $provision $p -Encoding UTF8

# Harden psql itself: never prompt for a password and never wait forever on a connection or lock.
$migrate = Join-Path $SourceRoot 'windows\migrate-native.ps1'
$mg = Get-Content $migrate -Raw -Encoding UTF8
if(-not $mg.Contains("$env:PGCONNECT_TIMEOUT='5'")){
  $anchor = "$env:PGCLIENTENCODING='UTF8'"
  $extra = $anchor + "`r`n" + "$env:PGCONNECT_TIMEOUT='5'" + "`r`n" + "$env:PGOPTIONS='-c statement_timeout=120000 -c lock_timeout=10000'"
  if(-not $mg.Contains($anchor)){ throw 'Migration environment anchor not found' }
  $mg = $mg.Replace($anchor,$extra)
}
# -w means never prompt for a password; fail fast if credentials are not inherited.
$mg = $mg.Replace("& $psql @Args", "& $psql -w @Args")
$mg = $mg.Replace("& $psql -X -v ON_ERROR_STOP=1 -qtAX -c", "& $psql -w -X -v ON_ERROR_STOP=1 -qtAX -c")
$mg = $mg.Replace("& $psql -X -v ON_ERROR_STOP=1 -1 -q -f", "& $psql -w -X -v ON_ERROR_STOP=1 -1 -q -f")
Set-Content $migrate $mg -Encoding UTF8

# Parse both scripts before the expensive build/install path.
foreach($file in @($provision,$migrate)){
  $tokens = $null
  $parseErrors = $null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file).Path,[ref]$tokens,[ref]$parseErrors) | Out-Null
  if($parseErrors.Count -gt 0){
    foreach($e in $parseErrors){ Write-Host ($e.Message + ' at line ' + $e.Extent.StartLineNumber) }
    throw ('PowerShell parse failure: ' + $file)
  }
}

$check = Get-Content $provision -Raw -Encoding UTF8
$mgCheck = Get-Content $migrate -Raw -Encoding UTF8
if($check.Contains('Start-Transcript -Path $LogFile')){ throw 'Transcript still locks install.log' }
if(-not $check.Contains('$proc.WaitForExit()')){ throw 'Final WaitForExit patch missing' }
if(-not $check.Contains('$exitCode = [int]$proc.ExitCode')){ throw 'Typed exit-code patch missing' }
if(-not $check.Contains("-Step 'Database migrations'")){ throw 'Migration timeout wrapper missing' }
if(-not $mgCheck.Contains("PGCONNECT_TIMEOUT='5'")){ throw 'psql connect timeout missing' }
if(-not $mgCheck.Contains('psql -w')){ throw 'psql noninteractive mode missing' }
Write-Host 'Installer reliability patches applied and syntax validated.'
