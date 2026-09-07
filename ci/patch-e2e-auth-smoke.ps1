param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$e2e = Join-Path $PSScriptRoot 'windows-e2e-validation.ps1'
if(-not(Test-Path $e2e)){ throw 'windows-e2e-validation.ps1 missing' }

$text = Get-Content $e2e -Raw -Encoding UTF8

# Migration total is now 13. Use single-quoted literals so Windows PowerShell does
# not expand $count/$v while this patch script is running.
$text = $text.Replace('FRESH-18 Migration count is 12','FRESH-18 Migration count is 13')
$text = $text.Replace('UPGRADE-DATA-02 Migration count remains 12','UPGRADE-DATA-02 Migration count remains 13')
$text = $text.Replace('if([int]$count -ne 12)','if([int]$count -ne 13)')
$text = $text.Replace('if([int]$v -ne 12)','if([int]$v -ne 13)')

# Shared HTTP authentication assertion used after fresh install, live upgrade and
# uninstall/reinstall. This catches the exact class of defect that a health-only
# smoke test cannot see: database/session/audit failures during real login.
$helperMarker = 'function Assert-HttpDown([string]$Url){'
if(-not $text.Contains($helperMarker)){ throw 'E2E HTTP helper marker not found' }
if(-not $text.Contains('function Assert-AuthLogin')){
$helper = @'
function Assert-AuthLogin([string]$Password){
  if(-not $Password){ throw 'authentication probe password unavailable' }
  $payload = @{ username='admin'; password=$Password } | ConvertTo-Json -Compress
  try {
    $r = Invoke-WebRequest 'http://127.0.0.1:8080/api/v1/auth/login' -Method POST -ContentType 'application/json' -Body $payload -UseBasicParsing -TimeoutSec 15
  } catch {
    $body=''
    try { $body=[string]$_.ErrorDetails.Message } catch {}
    throw ('authentication API request failed: '+$_.Exception.Message+' '+$body)
  }
  if($r.StatusCode -ne 200){ throw ('authentication API returned HTTP '+$r.StatusCode) }
  $data = $r.Content | ConvertFrom-Json
  if(-not $data.access_token){ throw 'authentication API returned no access token' }
}
'@
  $text = $text.Replace($helperMarker,$helper+"`r`n"+$helperMarker)
}

$freshMarker = "Run-Test 'FRESH-19 Create upgrade preservation sentinel' {"
if(-not $text.Contains($freshMarker)){ throw 'E2E fresh sentinel marker not found' }
if(-not $text.Contains('FRESH-18A Authentication API login succeeds')){
$freshAuth = @'
Run-Test 'FRESH-18A Authentication API login succeeds' {
  if(-not $cfg){ $script:cfg=Read-DcmsEnv $base }
  $script:authProbe = 'T' + [guid]::NewGuid().ToString('N')
  $sql = "UPDATE app_user SET password_hash=crypt('$script:authProbe', gen_salt('bf', 12)), failed_login_count=0, locked_until=NULL WHERE lower(username)='admin'; SELECT 1;"
  [void](Invoke-DbScalar $cfg $sql)
  Assert-AuthLogin $script:authProbe
}

'@
  $text = $text.Replace($freshMarker,$freshAuth+$freshMarker)
}

$upgradeMarker = "    Run-Test 'UPGRADE-SUCCESS-07 UI HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 60 }"
if(-not $text.Contains($upgradeMarker)){ throw 'E2E upgrade UI marker not found' }
if(-not $text.Contains('UPGRADE-SUCCESS-08 Authentication API login succeeds')){
  $upgradeAuth = $upgradeMarker + "`r`n" + "    Run-Test 'UPGRADE-SUCCESS-08 Authentication API login succeeds' { Assert-AuthLogin `$script:authProbe }"
  $text = $text.Replace($upgradeMarker,$upgradeAuth)
}

$reinstallMarker = "  Run-Test 'REINSTALL-05 UI HTTP 200' { Wait-Http 'http://127.0.0.1:8080/' 60 }"
if(-not $text.Contains($reinstallMarker)){ throw 'E2E reinstall UI marker not found' }
if(-not $text.Contains('REINSTALL-07 Authentication API login succeeds')){
  $reinstallAuth = $reinstallMarker + "`r`n" + "  Run-Test 'REINSTALL-07 Authentication API login succeeds' { Assert-AuthLogin `$script:authProbe }"
  $text = $text.Replace($reinstallMarker,$reinstallAuth)
}

# Account for the new checks if a dependent lifecycle branch cannot execute.
$text = $text.Replace("'UPGRADE-LOG-01 No locked-file/access-denied regression'))","'UPGRADE-LOG-01 No locked-file/access-denied regression','UPGRADE-SUCCESS-08 Authentication API login succeeds'))")
$text = $text.Replace("'REINSTALL-06 Preserved database still contains sentinel'))","'REINSTALL-06 Preserved database still contains sentinel','REINSTALL-07 Authentication API login succeeds'))")

if($text.Contains('Migration count is 12')){ throw 'fresh migration count still expects 12' }
if($text.Contains('Migration count remains 12')){ throw 'upgrade migration count still expects 12' }
if($text.Contains('if([int]$count -ne 12)')){ throw 'fresh migration assertion still expects 12' }
if($text.Contains('if([int]$v -ne 12)')){ throw 'lifecycle migration assertion still expects 12' }
foreach($required in @(
  'function Assert-AuthLogin',
  'FRESH-18A Authentication API login succeeds',
  'UPGRADE-SUCCESS-08 Authentication API login succeeds',
  'REINSTALL-07 Authentication API login succeeds'
)){
  if(-not $text.Contains($required)){ throw ('authentication E2E requirement missing: '+$required) }
}

[IO.File]::WriteAllText($e2e,$text,(New-Object Text.UTF8Encoding($true)))
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $e2e).Path,[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count -gt 0){
  foreach($e in $errors){ Write-Host ('E2E auth patch: '+$e.Message+' line '+$e.Extent.StartLineNumber) }
  throw 'windows-e2e-validation.ps1 parse failure after auth smoke patch'
}
Write-Host 'E2E AUTH PATCH PASS: migration count 13/13 plus fresh, upgrade and reinstall HTTP authentication checks enabled.'
