# Trading-Agent-CN · 停止
#
# 平时直接关掉 start-windows.cmd 打开的那个窗口就行。这个脚本用在：
# 那个窗口找不到了，或者提示端口被占用时，把本工具留下的后端 / 前端进程清掉。
# 只停本工具的进程；端口被别的程序占着时只报告，不动它。

. ([IO.Path]::Combine($PSScriptRoot, 'common.ps1'))

Set-ConsoleTitle 'Trading-Agent-CN · 停止'
Write-Host ''
Write-Host 'Trading-Agent-CN · 停止' -ForegroundColor Cyan

if ((Stop-App) -gt 0) {
    Write-Done '已停止。'
} else {
    Write-Done '没有在运行的 Trading-Agent-CN。'
}
foreach ($port in @($BackendPort, $FrontendPort)) {
    if (Test-PortOpen $port) { Write-Caution (Get-PortBusyMessage $port) }
}
exit 0
