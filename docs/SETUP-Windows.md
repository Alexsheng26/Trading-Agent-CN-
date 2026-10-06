# Windows 上手指南（不用 Docker）

直接在 Windows 上跑，浏览器打开就能用。**全程双击脚本，不用敲命令。**

需要电脑上有两样东西：

| 软件 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.11 或更新，64 位 | 3.14 已验证可用。已经装了就不用重装 |
| Node.js | 18.18 或更新（装长期支持版 LTS 就行） | 前端网页要用 |

> 想用 Docker 的看 [SETUP-Windows-Docker.md](SETUP-Windows-Docker.md)。
> 不推荐：Docker Desktop 要开 Windows 的虚拟化开关，和 VirtualBox 抢同一个开关。
> 本方案不碰这个开关，VirtualBox 照常用。

---

## 第 0 步：装 Node.js（只装一次）

二选一：

- **用命令装**：开始菜单搜「PowerShell」打开，输入下面这行回车，中途弹管理员确认就点「是」：
  ```powershell
  winget install OpenJS.NodeJS.LTS
  ```
- **手动装**：打开 <https://nodejs.org>，下载 **LTS**（长期支持版）安装包，一路「下一步」。

装完**不用做别的**。忘了装也没关系：第 2 步的脚本发现没有 Node.js，会问你要不要自动装。

> Python 还没装的话：<https://www.python.org/downloads/> 下载 3.14，安装第一页
> **勾上最下面的「Add python.exe to PATH」**。

---

## 第 1 步：把项目放到电脑上

1. 下载 zip 后**先别急着解压**：右键 zip → **属性** → 最下面勾上 **「解除锁定」** → 确定。

   > 不做这步，之后每次双击脚本 Windows 都会弹「无法验证发布者」的警告。
   > 已经解压了也没关系：弹警告时点「运行」，脚本第一次运行后会自己去掉这个标记。

2. 右键 zip → **全部解压缩**，解压到一个**路径里没有中文和空格**的地方，推荐 `C:\dev\`。
   解压出来是 `C:\dev\trading-agent-cn`，里面能看到 `start-windows.cmd`、`backend`、`frontend` 这些。

**以前用过 Docker 版、文件夹就在 `C:\dev\trading-agent-cn` 的**：直接解压覆盖。
你配好的 `.env` 会保留（zip 里不带 `.env`），接 DeepSeek 的配置不用重填。

---

## 第 2 步：双击 `start-windows.cmd`

在项目文件夹里双击 **`start-windows.cmd`**，会打开一个新窗口。

**第一次**它会先自动安装，屏幕上依次出现：

```
== [1/5] 配置文件 .env
== [2/5] Python 和后端依赖        ← 下载 Python 依赖，几分钟
== [3/5] Node.js
== [4/5] 前端依赖                 ← 下载前端依赖，几分钟
== [5/5] 构建前端网页             ← 1 到 3 分钟
```

总共大约 5 到 15 分钟，看网速。装完自动启动，**浏览器会自动打开**
<http://localhost:3000>，窗口里出现：

```
  ============================================================
   Trading-Agent-CN v17 已经启动

   电脑上打开：http://localhost:3000
   手机上打开（连同一个 Wi-Fi）：http://192.168.1.8:3000

   停止：关掉这个窗口，或者在窗口里按 Ctrl+C
   用的时候这个窗口要一直开着（可以最小化）
  ============================================================
```

**这个窗口就是程序本身**：开着 = 在运行，关掉 = 停止。可以最小化，别关。

中途可能弹出两种提示：

| 弹窗 | 怎么点 |
| --- | --- |
| 「Windows 已保护你的电脑」 | 点「更多信息」→「仍要运行」（第 1 步解除锁定了就不会弹） |
| 「Windows 安全中心」问是否允许 Node.js 访问网络 | **只勾「专用网络」**，点「允许」。只在电脑上用的话点「取消」也不影响 |

以后每次用：**双击 `start-windows.cmd`** 就行，已经装好的部分会自动跳过，几秒就起来。

---

## 第 3 步：确认跑通了

浏览器里应该看到「A股投研助手」。

- 右上角有黄色的**「离线桩」**标签 = 还没接模型（新装的默认就是这样），按第 4 步配置；
- 从 Docker 版沿用了 `.env` 的，应该**没有**这个标签，可以直接用。

打开「设置」页，标题旁边应该显示 **`v17 · Windows 原生`**。

---

## 第 4 步：接真实模型（改 `.env`）

`.env` 在项目文件夹的最外层。右键 → **打开方式** → **记事本**。

**用 DeepSeek**，改这几行：

```ini
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_API_KEY=你的key
OPENAI_COMPAT_BASE_URL=https://api.deepseek.com/v1
OPENAI_COMPAT_MODEL=deepseek-chat
```

**用 Claude**，改这两行：

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=你的key
```

保存（`Ctrl+S`）。**然后再双击一次 `start-windows.cmd`** —— 它发现 `.env` 变了，
会自动停掉旧的、重新启动，不用先手动关窗口。

> Key 只填在你自己电脑的 `.env` 里，别发到聊天或截图里。`.env` 不会被打包，也不会上传。

可选的几项（含义和 [Docker 版指南第 6 步](SETUP-Windows-Docker.md#第-6-步接真实模型和真实数据)
一样，只是改完是「再双击一次 `start-windows.cmd`」）：

```ini
# DeepSeek 的「深度」档：风控和决策两步改用推理模型
OPENAI_COMPAT_REASONING_MODEL=deepseek-reasoner
# 分析页「高级」里能手选的模型（逗号分隔）
LLM_MODEL_CHOICES=deepseek-chat,deepseek-reasoner
# 标准档的辩论轮数，调小更快更省
DEBATE_ROUNDS=1
```

---

## 第 5 步：在手机上用

1. 手机和电脑连**同一个 Wi-Fi**。
2. 手机浏览器打开窗口里那行「手机上打开」的地址，比如 `http://192.168.1.8:3000`。
3. 添加到主屏幕：
   - **iPhone**：用 **Safari** 打开 → 底部**分享** → **添加到主屏幕**
   - **Android**：用 **Chrome** 打开 → 右上角**三个点** → **安装应用** / **添加到主屏幕**

**手机打不开**，按顺序查：

1. 电脑上能打开 `http://那个地址:3000` 吗？不能就是地址不对，用窗口里显示的那个。
2. **家里的 Wi-Fi 要设成「专用网络」**：设置 → 网络和 Internet → WLAN → 点你连的网络 →
   「网络配置文件类型」选**专用网络**。（Windows 默认把新网络当「公用」，公用网络上防火墙会拦。）
3. 第 2 步的防火墙弹窗点了「取消」？以**管理员身份**打开 PowerShell（右键开始菜单 →
   **终端(管理员)**），执行：
   ```powershell
   New-NetFirewallRule -DisplayName "Trading Agent CN" -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow -Profile Private
   ```
   想撤销：`Remove-NetFirewallRule -DisplayName "Trading Agent CN"`
4. 路由器的「访客网络」会隔离设备，手机别连访客网络。

> ⚠️ **本工具没有登录密码**：谁能打开这个网页，谁就能用，包括花你的模型 API 额度。
> 所以防火墙只放行「专用网络」—— 在学校、咖啡店这类公用网络上，别人就访问不到它。
> 不要把 3000 端口在公用网络上放开。

---

## 日常使用

| 想做什么 | 怎么做 |
| --- | --- |
| 启动 | 双击 `start-windows.cmd` |
| 停止 | 关掉那个窗口（或者在窗口里按 `Ctrl+C`） |
| 改了 `.env` 让它生效 | 再双击一次 `start-windows.cmd`（会自动重启） |
| 更新到新版本 | 关掉窗口 → 新 zip 解压**覆盖到同一个文件夹** → 双击 `start-windows.cmd`（缺什么自动补装，前端自动重新构建） |
| 装出问题了，重新装一遍 | 双击 `setup-windows.cmd` |
| 提示端口被占用 / 窗口找不到了停不掉 | 双击 `stop-windows.cmd` |

### 数据存在哪

自选股和历史分析记录都在 `backend\data\` 里（`trading_agent.db`，旁边可能还有
`-wal`、`-shm` 两个文件，正常现象）。要备份：**先关掉窗口**，再把整个 `backend\data`
文件夹复制走。更新版本时解压覆盖不会动它。

> 从 Docker 版换过来的：Docker 版的数据存在 Docker 自己的数据卷里，这里读不到，
> 自选股需要重新加一遍。

### 不用 Docker 之后

Docker Desktop 可以在「设置 → 应用」里卸载。不想卸也行，至少别让它开机自启
（任务管理器 → 启动应用 → Docker Desktop → 禁用）—— 它开着的时候，旧容器会占用
3000 / 8000 端口，`start-windows.cmd` 会提示端口被占用。

---

## 遇到问题怎么办

先看那个窗口：出错时脚本会停下来，用红字写明原因和下一步该做什么，
**按回车之前窗口不会关**。需要求助时，把整个窗口截图发出来最有用。

### 安装卡在「找不到 Python」

窗口里会列出找到了哪些 Python、为什么跳过（版本太旧、32 位等）。
确认装的是 64 位的 3.11 或更新版本；装的时候没勾「Add python.exe to PATH」也能找到
常见安装位置，找不到就重装一次并勾上它。

### 装好了 Node.js 还是提示找不到

刚装完的程序，已经开着的窗口看不到。**关掉窗口，重新双击**就好。

### 安装依赖失败（pip / pnpm 报错）

脚本会先从官方源下载，失败了自动换国内镜像（清华 / npmmirror）再试一次。
两次都失败一般是网络问题：

- 开着代理软件的话，先关掉或切到「规则模式」再试；
- 换个网络（比如手机热点）再双击 `start-windows.cmd`，已经装好的部分不会重下。

### 提示「端口 8000 / 3000 被占用」

提示里会写是哪个程序占的：

- 是 Docker（`com.docker.backend`、`wslrelay` 之类）：退出 Docker Desktop；
- 是上次没关干净的本工具：双击 `stop-windows.cmd`；
- 都不是：关掉那个程序，或者重启电脑。

### 网页打不开 / 一直转圈

1. 那个窗口还开着吗？关了就是停了，双击 `start-windows.cmd`。
2. 窗口最下面有没有红色的报错（`Traceback`、`Error`）？截图。
3. 窗口标题开头变成了「选择」？那是不小心在窗口里点出了选择状态，**按一下 `Esc`**。
   （脚本已经关掉了这个窗口的「快速编辑」，正常不会出现。）

### `.env` 改了没生效

- 文件名必须是 `.env`，不能是 `.env.txt`（安装脚本发现 `.env.txt` 会自动改名）；
- 改完要**再双击一次 `start-windows.cmd`**；
- 用记事本「另存为」时编码要选 **UTF-8**。存成别的编码，启动脚本会直接提示你。

### 开着代理软件（VPN / Clash 等）时数据取不到

不用 Docker 之后，后端访问数据源会受 Windows「系统代理」影响。
如果「设置 → 数据源诊断」里大面积失败：把代理切到**规则模式**（国内网站直连），
或者先关掉「系统代理」，再到诊断页重新探测。

### 数据源、搜索、分析超时这类问题

打开「设置 → 数据源诊断」，点「开始探测」，它会逐项告诉你哪里不通、该怎么办。
常见情况的详细说明见 [Docker 版指南的「遇到问题怎么办」](SETUP-Windows-Docker.md#遇到问题怎么办)，
两种跑法通用 —— 把里面的 `docker compose up -d` 换成「再双击一次 `start-windows.cmd`」。

### 实在不行，从头装一遍

关掉窗口，删掉这几个文件夹：`backend\.venv`、`frontend\node_modules`、`frontend\.next`、`.run`，
再双击 `start-windows.cmd`。`.env` 和 `backend\data` 里的数据不受影响。

---

## 最后提醒一句

这个工具给出的所有结论都是 AI 生成的，**不构成投资建议**。
模型会犯事实性错误，数据源也可能延迟或有错漏。
把它当成一个"帮你把公开信息整理成多角度分析"的助手，
最终决策和后果都是你自己的。
