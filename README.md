# Trading-Agent-CN

**A股 AI 多智能体投研助手** —— 7 个 LLM 智能体分工协作，从基本面、技术面、消息面、资金面四个角度分析个股，经多空辩论和风控审查后，给出带完整理由链的决策建议。

电脑和手机都能用：一套响应式 Web + PWA，手机可"添加到主屏幕"像 App 一样打开。

> ⚠️ **本工具输出由 AI 生成，仅供研究参考，不构成投资建议。** 模型可能产生事实性错误，数据源可能延迟或错漏。证券投资存在本金损失风险，请独立决策并自行承担后果。

---

## 快速开始

```bash
git clone <本仓库>
cd Trading-Agent-CN-

# 1. 安装依赖并生成 .env
make setup

# 2. 直接启动 —— 此时是离线桩模式，不需要任何 API Key
make dev
```

打开 <http://localhost:3000>，整条链路（数据 → 智能体 → 决策 → 界面）立刻可跑。

**离线桩模式**下用的是模板化的占位分析和程序生成的模拟行情，**结论无任何参考价值**，作用是让你先看清系统长什么样。要跑真实分析，编辑 `.env`：

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...          # https://console.anthropic.com
DATA_PROVIDER_ORDER=akshare,tushare   # AKShare 免费免注册，够用
```

重启后端即可。Docker 用户可以直接 `make docker`。

### 在手机上用

1. 确保手机和电脑在同一局域网
2. 查到电脑 IP（`ipconfig` / `ifconfig`），把它加进 `.env` 的 `CORS_ORIGINS`：
   ```
   CORS_ORIGINS=http://localhost:3000,http://192.168.1.8:3000
   ```
3. 手机浏览器打开 `http://192.168.1.8:3000`
4. **iPhone**：Safari → 分享 → 添加到主屏幕　**Android**：Chrome → 菜单 → 安装应用

装好后全屏运行，和原生 App 观感一致。

---

## 它是怎么工作的

```
       用户输入股票代码 + 分析周期（短线/波段/中线）
                        │
                        ▼
        ┌───────────── 数据层 ─────────────┐
        │  AKShare ─┬─ 行情/K线/财报/新闻/资金流  │
        │  Tushare ─┘  （逐接口自动降级）          │
        └───────────────┬──────────────────┘
                        ▼
              渲染成统一的中文行情简报
           （所有智能体共享，命中提示缓存）
                        │
    ┌───────────────────▼───────────────────┐
    │ 第一阶段：四位分析师并发                 │
    │  基本面 │ 技术面 │ 消息面 │ 资金面        │
    └───────────────────┬───────────────────┘
                        ▼
    ┌───────────────────────────────────────┐
    │ 第二阶段：多空辩论（默认 2 轮，互相看得到对方）│
    │  多头研究员 ⇄ 空头研究员                  │
    └───────────────────┬───────────────────┘
                        ▼
    ┌───────────────────────────────────────┐
    │ 第三阶段：风控经理                        │
    │  波动/流动性/规则风险 → 仓位上限 + 止损方案  │
    └───────────────────┬───────────────────┘
                        ▼
    ┌───────────────────────────────────────┐
    │ 第四阶段：交易决策员（结构化 JSON 输出）      │
    │  操作 / 信心度 / 仓位 / 进出价位 / 理由 / 风险 │
    └───────────────────────────────────────┘
```

整个过程通过 SSE 实时推给前端，你能看到每个智能体逐字产出，而不是干等几分钟。

**手机友好的关键设计**：分析任务在服务端独立运行，与前端连接解耦。锁屏、切 App、地铁断网都不会让这次分析白跑 —— 重新连上时会从断点续传（`Last-Event-ID`），连接彻底失败则自动降级为轮询读取落库结果。

---

## 设计要点

### 数据源逐接口降级
两个数据源各有短板：AKShare 免费但偶发失败，Tushare 稳定但要积分、且**没有个股新闻接口**。所以降级是按**单个接口**做的，不是整体切换：

```
DATA_PROVIDER_ORDER=tushare,akshare
  → 行情/K线/财报 走 Tushare（规范稳定）
  → 新闻/资金流 自动落到 AKShare（Tushare 不支持）
```

每次分析的结果里都会带上 `data_sources`，明确告诉你每个维度的数据实际来自哪儿。

### 数据缺失绝不隐瞒
某个维度抓取失败时，行情简报里会明确写「⚠️ 财务数据缺失」，并要求相关分析师在结论中声明受此限制。**不会让模型对着空数据编结论**。用了 mock 数据时更会在简报里大字标注，要求模型在输出开头就声明。

### 成本优化：共享提示缓存
一次分析里 7 个智能体读的是同一份行情简报（约 2000+ token）。简报被放在 `system_blocks[0]` 并打上 `cache_control` 断点，后续智能体全部命中缓存 —— 顺序不能颠倒，否则前缀各不相同，缓存全部失效。

### 模型可替换
默认 Claude（`claude-opus-5`，开启自适应思考与拒答兜底）。分析师可单独配便宜的模型（`ANTHROPIC_ANALYST_MODEL=claude-sonnet-5`）来省钱，决策环节仍用主模型。国内网络不便直连时，可切到 DeepSeek / 通义千问 / Kimi 等 OpenAI 兼容接口：

```bash
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=https://api.deepseek.com/v1
OPENAI_COMPAT_API_KEY=sk-...
OPENAI_COMPAT_MODEL=deepseek-chat
```

### A股规则内建
- 代码规范化覆盖沪/深/北全部板块，各家数据源格式自动转换（`600519` / `sh600519` / `600519.SH` 都认）
- 涨跌幅限制按板块区分（主板 ±10%、创业板/科创板 ±20%、北交所 ±30%），风控据此推导止损距离
- T+1 制度写进提示词，决策必须在此约束下可执行
- 界面**红涨绿跌**（照搬欧美的绿涨红跌会让用户完全看反）

---

## 项目结构

```
backend/
  app/
    core/          配置、日志、领域异常
    data/          数据源适配器（akshare / tushare / mock）+ 降级路由 + 代码规范化
    llm/           模型抽象层（anthropic / openai 兼容 / 离线桩）
    agents/        角色提示词、上下文渲染、流水线编排
    services/      技术指标、快照组装、分析任务、SSE 事件缓冲
    api/v1/        HTTP 接口
    db/            SQLAlchemy 模型与会话
  tests/           89 个测试，全部离线跑（不联网、不花钱）

frontend/
  src/
    app/           页面（自选 / 分析 / 历史 / 设置）
    components/    外壳、智能体卡片、决策面板、K线图、搜索
    lib/           API 客户端、SSE Hook、格式化
  public/          PWA manifest、Service Worker、图标
```

---

## 常用命令

```bash
make setup          # 安装依赖
make dev            # 前后端一起起
make test           # 跑后端测试（89 个，约 2 秒）
make lint           # ruff + tsc
make docker         # docker compose 一键起全套
cd frontend && pnpm icons   # 改了 icon.svg 后重新生成 PWA 图标
```

接口文档：<http://localhost:8000/docs>

---

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/status` | 数据源健康状况、当前模型、进行中的任务 |
| GET | `/api/v1/market/search?q=` | 按代码或名称搜索 |
| GET | `/api/v1/market/snapshot/{symbol}` | 一次取齐个股全维度数据 |
| GET | `/api/v1/market/indicators/{symbol}` | 技术指标快照 + 形态标签 |
| POST | `/api/v1/analysis/runs` | 发起分析，立刻返回 `run_id` |
| GET | `/api/v1/analysis/runs/{id}/stream` | SSE 实时进度（支持 `Last-Event-ID` 续传）|
| GET | `/api/v1/analysis/runs/{id}` | 读取分析结果（已落库）|
| GET/POST/PATCH/DELETE | `/api/v1/watchlist` | 自选股管理 |

---

## 当前边界

这是一个**投研分析**工具，不是交易系统。明确**不包含**：

- **实盘下单**。A股散户拿不到公开下单 API（需券商 QMT/PTrade 等），本项目不碰资金操作。
- **策略回测**。指标计算已经有了（`services/indicators.py` 是纯函数，易于复用），但没有回测引擎。
- **多用户与鉴权**。当前是单人本地工具，`watchlist` 表没有 `user_id`。要做多用户需加鉴权和数据隔离。
- **多进程部署**。SSE 事件缓冲和行情缓存都在进程内存里，多 worker 会导致断线重连失败。需换 Redis（`services/run_store.py` 和 `data/cache.py` 的接口已为此预留）。

后续规划见 [docs/ROADMAP.md](docs/ROADMAP.md)，架构细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 许可

MIT
