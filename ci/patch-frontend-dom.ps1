param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$ui = Join-Path $SourceRoot 'frontend\js\ui.js'
$app = Join-Path $SourceRoot 'frontend\js\app.js'
foreach($p in @($ui,$app)){
  if(-not(Test-Path $p)){ throw ('required frontend file missing: '+$p) }
}

$text = Get-Content $ui -Raw -Encoding UTF8
$new = @'
  const appendKid = (k) => {
    if (Array.isArray(k)) { k.forEach(appendKid); return; }
    if (k === null || k === undefined || k === false) return;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  };
  kids.forEach(appendKid);
'@

$variants = @(
@'
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
'@,
@'
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false) continue;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
'@
)

$patched=$false
foreach($old in $variants){
  if($text.Contains($old)){
    $text=$text.Replace($old,$new)
    $patched=$true
    break
  }
}
if(-not $patched -and -not $text.Contains('const appendKid = (k) => {')){
  Write-Host '--- ui.js relevant lines ---'
  Get-Content $ui -Encoding UTF8 | Select-String -Pattern 'kids|append|createTextNode|instanceof Node' -Context 2,2 | ForEach-Object { Write-Host $_.ToString() }
  throw 'ui.js child append block not found'
}

if($text.Contains('for (const k of kids) {')){ throw 'ui.js still stringifies nested child arrays' }
if($text.Contains('kids.flat()')){ throw 'ui.js still relies on shallow flattening' }
if(-not $text.Contains('Array.isArray(k)')){ throw 'ui.js recursive array child handling missing' }
if(-not $text.Contains('kids.forEach(appendKid)')){ throw 'ui.js recursive child append entrypoint missing' }

# The screenshot defect is produced when NAV group arrays are passed as children and
# the DOM helper converts those arrays to String(), yielding text such as
# [object HTMLHeadingElement],http://localhost:8080/#/....
$appText = Get-Content $app -Raw -Encoding UTF8
if(-not $appText.Contains('NAV.map(g => [')){ throw 'sidebar nested NAV rendering pattern changed; review DOM patch' }
if(-not $appText.Contains('g.items.map(([href, label]) =>')){ throw 'sidebar item rendering pattern changed; review DOM patch' }

[IO.File]::WriteAllText($ui,$text,(New-Object Text.UTF8Encoding($false)))
Write-Host 'FRONTEND DOM PATCH PASS: nested child arrays render as DOM nodes instead of [object HTML*]/URL text.'
