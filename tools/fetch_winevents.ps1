# 抓 Windows 系统事件到 data\winevents.json（判定器的三闸门要用）
# 用法: pwsh -NoProfile -File tools\fetch_winevents.ps1 [-Days 12]
param([int]$Days = 12)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$out  = Join-Path $root 'data\winevents.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null

# 12/6005 开机；1074/6006/13/41 关机；42 睡眠
$ids = 12, 1074, 6006, 13, 41, 42, 6005

$ev = Get-WinEvent -FilterHashtable @{
        LogName   = 'System'
        Id        = $ids
        StartTime = (Get-Date).AddDays(-$Days)
      } -ErrorAction SilentlyContinue |
      Sort-Object TimeCreated |
      ForEach-Object {
        [pscustomobject]@{
          t  = $_.TimeCreated.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
          id = $_.Id
        }
      }

if (-not $ev) { throw "没有抓到任何事件，检查 System 日志权限" }

$json = ConvertTo-Json -InputObject @($ev) -Compress
# 必须 UTF-8 无 BOM：带 BOM 的 JSON 会让下游解析失败（本项目 10-17 那次崩溃就是 BOM 坑）
[System.IO.File]::WriteAllText($out, $json, (New-Object System.Text.UTF8Encoding($false)))

"写出 {0} 条事件 -> {1}" -f $ev.Count, $out
$ev | Group-Object id | Sort-Object Count -Descending | ForEach-Object { "  id={0,-5} {1}" -f $_.Name, $_.Count }
