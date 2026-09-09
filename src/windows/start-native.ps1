﻿param([int]$Port=8080)
$ErrorActionPreference="Stop"
$InstallDir=Split-Path -Parent $MyInvocation.MyCommand.Path
Get-Content "$InstallDir\.env" | ForEach-Object { if($_ -match '^([^#=]+)=(.*)$'){ [Environment]::SetEnvironmentVariable($matches[1],$matches[2],'Process') } }

$runtimeFile = Join-Path $InstallDir 'runtime\CURRENT-RUNTIME.txt'
$releaseFile = Join-Path $InstallDir 'releases\CURRENT-RELEASE.txt'
if(-not (Test-Path $runtimeFile)){ throw "UG-DCMS runtime pointer missing: $runtimeFile" }
if(-not (Test-Path $releaseFile)){ throw "UG-DCMS release pointer missing: $releaseFile" }
$runtimePath = (Get-Content $runtimeFile -Raw).Trim()
$releasePath = (Get-Content $releaseFile -Raw).Trim()
if(-not [IO.Path]::IsPathRooted($runtimePath)){ $runtimePath = Join-Path (Join-Path $InstallDir 'runtime') $runtimePath }
if(-not [IO.Path]::IsPathRooted($releasePath)){ $releasePath = Join-Path (Join-Path $InstallDir 'releases') $releasePath }
$python = Join-Path $runtimePath 'Scripts\python.exe'
$backend = Join-Path $releasePath 'backend'
if(-not (Test-Path $python)){ throw "UG-DCMS runtime python missing: $python" }
if(-not (Test-Path (Join-Path $backend 'app\main.py'))){ throw "UG-DCMS release backend missing: $backend" }
Set-Location $backend
& $python -m uvicorn app.main:app --host 0.0.0.0 --port $Port
