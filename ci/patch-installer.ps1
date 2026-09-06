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

# Keep transcript output separate from the application install log.
$oldTranscript = 'try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}'
if($p.Contains($oldTranscript)){
  $newTranscript = '$TranscriptFile = Join-Path $LogDir ''powershell-transcript.log''' + "`r`n" + 'try { Start-Transcript -Path $TranscriptFile -Append -Force | Out-Null } catch {}'
  $p = $p.Replace($oldTranscript,$newTranscript)
}

# Normalize external-process completion for Windows PowerShell 5.1.
# This helper is used for venv/pip/import checks, where it is already proven on the Windows runner.
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

# Harden psql itself. Keep the original direct migration invocation because it preserves
# PowerShell argument semantics and has already been proven to reach the migration script.
# -w prevents password prompts; the PostgreSQL timeouts make connection/SQL failures bounded.
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

# Parse modified PowerShell scripts before the expensive build/install path.
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
Write-Host 'Installer reliability patches applied and syntax validated.'
