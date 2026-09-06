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
$oldTranscript = 'try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}'
$newTranscript = '$TranscriptFile = Join-Path $LogDir ''powershell-transcript.log''' + "`r`n" + 'try { Start-Transcript -Path $TranscriptFile -Append -Force | Out-Null } catch {}'
if($p.Contains($oldTranscript)){
  $p = $p.Replace($oldTranscript,$newTranscript)
}

if(-not $p.Contains('$proc.Refresh()')){
  $pattern = '(?m)^(?<indent>\s*)if\s*\(\s*\$proc\.ExitCode\s*-ne\s*0\s*\)\s*\{\s*throw\s*"(?<msg>[^"\r\n]*)"\s*\}\s*$'
  $m = [regex]::Match($p,$pattern)
  if(-not $m.Success){
    Write-Host 'Nearby process exit-code lines:'
    ($p -split "`r?`n" | Where-Object { $_ -match 'proc|ExitCode|WaitForExit|Start-Process' }) | ForEach-Object { Write-Host $_ }
    throw 'Process exit-code expression not found'
  }
  $indent = $m.Groups['indent'].Value
  $replacement = @(
    $indent + '$proc.Refresh()',
    $indent + 'if(-not $proc.HasExited){ throw "Process did not exit cleanly for step: $Step" }'.Replace('\"','"'),
    $indent + '$exitCode = $proc.ExitCode',
    $indent + 'if($exitCode -ne 0){ throw "Step failed: $Step; exit code: $exitCode" }'.Replace('\"','"')
  ) -join "`r`n"
  $p = [regex]::Replace($p,$pattern,[System.Text.RegularExpressions.MatchEvaluator]{ param($x) $replacement },1)
}

Set-Content $provision $p -Encoding UTF8

$check = Get-Content $provision -Raw -Encoding UTF8
if($check.Contains('Start-Transcript -Path $LogFile')){ throw 'Transcript still locks install.log' }
if(-not $check.Contains('$proc.Refresh()')){ throw 'Exit-code refresh patch missing' }
if(-not $check.Contains('powershell-transcript.log')){ throw 'Transcript redirect patch missing' }

$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $provision).Path,[ref]$tokens,[ref]$parseErrors) | Out-Null
if($parseErrors.Count -gt 0){
  $parseErrors | ForEach-Object { Write-Host $_.Message }
  throw "Patched install-oneclick.ps1 has $($parseErrors.Count) parse error(s)"
}
Write-Host 'Installer reliability patches applied and syntax validated.'
