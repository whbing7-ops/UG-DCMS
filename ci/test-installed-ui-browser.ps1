param(
  [string]$BaseUrl='http://127.0.0.1:8080',
  [Parameter(Mandatory=$true)][string]$CredentialFile,
  [string]$ArtifactDir='ci-artifacts\validation'
)
$ErrorActionPreference='Stop'
$edge=@('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','C:\Program Files\Microsoft\Edge\Application\msedge.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
if(-not $edge){ throw 'Microsoft Edge not found' }
$creds=Get-Content $CredentialFile -Raw -Encoding UTF8 | ConvertFrom-Json
$releasePointer='C:\ProgramData\UG-DCMS\releases\CURRENT-RELEASE.txt'
$release=(Get-Content $releasePointer -Raw).Trim()
$front=Join-Path $release 'frontend'
$bootstrap=Join-Path $front '__ci_browser_bootstrap.html'
$results=New-Object System.Collections.Generic.List[object]

function Test-Route([string]$Name,[string]$Target,[string]$Username,[string]$Password,[string]$Expected){
  $profile=Join-Path $env:TEMP ('ugdcms-ui-'+[guid]::NewGuid().ToString('N'))
  $out=Join-Path $env:TEMP ('ugdcms-ui-'+[guid]::NewGuid().ToString('N')+'.html')
  $err=$out+'.err'
  $safeUser=($Username | ConvertTo-Json -Compress)
  $safePassword=($Password | ConvertTo-Json -Compress)
  $safeTarget=($Target | ConvertTo-Json -Compress)
  $html=@"
<!doctype html><meta charset="utf-8"><script>
(async()=>{const r=await fetch('/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$safeUser,password:$safePassword})});
if(!r.ok){document.body.textContent='CI LOGIN FAILED '+r.status+' '+await r.text();return}
const d=await r.json();sessionStorage.setItem('dcms.token',d.access_token);location.replace('/#'+$safeTarget)})().catch(e=>document.body.textContent='CI BOOTSTRAP FAILED '+e);
</script>
"@
  [IO.File]::WriteAllText($bootstrap,$html,(New-Object Text.UTF8Encoding($false)))
  try {
    New-Item -ItemType Directory -Force $profile | Out-Null
    $args=@('--headless=new','--disable-gpu','--disable-background-networking','--no-first-run','--no-default-browser-check','--virtual-time-budget=12000',('--user-data-dir='+$profile),'--dump-dom',($BaseUrl+'/__ci_browser_bootstrap.html'))
    $p=Start-Process -FilePath $edge -ArgumentList $args -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    if(-not $p.WaitForExit(30000)){ try { Stop-Process -Id $p.Id -Force } catch {}; throw 'Edge timeout' }
    $dom=Get-Content $out -Raw -Encoding UTF8
    $bad=@('[object HTML','无法打开这个页面','CI LOGIN FAILED','CI BOOTSTRAP FAILED') | Where-Object { $dom.Contains($_) }
    if($bad){ throw ('bad rendered DOM marker: '+($bad -join ',')) }
    if(-not $dom.Contains($Expected)){ throw ('expected rendered text missing: '+$Expected) }
    if($dom -notmatch '<nav[^>]*class="nav"' -or $dom -notmatch '<a[^>]*href="#/search"'){ throw 'real sidebar links not rendered' }
    $buttonCount=([regex]::Matches($dom,'<button\b')).Count
    $results.Add([pscustomobject]@{Route=$Name;Status='PASS';Buttons=$buttonCount;Expected=$Expected})
    Write-Host ('[PASS] UI '+$Name+' buttons='+$buttonCount)
  } catch {
    $results.Add([pscustomobject]@{Route=$Name;Status='FAIL';Buttons=0;Expected=$Expected;Error=$_.Exception.Message})
    Write-Host ('[FAIL] UI '+$Name+': '+$_.Exception.Message)
  } finally {
    Remove-Item $profile -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $out,$err -Force -ErrorAction SilentlyContinue
  }
}

try {
  foreach($r in @(
    @('overview','/','engineer','设计构型管理系统'),
    @('search-results','/search?q=CI演示','engineer','匹配到'),
    @('families','/families','engineer','设计族'),
    @('files','/files','engineer','设计文件'),
    @('external-parts','/external-parts','engineer','外部件'),
    @('software','/software','engineer','软件对象'),
    @('approvals','/approvals','engineer','审批'),
    @('quality','/quality','engineer','数据质量'),
    @('reports','/reports','engineer','统计'),
    @('dictionary','/dictionary','engineer','受控字典')
  )){ Test-Route $r[0] $r[1] $creds.username $creds.password $r[3] }
  foreach($r in @(
    @('admin','/admin','系统管理'),@('audit','/audit','审计记录')
  )){ Test-Route $r[0] $r[1] $creds.admin_username $creds.admin_password $r[2] }
} finally { Remove-Item $bootstrap -Force -ErrorAction SilentlyContinue }

$results | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $ArtifactDir 'INSTALLED-UI-RESULTS.json') -Encoding UTF8
$results | Format-Table -AutoSize | Out-String -Width 260 | Set-Content (Join-Path $ArtifactDir 'INSTALLED-UI-SUMMARY.txt') -Encoding UTF8
$failed=@($results | Where-Object Status -eq 'FAIL')
if($failed.Count){ throw ('installed UI routes failed: '+(($failed.Route)-join ',')) }
Write-Host ('INSTALLED UI BROWSER PASS: routes='+$results.Count)
