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
function Invoke-SetupBounded([string]$Exe,[string]$Log,[int]$Minutes=12){
  if(-not(Test-Path $Exe)){ throw ('Setup.exe missing: '+$Exe) }
  $p=Start-Process -FilePath $Exe -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/LOG='+$Log)) -PassThru
  $deadline=(Get-Date).AddMinutes($Minutes)
  while(-not $p.HasExited -and (Get-Date) -lt $deadline){ Start-Sleep -Seconds 5; $p.Refresh() }
  if(-not $p.HasExited){
    try { taskkill /PID $p.Id /T /F | Out-Host } catch {}
    throw ('Setup timeout after '+$Minutes+' minutes')
  }
  $p.WaitForExit(); $p.Refresh()
  if(-not $p.HasExited){ throw 'Setup process did not finalize cleanly' }
  $code=[int]$p.ExitCode
  if($code -ne 0){ throw ('Setup exit code '+$code) }
}
function Invoke-DbScalar([hashtable]$Cfg,[string]$Sql){
  $psql=Find-Psql
  $env:PGPASSWORD=$Cfg['DCMS_PG_PASSWORD']
  $raw=@(& $psql -w -X -h $Cfg['DCMS_PG_HOST'] -p $Cfg['DCMS_PG_PORT'] -U $Cfg['DCMS_PG_USER'] -d $Cfg['DCMS_PG_DATABASE'] -qtAX -v ON_ERROR_STOP=1 -c $Sql 2>&1)
  $code=[int]$LASTEXITCODE
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

$base='C:\ProgramData\UG-DCMS'
$statusFile=Join-Path $base 'INSTALLATION-STATUS.txt'
$errorFile=Join-Path $base 'logs\LAST-ERROR.txt'
$oldRuntime=$null; $oldRelease=$null; $oldStatusTicks=$null
$freshSetupOk=$false; $freshCoreOk=$false; $cfg=$null

Run-Test 'FRESH-01 Setup executable exists' { if(-not(Test-Path $SetupPath)){ throw 'Setup.exe missing' } }
Run-Test 'FRESH-02 Silent fresh install completes' {
  Invoke-SetupBounded -Exe $SetupPath -Log 'C:\ugdcms-inno.log'
  $script:freshSetupOk=$true
}
Run-Test 'FRESH-03 Provisioning status completed' {
  if(-not $freshSetupOk){ throw 'fresh Setup did not pass' }
  $deadline=(Get-Date).AddMinutes(3)
  do {
    if(Test-Path $errorFile){ throw ((Get-Content $errorFile -Raw -ErrorAction SilentlyContinue)) }
    if(Test-Path $statusFile){ break }
    Start-Sleep -Seconds 3
  } while((Get-Date) -lt $deadline)
  if(-not(Test-Path $statusFile)){ throw 'installation status marker missing' }
}
Run-Test 'FRESH-04 PostgreSQL service running' { if((Get-Service 'UGDCMS-PostgreSQL' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-PostgreSQL not Running' } }
Run-Test 'FRESH-05 Application service running' { if((Get-Service 'UGDCMS-App' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-App not Running' } }
Run-Test 'FRESH-06 Release pointer valid' {
  $p=([string](Get-Content (Join-Path $base 'releases\CURRENT-RELEASE.txt') -Raw)).Trim(); if(-not $p -or -not(Test-Path $p)){ throw ('invalid release pointer: '+$p) }
  $script:oldRelease=$p
}
Run-Test 'FRESH-07 Runtime pointer valid' {
  $p=([string](Get-Content (Join-Path $base 'runtime\CURRENT-RUNTIME.txt') -Raw)).Trim(); if(-not $p -or -not(Test-Path $p)){ throw ('invalid runtime pointer: '+$p) }
  $script:oldRuntime=$p
}
Run-Test 'FRESH-08 Health endpoint HTTP 200' { Wait-Http 'http://127.0.0.1:8080/api/v1/health' 60 }
Run-Test 'FRESH-09 UI endpoint HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 60 }
Run-Test 'FRESH-10 Database configuration readable' { $script:cfg=Read-DcmsEnv $base }
Run-Test 'FRESH-11 Migration count is 12' {
  if(-not $cfg){ $script:cfg=Read-DcmsEnv $base }
  $count=([string](Invoke-DbScalar $cfg 'SELECT count(*) FROM schema_migration;')).Trim(); if([int]$count -ne 12){ throw ('migration count='+$count) }
}
Run-Test 'FRESH-12 Create upgrade preservation sentinel' {
  if(-not $cfg){ $script:cfg=Read-DcmsEnv $base }
  [void](Invoke-DbScalar $cfg "CREATE TABLE IF NOT EXISTS ci_upgrade_sentinel (id integer PRIMARY KEY, note text NOT NULL); INSERT INTO ci_upgrade_sentinel(id,note) VALUES (1,'preserve-me') ON CONFLICT (id) DO UPDATE SET note=excluded.note; SELECT 'ok';")
  if(Test-Path $statusFile){ $script:oldStatusTicks=(Get-Item $statusFile).LastWriteTimeUtc.Ticks }
  $script:freshCoreOk=$true
}

if($freshCoreOk -and $oldRuntime -and $oldRelease){
  Run-Test 'UPGRADE-01 App running before live reinstall' { if((Get-Service 'UGDCMS-App' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-App not Running before upgrade' } }
  Start-Sleep -Seconds 2
  Run-Test 'UPGRADE-02 Live reinstall completes' { Invoke-SetupBounded -Exe $SetupPath -Log 'C:\ugdcms-inno-upgrade.log' }
  Run-Test 'UPGRADE-03 No LAST-ERROR after reinstall' { if(Test-Path $errorFile){ throw ([string](Get-Content $errorFile -Raw -ErrorAction SilentlyContinue)) } }
  Run-Test 'UPGRADE-04 Status marker refreshed' {
    if(-not(Test-Path $statusFile)){ throw 'status marker missing' }
    if($oldStatusTicks -and (Get-Item $statusFile).LastWriteTimeUtc.Ticks -le [int64]$oldStatusTicks){ throw 'status marker was not refreshed' }
  }
  Run-Test 'UPGRADE-05 Release pointer changed and valid' {
    $p=([string](Get-Content (Join-Path $base 'releases\CURRENT-RELEASE.txt') -Raw)).Trim(); if($p -eq $oldRelease){ throw 'release pointer unchanged' }; if(-not(Test-Path $p)){ throw 'new release target missing' }
  }
  Run-Test 'UPGRADE-06 Runtime pointer changed and valid' {
    $p=([string](Get-Content (Join-Path $base 'runtime\CURRENT-RUNTIME.txt') -Raw)).Trim(); if($p -eq $oldRuntime){ throw 'runtime pointer unchanged' }; if(-not(Test-Path $p)){ throw 'new runtime target missing' }
  }
  Run-Test 'UPGRADE-07 PostgreSQL service still running' { if((Get-Service 'UGDCMS-PostgreSQL' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-PostgreSQL not Running after upgrade' } }
  Run-Test 'UPGRADE-08 App service still running' { if((Get-Service 'UGDCMS-App' -ErrorAction Stop).Status -ne 'Running'){ throw 'UGDCMS-App not Running after upgrade' } }
  Run-Test 'UPGRADE-09 Health endpoint still HTTP 200' { Wait-Http 'http://127.0.0.1:8080/api/v1/health' 60 }
  Run-Test 'UPGRADE-10 UI endpoint still HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 60 }
  Run-Test 'UPGRADE-11 Database sentinel preserved' {
    $cfg2=Read-DcmsEnv $base; $v=([string](Invoke-DbScalar $cfg2 'SELECT note FROM ci_upgrade_sentinel WHERE id=1;')).Trim(); if($v -ne 'preserve-me'){ throw ('sentinel='+$v) }
  }
  Run-Test 'UPGRADE-12 Migration count remains 12' {
    $cfg2=Read-DcmsEnv $base; $v=([string](Invoke-DbScalar $cfg2 'SELECT count(*) FROM schema_migration;')).Trim(); if([int]$v -ne 12){ throw ('migration count='+$v) }
  }
  Run-Test 'UPGRADE-13 No locked-file/access-denied regression' {
    $bad=@(); foreach($log in @((Join-Path $base 'logs\install.log'),(Join-Path $base 'logs\UGDCMS-App.err.log'),'C:\ugdcms-inno-upgrade.log')){
      if(Test-Path $log){ $hits=Select-String -Path $log -Pattern 'UnauthorizedAccessException|Access Denied|_bcrypt\.pyd'; if($hits){ $bad += $hits } }
    }
    if($bad.Count -gt 0){ throw (($bad | ForEach-Object {$_.Line}) -join ' | ') }
  }
}else{
  foreach($name in @('UPGRADE-01 App running before live reinstall','UPGRADE-02 Live reinstall completes','UPGRADE-03 No LAST-ERROR after reinstall','UPGRADE-04 Status marker refreshed','UPGRADE-05 Release pointer changed and valid','UPGRADE-06 Runtime pointer changed and valid','UPGRADE-07 PostgreSQL service still running','UPGRADE-08 App service still running','UPGRADE-09 Health endpoint still HTTP 200','UPGRADE-10 UI endpoint still HTTP 200','UPGRADE-11 Database sentinel preserved','UPGRADE-12 Migration count remains 12','UPGRADE-13 No locked-file/access-denied regression')){ Skip-Test $name 'fresh-install prerequisite unavailable' }
}

$results | Format-Table -AutoSize | Out-String -Width 260 | Set-Content (Join-Path $ArtifactDir 'TEST-SUMMARY.txt') -Encoding UTF8
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
Write-Host 'ALL EXECUTABLE WINDOWS INSTALLER TESTS PASSED'
exit 0
