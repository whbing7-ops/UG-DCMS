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

# Match both LF and CRLF sources and both the original shallow-flat loop and a
# possible unflattened loop. The original helper uses kids.flat(), which only
# flattens one level; the sidebar passes arrays nested two levels deep.
$pattern = '(?ms)^\s{2}for \(const k of kids(?:\.flat\(\))?\) \{\r?\n\s{4}if \(k === null \|\| k === undefined \|\| k === false\) continue;\r?\n\s{4}n\.append\(k instanceof Node \? k : document\.createTextNode\(String\(k\)\)\);\r?\n\s{2}\}'
if(-not $text.Contains('const appendKid = (k) => {')){
  $next = [regex]::Replace($text,$pattern,[System.Text.RegularExpressions.MatchEvaluator]{ param($m) $new },1)
  if($next -eq $text){
    Write-Host '--- ui.js relevant lines ---'
    Get-Content $ui -Encoding UTF8 | Select-String -Pattern 'kids|append|createTextNode|instanceof Node' -Context 2,2 | ForEach-Object { Write-Host $_.ToString() }
    throw 'ui.js child append block not found'
  }
  $text=$next
}

if($text -match 'for \(const k of kids(?:\.flat\(\))?\)'){ throw 'ui.js still contains legacy child loop' }
if(-not $text.Contains('Array.isArray(k)')){ throw 'ui.js recursive array child handling missing' }
if(-not $text.Contains('kids.forEach(appendKid)')){ throw 'ui.js recursive child append entrypoint missing' }

# The screenshot defect is produced when NAV group arrays remain nested after one
# Array.flat() level and are converted with String(), yielding text like
# [object HTMLHeadingElement],http://localhost:8080/#/....
$appText = Get-Content $app -Raw -Encoding UTF8
if(-not ($appText.Contains('NAV.map(g => [') -or $appText.Contains('NAV.map((g) => ['))){
  throw 'sidebar nested NAV rendering pattern changed; review DOM patch'
}
if(-not $appText.Contains('g.items.map(([href, label]) =>')){ throw 'sidebar item rendering pattern changed; review DOM patch' }

[IO.File]::WriteAllText($ui,$text,(New-Object Text.UTF8Encoding($false)))
Write-Host 'FRONTEND DOM PATCH PASS: nested child arrays recursively render as DOM nodes instead of [object HTML*]/URL text.'
