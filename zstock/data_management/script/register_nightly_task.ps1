# 注册 Windows 计划任务：每个交易日晚上跑 stockdb 同步 + 因子预计算
# 用法（管理员 PowerShell 可选，一般用户也可）：
#   powershell -ExecutionPolicy Bypass -File zstock/data_management/script/register_nightly_task.ps1
#   powershell -ExecutionPolicy Bypass -File zstock/data_management/script/register_nightly_task.ps1 -Time 21:00
#   powershell -ExecutionPolicy Bypass -File zstock/data_management/script/register_nightly_task.ps1 -Unregister

param(
    [string]$TaskName = "ZStockNightlyPipeline",
    [string]$Time = "21:00",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

if ($Unregister) {
    schtasks /Delete /TN $TaskName /F 2>$null
    Write-Host "已删除计划任务: $TaskName"
    exit 0
}

$Module = "zstock.data_management.script.nightly_pipeline"
$LogDir = Join-Path $RepoRoot "zstock\data_management\script\output\nightly"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 用 cmd 包装，保证工作目录与日志可追踪
$Action = "cmd.exe"
$Arg = "/c cd /d `"$RepoRoot`" && `"$Python`" -m $Module >> `"$LogDir\scheduler.log`" 2>&1"

# 每天 $Time 运行
schtasks /Create /F /TN $TaskName /SC DAILY /ST $Time /RL LIMITED /TR "$Action $Arg" | Out-Host

Write-Host ""
Write-Host "已注册计划任务: $TaskName"
Write-Host "  时间: 每天 $Time"
Write-Host "  目录: $RepoRoot"
Write-Host "  Python: $Python"
Write-Host "  命令: python -m $Module"
Write-Host "  日志: $LogDir"
Write-Host ""
Write-Host "手动试跑:"
Write-Host "  & `"$Python`" -m $Module"
Write-Host "立即触发:"
Write-Host "  schtasks /Run /TN $TaskName"
