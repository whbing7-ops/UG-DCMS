param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$build = Join-Path $SourceRoot 'installer\Build-Setup.ps1'
$t = Get-Content $build -Raw -Encoding UTF8
$old = '(Get-Content $MyInvocation.MyCommand.Path -Raw -Encoding UTF8)'
$new = '(Get-Content $PSCommandPath -Raw -Encoding UTF8)'
if(-not $t.Contains($old)){ throw 'Build-Setup self-path expression not found' }
$t = $t.Replace($old,$new)
Set-Content $build $t -Encoding UTF8

$provision = Join-Path $SourceRoot 'windows\install-oneclick.ps1'
$p = Get-Content $provision -Raw -Encoding UTF8
$oldTranscript = 'try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch {}'
$newTranscript = '$TranscriptFile = Join-Path $LogDir ''powershell-transcript.log''' + "`r`n" + 'try { Start-Transcript -Path $TranscriptFile -Append -Force | Out-Null } catch {}'
if(-not $p.Contains($oldTranscript)){ throw 'Transcript expression not found' }
$p = $p.Replace($oldTranscript,$newTranscript)

$oldExit = '  if($proc.ExitCode -ne 0){ throw "$Step 失败，退出码 $($proc.ExitCode)" }'
$newExit = @'
  $proc.Refresh()
  if(-not $proc.HasExited){ throw "$Step 结束状态异常：进程未退出。" }
  $exitCode = $proc.ExitCode
  if($exitCode -ne 0){ throw "$Step 失败，退出码 $exitCode" }
'@
if(-not $p.Contains($oldExit)){ throw 'Process exit-code expression not found' }
$p = $p.Replace($oldExit,$newExit.TrimEnd())
Set-Content $provision $p -Encoding UTF8

$check = Get-Content $provision -Raw -Encoding UTF8
if($check.Contains('Start-Transcript -Path $LogFile')){ throw 'Transcript still locks install.log' }
if(-not $check.Contains('$proc.Refresh()')){ throw 'Exit-code refresh patch missing' }
Write-Host 'Installer reliability patches applied.'
