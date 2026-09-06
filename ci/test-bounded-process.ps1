$ErrorActionPreference='Stop'

function Quote-Arg([string]$Arg){
  if($null -eq $Arg){ return '""' }
  if($Arg -notmatch '[\s"]'){ return $Arg }
  return '"' + $Arg.Replace('"','\"') + '"'
}

function Invoke-BoundedProcess {
  param(
    [Parameter(Mandatory=$true)][string]$FilePath,
    [string[]]$ArgumentList=@(),
    [int]$TimeoutSeconds=10
  )

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $FilePath
  $psi.Arguments = (($ArgumentList | ForEach-Object { Quote-Arg ([string]$_) }) -join ' ')
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true

  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  if(-not $proc.Start()){ throw ('Process failed to start: ' + $FilePath) }

  $outTask = $proc.StandardOutput.ReadToEndAsync()
  $errTask = $proc.StandardError.ReadToEndAsync()

  if(-not $proc.WaitForExit($TimeoutSeconds * 1000)){
    try { $proc.Kill() } catch {}
    try { $proc.WaitForExit() } catch {}
    throw ('Process timeout: ' + $FilePath)
  }

  $proc.WaitForExit()
  $code = [int]$proc.ExitCode
  $stdout = [string]$outTask.GetAwaiter().GetResult()
  $stderr = [string]$errTask.GetAwaiter().GetResult()
  $proc.Dispose()

  [pscustomobject]@{
    ExitCode = $code
    StdOut = $stdout
    StdErr = $stderr
  }
}

Write-Host ('PowerShell=' + $PSVersionTable.PSVersion)
Write-Host ('OS=' + [Environment]::OSVersion.VersionString)

$r1 = Invoke-BoundedProcess -FilePath $env:ComSpec -ArgumentList @('/d','/c','exit /b 0') -TimeoutSeconds 5
if($r1.ExitCode -ne 0){ throw 'empty-output process exit code mismatch' }
if(([string]$r1.StdOut).Length -ne 0){ throw 'empty-output process unexpectedly produced stdout' }
Write-Host 'FAST-GATE empty-output PASS'

$r2 = Invoke-BoundedProcess -FilePath $env:ComSpec -ArgumentList @('/d','/c','echo UGDCMS_FAST_GATE') -TimeoutSeconds 5
if($r2.ExitCode -ne 0){ throw 'stdout process exit code mismatch' }
if(([string]$r2.StdOut).Trim() -ne 'UGDCMS_FAST_GATE'){ throw ('stdout mismatch: ' + [string]$r2.StdOut) }
Write-Host 'FAST-GATE stdout PASS'

$r3 = Invoke-BoundedProcess -FilePath $env:ComSpec -ArgumentList @('/d','/c','echo UGDCMS_ERR 1>&2 & exit /b 7') -TimeoutSeconds 5
if($r3.ExitCode -ne 7){ throw ('stderr process exit code mismatch: ' + $r3.ExitCode) }
if(([string]$r3.StdErr).Trim() -ne 'UGDCMS_ERR'){ throw ('stderr mismatch: ' + [string]$r3.StdErr) }
Write-Host 'FAST-GATE stderr/exit-code PASS'

$timeoutObserved=$false
try {
  Invoke-BoundedProcess -FilePath $env:ComSpec -ArgumentList @('/d','/c','ping 127.0.0.1 -n 6 >nul') -TimeoutSeconds 1 | Out-Null
}
catch {
  if($_.Exception.Message -like 'Process timeout:*'){ $timeoutObserved=$true }
  else { throw }
}
if(-not $timeoutObserved){ throw 'timeout gate did not observe timeout' }
Write-Host 'FAST-GATE timeout PASS'

# Regression for psql NOTICE/WARNING behavior on Windows PowerShell 5.1:
# stderr with exit code 0 must not become a terminating error merely because
# the surrounding script uses ErrorActionPreference=Stop.
$savedErrorActionPreference=$ErrorActionPreference
$ErrorActionPreference='Continue'
try {
  $nativeNotice=@(& $env:ComSpec /d /c 'echo PG_NOTICE 1>&2 & exit /b 0' 2>&1)
  $nativeNoticeCode=[int]$LASTEXITCODE
}
finally {
  $ErrorActionPreference=$savedErrorActionPreference
}
$nativeNoticeText=(($nativeNotice | ForEach-Object { [string]$_ }) -join "`n").Trim()
if($nativeNoticeCode -ne 0){ throw ('native stderr success code mismatch: ' + $nativeNoticeCode) }
if($nativeNoticeText -notmatch 'PG_NOTICE'){ throw ('native stderr success output missing: ' + $nativeNoticeText) }
if($ErrorActionPreference -ne 'Stop'){ throw 'ErrorActionPreference was not restored' }
Write-Host 'FAST-GATE native-stderr-success PASS'

Write-Host 'WINDOWS BOUNDED PROCESS FAST GATE PASS'
