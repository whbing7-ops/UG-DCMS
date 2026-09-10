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
# PowerShell 默认不会把原生子进程退出码作为脚本退出码返回。恢复流程使用
# 专用非零退出码 75 请求 WinSW 重启；必须原样传递，否则 WinSW 会误判为
# “正常停止”，服务不再拉起，恢复界面只能一直显示估算进度。
exit $LASTEXITCODE
