param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$e2e = Join-Path $PSScriptRoot 'windows-e2e-validation.ps1'
if(-not(Test-Path $e2e)){ throw 'windows-e2e-validation.ps1 missing' }

$text = Get-Content $e2e -Raw -Encoding UTF8
$text = $text.Replace("FRESH-18 Migration count is 12","FRESH-18 Migration count is 13")
$text = $text.Replace("UPGRADE-DATA-02 Migration count remains 12","UPGRADE-DATA-02 Migration count remains 13")
$text = $text.Replace("if([int]$count -ne 12)","if([int]$count -ne 13)")
$text = $text.Replace("if([int]$v -ne 12)","if([int]$v -ne 13)")

$marker = "Run-Test 'FRESH-19 Create upgrade preservation sentinel' {"
if(-not $text.Contains($marker)){ throw 'E2E sentinel marker not found' }
if(-not $text.Contains("FRESH-18A Authentication API login succeeds")){
$authTest = @'
Run-Test 'FRESH-18A Authentication API login succeeds' {
  if(-not $cfg){ $script:cfg=Read-DcmsEnv $base }
  $probe = 'T' + [guid]::NewGuid().ToString('N')
  $sql = "UPDATE app_user SET password_hash=crypt('$probe', gen_salt('bf', 12)), failed_login_count=0, locked_until=NULL WHERE lower(username)='admin'; SELECT 1;"
  [void](Invoke-DbScalar $cfg $sql)
  $payload = @{ username='admin'; password=$probe } | ConvertTo-Json -Compress
  try {
    $r = Invoke-WebRequest 'http://127.0.0.1:8080/api/v1/auth/login' -Method POST -ContentType 'application/json' -Body $payload -UseBasicParsing -TimeoutSec 15
  } catch {
    $body=''
    try { $body=$_.ErrorDetails.Message } catch {}
    throw ('authentication API request failed: '+$_.Exception.Message+' '+$body)
  }
  if($r.StatusCode -ne 200){ throw ('authentication API returned HTTP '+$r.StatusCode) }
  $data = $r.Content | ConvertFrom-Json
  if(-not $data.access_token){ throw 'authentication API returned no access token' }
}

'@
  $text = $text.Replace($marker,$authTest+$marker)
}

if($text.Contains('Migration count is 12')){ throw 'fresh migration count still expects 12' }
if($text.Contains('Migration count remains 12')){ throw 'upgrade migration count still expects 12' }
if(-not $text.Contains('FRESH-18A Authentication API login succeeds')){ throw 'authentication E2E smoke test missing' }

[IO.File]::WriteAllText($e2e,$text,(New-Object Text.UTF8Encoding($true)))
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $e2e).Path,[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count -gt 0){
  foreach($e in $errors){ Write-Host ('E2E auth patch: '+$e.Message+' line '+$e.Extent.StartLineNumber) }
  throw 'windows-e2e-validation.ps1 parse failure after auth smoke patch'
}
Write-Host 'E2E AUTH PATCH PASS: migration count 13/13 and real HTTP authentication smoke test enabled.'
