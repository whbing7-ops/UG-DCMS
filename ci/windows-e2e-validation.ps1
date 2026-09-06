param(
  [Parameter(Mandatory=$true)][string]$SetupPath,
  [string]$ArtifactDir = 'ci-artifacts\validation'
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
$results = New-Object System.Collections.Generic.List[object]

function Add-Result([string]$Name,[string]$Status,[string]$Detail=''){
  $obj=[pscustomobject]@{ Name=$Name; Status=$Status; Detail=$Detail }
  $results.Add($obj)
  Write-Host ('['+$Status+'] '+$Name+($(if($Detail){': '+$Detail}else{''})))
}
function Run-Test([string]$Name,[scriptblock]$Body){
  try { & $Body; Add-Result $Name 'PASS' }
  catch { Add-Result $Name 'FAIL' ([string]$_.Exception.Message) }
}
function Skip-Test([string]$Name,[string]$Reason){ Add-Result $Name 'SKIP' $Reason }
function Read-DcmsEnv([string]$Base){
  $cfg=@{}
  $path=Join-Path $Base '.env'
  if(-not(Test-Path $path)){ throw '.env missing' }
  Get-Content $path | ForEach-Object {
    $line=$_.Trim()
    if($line -and -not $line.StartsWith('#') -and $line.Contains('=')){
      $parts=$line.Split('=',2); $cfg[$parts[0].Trim()]=$parts[1].Trim()
    }
  }
  foreach($key in @('DCMS_PG_HOST','DCMS_PG_PORT','DCMS_PG_USER','DCMS_PG_PASSWORD','DCMS_PG_DATABASE')){
    if(-not $cfg.ContainsKey($key)){ throw ('.env missing '+$key) }
  }
  return $cfg
}
function Find-Psql {
  $pg=Get-ChildItem 'C:\Program Files\PostgreSQL' -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
  if(-not $pg){ throw 'PostgreSQL installation not found' }
  $psql=Join-Path $pg.FullName 'bin\psql.exe'
  if(-not(Test-Path $psql)){ throw ('psql.exe missing: '+$psql) }
  return $psql
}
function Invoke-ProcessCapture([string]$Exe,[string[]]$Args,[int]$Minutes=12){
  if(-not(Test-Path $Exe)){ throw ('executable missing: '+$Exe) }
  $p=Start-Process -FilePath $Exe -ArgumentList $Args -PassThru
  $deadline=(Get-Date).AddMinutes($Minutes)
  while(-not $p.HasExited -and (Get-Date) -lt $deadline){ Start-Sleep -Seconds 3; $p.Refresh() }
  if(-not $p.HasExited){
    try { taskkill /PID $p.Id /T /F | Out-Host } catch {}
    return [pscustomobject]@{ TimedOut=$true; ExitCode=$null }
  }
  $p.WaitForExit(); $p.Refresh()
  if(-not $p.HasExited){ return [pscustomobject]@{ TimedOut=$false; ExitCode=$null } }
  return [pscustomobject]@{ TimedOut=$false; ExitCode=[int]$p.ExitCode }
}
function Invoke-SetupCapture([string]$Exe,[string]$Log,[int]$Minutes=12){
  return Invoke-ProcessCapture -Exe $Exe -Args @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/LOG='+$Log)) -Minutes $Minutes
}
function Invoke-DbScalar([hashtable]$Cfg,[string]$Sql){
  $psql=Find-Psql
  $env:PGPASSWORD=$Cfg['DCMS_PG_PASSWORD']
  $oldEap=$ErrorActionPreference
  try {
    $ErrorActionPreference='Continue'
    $raw=@(& $psql -w -X -h $Cfg['DCMS_PG_HOST'] -p $Cfg['DCMS_PG_PORT'] -U $Cfg['DCMS_PG_USER'] -d $Cfg['DCMS_PG_DATABASE'] -qtAX -v ON_ERROR_STOP=1 -c $Sql 2>&1)
    $code=[int]$LASTEXITCODE
  } finally { $ErrorActionPreference=$oldEap }
  $text=(($raw | ForEach-Object {[string]$_}) -join "`n").Trim()
  if($code -ne 0){ throw ('psql exit '+$code+': '+$text) }
  return $text
}
function Wait-Http([string]$Url,[int]$Seconds=60){
  $deadline=(Get-Date).AddSeconds($Seconds); $last=''
  do {
    try { $r=Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 5; if($r.StatusCode -eq 200){ return } else { $last='HTTP '+$r.StatusCode } }
    catch { $last=$_.Exception.Message }
    Start-Sleep -Seconds 2
  } while((Get-Date) -lt $deadline)
  throw ('HTTP not ready: '+$Url+'; '+$last)
}
function Assert-HttpDown([string]$Url){
  try { $r=Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 5; throw ('endpoint still responds HTTP '+$r.StatusCode) }
  catch {
    if($_.Exception.Message -like 'endpoint still responds*'){ throw }
  }
}
function Read-Pointer([string]$Path){ return ([string](Get-Content $Path -Raw -ErrorAction Stop)).Trim() }
function Get-Uninstaller {
  $items=@()
  foreach($root in @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')){
    $items += Get-ItemProperty $root -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'UG-DCMS*' }
  }
  $entry=$items | Select-Object -First 1
  if(-not $entry){ throw 'UG-DCMS uninstall registry entry missing' }
  $raw=[string]$entry.UninstallString
  if(-not $raw){ throw 'UninstallString missing' }
  if($raw -match '^"([^"]+)"'){ return $Matches[1] }
  return ($raw -split ' ')[0]
}

$base='C:\ProgramData\UG-DCMS'
$statusFile=Join-Path $base 'INSTALLATION-STATUS.txt'
$errorFile=Join-Path $base 'logs\LAST-ERROR.txt'
$envFile=Join-Path $base '.env'
$releasePointer=Join-Path $base 'releases\CURRENT-RELEASE.txt'
$runtimePointer=Join-Path $base 'runtime\CURRENT-RUNTIME.txt'
$script:freshExit=$null; $script:upgradeExit=$null; $script:uninstallExit=$null; $script:reinstallExit=$null
$script:freshSetupOk=$false; $script:upgradeRan=$false; $script:uninstallRan=$false
$script:oldRuntime=$null; $script:oldRelease=$null; $script:oldStatusTicks=$null; $script:cfg=$null; $script:sentinelCreated=$false

Run-Test 'FRESH-01 Setup executable exists' { if(-not(Test-Path $SetupPath)){ throw 'Setup.exe missing' } }
Run-Test 'FRESH-02 Silent fresh install completes with exit 0' {
  $r=Invoke-SetupCapture -Exe $SetupPath -Log 'C:\ugdcms-inno.log'
  $script:freshExit=$r.ExitCode
  if($r.TimedOut){ throw 'fresh Setup timed out' }
  if($null -eq $r.ExitCode){ throw 'fresh Setup exit code unavailable' }
  if([int]$r.ExitCode -ne 0){ throw ('fresh Setup exit code '+$r.ExitCode) }
  $script:freshSetupOk=$true
}
Run-Test 'FRESH-03 Provisioning status completed without LAST-ERROR' {
  if(-not $freshSetupOk){ throw 'fresh Setup did not pass' }
  $deadline=(Get-Date).AddMinutes(3)
  do {
    if(Test-Path $errorFile){ throw ([string](Get-Content $errorFile -Raw -ErrorAction SilentlyContinue)) }
    if(Test-Path $statusFile){ break }
    Start-Sleep -Seconds 3
  } while((Get-Date) -lt $deadline)
  if(-not(Test-Path $statusFile)){ throw 'installation status marker missing' }
  $script:oldStatusTicks=(Get-Item $statusFile).LastWriteTimeUtc.Ticks
}
Run-Test 'FRESH-04 PostgreSQL service running' { if((Get-Service 'UGDCMS-PostgreSQL' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-PostgreSQL not Running' } }
Run-Test 'FRESH-05 Application service running' { if((Get-Service 'UGDCMS-App' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-App not Running' } }
Run-Test 'FRESH-06 PostgreSQL service automatic' { if((Get-CimInstance Win32_Service -Filter "Name='UGDCMS-PostgreSQL'" -ErrorAction Stop).StartMode -ne 'Auto'){ throw 'UGDCMS-PostgreSQL not Automatic' } }
Run-Test 'FRESH-07 Application service automatic' { if((Get-CimInstance Win32_Service -Filter "Name='UGDCMS-App'" -ErrorAction Stop).StartMode -ne 'Auto'){ throw 'UGDCMS-App not Automatic' } }
Run-Test 'FRESH-08 Release pointer valid' {
  $p=Read-Pointer $releasePointer; if(-not $p -or -not(Test-Path $p)){ throw ('invalid release pointer: '+$p) }; $script:oldRelease=$p
}
Run-Test 'FRESH-09 Runtime pointer valid' {
  $p=Read-Pointer $runtimePointer; if(-not $p -or -not(Test-Path $p)){ throw ('invalid runtime pointer: '+$p) }; $script:oldRuntime=$p
}
Run-Test 'FRESH-10 Health endpoint HTTP 200' { Wait-Http 'http://127.0.0.1:8080/api/v1/health' 60 }
Run-Test 'FRESH-11 UI endpoint HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 60 }
Run-Test 'FRESH-12 Listener bound for LAN access' {
  $listeners=@(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction Stop)
  if($listeners.Count -eq 0){ throw 'no TCP/8080 listener' }
  if(-not @($listeners | Where-Object { $_.LocalAddress -eq '0.0.0.0' -or $_.LocalAddress -eq '::' }).Count){ throw ('listener not bound to all interfaces: '+(($listeners.LocalAddress | Sort-Object -Unique) -join ',')) }
}
Run-Test 'FRESH-13 Firewall is Domain/Private only' {
  $r=Get-NetFirewallRule -DisplayName 'UG-DCMS Web Access' -ErrorAction Stop
  $p=[string]$r.Profile
  if($p -match 'Public' -or $p -match 'Any'){ throw ('unsafe firewall profile: '+$p) }
  if($p -notmatch 'Domain' -or $p -notmatch 'Private'){ throw ('expected Domain,Private; actual '+$p) }
}
Run-Test 'FRESH-14 Public desktop shortcut exists' { if(-not(Test-Path 'C:\Users\Public\Desktop\UG-DCMS.lnk')){ throw 'public desktop shortcut missing' } }
Run-Test 'FRESH-15 Database configuration readable' { $script:cfg=Read-DcmsEnv $base }
Run-Test 'FRESH-16 .env can be opened read/write by elevated installer context' {
  $fs=[System.IO.File]::Open($envFile,[System.IO.FileMode]::Open,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::ReadWrite)
  try { if(-not $fs.CanWrite){ throw '.env stream is not writable' } } finally { $fs.Dispose() }
}
Run-Test 'FRESH-17 .env ACL has no broad Users/Everyone/AuthUsers read grant' {
  $acl=Get-Acl $envFile
  $broad=@('S-1-1-0','S-1-5-11','S-1-5-32-545')
  foreach($rule in $acl.Access){
    try { $sid=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { $sid=[string]$rule.IdentityReference }
    if($broad -contains $sid -and $rule.AccessControlType -eq 'Allow'){
      $rights=[string]$rule.FileSystemRights
      if($rights -match 'Read|Modify|FullControl'){ throw ('broad ACL '+$sid+' '+$rights) }
    }
  }
}
try { (Get-Acl $envFile | Format-List * | Out-String -Width 260) | Set-Content (Join-Path $ArtifactDir 'ENV-ACL.txt') -Encoding UTF8 } catch {}
Run-Test 'FRESH-18 Migration count is 12' {
  if(-not $cfg){ $script:cfg=Read-DcmsEnv $base }
  $count=([string](Invoke-DbScalar $cfg 'SELECT count(*) FROM schema_migration;')).Trim(); if([int]$count -ne 12){ throw ('migration count='+$count) }
}
Run-Test 'FRESH-19 Create upgrade preservation sentinel' {
  if(-not $cfg){ $script:cfg=Read-DcmsEnv $base }
  [void](Invoke-DbScalar $cfg "CREATE TABLE IF NOT EXISTS ci_upgrade_sentinel (id integer PRIMARY KEY, note text NOT NULL); INSERT INTO ci_upgrade_sentinel(id,note) VALUES (1,'preserve-me') ON CONFLICT (id) DO UPDATE SET note=excluded.note; SELECT 'ok';")
  $v=([string](Invoke-DbScalar $cfg 'SELECT note FROM ci_upgrade_sentinel WHERE id=1;')).Trim(); if($v -ne 'preserve-me'){ throw ('sentinel='+$v) }
  $script:sentinelCreated=$true
}

$canUpgrade=$freshSetupOk -and $oldRuntime -and $oldRelease -and (Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue)
if($canUpgrade){
  Run-Test 'UPGRADE-01 App running before live reinstall' { if((Get-Service 'UGDCMS-App').Status -ne 'Running'){ throw 'UGDCMS-App not Running before upgrade' } }
  Start-Sleep -Seconds 2
  Run-Test 'UPGRADE-02 Live reinstall process completes' {
    $r=Invoke-SetupCapture -Exe $SetupPath -Log 'C:\ugdcms-inno-upgrade.log'
    $script:upgradeRan=$true; $script:upgradeExit=$r.ExitCode
    if($r.TimedOut){ throw 'live reinstall timed out' }
    if($null -eq $r.ExitCode){ throw 'upgrade Setup exit code unavailable' }
    if([int]$r.ExitCode -ne 0){ throw ('upgrade Setup exit code '+$r.ExitCode) }
  }
  $upgradeHasError=Test-Path $errorFile
  Run-Test 'UPGRADE-03 Setup exit code agrees with provisioning result' {
    if($upgradeHasError -and [int]$upgradeExit -eq 0){ throw 'Setup returned 0 even though provisioning wrote LAST-ERROR.txt' }
    if(-not $upgradeHasError -and [int]$upgradeExit -ne 0){ throw ('Setup returned '+$upgradeExit+' without LAST-ERROR.txt') }
  }
  Run-Test 'UPGRADE-04 No provisioning LAST-ERROR' { if($upgradeHasError){ throw ([string](Get-Content $errorFile -Raw -ErrorAction SilentlyContinue)) } }

  if(-not $upgradeHasError -and [int]$upgradeExit -eq 0){
    Run-Test 'UPGRADE-SUCCESS-01 Status marker refreshed' {
      if(-not(Test-Path $statusFile)){ throw 'status marker missing' }
      if($oldStatusTicks -and (Get-Item $statusFile).LastWriteTimeUtc.Ticks -le [int64]$oldStatusTicks){ throw 'status marker was not refreshed' }
    }
    Run-Test 'UPGRADE-SUCCESS-02 Release pointer changed and valid' { $p=Read-Pointer $releasePointer; if($p -eq $oldRelease){ throw 'release pointer unchanged' }; if(-not(Test-Path $p)){ throw 'new release target missing' } }
    Run-Test 'UPGRADE-SUCCESS-03 Runtime pointer changed and valid' { $p=Read-Pointer $runtimePointer; if($p -eq $oldRuntime){ throw 'runtime pointer unchanged' }; if(-not(Test-Path $p)){ throw 'new runtime target missing' } }
    Run-Test 'UPGRADE-SUCCESS-04 PostgreSQL service running' { if((Get-Service 'UGDCMS-PostgreSQL').Status -ne 'Running'){ throw 'DB not Running after upgrade' } }
    Run-Test 'UPGRADE-SUCCESS-05 App service running' { if((Get-Service 'UGDCMS-App').Status -ne 'Running'){ throw 'App not Running after upgrade' } }
    Run-Test 'UPGRADE-SUCCESS-06 Health HTTP 200' { Wait-Http 'http://127.0.0.1:8080/api/v1/health' 60 }
    Run-Test 'UPGRADE-SUCCESS-07 UI HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 60 }
  } else {
    Run-Test 'UPGRADE-ROLLBACK-01 Status marker not falsely refreshed' {
      if(Test-Path $statusFile -and $oldStatusTicks -and (Get-Item $statusFile).LastWriteTimeUtc.Ticks -gt [int64]$oldStatusTicks){ throw 'failed upgrade refreshed success status marker' }
    }
    Run-Test 'UPGRADE-ROLLBACK-02 Release pointer restored' { $p=Read-Pointer $releasePointer; if($p -ne $oldRelease){ throw ('release pointer not restored: '+$p) }; if(-not(Test-Path $p)){ throw 'restored release target missing' } }
    Run-Test 'UPGRADE-ROLLBACK-03 Runtime pointer restored' { $p=Read-Pointer $runtimePointer; if($p -ne $oldRuntime){ throw ('runtime pointer not restored: '+$p) }; if(-not(Test-Path $p)){ throw 'restored runtime target missing' } }
    Run-Test 'UPGRADE-ROLLBACK-04 PostgreSQL service restored Running' { if((Get-Service 'UGDCMS-PostgreSQL' -ErrorAction Stop).Status -ne 'Running'){ throw 'DB not Running after failed upgrade rollback' } }
    Run-Test 'UPGRADE-ROLLBACK-05 App service restored Running' { if((Get-Service 'UGDCMS-App' -ErrorAction Stop).Status -ne 'Running'){ throw 'App not Running after failed upgrade rollback' } }
    Run-Test 'UPGRADE-ROLLBACK-06 Health restored HTTP 200' { Wait-Http 'http://127.0.0.1:8080/api/v1/health' 30 }
    Run-Test 'UPGRADE-ROLLBACK-07 UI restored HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 30 }
    Run-Test 'UPGRADE-ROLLBACK-08 No rollback warning' {
      $log=Join-Path $base 'logs\install.log'; if(Test-Path $log){ $h=Select-String -Path $log -Pattern '\[ROLLBACK-WARN\]' -ErrorAction SilentlyContinue; if($h){ throw (($h | ForEach-Object {$_.Line}) -join ' | ') } }
    }
  }
  if($cfg -and $sentinelCreated){
    Run-Test 'UPGRADE-DATA-01 Database sentinel preserved' { $v=([string](Invoke-DbScalar $cfg 'SELECT note FROM ci_upgrade_sentinel WHERE id=1;')).Trim(); if($v -ne 'preserve-me'){ throw ('sentinel='+$v) } }
    Run-Test 'UPGRADE-DATA-02 Migration count remains 12' { $v=([string](Invoke-DbScalar $cfg 'SELECT count(*) FROM schema_migration;')).Trim(); if([int]$v -ne 12){ throw ('migration count='+$v) } }
  } else {
    Skip-Test 'UPGRADE-DATA-01 Database sentinel preserved' 'fresh DB config/sentinel unavailable'
    Skip-Test 'UPGRADE-DATA-02 Migration count remains 12' 'fresh DB config/sentinel unavailable'
  }
  Run-Test 'UPGRADE-LOG-01 No locked-file/access-denied regression' {
    $bad=@(); foreach($log in @((Join-Path $base 'logs\install.log'),(Join-Path $base 'logs\UGDCMS-App.err.log'),'C:\ugdcms-inno-upgrade.log')){
      if(Test-Path $log){ $hits=Select-String -Path $log -Pattern 'UnauthorizedAccessException|Access Denied|_bcrypt\.pyd'; if($hits){ $bad += $hits } }
    }
    if($bad.Count -gt 0){ throw (($bad | ForEach-Object {$_.Line}) -join ' | ') }
  }
} else {
  foreach($name in @('UPGRADE-01 App running before live reinstall','UPGRADE-02 Live reinstall process completes','UPGRADE-03 Setup exit code agrees with provisioning result','UPGRADE-04 No provisioning LAST-ERROR','UPGRADE-DATA-01 Database sentinel preserved','UPGRADE-DATA-02 Migration count remains 12','UPGRADE-LOG-01 No locked-file/access-denied regression')){ Skip-Test $name 'fresh install did not establish minimum upgrade prerequisites' }
}

$uninstaller=$null
Run-Test 'UNINSTALL-01 Uninstaller registered' { $script:uninstaller=Get-Uninstaller; if(-not(Test-Path $script:uninstaller)){ throw ('uninstaller missing: '+$script:uninstaller) } }
if($uninstaller){
  Run-Test 'UNINSTALL-02 Silent uninstall completes' {
    $r=Invoke-ProcessCapture -Exe $uninstaller -Args @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -Minutes 5
    $script:uninstallRan=$true; $script:uninstallExit=$r.ExitCode
    if($r.TimedOut){ throw 'uninstall timed out' }
    if($null -eq $r.ExitCode -or [int]$r.ExitCode -ne 0){ throw ('uninstall exit code '+$r.ExitCode) }
  }
  Run-Test 'UNINSTALL-03 Application service removed' { if(Get-Service 'UGDCMS-App' -ErrorAction SilentlyContinue){ throw 'UGDCMS-App service still exists after uninstall' } }
  Run-Test 'UNINSTALL-04 PostgreSQL service survives and is Running' { if((Get-Service 'UGDCMS-PostgreSQL' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-PostgreSQL not Running after uninstall' } }
  Run-Test 'UNINSTALL-05 Web application is no longer reachable' { Assert-HttpDown 'http://127.0.0.1:8080/api/v1/health' }
  if($cfg -and $sentinelCreated){
    Run-Test 'UNINSTALL-06 Database sentinel survives uninstall' { $v=([string](Invoke-DbScalar $cfg 'SELECT note FROM ci_upgrade_sentinel WHERE id=1;')).Trim(); if($v -ne 'preserve-me'){ throw ('sentinel='+$v) } }
    Run-Test 'UNINSTALL-07 Migration history survives uninstall' { $v=([string](Invoke-DbScalar $cfg 'SELECT count(*) FROM schema_migration;')).Trim(); if([int]$v -ne 12){ throw ('migration count='+$v) } }
  } else {
    Skip-Test 'UNINSTALL-06 Database sentinel survives uninstall' 'DB config/sentinel unavailable'
    Skip-Test 'UNINSTALL-07 Migration history survives uninstall' 'DB config/sentinel unavailable'
  }

  Run-Test 'REINSTALL-01 Reinstall after uninstall completes with exit 0' {
    Remove-Item $errorFile -Force -ErrorAction SilentlyContinue
    $r=Invoke-SetupCapture -Exe $SetupPath -Log 'C:\ugdcms-inno-reinstall.log'
    $script:reinstallExit=$r.ExitCode
    if($r.TimedOut){ throw 'reinstall timed out' }
    if($null -eq $r.ExitCode -or [int]$r.ExitCode -ne 0){ throw ('reinstall exit code '+$r.ExitCode) }
  }
  Run-Test 'REINSTALL-02 No LAST-ERROR after reinstall' { if(Test-Path $errorFile){ throw ([string](Get-Content $errorFile -Raw -ErrorAction SilentlyContinue)) } }
  Run-Test 'REINSTALL-03 Application service Running' { if((Get-Service 'UGDCMS-App' -ErrorAction Stop).Status -ne 'Running'){ throw 'App not Running after reinstall' } }
  Run-Test 'REINSTALL-04 Health HTTP 200' { Wait-Http 'http://127.0.0.1:8080/api/v1/health' 60 }
  Run-Test 'REINSTALL-05 UI HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 60 }
  if($cfg -and $sentinelCreated){
    Run-Test 'REINSTALL-06 Preserved database still contains sentinel' { $v=([string](Invoke-DbScalar $cfg 'SELECT note FROM ci_upgrade_sentinel WHERE id=1;')).Trim(); if($v -ne 'preserve-me'){ throw ('sentinel='+$v) } }
  } else { Skip-Test 'REINSTALL-06 Preserved database still contains sentinel' 'DB config/sentinel unavailable' }
} else {
  foreach($name in @('UNINSTALL-02 Silent uninstall completes','UNINSTALL-03 Application service removed','UNINSTALL-04 PostgreSQL service survives and is Running','UNINSTALL-05 Web application is no longer reachable','UNINSTALL-06 Database sentinel survives uninstall','UNINSTALL-07 Migration history survives uninstall','REINSTALL-01 Reinstall after uninstall completes with exit 0','REINSTALL-02 No LAST-ERROR after reinstall','REINSTALL-03 Application service Running','REINSTALL-04 Health HTTP 200','REINSTALL-05 UI HTTP 200','REINSTALL-06 Preserved database still contains sentinel')){ Skip-Test $name 'uninstaller unavailable' }
}

$results | Format-Table -AutoSize | Out-String -Width 320 | Set-Content (Join-Path $ArtifactDir 'TEST-SUMMARY.txt') -Encoding UTF8
$results | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $ArtifactDir 'TEST-RESULTS.json') -Encoding UTF8
$failed=@($results | Where-Object Status -eq 'FAIL')
$skipped=@($results | Where-Object Status -eq 'SKIP')
$passed=@($results | Where-Object Status -eq 'PASS')
Write-Host ('FINAL TEST SUMMARY: PASS='+$passed.Count+' FAIL='+$failed.Count+' SKIP='+$skipped.Count)
if($failed.Count -gt 0){
  Write-Host 'FAILED TESTS:'
  $failed | ForEach-Object { Write-Host (' - '+$_.Name+': '+$_.Detail) }
  exit 1
}
if($skipped.Count -gt 0){
  Write-Host 'SKIPPED TESTS:'
  $skipped | ForEach-Object { Write-Host (' - '+$_.Name+': '+$_.Detail) }
}
Write-Host 'ALL EXECUTABLE WINDOWS INSTALLER TESTS PASSED'
exit 0
