# 一键准备缺陷报告：把正文塞进剪贴板，并打开 Discussions 新建页。
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File tools\send_report.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$path = Join-Path $root 'reports\submit\discussion-post.md'

if (-not (Test-Path $path)) { throw "找不到 $path" }

$md = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
$idx = $md.IndexOf('# 正文')
if ($idx -ge 0) {
    $body = $md.Substring($idx)
    $nl = $body.IndexOf("`n")
    $body = $body.Substring($nl + 1).Trim()
} else {
    $body = $md.Trim()
}

$titleLine = ($md -split "`n" | Where-Object { $_ -match '^\[Bug\]' } | Select-Object -First 1)
if (-not $titleLine) { $titleLine = '（标题见 discussion-post.md）' }

Set-Clipboard -Value $body

Write-Host ""
Write-Host "  正文已复制到剪贴板（$($body.Length) 字符）"
Write-Host "  标题（需另外粘贴）："
Write-Host "  $titleLine"
Write-Host ""
Write-Host "  马上打开 GitHub Discussions 新建页：粘标题 → 粘正文 → 提交"
Write-Host ""

Start-Process 'https://github.com/deepseek-ai/deepseek-harness/discussions/new'
Write-Host "  已打开浏览器。"
Write-Host ""
Write-Host "  备用渠道（应用内）：在对话框输入 /feedback，粘同一份正文。"
