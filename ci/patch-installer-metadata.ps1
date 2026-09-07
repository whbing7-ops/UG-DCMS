param([string]$SourceRoot='src')
$ErrorActionPreference='Stop'

$issPath = Join-Path $SourceRoot 'installer\UG-DCMS-Setup.iss'
if(-not(Test-Path $issPath)){ throw ('installer script missing: ' + $issPath) }

$text = Get-Content $issPath -Raw -Encoding UTF8

# Canonical product identity. Keep the legal/brand identity in one place so a future
# code-signing certificate can replace "UG" with the exact certificate subject name
# without touching the rest of the installer definition.
$defines = @(
  '#define MyAppName "UG-DCMS"',
  '#define MyAppVersion "1.0.0-rc2.10"',
  '#define MyBinaryVersion "1.0.0.0"',
  '#define MyAppPublisher "UG"',
  '#define MyAppURL "https://github.com/whbing7-ops/UG-DCMS"',
  '#define MyAppDescription "UG-DCMS Configuration Management System Setup"',
  '#define MyOriginalFilename "UG-DCMS-Setup-1.0.0-rc2.10.exe"',
  '#define MyCopyright "Copyright (C) 2026 UG. All rights reserved."'
)

# Replace the original header defines as one block to avoid duplicate definitions.
$headerPattern = '(?ms)\A#define MyAppName .*?\r?\n#define MyAppVersion .*?\r?\n#define MyAppPublisher .*?\r?\n#define MyAppURL .*?\r?\n'
if(-not [regex]::IsMatch($text,$headerPattern)){ throw 'unexpected Inno header; metadata block not patchable' }
$text = [regex]::Replace($text,$headerPattern,(($defines -join "`r`n") + "`r`n"),1)

$metadataBlock = @'
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppComments={#MyAppDescription}
AppCopyright={#MyCopyright}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyBinaryVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppDescription}
VersionInfoOriginalFileName={#MyOriginalFilename}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyBinaryVersion}
VersionInfoTextVersion={#MyAppVersion}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoCopyright={#MyCopyright}
'@

# Remove any pre-existing copies of the same directives, then insert the canonical
# metadata block immediately after AppPublisher.
$directiveNames = @(
  'AppVerName','AppPublisherURL','AppSupportURL','AppUpdatesURL','AppComments','AppCopyright',
  'UninstallDisplayName','VersionInfoVersion','VersionInfoCompany','VersionInfoDescription',
  'VersionInfoOriginalFileName','VersionInfoProductName','VersionInfoProductVersion',
  'VersionInfoTextVersion','VersionInfoProductTextVersion','VersionInfoCopyright'
)
foreach($name in $directiveNames){
  $text = [regex]::Replace($text,('(?m)^' + [regex]::Escape($name) + '=.*\r?\n?'),'')
}
$anchor = 'AppPublisher={#MyAppPublisher}'
if(-not $text.Contains($anchor)){ throw 'AppPublisher anchor missing' }
$text = $text.Replace($anchor,($anchor + "`r`n" + $metadataBlock.TrimEnd()))

# Normalize hard-coded product paths/names where safe. This does not alter behavior.
$text = $text.Replace('DefaultDirName={commonappdata}\UG-DCMS','DefaultDirName={commonappdata}\{#MyAppName}')
$text = $text.Replace('DefaultGroupName=UG-DCMS','DefaultGroupName={#MyAppName}')
$text = $text.Replace('OutputBaseFilename=UG-DCMS-Setup-1.0.0-rc2.10','OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}')

$utf8Bom = New-Object Text.UTF8Encoding($true)
[IO.File]::WriteAllText($issPath,$text,$utf8Bom)

# Static contract: every user-visible Windows version-resource field must exist exactly
# once and contain the canonical value.
$checks = [ordered]@{
  'AppName'='AppName={#MyAppName}'
  'AppVersion'='AppVersion={#MyAppVersion}'
  'AppPublisher'='AppPublisher={#MyAppPublisher}'
  'AppVerName'='AppVerName={#MyAppName} {#MyAppVersion}'
  'AppPublisherURL'='AppPublisherURL={#MyAppURL}'
  'AppSupportURL'='AppSupportURL={#MyAppURL}'
  'AppUpdatesURL'='AppUpdatesURL={#MyAppURL}'
  'VersionInfoVersion'='VersionInfoVersion={#MyBinaryVersion}'
  'VersionInfoCompany'='VersionInfoCompany={#MyAppPublisher}'
  'VersionInfoDescription'='VersionInfoDescription={#MyAppDescription}'
  'VersionInfoOriginalFileName'='VersionInfoOriginalFileName={#MyOriginalFilename}'
  'VersionInfoProductName'='VersionInfoProductName={#MyAppName}'
  'VersionInfoProductVersion'='VersionInfoProductVersion={#MyBinaryVersion}'
  'VersionInfoTextVersion'='VersionInfoTextVersion={#MyAppVersion}'
  'VersionInfoProductTextVersion'='VersionInfoProductTextVersion={#MyAppVersion}'
  'VersionInfoCopyright'='VersionInfoCopyright={#MyCopyright}'
  'UninstallDisplayName'='UninstallDisplayName={#MyAppName} {#MyAppVersion}'
}
foreach($kv in $checks.GetEnumerator()){
  $count = ([regex]::Matches($text,[regex]::Escape($kv.Value))).Count
  if($count -ne 1){ throw ($kv.Key + ' metadata check failed; expected exactly one occurrence, got ' + $count) }
}

Write-Host 'INSTALLER METADATA PATCH PASS: Publisher/Company/Product/File/ProductVersion/TextVersion/URLs/copyright normalized.'
