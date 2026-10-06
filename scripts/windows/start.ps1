# Trading-Agent-CN · 启动（Windows 原生运行，不用 Docker）
#
# 双击根目录的 start-windows.cmd，它会开一个新窗口跑这个脚本：
#   - 没装好、或者代码有更新 → 先自动跑 setup.ps1
#   - 已经在运行而且是最新的 → 直接打开浏览器
#   - 已经在运行，但代码、依赖或 .env 变了 → 停掉旧的重新启动（改完 .env 再双击一次就生效）
#   - 启动后端（只听本机 127.0.0.1:8000）和前端（0.0.0.0:3000，手机也能访问），打开浏览器
#   - 窗口一直开着，日志都在里面；关掉窗口或按 Ctrl+C 就是停止
#
# -NoBrowser：不自动打开浏览器（CI 用）

param([switch]$NoBrowser)

. ([IO.Path]::Combine($PSScriptRoot, 'common.ps1'))

$WindowTitle = 'Trading-Agent-CN（关掉这个窗口 = 停止）'
Set-ConsoleTitle $WindowTitle
Disable-QuickEdit
Unblock-Scripts

$version = Get-AppVersion
Write-Host ''
Write-Host "Trading-Agent-CN $version" -ForegroundColor Cyan

function Open-Browser {
    if ($NoBrowser -or -not $OnWindows) { return }
    try {
        Start-Process "http://localhost:$FrontendPort/"
    } catch {
        Write-Caution "没能自动打开浏览器，请手动访问 http://localhost:$FrontendPort"
    }
}

function Wait-Ready([string]$Url, [System.Diagnostics.Process]$Process, [int]$Seconds) {
    # 等到 $Url 能访问。返回 ready / exited（进程自己退出了）/ timeout
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk $Url) { return 'ready' }
        if ($Process.HasExited) { return 'exited' }
        Start-Sleep -Milliseconds 500
    }
    return 'timeout'
}

function Get-StartFailure([string]$What, [System.Diagnostics.Process]$Process, [string]$State) {
    if ($State -eq 'exited') {
        return "${What}没能启动（退出码 $($Process.ExitCode)），原因一般就在上面几行。"
    }
    return "${What}启动超时：等了很久还是访问不到，上面的输出里可能有原因。"
}

function Write-Banner {
    $line = '  ' + ('=' * 60)
    Write-Host ''
    Write-Host $line -ForegroundColor Green
    Write-Host "   Trading-Agent-CN $version 已经启动" -ForegroundColor Green
    Write-Host ''
    Write-Host "   电脑上打开：http://localhost:$FrontendPort"
    $lan = @(Get-LanAddresses)
    if ($lan.Count -gt 0) {
        foreach ($ip in $lan) { Write-Host "   手机上打开（连同一个 Wi-Fi）：http://${ip}:$FrontendPort" }
    } else {
        Write-Host "   手机上打开：在电脑上运行 ipconfig 查到 IPv4 地址，访问 http://那个地址:$FrontendPort"
    }
    Write-Host ''
    Write-Host '   停止：关掉这个窗口，或者在窗口里按 Ctrl+C'
    Write-Host '   用的时候这个窗口要一直开着（可以最小化）'
    Write-Host $line -ForegroundColor Green
    Write-Host '   下面是运行日志。遇到问题时，把这个窗口截图下来最有用。'
    Write-Host ''
}

# ---------------------------------------------------------------- 1. 已经在运行？

$busy = @(@($BackendPort, $FrontendPort) | Where-Object { Test-PortOpen $_ })
if ($busy.Count -gt 0) {
    $healthy = (Test-HttpOk "$ApiBase/api/v1/health") -and
        (Test-HttpOk "http://127.0.0.1:$FrontendPort/api/v1/health")
    if ($healthy -and (Read-Stamp 'running') -eq (Get-RunningKey)) {
        Write-Done '已经在运行了（在另一个标题是 Trading-Agent-CN 的窗口里），直接打开浏览器。'
        Open-Browser
        Start-Sleep -Seconds 4
        exit 0
    }
    if ((Stop-App) -gt 0) {
        Write-Step '已停掉旧的实例：代码、依赖或 .env 有变化，重新启动才会生效'
    }
    foreach ($port in $busy) {
        if (Test-PortOpen $port) { Exit-WithError (Get-PortBusyMessage $port) }
    }
}

# ---------------------------------------------------------------- 2. 没装好的先装

$reasons = @(Get-SetupReasons)
if ($reasons.Count -gt 0) {
    Write-Step ('先安装 / 更新：' + ($reasons -join '；'))
    & $SetupScript
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError '安装没有完成，原因见上面的输出。处理好后再双击 start-windows.cmd。'
    }
    Set-ConsoleTitle $WindowTitle
}
$envProblem = Get-EnvFileProblem
if ($envProblem) { Exit-WithError $envProblem }
$node = Find-Node
if (-not $node) { Exit-WithError '找不到 Node.js。双击 setup-windows.cmd，按提示安装。' }

# ---------------------------------------------------------------- 3. 启动

Write-PidFile
# 后端据此把"改完 .env 怎么生效"之类的提示写成适合这种运行方式的说法
$env:APP_RUNTIME = 'windows'
# 中文 Windows 上 Python 默认按 GBK 读写文件和管道，统一成 UTF-8
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
Set-FrontendEnv
# Next.js 依赖的 http-proxy 在新版 Node 上会打一条 DeprecationWarning，对用户只是噪音
$env:NODE_OPTIONS = ($env:NODE_OPTIONS + ' --no-deprecation').Trim()

$backend = $null
$frontend = $null
$failed = ''
try {
    Write-Step "启动后端（http://127.0.0.1:$BackendPort）"
    # --app-dir 带上绝对路径：stop 脚本靠它认出"这是本工具的进程"
    $uvicornArgs = @('-m', 'uvicorn', 'app.main:app', '--app-dir', $BackendDir,
        '--host', '127.0.0.1', '--port', [string]$BackendPort, '--timeout-graceful-shutdown', '3')
    $backend = Start-Tool $VenvPython $uvicornArgs $BackendDir
    # 给足时间：刚装完第一次启动时，杀毒软件会逐个扫描新装的依赖，导入会慢很多
    $state = Wait-Ready "$ApiBase/api/v1/health" $backend 180
    if ($state -ne 'ready') { throw (Get-StartFailure '后端' $backend $state) }

    if (-not (Read-Stamp 'firewall-hint')) {
        Write-Caution '接下来可能弹出"Windows 安全中心"，询问是否允许 Node.js 访问网络：'
        Write-Caution '只勾"专用网络"，点"允许"。只在这台电脑上用的话，点"取消"也不影响。'
        Write-Stamp 'firewall-hint' '1'
    }
    Write-Step "启动前端（http://localhost:$FrontendPort）"
    $nextArgs = @([IO.Path]::Combine($FrontendDir, $NextBin), 'start', '-H', '0.0.0.0', '-p', [string]$FrontendPort)
    $frontend = Start-Tool $node.Exe $nextArgs $FrontendDir
    # 经前端访问后端的接口：前端起来了、转发也通了，才算真的能用
    $state = Wait-Ready "http://127.0.0.1:$FrontendPort/api/v1/health" $frontend 90
    if ($state -ne 'ready') { throw (Get-StartFailure '前端' $frontend $state) }
    # Next.js 启动时会把窗口标题改成 "next-server (v15...)"，改回来：标题要告诉人"关掉就停了"
    Set-ConsoleTitle $WindowTitle

    Write-Stamp 'running' (Get-RunningKey)
    Open-Browser
    Write-Banner

    while ($true) {
        if ($backend.HasExited) { throw "后端停止了（退出码 $($backend.ExitCode)），原因一般就在上面几行。" }
        if ($frontend.HasExited) { throw "前端停止了（退出码 $($frontend.ExitCode)），原因一般就在上面几行。" }
        Start-Sleep -Milliseconds 500
    }
} catch {
    $failed = $_.Exception.Message
} finally {
    foreach ($p in @($frontend, $backend)) {
        if (-not $p) { continue }
        try {
            # 按 Ctrl+C 时子进程自己也收到了信号，先给它们一点时间正常退出
            if (-not $p.HasExited -and -not $p.WaitForExit(2000)) { Stop-ProcessTree $p.Id }
        } catch { }
    }
    Clear-Stamp 'running'
    $recorded = Read-PidFile
    if ($recorded -and $recorded.ProcessId -eq $PID) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

if ($failed) {
    Write-Host ''
    Write-Problem $failed
    Write-Note '需要求助时，把这个窗口截图（连同上面的输出）发出去。'
    Wait-Key
    exit 1
}
exit 0
