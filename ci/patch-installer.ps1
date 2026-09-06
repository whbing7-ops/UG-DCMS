param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

function Replace-RegexOnce {
  param(
    [string]$Text,
    [string]$Pattern,
    [string]$Replacement,
    [string]$Label
  )
  $rx = [regex]::new($Pattern,[System.Text.RegularExpressions.RegexOptions]::Multiline)
  $matches = $rx.Matches($Text)
  if($matches.Count -ne 1){ throw ($Label + ' match count was ' + $matches.Count + ', expected 1') }
  return $rx.Replace($Text,$Replacement,1)
}

$build = Join-Path $SourceRoot 'installer\Build-Setup.ps1'
$t = Get-Content $build -Raw -Encoding UTF8
$oldBuild = '(Get-Content $MyInvocation.MyCommand.Path -Raw -Encoding UTF8)'
if($t.Contains($oldBuild)){
  $t = $t.Replace($oldBuild,'(Get-Content $PSCommandPath -Raw -Encoding UTF8)')
  Set-Content $build $t -Encoding UTF8
}

$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$p = Get-Content $provision -Raw -Encoding UTF8

# Keep transcript output separate from the application install log.
$oldTranscript = 'try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}'
if($p.Contains($oldTranscript)){
  $newTranscript = '$TranscriptFile = Join-Path $LogDir ''powershell-transcript.log''' + "`r`n" + 'try { Start-Transcript -Path $TranscriptFile -Append -Force | Out-Null } catch {}'
  $p = $p.Replace($oldTranscript,$newTranscript)
}

# Normalize external-process completion for Windows PowerShell 5.1.
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

# Wrap the migration script in the same bounded external-process helper.
if(-not $p.Contains("-Step 'Database migrations'")){
  $migrationPattern = '^(?<indent>[^\S\r\n]*)& powershell\.exe -NoProfile -ExecutionPolicy Bypass -File \$migrateScript -InstallDir \$newRelease -PgBin \$pgBin -PgHost ''127\.0\.0\.1'' -PgPort \$PgPort -PgUser ''dcms'' -PgDatabase ''dcms''\r?\n[^\S\r\n]*\$migrateExit = \$LASTEXITCODE\r?\n[^\S\r\n]*if\(\$migrateExit -ne 0\)\{ Fail .*? \}$'
  $mm = [regex]::Match($p,$migrationPattern,[System.Text.RegularExpressions.RegexOptions]::Multiline)
  if(-not $mm.Success){
    $near = ($p -split "`r?`n" | Where-Object { $_ -match 'migrateScript|migrateExit|migrate-native' }) -join "`n"
    Write-Host $near
    throw 'Migration invocation block not found'
  }
  $indent = $mm.Groups['indent'].Value
  $migrationLines = @(
    '$migrateArgs = @(''-NoProfile'',''-ExecutionPolicy'',''Bypass'',''-File'',$migrateScript,''-InstallDir'',$newRelease,''-PgBin'',$pgBin,''-PgHost'',''127.0.0.1'',''-PgPort'',[string]$PgPort,''-PgUser'',''dcms'',''-PgDatabase'',''dcms'')',
    'Invoke-ProcessWithTimeout -FilePath ''powershell.exe'' -ArgumentList $migrateArgs -TimeoutSeconds 240 -Step ''Database migrations'''
  )
  $migrationReplacement = ($migrationLines | ForEach-Object { $indent + $_ }) -join "`r`n"
  $p = $p.Substring(0,$mm.Index) + $migrationReplacement + $p.Substring($mm.Index + $mm.Length)
}
Set-Content $provision $p -Encoding UTF8

# Harden psql: noninteractive authentication plus bounded connection/statement/lock waits.
$migrate = Join-Path $SourceRoot 'windows\migrate-native.ps1'
$mg = Get-Content $migrate -Raw -Encoding UTF8
$anchor = '$env:PGCLIENTENCODING=''UTF8'''
if(-not $mg.Contains('$env:PGCONNECT_TIMEOUT=''5''')){
  if(-not $mg.Contains($anchor)){ throw 'Migration environment anchor not found' }
  $extra = $anchor + "`r`n" + '$env:PGCONNECT_TIMEOUT=''5''' + "`r`n" + '$env:PGOPTIONS=''-c statement_timeout=120000 -c lock_timeout=10000'''
  $mg = $mg.Replace($anchor,$extra)
}
$mg = $mg.Replace('& $psql @Args','& $psql -w @Args')
$mg = $mg.Replace('& $psql -X -v ON_ERROR_STOP=1 -qtAX -c','& $psql -w -X -v ON_ERROR_STOP=1 -qtAX -c')
$mg = $mg.Replace('& $psql -X -v ON_ERROR_STOP=1 -1 -q -f','& $psql -w -X -v ON_ERROR_STOP=1 -1 -q -f')
Set-Content $migrate $mg -Encoding UTF8

# The audit must recognize both the original direct invocation and the bounded argument-array invocation.
$verify = Join-Path $SourceRoot 'installer\verify_installer_source.py'
$v = Get-Content $verify -Raw -Encoding UTF8
$oldAudit = "    'migration runs from new release': '-InstallDir `$newRelease' in ps,"
$newAudit = "    'migration runs from new release': ('-InstallDir `$newRelease' in ps or \"'-InstallDir',`$newRelease\" in ps),"
if($v.Contains($oldAudit)){
  $v = $v.Replace($oldAudit,$newAudit)
  Set-Content $verify $v -Encoding UTF8
}

# Parse all modified PowerShell files before spending time on prerequisites/build/install.
foreach($file in @($provision,$migrate)){
  $tokens = $null
  $parseErrors = $null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file).Path,[ref]$tokens,[ref]$parseErrors) | Out-Null
  if($parseErrors.Count -gt 0){
    $lines = Get-Content $file
    foreach($e in $parseErrors){
      Write-Host ($e.Message + ' at line ' + $e.Extent.StartLineNumber + ', column ' + $e.Extent.StartColumnNumber)
      $n = $e.Extent.StartLineNumber
      if($n -ge 1 -and $n -le $lines.Count){ Write-Host ('SOURCE: ' + $lines[$n-1]) }
    }
    throw ('PowerShell parse failure: ' + $file)
  }
}

$check = Get-Content $provision -Raw -Encoding UTF8
$mgCheck = Get-Content $migrate -Raw -Encoding UTF8
$vCheck = Get-Content $verify -Raw -Encoding UTF8
if($check.Contains('Start-Transcript -Path $LogFile')){ throw 'Transcript still locks install.log' }
if(-not $check.Contains('$proc.WaitForExit()')){ throw 'Final WaitForExit patch missing' }
if(-not $check.Contains('$exitCode = [int]$proc.ExitCode')){ throw 'Typed exit-code patch missing' }
if(-not $check.Contains("-Step 'Database migrations'")){ throw 'Migration timeout wrapper missing' }
if(-not $mgCheck.Contains('$env:PGCONNECT_TIMEOUT=''5''')){ throw 'psql connect timeout missing' }
if(-not $mgCheck.Contains('$env:PGOPTIONS=''-c statement_timeout=120000 -c lock_timeout=10000''')){ throw 'psql statement/lock timeout missing' }
if(-not $mgCheck.Contains('& $psql -w')){ throw 'psql noninteractive mode missing' }
if(-not $vCheck.Contains("\"'-InstallDir',`$newRelease\" in ps")){ throw 'Audit compatibility patch missing' }
Write-Host 'Installer reliability patches applied and syntax validated.'
