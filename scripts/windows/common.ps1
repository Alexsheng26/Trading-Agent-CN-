# Trading-Agent-CN · Windows 原生运行（不用 Docker）的公共部分
#
# setup.ps1 / start.ps1 / stop.ps1 开头都会 dot-source 这个文件。
#
# 兼容性约定 —— 这些脚本要在 Win10/11 自带的 Windows PowerShell 5.1 上跑：
#   - 不用 PowerShell 7 才有的语法：?? 、三元 ? : 、管道链 && 和 ||
#   - Join-Path 只给两个参数；多段路径用 [IO.Path]::Combine
#   - 外部程序用 ProcessStartInfo 启动（Start-Tool）。5.1 的 Start-Process 不会给
#     带空格的参数加引号；用 & 直接调的话，stderr 又会被包装成一行行红色的"错误"
#   - 不设 $ErrorActionPreference = 'Stop'：5.1 下外部程序往 stderr 写一行
#     （pip 的普通警告就会）都可能被当成致命错误。外部程序一律看退出码
#   - 文件存成 UTF-8 带 BOM：5.1 读不带 BOM 的脚本时按系统代码页（中文系统是 GBK）
#     解码，中文会变成乱码，严重时吞掉引号，整个脚本语法错误
#   - 字符串里变量后面紧跟中文时写成 ${name} 或 $()：中文算"字母"，
#     "$version已启动" 会被当成一个叫 version已启动 的变量
#
# $OnWindows 为假的分支只用来在 Linux 上自测脚本逻辑，用户用不到。

$ErrorActionPreference = 'Continue'

$OnWindows = ($env:OS -eq 'Windows_NT')

$Root = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, '..', '..'))
$BackendDir = [IO.Path]::Combine($Root, 'backend')
$FrontendDir = [IO.Path]::Combine($Root, 'frontend')
$RunDir = [IO.Path]::Combine($Root, '.run')
$PidFile = [IO.Path]::Combine($RunDir, 'app.pid')
$EnvFile = [IO.Path]::Combine($Root, '.env')
$EnvExample = [IO.Path]::Combine($Root, '.env.example')
$SetupScript = [IO.Path]::Combine($PSScriptRoot, 'setup.ps1')

$VenvDir = [IO.Path]::Combine($BackendDir, '.venv')
if ($OnWindows) {
    $VenvPython = [IO.Path]::Combine($VenvDir, 'Scripts', 'python.exe')
} else {
    $VenvPython = [IO.Path]::Combine($VenvDir, 'bin', 'python')
}
# 相对 frontend 目录。运行时直接用 node 跑 next，不需要 pnpm
$NextBin = [IO.Path]::Combine('node_modules', 'next', 'dist', 'bin', 'next')
$NextBuildId = [IO.Path]::Combine($FrontendDir, '.next', 'BUILD_ID')

$BackendPort = 8000
$FrontendPort = 3000
# 后端只听本机：手机和别的电脑只访问前端，由前端在服务端把 /api/* 转发过来。
# 写 127.0.0.1 而不是 localhost：Windows 上 localhost 可能先解析成 IPv6 的 ::1，
# 而后端只听 IPv4，转发就会连不上
$ApiBase = "http://127.0.0.1:$BackendPort"
# 和 Docker 镜像装的一样（backend/Dockerfile）
$BackendExtras = '.[akshare,tushare,openai]'
$MinPython = [version]'3.11'
# Next.js 15 的最低要求
$MinNode = [version]'18.18.0'
# 官方源装失败时换国内镜像再试一次
$PipMirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$NpmMirror = 'https://registry.npmmirror.com'
# 前端构建的输入：这些变了就要重新构建。next-env.d.ts 不算 —— 它是构建时生成的
$FrontendBuildInputs = @(
    'frontend/src', 'frontend/public', 'frontend/package.json', 'frontend/pnpm-lock.yaml',
    'frontend/next.config.ts', 'frontend/tsconfig.json', 'frontend/postcss.config.mjs'
)

# ---------------------------------------------------------------- 输出

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Done([string]$Text) { Write-Host "   $Text" -ForegroundColor Green }

function Write-Note([string]$Text) { Write-Host "   $Text" }

function Write-Caution([string]$Text) { Write-Host "   $Text" -ForegroundColor Yellow }

function Write-Problem([string]$Text) { Write-Host "   $Text" -ForegroundColor Red }

function Set-ConsoleTitle([string]$Title) {
    try { $Host.UI.RawUI.WindowTitle = $Title } catch { }
}

function Test-Interactive {
    # 被 CI 或别的程序调用时（输入被重定向）不能停下来等人按键
    try { return -not [Console]::IsInputRedirected } catch { return $true }
}

function Wait-Key {
    if (-not (Test-Interactive)) { return }
    Write-Host ''
    # -NonInteractive 启动时 Read-Host 会抛错，吞掉就行
    try { [void](Read-Host '按回车关闭这个窗口') } catch { }
}

function Exit-WithError([string]$Message) {
    Write-Host ''
    Write-Problem $Message
    Wait-Key
    exit 1
}

function Disable-QuickEdit {
    # 控制台的"快速编辑模式"下，鼠标在窗口里点一下就会进入选择状态，往窗口写输出的
    # 程序随即被**挂起** —— 后端写不出日志、整个卡住，网页上表现为"分析一直转圈"。
    # 按 Esc 能恢复，但谁也想不到是这个原因。这里只关掉本窗口的快速编辑，不改系统设置
    if (-not $OnWindows) { return }
    try {
        if (-not ('TradingAgentCN.ConsoleMode' -as [type])) {
            Add-Type -Namespace TradingAgentCN -Name ConsoleMode -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern IntPtr GetStdHandle(int nStdHandle);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
'@
        }
        $handle = [TradingAgentCN.ConsoleMode]::GetStdHandle(-10)  # STD_INPUT_HANDLE
        $mode = [uint32]0
        if ([TradingAgentCN.ConsoleMode]::GetConsoleMode($handle, [ref]$mode)) {
            # 去掉 ENABLE_QUICK_EDIT_MODE(0x40)；必须同时带上 ENABLE_EXTENDED_FLAGS(0x80) 才生效
            $new = [uint32](([int64]$mode -band (-bnot [int64]0x40)) -bor 0x80)
            [void][TradingAgentCN.ConsoleMode]::SetConsoleMode($handle, $new)
        }
    } catch { }
}

# ---------------------------------------------------------------- 外部程序

function ConvertTo-CommandLine([string[]]$Arguments) {
    # 按 Windows 命令行的解析规则（CommandLineToArgvW）拼参数：带空格或引号的加引号转义。
    # .NET 在 Linux 上解析 ProcessStartInfo.Arguments 用的也是这套规则
    $parts = foreach ($a in $Arguments) {
        if ($a -eq '') {
            '""'
        } elseif ($a -notmatch '[\s"]') {
            $a
        } else {
            $escaped = $a -replace '(\\*)"', '$1$1\"'
            $escaped = $escaped -replace '(\\+)$', '$1$1'
            '"' + $escaped + '"'
        }
    }
    return ($parts -join ' ')
}

function Start-Tool([string]$Exe, [string[]]$Arguments, [string]$WorkDir, [string]$RawArguments = '') {
    # 子进程直接用当前控制台，不经过 PowerShell 管道：pip / pnpm 的进度条照常显示，
    # 关掉窗口时它们也跟着结束
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    if ($RawArguments) {
        $psi.Arguments = $RawArguments
    } else {
        $psi.Arguments = ConvertTo-CommandLine $Arguments
    }
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    return [System.Diagnostics.Process]::Start($psi)
}

function Invoke-Tool([string]$Exe, [string[]]$Arguments, [string]$WorkDir, [string]$RawArguments = '') {
    # 跑完返回退出码。程序本身起不来（找不到文件等）返回 127
    try {
        $p = Start-Tool $Exe $Arguments $WorkDir $RawArguments
    } catch {
        Write-Problem "启动 $Exe 失败：$($_.Exception.Message)"
        return 127
    }
    $p.WaitForExit()
    return $p.ExitCode
}

function Invoke-Npx([string[]]$Arguments, [string]$WorkDir) {
    if ($OnWindows) {
        # npx 在 Windows 上是 npx.cmd，批处理要经过 cmd.exe 才能跑。
        # /s /c "..."：cmd 去掉最外层那对引号，里面原样执行。这里的参数都没有 cmd 的特殊字符
        $inner = 'npx ' + (ConvertTo-CommandLine $Arguments)
        return Invoke-Tool $env:ComSpec @() $WorkDir ('/d /s /c "' + $inner + '"')
    }
    $npx = Get-Command npx -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $npx) {
        Write-Problem '找不到 npx（它和 Node.js 一起安装）'
        return 127
    }
    return Invoke-Tool $npx.Path $Arguments $WorkDir
}

function Stop-ProcessTree([int]$ProcessId) {
    if ($OnWindows) {
        # /T 连子进程一起：venv 里的 python.exe 只是个启动器，真正干活的是它的子进程
        & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
    } else {
        try { [System.Diagnostics.Process]::GetProcessById($ProcessId).Kill($true) } catch { }
    }
}

function Get-ProcessStartTicks([int]$ProcessId) {
    # 进程的启动时刻（UTC ticks），查不到返回 0。和 PID 合起来才能认准一个进程：PID 会被系统复用
    try {
        return [int64](Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime().Ticks
    } catch {
        return [int64]0
    }
}

function Write-PidFile {
    # 记下启动窗口自己：PID + 启动时刻。stop / 重启时靠它找到整个窗口，连同后端、前端一起结束
    Initialize-RunDir
    [IO.File]::WriteAllText($PidFile, "$PID $(Get-ProcessStartTicks $PID)")
}

function Read-PidFile {
    # 返回 @{ProcessId; StartTicks}；没有或内容不对返回 $null
    if (-not (Test-Path -LiteralPath $PidFile)) { return $null }
    try {
        $parts = -split ([IO.File]::ReadAllText($PidFile))
        if ($parts.Count -ne 2) { return $null }
        return [pscustomobject]@{ ProcessId = [int]$parts[0]; StartTicks = [int64]$parts[1] }
    } catch {
        return $null
    }
}

function Get-ProcessCommandLine([int]$ProcessId) {
    if ($OnWindows) {
        try {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
            if ($p) { return [string]$p.CommandLine }
        } catch { }
        return ''
    }
    $f = "/proc/$ProcessId/cmdline"
    if (Test-Path -LiteralPath $f) {
        try { return ([IO.File]::ReadAllText($f)) -replace "`0", ' ' } catch { }
    }
    return ''
}

function Update-PathFromRegistry {
    # winget 装好的程序写进的是注册表里的 PATH，当前窗口还看不到 —— 补读一遍
    if (-not $OnWindows) { return }
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = $env:Path + ';' + $machine + ';' + $user
}

function Install-WithWinget([string]$Id, [string]$What) {
    # 问一句再装。没有 winget（老版本 Win10）或者不是交互运行时返回 $false，由调用方给出手动安装的说明
    if (-not $OnWindows -or -not (Test-Interactive)) { return $false }
    $winget = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) { return $false }
    $answer = ''
    try { $answer = Read-Host "   要现在自动安装 $What 吗？中途可能弹一次管理员确认 [Y/n]" } catch { return $false }
    if ($answer -and $answer.Trim() -notmatch '^(y|yes|是|好)$') { return $false }
    $wingetArgs = @('install', '--id', $Id, '-e', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements')
    [void](Invoke-Tool $winget.Path $wingetArgs $Root)
    Update-PathFromRegistry
    # 退出码不可靠（比如"已经装过"也是非零），由调用方重新检测
    return $true
}

# ---------------------------------------------------------------- Python / Node

# 探测解释器用的一行 Python。不能含双引号（5.1 传给外部程序时会把它弄丢），也不用 %。
# 路径按 UTF-8 转成十六进制再打印：用户名是中文时，路径经过控制台代码页一转就乱了
$PythonProbe = "import sys,struct,sysconfig;print('.'.join(map(str,sys.version_info[:3])));" +
    "print(struct.calcsize('P')*8);print(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0);" +
    "print(sys.executable.encode('utf-8').hex())"

function Get-PythonInfo([string]$Exe, [string[]]$Prefix) {
    $argv = New-Object System.Collections.Generic.List[string]
    foreach ($a in $Prefix) { if ($a) { $argv.Add($a) } }
    $argv.Add('-c')
    $argv.Add($PythonProbe)
    $arr = $argv.ToArray()
    try {
        $out = @(& $Exe @arr 2>$null)
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or $out.Count -lt 4) { return $null }
    $ver = $null
    if (-not [version]::TryParse(([string]$out[0]).Trim(), [ref]$ver)) { return $null }
    try {
        $hex = ([string]$out[3]).Trim()
        $bytes = New-Object byte[] ([int]($hex.Length / 2))
        for ($i = 0; $i -lt $bytes.Length; $i++) {
            $bytes[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16)
        }
        $path = [Text.Encoding]::UTF8.GetString($bytes)
    } catch { return $null }
    # 不用 Test-Path 再核对：Microsoft Store 版的路径是"应用执行别名"，5.1 的 Test-Path 认不出来
    if (-not $path) { return $null }
    return [pscustomobject]@{
        Exe          = $path
        Version      = $ver
        Bits         = ([string]$out[1]).Trim()
        FreeThreaded = (([string]$out[2]).Trim() -eq '1')
    }
}

function Get-PythonRank($Info) {
    # 优先用验证过的 3.11 ~ 3.14，其中越新越好；更新的版本（3.15+）依赖未必都有安装包，排在后面
    $tested = 0
    if ($Info.Version.Major -eq 3 -and $Info.Version.Minor -le 14) { $tested = 1 }
    return $tested * 1000 + $Info.Version.Minor
}

function Find-Python {
    # 找一个能用的 Python：3.11 及以上、64 位、不是自由线程版。找不到返回 $null。
    # 依次试 py 启动器、PATH 里的 python / python3、常见安装位置（装的时候没勾"加入 PATH"也能找到）。
    # Microsoft Store 那个 python 占位程序带参数运行时只会报错退出，不会弹出商店，可以放心试
    $candidates = New-Object System.Collections.Generic.List[object]
    $py = Get-Command py -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like '*.exe' } | Select-Object -First 1
    if ($py) { $candidates.Add(@($py.Path, '-3')) }
    foreach ($name in @('python', 'python3')) {
        foreach ($cmd in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            # 只要 .exe：pyenv-win 之类的 .bat 包装要经过 cmd，参数会被改写
            if (-not $OnWindows -or $cmd.Path -like '*.exe') { $candidates.Add(@($cmd.Path)) }
        }
    }
    if ($OnWindows) {
        $patterns = @(
            [IO.Path]::Combine($env:LOCALAPPDATA, 'Programs', 'Python', 'Python3*', 'python.exe'),
            [IO.Path]::Combine($env:LOCALAPPDATA, 'Python', 'pythoncore-3*', 'python.exe'),
            [IO.Path]::Combine($env:ProgramFiles, 'Python3*', 'python.exe')
        )
        foreach ($pattern in $patterns) {
            foreach ($f in @(Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue)) {
                $candidates.Add(@($f.FullName))
            }
        }
    }

    $seen = @{}
    $best = $null
    foreach ($c in $candidates) {
        $info = Get-PythonInfo $c[0] @($c | Select-Object -Skip 1)
        if (-not $info -or $seen.ContainsKey($info.Exe)) { continue }
        $seen[$info.Exe] = $true
        $label = "Python $($info.Version)（$($info.Exe)）"
        if ($info.Version -lt $MinPython) {
            Write-Note "跳过 ${label}：需要 3.11 或更新"
            continue
        }
        if ($info.Bits -ne '64') {
            Write-Note "跳过 ${label}：是 32 位的，很多依赖没有 32 位安装包"
            continue
        }
        if ($info.FreeThreaded) {
            Write-Note "跳过 ${label}：是自由线程版，部分依赖不支持"
            continue
        }
        if (-not $best -or (Get-PythonRank $info) -gt (Get-PythonRank $best)) { $best = $info }
    }
    return $best
}

function Test-VenvPython {
    # 后端环境能不能用。卸载或挪动过原来的 Python 之后，venv 会失效
    if (-not (Test-Path -LiteralPath $VenvPython)) { return $false }
    $out = @(& $VenvPython -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $out.Count -lt 1) { return $false }
    $n = 0
    return ([int]::TryParse(([string]$out[0]).Trim(), [ref]$n) -and $n -ge 311)
}

function Find-Node {
    $cmd = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { return $null }
    $out = @(& $cmd.Path --version 2>$null)
    if ($LASTEXITCODE -ne 0 -or $out.Count -lt 1) { return $null }
    $text = ([string]$out[0]).Trim().TrimStart('v')
    $ver = $null
    if (-not [version]::TryParse(($text -split '-')[0], [ref]$ver)) { return $null }
    return [pscustomobject]@{ Exe = $cmd.Path; Version = $ver }
}

function Get-PnpmSpec {
    # 和 Docker、CI 用同一个 pnpm 版本：读 package.json 的 packageManager 字段
    try {
        $file = [IO.Path]::Combine($FrontendDir, 'package.json')
        $pkg = [IO.File]::ReadAllText($file) | ConvertFrom-Json
        $spec = ([string]$pkg.packageManager -split '\+')[0]
        if ($spec -match '^pnpm@\d+\.\d+\.\d+$') { return $spec }
    } catch { }
    return 'pnpm@10.33.0'
}

function Set-FrontendEnv {
    # next.config.ts 构建时就把 /api/* 的转发目标定死了，构建和运行都设成同一个值。
    # 根目录 .env 里的 NEXT_PUBLIC_API_BASE_URL 管不到这里（Next 只读 frontend 目录下的 .env）
    $env:NEXT_PUBLIC_API_BASE_URL = $ApiBase
    $env:NEXT_TELEMETRY_DISABLED = '1'
}

function Get-AppVersion {
    try {
        $text = [IO.File]::ReadAllText([IO.Path]::Combine($BackendDir, 'app', 'core', 'version.py'))
        if ($text -match 'APP_VERSION\s*=\s*"([^"]+)"') { return $Matches[1] }
    } catch { }
    return ''
}

function Get-EnvFileProblem {
    # 后端按 UTF-8 读 .env。用记事本存成 ANSI（中文系统上就是 GBK）的话，
    # 只要里面有中文（哪怕只在注释里），后端一启动就报 UnicodeDecodeError
    if (-not (Test-Path -LiteralPath $EnvFile)) { return '' }
    try {
        $bytes = [IO.File]::ReadAllBytes($EnvFile)
        $strict = New-Object System.Text.UTF8Encoding($false, $true)
        [void]$strict.GetString($bytes)
        return ''
    } catch {
        return ".env 不是 UTF-8 编码，后端读不了。用记事本打开 .env → 文件 → 另存为，" +
            "最下面的「编码」选 UTF-8，保存覆盖原文件，然后再双击 start-windows.cmd。"
    }
}

# ---------------------------------------------------------------- 状态记录（.run 目录）

function Remove-Directory([string]$Path) {
    # 删掉整个目录，删干净了返回 $true。文件被占用时（比如网站还开着）会删不干净
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    }
    return -not (Test-Path -LiteralPath $Path)
}

function Initialize-RunDir {
    if (-not (Test-Path -LiteralPath $RunDir)) { [void](New-Item -ItemType Directory -Path $RunDir -Force) }
}

function Read-Stamp([string]$Name) {
    $f = [IO.Path]::Combine($RunDir, "$Name.stamp")
    if (Test-Path -LiteralPath $f) {
        try { return ([IO.File]::ReadAllText($f)).Trim() } catch { }
    }
    return ''
}

function Write-Stamp([string]$Name, [string]$Value) {
    Initialize-RunDir
    [IO.File]::WriteAllText([IO.Path]::Combine($RunDir, "$Name.stamp"), $Value)
}

function Clear-Stamp([string]$Name) {
    Remove-Item -LiteralPath ([IO.Path]::Combine($RunDir, "$Name.stamp")) -Force -ErrorAction SilentlyContinue
}

function Get-TreeHash([string[]]$Paths) {
    # 一组文件 / 目录（相对项目根目录）的内容指纹。只看内容不看修改时间：
    # 解压新版本覆盖旧文件夹时，没改过的文件时间戳也会变
    $entries = New-Object System.Collections.Generic.List[string]
    foreach ($p in $Paths) {
        $full = [IO.Path]::Combine($Root, ($p -replace '/', [IO.Path]::DirectorySeparatorChar))
        if (Test-Path -LiteralPath $full -PathType Container) {
            $files = @(Get-ChildItem -LiteralPath $full -Recurse -File -Force |
                Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' })
        } elseif (Test-Path -LiteralPath $full -PathType Leaf) {
            $files = @(Get-Item -LiteralPath $full -Force)
        } else {
            $entries.Add("(missing) $p")
            continue
        }
        foreach ($f in $files) {
            $rel = $f.FullName.Substring($Root.Length).TrimStart('\', '/') -replace '\\', '/'
            $entries.Add($rel + ' ' + (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash)
        }
    }
    $entries.Sort([StringComparer]::Ordinal)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($entries -join "`n")))
    } finally {
        $sha.Dispose()
    }
    return (([BitConverter]::ToString($digest)) -replace '-', '').Substring(0, 16).ToLowerInvariant()
}

function Get-BackendDepsKey {
    # 路径也算进去：pip install -e 记的是绝对路径，换了文件夹要重新装
    return "deps1|$BackendExtras|$BackendDir|" + (Get-TreeHash @('backend/pyproject.toml'))
}

function Get-FrontendInstallKey {
    return "install1|$(Get-PnpmSpec)|" + (Get-TreeHash @('frontend/package.json', 'frontend/pnpm-lock.yaml'))
}

function Get-FrontendBuildKey {
    return "build1|$ApiBase|" + (Get-TreeHash $FrontendBuildInputs)
}

function Get-RunningKey {
    # 正在运行的实例是不是用"当前这份代码 + 当前 .env"启动的。
    # 不是的话再双击 start 会自动重启 —— 改完 .env 不用先手动关窗口
    return 'run1|' + (Get-TreeHash (@('backend/app', 'backend/pyproject.toml', '.env') + $FrontendBuildInputs))
}

function Get-SetupReasons {
    # 启动前还缺什么（空数组 = 都装好了，直接启动）
    $reasons = @()
    if (-not (Test-Path -LiteralPath $EnvFile)) { $reasons += '还没有配置文件 .env' }
    if (-not (Test-VenvPython)) {
        $reasons += '后端环境还没装好'
    } elseif ((Read-Stamp 'backend-deps') -ne (Get-BackendDepsKey)) {
        $reasons += '后端依赖有变化'
    }
    if (-not (Test-Path -LiteralPath ([IO.Path]::Combine($FrontendDir, $NextBin)))) {
        $reasons += '前端依赖还没装'
    } elseif ((Read-Stamp 'frontend-install') -ne (Get-FrontendInstallKey)) {
        $reasons += '前端依赖有变化'
    }
    if (-not (Test-Path -LiteralPath $NextBuildId)) {
        $reasons += '前端还没构建'
    } elseif ((Read-Stamp 'frontend-build') -ne (Get-FrontendBuildKey)) {
        $reasons += '前端代码有更新'
    }
    return $reasons
}

# ---------------------------------------------------------------- 端口与进程

function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne(700)) { return $false }
        $client.EndConnect($ar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-HttpOk([string]$Url) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        # 不走系统代理：开着代理软件时，发往 127.0.0.1 的探测也可能被送去代理
        $req.Proxy = $null
        $req.Timeout = 3000
        $req.ReadWriteTimeout = 3000
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return ($code -ge 200 -and $code -lt 300)
    } catch {
        return $false
    }
}

function Get-PortOwner([int]$Port) {
    # 谁在监听这个端口：@{ProcessId; Name; CommandLine}，没人监听（或查不到）返回 $null
    $owner = 0
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($conn) { $owner = [int]$conn.OwningProcess }
    }
    if (-not $owner -and $OnWindows) {
        # 兜底：netstat 在所有 Windows 上都有。只认"本地 :端口、远端 :0"的监听行，不依赖语言
        foreach ($line in @(& netstat.exe -ano -p TCP 2>$null)) {
            $cols = -split [string]$line
            if ($cols.Count -ge 5 -and $cols[1] -match ":$Port$" -and $cols[2] -match ':0$') {
                $owner = [int]$cols[4]
                break
            }
        }
    }
    if (-not $owner) { return $null }
    $name = ''
    try { $name = (Get-Process -Id $owner -ErrorAction Stop).ProcessName } catch { }
    return [pscustomobject]@{
        ProcessId   = $owner
        Name        = $name
        CommandLine = (Get-ProcessCommandLine $owner)
    }
}

function Test-OurCommandLine([string]$CommandLine) {
    # 本工具启动的后端带 --app-dir <backend 目录>，前端带 <frontend 目录>\node_modules\next\...，
    # 认绝对路径，不会误伤别的项目
    if (-not $CommandLine) { return $false }
    foreach ($dir in @($BackendDir, $FrontendDir)) {
        if ($CommandLine.IndexOf($dir, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
}

function Wait-PortsFree([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PortOpen $BackendPort) -and -not (Test-PortOpen $FrontendPort)) { return }
        Start-Sleep -Milliseconds 300
    }
}

function Stop-App {
    # 停掉本工具：先按 .run\app.pid 找到启动窗口，连同它启动的后端、前端一起结束；
    # 再按端口兜底。只动确认是本工具的进程 —— 端口被别的程序占着时只报告，不杀。
    # 返回停掉了几个
    $count = 0
    $recorded = Read-PidFile
    if ($recorded -and $recorded.ProcessId -ne $PID) {
        $ticks = Get-ProcessStartTicks $recorded.ProcessId
        # 同一个 PID、同一个启动时刻，才是当初那个启动窗口（PID 会被系统复用，光看 PID 会误杀）
        if ($ticks -ne 0 -and [Math]::Abs($ticks - $recorded.StartTicks) -lt [TimeSpan]::TicksPerSecond) {
            Stop-ProcessTree $recorded.ProcessId
            $count++
        }
    }
    if ($recorded -and $recorded.ProcessId -ne $PID) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    foreach ($port in @($BackendPort, $FrontendPort)) {
        $owner = Get-PortOwner $port
        if ($owner -and (Test-OurCommandLine $owner.CommandLine)) {
            Stop-ProcessTree $owner.ProcessId
            $count++
        }
    }
    if ($count -gt 0) { Wait-PortsFree 10 }
    return $count
}

function Get-PortBusyMessage([int]$Port) {
    $owner = Get-PortOwner $Port
    $who = '另一个程序'
    if ($owner) { $who = " $($owner.Name)（PID $($owner.ProcessId)）" }
    $msg = "端口 $Port 被${who}占用了，本工具要用这个端口。"
    if ($owner -and $owner.Name -match 'docker|wslrelay|vpnkit') {
        $msg += "`n   看起来是 Docker 版还在运行：在 Docker Desktop 里把它停掉（或者直接退出 Docker Desktop），" +
            '再双击 start-windows.cmd。'
    } else {
        $msg += "`n   关掉那个程序后再双击 start-windows.cmd；不知道是什么程序的话，重启电脑最省事。"
    }
    return $msg
}

function Get-LanAddresses {
    # 手机该访问的地址：有默认网关、处于连接状态的真实网卡的 IPv4。
    # 排除 VirtualBox / VMware / Hyper-V / WSL / 各种 VPN 的虚拟网卡 —— 手机连不到那些地址
    if (-not $OnWindows) { return @() }
    $virtual = 'VirtualBox|VMware|Hyper-V|vEthernet|WSL|Docker|Loopback|Bluetooth|\bTAP\b|\bTUN\b|' +
        'Wintun|WireGuard|Tailscale|ZeroTier|Clash|Mihomo|VPN|Virtual'
    try {
        $ips = foreach ($c in @(Get-NetIPConfiguration -ErrorAction Stop)) {
            if (-not $c.IPv4DefaultGateway -or -not $c.NetAdapter -or $c.NetAdapter.Status -ne 'Up') { continue }
            if (($c.InterfaceAlias + ' ' + $c.InterfaceDescription) -match $virtual) { continue }
            foreach ($a in @($c.IPv4Address)) { $a.IPAddress }
        }
        return @($ips | Where-Object { $_ -and $_ -notlike '169.254.*' -and $_ -notlike '127.*' } |
            Select-Object -Unique)
    } catch {
        return @()
    }
}

function Unblock-Scripts {
    # 从网上下载的 zip 解压出来的文件带"来自其他计算机"的标记，双击 .cmd 会弹安全警告。
    # 第一次运行后把本工具自己的脚本去掉这个标记，以后就不弹了
    if (-not $OnWindows) { return }
    try {
        Get-ChildItem -LiteralPath $Root -Filter '*.cmd' -File | Unblock-File
        Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File | Unblock-File
    } catch { }
}
