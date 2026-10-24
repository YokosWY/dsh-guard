# 同步插件：把仓库里的 plugin/ 覆盖到 DSH 的 local-plugins。
# 仓库是唯一真源；DSH 侧只是运行副本，别在那边直接改。
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File tools\sync-plugin.ps1 [-Force]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$src  = Join-Path $root 'plugin'

$home_ = $env:DSH_HOME
if (-not $home_) {
    $cfg = Join-Path $root 'config.json'
    if (Test-Path $cfg) {
        $home_ = (Get-Content $cfg -Raw | ConvertFrom-Json).home
    }
}
if (-not $home_) {
    # 和 paths.py 同一条思路：认 logs\desktop-next*.jsonl
    $cands = @("$env:USERPROFILE\.dsh", 'D:\AI\DSH-NEXT', 'C:\DSH')
    foreach ($c in $cands) {
        if (Test-Path (Join-Path $c 'logs')) { $home_ = $c; break }
    }
}
if (-not $home_) { throw 'cannot locate DSH home; set DSH_HOME or config.json home' }

$dst = Join-Path $home_ 'local-plugins\dsh-guard-ui'
if (-not (Test-Path $src)) { throw "missing $src" }

Write-Host "source: $src"
Write-Host "target: $dst"
if ((Test-Path $dst) -and -not $Force) {
    Write-Host "target exists; re-run with -Force to overwrite" -ForegroundColor Yellow
    exit 1
}

New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
Write-Host "copied. Restart DSH (or Ctrl+R in the window) for the client half to reload."
