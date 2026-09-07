param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$ui = Join-Path $SourceRoot 'frontend\js\ui.js'
$app = Join-Path $SourceRoot 'frontend\js\app.js'
foreach($p in @($ui,$app)){
  if(-not(Test-Path $p)){ throw ('required frontend file missing: '+$p) }
}

$text = Get-Content $ui -Raw -Encoding UTF8
$old = @'
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false) continue;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
'@
$new = @'
  const appendKid = (k) => {
    if (Array.isArray(k)) { k.forEach(appendKid); return; }
    if (k === null || k === undefined || k === false) return;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  };
  kids.forEach(appendKid);
'@

if($text.Contains($old)){
  $text = $text.Replace($old,$new)
} elseif(-not $text.Contains('const appendKid = (k) => {')) {
  throw 'ui.js child append block not found'
}

if($text.Contains('kids.flat()')){ throw 'ui.js still performs only one-level child flattening' }
if(-not $text.Contains('Array.isArray(k)')){ throw 'ui.js recursive array child handling missing' }
if(-not $text.Contains('kids.forEach(appendKid)')){ throw 'ui.js recursive child append entrypoint missing' }

# The sidebar intentionally passes nested arrays: NAV.map(group => [heading, items.map(...)])
# so this is a required rendering contract, not merely an implementation preference.
$appText = Get-Content $app -Raw -Encoding UTF8
if(-not $appText.Contains('NAV.map(g => [')){ throw 'sidebar nested NAV rendering pattern changed; review DOM patch' }
if(-not $appText.Contains('g.items.map(([href, label]) =>')){ throw 'sidebar item rendering pattern changed; review DOM patch' }

[IO.File]::WriteAllText($ui,$text,(New-Object Text.UTF8Encoding($false)))
Write-Host 'FRONTEND DOM PATCH PASS: nested child arrays render as DOM nodes instead of [object HTML*] text.'
