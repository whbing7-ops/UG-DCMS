# 用 Word 更新目录域、导出 PDF。分两趟跑:
#   1) 不带 -NoSave: 更新目录域并保存(此时 Word 会把标题颜色按主题刷回蓝色)
#   2) 改完颜色后带 -NoSave 再跑: 只导 PDF, 不回存, 颜色得以保留
#
# 参数只收 ASCII: 文件名含中文时, 经 bash → powershell.exe 传参会被按 ANSI 解码而损坏,
# 所以这里收一个 ASCII 前缀通配, 由 PowerShell 自己在本地编码下解析出真实文件名。
param([Parameter(Mandatory=$true)][string]$Pattern,
      [Parameter(Mandatory=$true)][string]$Pdf,
      [switch]$NoSave)
$f = (Get-ChildItem $Pattern | Select-Object -First 1).FullName
if (-not $f) { throw "no file matches $Pattern" }
$w = New-Object -ComObject Word.Application
$w.Visible = $false; $w.DisplayAlerts = 0
$d = $w.Documents.Open($f, $false, $NoSave.IsPresent)
if (-not $NoSave.IsPresent) {
  $d.Fields.Update() | Out-Null
  foreach ($t in $d.TablesOfContents) { $t.Update() | Out-Null }
}
$d.Repaginate()
'pages=' + $d.ComputeStatistics(2)
if (-not $NoSave.IsPresent) { $d.SaveAs2($f) }
$d.ExportAsFixedFormat($Pdf, 17)
$d.Close($false); $w.Quit(); 'ok'
