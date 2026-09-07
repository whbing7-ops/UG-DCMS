param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$frontend = Join-Path $SourceRoot 'frontend'
$ui = Join-Path $frontend 'js\ui.js'
if(-not(Test-Path $ui)){ throw 'frontend ui.js missing' }

$edgeCandidates=@(
  'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
  'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
)
$edge=$edgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if(-not $edge){ throw 'Microsoft Edge not found on Windows runner' }

$testFile=Join-Path $frontend '__ugdcms_dom_smoke.html'
$outFile=Join-Path $env:TEMP 'ugdcms-edge-dom.out.txt'
$errFile=Join-Path $env:TEMP 'ugdcms-edge-dom.err.txt'
$profile=Join-Path $env:TEMP ('ugdcms-edge-profile-'+[guid]::NewGuid().ToString('N'))
$server=$null
try {
  $html=@'
<!doctype html>
<meta charset="utf-8">
<title>UG-DCMS DOM smoke</title>
<body>
<script type="module">
import { el } from './js/ui.js';
const tree = el('nav', { id:'probe' },
  [el('h3', {}, '基础数据'), [el('a', {href:'#/parts'}, '内部件号'), el('a', {href:'#/externals'}, '外部件')]],
  [el('h3', {}, '构型管理'), [el('a', {href:'#/bom'}, 'BOM'), el('a', {href:'#/baselines'}, '设计基线')]]
);
document.body.append(tree);
const bodyText=document.body.innerText;
const ok = tree.querySelectorAll('h3').length===2 && tree.querySelectorAll('a').length===4 &&
  bodyText.includes('基础数据') && bodyText.includes('内部件号') && bodyText.includes('构型管理') &&
  !bodyText.includes('[object HTML') && !bodyText.includes('http://localhost');
document.documentElement.setAttribute('data-ugdcms-dom-smoke', ok ? 'pass' : 'fail');
</script>
</body>
'@
  [IO.File]::WriteAllText($testFile,$html,(New-Object Text.UTF8Encoding($false)))

  $py=(Get-Command python -ErrorAction SilentlyContinue).Source
  if(-not $py){ throw 'python command unavailable for local static server' }
  $frontPath=(Resolve-Path $frontend).Path
  $server=Start-Process -FilePath $py -ArgumentList @('-m','http.server','8765','--bind','127.0.0.1','--directory',$frontPath) -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds 2
  if($server.HasExited){ throw 'temporary frontend HTTP server exited early' }

  New-Item -ItemType Directory -Force $profile | Out-Null
  Remove-Item $outFile,$errFile -Force -ErrorAction SilentlyContinue
  $args=@(
    '--headless=new',
    '--disable-gpu',
    '--disable-background-networking',
    '--no-first-run',
    '--no-default-browser-check',
    ('--user-data-dir='+$profile),
    '--dump-dom',
    'http://127.0.0.1:8765/__ugdcms_dom_smoke.html'
  )
  $p=Start-Process -FilePath $edge -ArgumentList $args -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
  if(-not $p.WaitForExit(30000)){
    try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
    throw 'Edge DOM smoke timed out'
  }
  try { $p.WaitForExit() } catch {}
  try { $p.Refresh() } catch {}
  Start-Sleep -Milliseconds 500

  # Edge on hosted Windows can launch a browser subprocess and leave the launcher
  # Process.ExitCode unset even though --dump-dom completed successfully. The actual
  # rendered DOM is the acceptance criterion; process exit is only diagnostic.
  $dom=''
  if(Test-Path $outFile){ $dom=Get-Content $outFile -Raw -Encoding UTF8 }
  if(-not $dom.Contains('data-ugdcms-dom-smoke="pass"')){
    Write-Host '--- Edge stderr (tail) ---'
    Get-Content $errFile -ErrorAction SilentlyContinue | Select-Object -Last 100
    Write-Host '--- dumped DOM ---'
    Write-Host $dom
    $code='unavailable'
    try { if($p.HasExited){ $code=[string]([int]$p.ExitCode) } } catch {}
    throw ('real browser DOM smoke failed; Edge exit='+$code)
  }
  if($dom.Contains('[object HTMLHeadingElement]') -or $dom.Contains('[object HTMLAnchorElement]') -or $dom.Contains('http://127.0.0.1:8765/#/')){
    throw 'browser DOM still contains stringified HTML elements or sidebar URLs'
  }

  $edgeCode='unavailable'
  try { if($p.HasExited){ $edgeCode=[string]([int]$p.ExitCode) } } catch {}
  Write-Host ('FRONTEND EDGE DOM SMOKE PASS: nested sidebar-style arrays render into real headings and links. EdgeExit='+$edgeCode)
}
finally {
  if($server -and -not $server.HasExited){ try { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue } catch {} }
  Remove-Item $testFile,$outFile,$errFile -Force -ErrorAction SilentlyContinue
  Remove-Item $profile -Recurse -Force -ErrorAction SilentlyContinue
}
