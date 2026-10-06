# Trading-Agent-CN · 安装 / 修复（Windows 原生运行，不用 Docker）
#
# 双击根目录的 setup-windows.cmd 运行；start-windows.cmd 发现没装好时也会先自动跑它。
# 可以反复运行：已经装好的部分会自动跳过（凭 .run 目录里的记录判断）。
#
#   1. 准备 .env（没有就从 .env.example 复制一份）
#   2. 找 Python 3.11+，建 backend\.venv，装后端依赖（和 Docker 镜像装的一样）
#   3. 找 Node.js（没有的话可以用 winget 装）
#   4. 装前端依赖（pnpm 版本跟 package.json 的 packageManager 一致）
#   5. 构建前端
#
# -Force：不看记录，全部重新装一遍

param([switch]$Force)

. ([IO.Path]::Combine($PSScriptRoot, 'common.ps1'))

Set-ConsoleTitle 'Trading-Agent-CN · 安装'
Unblock-Scripts

Write-Host ''
Write-Host "Trading-Agent-CN $(Get-AppVersion) · 安装 / 修复" -ForegroundColor Cyan
Write-Host '可以反复运行：已经装好的部分会自动跳过。'

# 网站开着的话先停：运行中的 python / node 占着文件，覆盖不了
if ((Stop-App) -gt 0) {
    Write-Step '已先停掉正在运行的网站（安装时它占着文件）'
}

# ---------------------------------------------------------------- 1. .env

Write-Step '[1/5] 配置文件 .env'
if (Test-Path -LiteralPath $EnvFile) {
    Write-Done '已有 .env，沿用（不会改动里面的内容）'
} elseif (Test-Path -LiteralPath "$EnvFile.txt") {
    Move-Item -LiteralPath "$EnvFile.txt" -Destination $EnvFile
    Write-Done '找到 .env.txt（记事本另存为时多加了 .txt），已改名为 .env'
} elseif (Test-Path -LiteralPath $EnvExample) {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Done '已生成 .env：默认是离线体验模式，不填任何 Key 也能跑起来'
    Write-Note '以前在 Docker 版里配过 .env 的话，把旧文件夹里的 .env 复制过来覆盖它就行'
} else {
    Write-Problem '找不到 .env.example —— 项目文件不完整，重新解压一遍'
    exit 1
}
$envProblem = Get-EnvFileProblem
if ($envProblem) {
    Write-Problem $envProblem
    exit 1
}

# ---------------------------------------------------------------- 2. Python 与后端依赖

Write-Step '[2/5] Python 和后端依赖'
$venvLocked = '删不掉 backend\.venv，有程序正占用里面的文件：关掉所有 Trading-Agent-CN 窗口' +
    '（还不行就重启电脑），再重新运行'
if ($Force -and (Test-Path -LiteralPath $VenvDir)) {
    Write-Note '按 -Force 删除旧的后端环境 backend\.venv'
    if (-not (Remove-Directory $VenvDir)) {
        Write-Problem $venvLocked
        exit 1
    }
}
if (-not (Test-VenvPython)) {
    if (Test-Path -LiteralPath $VenvDir) {
        Write-Caution '原来的后端环境 backend\.venv 用不了了（多半是 Python 卸载或升级过），删掉重建'
        if (-not (Remove-Directory $VenvDir)) {
            Write-Problem $venvLocked
            exit 1
        }
    }
    $python = Find-Python
    if (-not $python) {
        Write-Caution '没找到能用的 Python（需要 3.11 或更新的 64 位版本）'
        if (Install-WithWinget 'Python.Python.3.14' 'Python 3.14') { $python = Find-Python }
    }
    if (-not $python) {
        Write-Problem '请先安装 Python：'
        Write-Note '  打开 https://www.python.org/downloads/ 下载 Python 3.14 安装包，'
        Write-Note '  安装第一页勾上最下面的 "Add python.exe to PATH"'
        Write-Note '装好后关掉这个窗口，重新双击 setup-windows.cmd'
        exit 1
    }
    Write-Note "使用 Python $($python.Version)：$($python.Exe)"
    $code = Invoke-Tool $python.Exe @('-m', 'venv', $VenvDir) $BackendDir
    if ($code -ne 0 -or -not (Test-VenvPython)) {
        Write-Problem "建后端环境失败（退出码 $code），原因见上面的输出"
        exit 1
    }
    Clear-Stamp 'backend-deps'
}

$depsKey = Get-BackendDepsKey
if ($Force -or (Read-Stamp 'backend-deps') -ne $depsKey) {
    Write-Note '安装后端依赖（第一次要下载一两百 MB，需要几分钟）……'
    # --prefer-binary：有现成安装包就不从源码编译（Windows 上一般没装编译器）
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--prefer-binary',
        '--timeout', '30', '-e', $BackendExtras)
    $code = Invoke-Tool $VenvPython $pipArgs $BackendDir
    if ($code -ne 0) {
        Write-Caution '从官方源 pypi.org 安装失败，换清华镜像再试一次……'
        $code = Invoke-Tool $VenvPython ($pipArgs + @('-i', $PipMirror)) $BackendDir
    }
    if ($code -ne 0) {
        Write-Problem "后端依赖没装上（退出码 $code），原因见上面的输出"
        exit 1
    }
    Write-Stamp 'backend-deps' $depsKey
    Write-Done '后端依赖装好了'
} else {
    Write-Done '后端依赖已是最新，跳过'
}

# ---------------------------------------------------------------- 3. Node.js

Write-Step '[3/5] Node.js'
$node = Find-Node
if (-not $node -or $node.Version -lt $MinNode) {
    if ($node) {
        Write-Caution "Node.js 版本太旧（$($node.Version)），需要 18.18 或更新"
    } else {
        Write-Caution '没找到 Node.js（前端网页要用它）'
    }
    if (Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js 长期支持版') { $node = Find-Node }
}
if (-not $node -or $node.Version -lt $MinNode) {
    Write-Problem '请先安装 Node.js 长期支持版（LTS）：'
    Write-Note '  方法一：开一个 PowerShell 窗口，执行  winget install OpenJS.NodeJS.LTS'
    Write-Note '  方法二：打开 https://nodejs.org ，下载 LTS 安装包，一路"下一步"'
    Write-Note '装好后关掉这个窗口，重新双击 setup-windows.cmd'
    exit 1
}
Write-Done "Node.js $($node.Version)：$($node.Exe)"

# ---------------------------------------------------------------- 4. 前端依赖

Write-Step '[4/5] 前端依赖'
$installKey = Get-FrontendInstallKey
$nextBinPath = [IO.Path]::Combine($FrontendDir, $NextBin)
if ($Force -or -not (Test-Path -LiteralPath $nextBinPath) -or (Read-Stamp 'frontend-install') -ne $installKey) {
    $spec = Get-PnpmSpec
    Write-Note "用 $spec 安装（第一次要下载一两百 MB，需要几分钟）……"
    # 用 npx 临时取指定版本的 pnpm，不往系统里装东西。
    # confirm-modules-purge=false：node_modules 是别的方式装的时，pnpm 默认会停下来问要不要清掉重装；
    # update-notifier=false：不显示"pnpm 有新版本"—— 版本是刻意钉住的，别让人去升级
    $pnpmArgs = @('--yes', $spec, 'install', '--frozen-lockfile',
        '--config.confirm-modules-purge=false', '--config.update-notifier=false')
    $code = Invoke-Npx $pnpmArgs $FrontendDir
    if ($code -ne 0) {
        Write-Caution '从官方源 registry.npmjs.org 安装失败，换国内镜像 npmmirror 再试一次……'
        $env:npm_config_registry = $NpmMirror
        try {
            $code = Invoke-Npx $pnpmArgs $FrontendDir
        } finally {
            Remove-Item Env:npm_config_registry -ErrorAction SilentlyContinue
        }
    }
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $nextBinPath)) {
        Write-Problem "前端依赖没装上（退出码 $code），原因见上面的输出"
        exit 1
    }
    Write-Stamp 'frontend-install' $installKey
    Clear-Stamp 'frontend-build'
    Write-Done '前端依赖装好了'
} else {
    Write-Done '前端依赖已是最新，跳过'
}

# ---------------------------------------------------------------- 5. 构建前端

Write-Step '[5/5] 构建前端网页'
if ($Force -or -not (Test-Path -LiteralPath $NextBuildId) -or (Read-Stamp 'frontend-build') -ne (Get-FrontendBuildKey)) {
    Write-Note '构建中（一般要 1 到 3 分钟）……'
    Clear-Stamp 'frontend-build'
    Set-FrontendEnv
    $code = Invoke-Tool $node.Exe @($NextBin, 'build') $FrontendDir
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $NextBuildId)) {
        Write-Problem "前端构建失败（退出码 $code），原因见上面的输出"
        exit 1
    }
    # 构建完再算指纹：next build 有时会顺手改写 tsconfig.json
    Write-Stamp 'frontend-build' (Get-FrontendBuildKey)
    Write-Done '前端构建好了'
} else {
    Write-Done '前端已是最新，跳过'
}

Write-Host ''
Write-Host '安装完成。以后每次使用：双击 start-windows.cmd' -ForegroundColor Green
exit 0
