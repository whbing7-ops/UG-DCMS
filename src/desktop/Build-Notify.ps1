$ErrorActionPreference='Stop'
$out=Join-Path $PSScriptRoot 'output'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$csc=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(-not(Test-Path $csc)){throw '.NET Framework C# compiler missing'}
& $csc /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll "/out:$out\UG-DCMS-Notify-rc2.41.exe" (Join-Path $PSScriptRoot 'Notify.cs')
if($LASTEXITCODE -ne 0){throw 'Windows notification assistant compilation failed'}
