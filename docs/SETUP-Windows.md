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

### ⚠️ 升级到 v8 后必须改一下 `.env`

早期版本的 `.env.example` 里写的是：

```
DATA_PROVIDER_ORDER=akshare,tushare,mock
```

**请把末尾的 `mock` 删掉**，改成：

```
DATA_PROVIDER_ORDER=akshare,tushare
```

然后 **`docker compose up -d`**。

> ⚠️ **不要用 `docker compose restart`。** 它只是重启现有容器，
> **不会重新读取 `.env`** —— 你改了文件但容器里的环境变量还是旧的，
> 看起来什么都没发生。`up -d` 会检测到配置变化并重建容器。
> 改完怎么确认生效：打开设置页点「开始探测」，最上面那行「尝试顺序」
> 里不该再出现 mock。

为什么：`mock` 是程序生成的**假行情**。真实数据源取不到时会自动降级到它，
于是界面上会出现"模拟股份741 / 299.42"这种东西，AI 还会基于这个假价格
给出一整套止盈止损点位，看起来和真的一模一样 —— 在一个输出买卖点位的工具里
这非常危险。删掉之后，取不到真数据就会明确报错并说出原因，绝不编数字。

（新装的用户不用管这条，新的 `.env.example` 里已经不含 mock。）

### 搜索不出结果 / 价格明显是假的 —— 先跑数据源诊断
打开 **设置页**，用两个自助工具，不用翻日志：

1. **股票代码目录**：显示"按名称搜索可用"就说明按名字搜没问题。显示"尚未建立"，
   就是按名称搜不出结果的直接原因 —— 点「重建目录」，它会当场告诉你成功还是失败。
2. **数据源诊断** → 点「开始探测」：它会真的去调用每个数据源的每个接口，
   逐项列出 `正常 / 空结果 / 失败 / 超时`，失败的还会给出原因，
   底部还有一句"接下来该做什么"。

常见结论：

| 诊断结果 | 含义与处理 |
| --- | --- |
| 只有 `mock` 一个源 | 界面上的价格全是程序生成的假数据。在 `.env` 里设 `DATA_PROVIDER_ORDER=akshare,tushare` 后重启 |
| akshare 各接口 `失败`，原因带 403 / timeout / ConnectionError | 上不去东方财富的接口。检查代理软件、公司网络、防火墙；或直接换 Tushare（见下） |
| akshare 只有「搜索」`超时`，其余正常 | **这是正常的**。全市场列表接口天生要 60-90 秒（要翻 54 页、每页还 sleep 1 秒），诊断给它的预算只有 12 秒。按名称搜索不靠它实时取数，靠后台建好的本地代码目录 —— 看「股票代码目录」那一栏显示多少只股票即可 |
| akshare「实时行情」`失败` | 这个是单只股票的盘口接口（单请求、很快），它失败就是真的连不上 push2.eastmoney.com |
| akshare `超时`（除搜索以外的接口） | 对方限流或改版。AKShare 是抓网页的，这种情况会反复出现 —— 建议换 Tushare |
| 全部 `正常` | 数据是真的，界面上顶部那条"模拟数据"警告条也应该消失了 |

**换 Tushare 更省心**：它是正式 API 而不是抓网页，稳定得多。去 <https://tushare.pro>
免费注册拿 token，然后在 `.env` 里：

```
DATA_PROVIDER_ORDER=tushare,akshare,mock
TUSHARE_TOKEN=你的token
```

然后 `docker compose up -d`（**不是 restart** —— restart 不重新读 `.env`），
回设置页再跑一次诊断确认。

> 顺带说明：Tushare 没有个股新闻接口，新闻那一项会自动落到 AKShare ——
> 逐接口降级是设计好的，不影响其他维度。

另一个更硬的判断方法：访问 <http://localhost:8000/api/v1/status> 看 `using_mock_data`，
`true` 就说明至少有一个维度用的是假数据。

### 诊断页现在会自己告诉你结论

点「开始探测」后，从上往下看三样东西：

1. **后端版本 + 浏览器指纹状态** —— 先确认跑的是你刚装的那一版、
   指纹伪装有没有生效。（版本号也显示在「设置与状态」标题旁边。）
2. **主机可达性** —— 每个域名用普通 requests 和浏览器指纹**各打一次**：

   | 两次结果 | 含义 | 怎么办 |
   | --- | --- | --- |
   | 都正常 | 该主机没问题 | 问题在接口层面，看下面的逐接口结果 |
   | 只有普通 requests 失败 | 被按客户端指纹拦了 | 开着 `AKSHARE_BROWSER_TRANSPORT=true` 就行 |
   | 都失败 | **这台机器连不上这个域名** | 查代理/防火墙，或换用能通的数据源 |

   表格下面会直接给一句结论，包含可以照抄的 `DATA_PROVIDER_ORDER`。
3. **逐接口结果**，以及最底下一句"所以数据是真的还是假的"。

### 东方财富连不上，但雪球/新浪能通 —— 换数据源就行

这是最常见的一种情况：`push2*.eastmoney.com` 这一组主机在部分网络下不可达
（代理软件、公司网络、个别运营商线路），而同域名下的 `search-api-web`
和其他站点却是通的。

本项目内置了**主机互不相同**的备份源：

| 数据源 | 提供 | 主机 |
| --- | --- | --- |
| `akshare` | 全部（字段最全） | `push2*.eastmoney.com` |
| `xueqiu` | 实时行情 | `xueqiu.com` |
| `sina` | 日线、大盘指数 | `*.sina.com.cn` |
| `tushare` | 行情、日线、财报等 | `api.tushare.pro` |

`.env` 里按这个顺序写（v11 起是默认值）：

```
DATA_PROVIDER_ORDER=akshare,xueqiu,sina,tushare
```

然后 `docker compose up -d`。东财通就用东财，不通就自动落到雪球和新浪 ——
降级是**按接口**做的，不是整体切换。设置页的「数据源」一节会显示每个维度
实际由谁提供。

### 诊断里只有「个股新闻」正常，其余全是「对方直接掐断连接」

失败原因长这样：

```
('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

**这不是超时，也不是没网。** 是对方在 TLS 握手层面拒绝了普通 Python 客户端：
akshare 里唯一用 `curl_cffi`（会模拟真实 Chrome 的指纹）取数的接口就是个股新闻，
其余都用普通 `requests` —— 一个能通、其余全挂，正好对应上。

本项目现在默认开启浏览器指纹伪装（`AKSHARE_BROWSER_TRANSPORT=true`），
只换运输层、不动 akshare 的字段解析。升级后重新探测即可。

如果开着还是全部掐断，说明是**本机网络层面**的问题，按这个顺序查：

1. 代理软件 / VPN：很多代理会拦或重置对 `push2.eastmoney.com` 的连接，
   试试关掉再探测；
2. 安全软件、公司网络、校园网的出口策略；
3. 想单独验证到底是客户端还是网络的问题，跑这一条命令（两种客户端各打一次）：

```powershell
docker compose exec backend python -c "import requests; from curl_cffi import requests as c; u='https://push2.eastmoney.com/api/qt/stock/get'; p={'fltt':'2','invt':'2','fields':'f43,f58,f60','secid':'0.300741'}; [print(n, end=': ') or (lambda f: print(f()))(g) for n, g in [('普通requests', lambda: requests.get(u,params=p,timeout=10).text[:100]), ('curl_cffi', lambda: c.get(u,params=p,timeout=10,impersonate='chrome').text[:100])]]"
```

两个都失败 = 网络问题；只有普通 requests 失败 = 指纹拦截，开着伪装就行。

### 价格不自动刷新 / 一直显示"已收盘"
这通常是**对的**：A股交易时间是周一至周五 09:15-09:25（集合竞价）、09:30-11:30、
13:00-15:00（北京时间）。非交易时段前端会刻意**完全不刷新** —— 收盘后每 5 秒拉一次
价格毫无意义，还会把免费数据源打得更快挂掉。此时头部会显示"已收盘 · 距开盘 X 小时"。

盘中还是不刷新，按这个顺序查：
- 头部徽标显示的是什么？「连续竞价 · 5 秒自动刷新」+ 一个呼吸的小圆点 = 正在轮询
- 切到别的标签页或锁屏时会**暂停**轮询（省电），回到前台会立刻补一次
- 访问 <http://localhost:8000/api/v1/market/session> 看后端判断的时段对不对。
  若明明是盘中却显示"已收盘"，看 `as_of` 这个时间对不对 ——
  它是后端算出的北京时间，用的是固定 UTC+8，不依赖容器时区，正常不会错
- 法定节假日会被当成交易日（没接交易日历），表现是"显示交易中但价格不动"，属已知取舍

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
# 先停后端：SQLite 开了 WAL 模式，运行中最近的写入可能还在 -wal 文件里，
# 只拷 .db 会丢掉那部分
docker compose stop backend
docker compose cp backend:/app/data/trading_agent.db .\backup.db
docker compose start backend
```

放回去：
```powershell
docker compose stop backend
docker compose cp .\backup.db backend:/app/data/trading_agent.db
docker compose start backend
```

> 为什么是 WAL：本工具会在后台建"全市场代码-名称目录"（按名称搜索靠它），
> 那是一次 5000 多行的写入，同时前端还在读。默认的日志模式下写会把读全挡住，
> 随机报 `database is locked`；WAL 下读写互不阻塞。
> 代价是数据库旁边会多出 `-wal` 和 `-shm` 两个文件，属正常现象。

---

## 最后提醒一句

这个工具给出的所有结论都是 AI 生成的，**不构成投资建议**。
模型会犯事实性错误，数据源也可能延迟或有错漏。
把它当成一个"帮你把公开信息整理成多角度分析"的助手，
最终决策和后果都是你自己的。
