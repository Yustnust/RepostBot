# RepostBot 每日运行脚本（供 Windows 任务计划程序调用）
#
# 行为由 .env 决定：
#   DRY_RUN=true  -> 只检查、不真点（默认）
#   DRY_RUN=false -> 满 20 天当天真正置顶
#
# 输出：控制台 + logs/daily_<时间戳>.log
# 抓包：logs/trace_<时间戳>.json（记录网络请求，10-14 真实置顶后可据此分析接口）

$ErrorActionPreference = "Continue"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log   = Join-Path $logDir "daily_$stamp.log"
$trace = Join-Path $logDir "trace_$stamp.json"

# 定位 python：任务计划程序里 PATH 可能不完整，优先用已知安装路径
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"
    if (Test-Path $candidate) { $python = $candidate }
}
if (-not $python) {
    Write-Output "未找到 python，请在脚本中指定完整路径"
    exit 1
}

Write-Output "==== RepostBot $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===="
Write-Output "Python: $python"

& $python src/index.py --browser msedge --trace $trace 2>&1 | Tee-Object -FilePath $log
$code = $LASTEXITCODE

Write-Output "退出码: $code"
exit $code
