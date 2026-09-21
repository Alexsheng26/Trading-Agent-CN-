# Windows 上手指南（Docker 方案）

从零到能用，大约 15 分钟，其中大部分是等 Docker 下载。
**全程只需要装一个软件**（Docker Desktop），不用自己装 Python、Node、pnpm。

---

## 第 0 步：装 Docker Desktop

1. 打开 <https://www.docker.com/products/docker-desktop/>，点 **Download for Windows**
2. 双击安装，一路下一步。中途会提示需要 **WSL 2**，勾上让它自动装
3. 装完**重启电脑**（必须重启，否则 WSL 起不来）
4. 重启后在开始菜单打开 **Docker Desktop**，等左下角的小鲸鱼图标变成绿色 / 显示 `Engine running`

> 每次要用本项目，都得先确认 Docker Desktop 正在运行。它没开，后面的命令会报
> `error during connect: ... The system cannot find the file specified`。

**验证装好了**：按 `Win + X` → 选 **终端** 或 **PowerShell**，输入：

```powershell
docker --version
docker compose version
```

两条都能打印版本号就算成功。

---

## 第 1 步：把项目放到电脑上

我会把项目打包成 zip 发给你（因为 GitHub 推送权限还没开通）。

1. 下载 zip，右键 → **全部解压缩**
2. 解压到一个**路径里没有中文和空格**的地方，推荐：
   ```
   C:\dev\trading-agent-cn
   ```
   > 路径带中文或空格时，Docker 挂载目录偶尔会出莫名其妙的问题，躲开省事。

解压后这个文件夹里应该能看到 `docker-compose.yml`、`README.md`、`backend`、`frontend` 这些。

**如果之后 GitHub 权限开通了**，就可以改用（需要先装 Git）：

```powershell
git clone https://github.com/Alexsheng26/Trading-Agent-CN-.git
cd Trading-Agent-CN-
git checkout claude/sleepy-pascal-4uqlk1
```

---

## 第 2 步：在项目文件夹里打开 PowerShell

最快的办法：在**文件资源管理器**里进到项目文件夹，然后

- 在地址栏里把路径删掉，输入 `powershell`，回车

或者手动：

```powershell
cd C:\dev\trading-agent-cn
```

确认位置对不对：

```powershell
dir docker-compose.yml
```

能列出这个文件就说明位置正确。如果报"找不到路径"，说明还没进到项目文件夹。

---

## 第 3 步：生成配置文件 `.env`

```powershell
Copy-Item .env.example .env
```

> ⚠️ **不要用记事本"另存为"来做这一步。** Windows 会偷偷存成 `.env.txt`，
> 程序读不到。用上面这条命令最稳妥。
>
> 想确认名字对不对：`dir .env` 应该显示 `.env`，而不是 `.env.txt`。

这一步之后**什么都不用改**，直接进第 4 步。默认配置是"离线体验模式"，
不需要任何 API Key 就能把整个系统跑起来。

---

## 第 4 步：启动

```powershell
docker compose up -d --build
```

**第一次会比较久（5-10 分钟）**，它在下载 Python 和 Node 的基础镜像、装依赖。
屏幕上会滚很多行日志，这是正常的。

看到最后出现类似这样就成功了：

```
[+] Running 3/3
 ✔ Network trading-agent-cn-_default      Created
 ✔ Container trading-agent-cn--backend-1  Healthy
 ✔ Container trading-agent-cn--frontend-1 Started
```

**关键点**：必须等 `backend` 变成 `Healthy` 才会启动 `frontend`（这是故意设计的，
避免前端连上一个还没准备好的后端）。如果卡在 `Waiting`，稍等一下，
后端首次启动要建数据库表。

---

## 第 5 步：打开用

浏览器访问 <http://localhost:3000>

你应该看到「A股投研助手」，右上角有个黄色的**「离线桩」**标签。

试一下完整流程：

1. 在搜索框输入 `600519`，点出来的「贵州茅台」→ 加入自选
2. 点这只股票 → 进入分析页
3. 选「波段」→ 点**开始分析**
4. 看 7 个智能体一个个逐字输出分析（这时你会看到打字机效果），最后出决策面板

> 🔴 **此时的分析内容是假的**：模型是占位模板，行情也是程序生成的模拟数据。
> 这一步的目的是确认整个系统跑通了。下一步接真模型和真数据。

---

## 第 6 步：接真实模型和真实数据

### 6.1 拿一个 Claude API Key

1. 打开 <https://console.anthropic.com>，注册/登录
2. 左侧 **API Keys** → **Create Key**，复制那串以 `sk-ant-` 开头的字符
3. Anthropic 是按用量付费的，需要先在 **Billing** 里充一点额度（几美元够跑很多次）

> **一次分析大概花多少？**
>
> 实测（辩论 2 轮）：一次分析会发起 **10 次模型调用**，输入合计约 **5.5 万字符**，
> 其中 **78% 是所有智能体共享的行情简报** —— 这部分命中提示缓存后只按 10% 计费，
> 所以实际成本远低于字符量的直觉估计。
>
> 按 Claude Opus 5 单价（输入 $5/百万、输出 $25/百万）推算，单次约 **$0.3-0.8**，
> 波动主要来自模型思考的深度（思考 token 按输出计费）。
>
> ⚠️ 这是**估算**，不是实测账单 —— 我的开发环境没有 API Key，没法真实调用来核对。
> 建议你第一次跑完后去 <https://console.anthropic.com> 的 Usage 页面看实际用量，
> 再决定要不要按 6.4 降配。

### 6.2 改 `.env`

用 VS Code 或记事本打开项目里的 `.env` 文件，找到这几行改掉：

```ini
# 原来是 echo，改成 anthropic
LLM_PROVIDER=anthropic

# 填你刚才复制的 Key
ANTHROPIC_API_KEY=sk-ant-你的key

# 原来是 akshare,tushare,mock，把 mock 去掉，强制用真实数据源
# （留着 mock 的话，真实数据源挂了会悄悄用假数据，不如直接报错）
DATA_PROVIDER_ORDER=akshare,tushare
```

保存。

> AKShare 完全免费、不用注册，能拿行情、K线、财报、新闻、资金流，够用了。
> `TUSHARE_TOKEN` 可以不填，不填就自动跳过。

### 6.3 重启让配置生效

```powershell
docker compose up -d
```

改 `.env` 不需要加 `--build`（没改代码）。重启后刷新浏览器，
右上角的「离线桩」标签应该消失了。

到「设置」页面可以确认：模型显示 `claude-opus-5`，数据源里 `akshare` 显示**可用**。

### 6.4 想省钱：让分析师用便宜的模型

在 `.env` 里加一行，让 4 个分析师和辩论用 Sonnet，只有最后的风控和决策用 Opus：

```ini
ANTHROPIC_ANALYST_MODEL=claude-sonnet-5
```

Sonnet 的价格是 Opus 的 40%，而分析师干的是"读数据写结论"这类相对直接的活，
单次成本能降到 $0.2-0.3 左右。

还想再省，可以减少辩论轮数（默认 2 轮，每轮多空各发言一次）：

```ini
DEBATE_ROUNDS=1
```

---

## 第 7 步：装到手机上

1. 手机和电脑连**同一个 Wi-Fi**

2. 查电脑的局域网 IP：
   ```powershell
   ipconfig
   ```
   找 **"无线局域网适配器 WLAN"** 下面的 **IPv4 地址**，
   通常长这样：`192.168.1.8` 或 `192.168.0.123`

3. **在防火墙放行 3000 端口**（Windows 默认会拦掉局域网访问）。
   以**管理员身份**打开 PowerShell（右键开始菜单 → **终端(管理员)**），执行：
   ```powershell
   New-NetFirewallRule -DisplayName "Trading Agent CN" -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow
   ```
   > 只放行 3000 就够 —— 手机只访问前端，后端是由前端在服务端内部转发的。
   >
   > 想撤销：`Remove-NetFirewallRule -DisplayName "Trading Agent CN"`

4. 手机浏览器打开 `http://192.168.1.8:3000`（换成你自己的 IP）

5. 添加到主屏幕：
   - **iPhone**：必须用 **Safari** 打开 → 底部**分享**按钮 → 往下滑找到**添加到主屏幕**
   - **Android**：用 **Chrome** 打开 → 右上角**三个点** → **安装应用** / **添加到主屏幕**

装好后从主屏图标打开就是全屏的，没有浏览器地址栏，和原生 App 一样。

> 分析跑到一半锁屏、切到微信、或者进地铁断网都没关系 —— 分析在电脑上继续跑，
> 你回来时进度会自动接上。这是刻意设计的，不是碰巧。

---

## 日常使用

```powershell
# 启动（每天用之前，先确保 Docker Desktop 开着）
docker compose up -d

# 停止
docker compose down

# 看日志（排查问题时用，Ctrl+C 退出）
docker compose logs -f

# 只看后端日志
docker compose logs -f backend

# 改了代码后重新构建
docker compose up -d --build
```

---

## 遇到问题怎么办

### `error during connect` / `cannot find the file specified`
Docker Desktop 没启动。开始菜单打开它，等小鲸鱼变绿。

### 浏览器打开 localhost:3000 显示"无法访问"
先看容器状态：
```powershell
docker compose ps
```
- `frontend` 不在列表里或状态是 `Exited` → 看日志：`docker compose logs frontend`
- `backend` 状态一直是 `starting` → 等 30 秒，它在建数据库表
- 都 `Up` 但打不开 → 试试 <http://127.0.0.1:3000>

### 页面能开，但搜索股票报错
说明后端或数据源有问题。访问 <http://localhost:8000/api/v1/status>，看 `data_providers`：
- 全部 `healthy: false` → 网络问题，AKShare 要访问东方财富/新浪的接口，
  确认电脑能正常上网、没有代理软件拦截
- 想先跳过这个问题继续体验 → 在 `.env` 里把 `DATA_PROVIDER_ORDER` 改回
  `akshare,tushare,mock`，实在拿不到真数据时会回落到模拟数据

### 分析一直转圈不出结果
```powershell
docker compose logs -f backend
```
常见原因：
- `Claude 鉴权失败` → `ANTHROPIC_API_KEY` 填错了，或者 Anthropic 账户没充值
- `触发 Claude 速率限制` → 新账户额度低，在 `.env` 里设 `DEBATE_ROUNDS=1` 降低并发
- `分析超时` → 在 `.env` 里把 `ANALYSIS_TIMEOUT` 调大，比如 `900`

### 构建前端时报 `pnpm install --frozen-lockfile` 失败
如果报错里提到 `minimumReleaseAge` 或 `the active policies reject`，说明你的 pnpm
版本比 lockfile 的生成版本新，新版有个"拒绝刚发布不久的包"的供应链策略。

本项目已经在 `frontend/package.json` 里钉住了 `packageManager: pnpm@10.33.0`，
Docker 构建会自动用这个版本，正常不该再遇到。若还是遇到：

```powershell
# 让 Docker 不用缓存重新构建前端
docker compose build --no-cache frontend
docker compose up -d
```

实在不行，重建 lockfile（需要本机装 Node 和 pnpm）：
```powershell
cd frontend
pnpm clean --lockfile
pnpm install
cd ..
docker compose up -d --build
```

### `.env` 改了没生效
- 确认文件名是 `.env` 不是 `.env.txt`：`dir .env`
- 确认改完执行了 `docker compose up -d`
- 用 `docker compose config` 看 Compose 实际读到的值对不对

### 手机访问不了
按顺序排查：
1. 电脑上能不能开 `http://<电脑IP>:3000`？不能 → IP 查错了
2. 防火墙规则加了吗？（第 7 步第 3 小步）
3. 手机和电脑真的在同一个 Wi-Fi 吗？（注意有些路由器的访客网络会隔离设备）

### 想彻底清掉重来
```powershell
docker compose down
docker compose up -d --build
```

这样重建镜像但**保留数据**（自选股、历史分析记录）。

连数据一起清空：
```powershell
docker compose down -v
docker compose up -d --build
```
`-v` 会删掉存数据的命名卷，自选股和历史分析全部清空，不可恢复。

### 想备份数据 / 看一眼数据库文件

数据存在 Docker 的**命名卷**里，不在项目文件夹中（这是故意的：Windows 上
把宿主目录挂进 Linux 容器要过一层文件系统转换，SQLite 的文件锁在那层上
容易出问题）。要取出来：

```powershell
docker compose cp backend:/app/data/trading_agent.db .\backup.db
```

放回去：
```powershell
docker compose cp .\backup.db backend:/app/data/trading_agent.db
docker compose restart backend
```

---

## 最后提醒一句

这个工具给出的所有结论都是 AI 生成的，**不构成投资建议**。
模型会犯事实性错误，数据源也可能延迟或有错漏。
把它当成一个"帮你把公开信息整理成多角度分析"的助手，
最终决策和后果都是你自己的。
