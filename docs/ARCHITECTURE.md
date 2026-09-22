# 架构说明

面向要改这份代码的人，说明**为什么这么设计**，而不是重复 README 里的功能介绍。

## 分层

```
API 层     (app/api/v1)        HTTP / SSE，只做参数校验和序列化
服务层     (app/services)      任务编排、指标计算、快照组装、事件缓冲
智能体层   (app/agents)        角色提示词、上下文渲染、流水线
抽象层     (app/llm, app/data) 模型与数据源的可替换接口
```

依赖方向严格单向向下。智能体层不认识 FastAPI，数据层不认识智能体。
换一个前端、换一个模型、加一个数据源，都只动一层。

## 三个关键抽象

### 1. `DataProvider`（app/data/base.py）

数据源适配器协议。新增数据源只需两步：

1. 实现协议，在 `capabilities` 里声明**自己支持哪些接口**
2. 在 `registry.py` 的 `build_provider()` 里注册

`capabilities` 是关键。Tushare 没有个股新闻，就不声明 `CAP_NEWS`，
`ProviderRegistry._dispatch()` 会跳过它去找下一个源。降级粒度是单个接口，
不是整个数据源 —— 这样才能"用 Tushare 的规范财报 + AKShare 的新闻"。

`_dispatch()` 的降级触发条件有三种：
- 不声明该能力 → 直接跳过
- `healthcheck()` 返回 False（缺依赖/缺 token）→ 跳过，且**不会被调用**
- 调用抛 `ProviderUnavailable` 或**返回空结果** → 记下原因，试下一个

空结果也算失败，是因为免费数据源经常某个接口就是返回空表，
静默返回空列表会让分析师对着空数据编结论。

全部失败时抛的 `DataNotFound` 会带上每个源的失败原因，便于排查。

### 2. `LLMClient`（app/llm/base.py）

模型客户端协议。`system_blocks` 是**有序**的系统提示分段：

```python
system_blocks = [
    ctx.market_brief(),              # [0] 本次分析共享的行情简报（长，打缓存断点）
    with_discipline(role_prompt),    # [1] 该智能体的角色指令（短，各不相同）
]
```

顺序不能颠倒。提示缓存是**前缀匹配**的：共享内容排第一，7 个智能体才能复用同一段缓存；
角色指令排第一的话，每个智能体的前缀都不同，缓存全部失效。

`AnthropicLLM._system()` 只在第 0 段且长度 > 2000 字符时加 `cache_control` ——
太短的前缀本来就不会被缓存，加断点是白加。

三个实现：
- `AnthropicLLM` —— 默认。自适应思考、结构化输出、拒答兜底
- `OpenAICompatLLM` —— DeepSeek / 通义千问 / Kimi，国内直连友好
- `EchoLLM` —— 离线桩。**测试和首次体验全靠它**，不联网、不花钱、输出确定

### 3. `Event` 流（app/agents/orchestrator.py）

`AnalysisOrchestrator.run()` 是一个 `AsyncIterator[Event]`。
它不知道事件会被推到 SSE、写进数据库还是打到日志，调用方决定。

并发阶段（四位分析师）的流式增量通过 `asyncio.Queue` 汇聚。
每个片段都带 `agent` 标识，前端按 agent 分卡片渲染，所以四个分析师能**同时**打字。

`_run_concurrent()` 的 `finally` 里会取消所有子任务 ——
用户关页面时调用方会提前中断迭代，不取消就会留下孤儿任务继续烧 token。

## 断线续传（app/services/run_store.py）

手机场景的核心设计。传统做法是"一个请求流式返回结果"，
但手机锁屏/切 App/进隧道都会断连，断了这次分析就白跑了。

这里改成三步：

```
POST /analysis/runs              → 立刻返回 run_id，后台任务开始跑
GET  /analysis/runs/{id}/stream  → SSE 跟进
GET  /analysis/runs/{id}         → 任何时候读最终结果
```

`RunStore` 把每次 run 的事件**按序号全量留在内存**：

- 客户端带 `Last-Event-ID: 11` 重连 → 从第 12 条接着发，不重不漏
- 分析跑完后才连上来 → 从第 0 条快速回放全过程，界面立刻恢复
- SSE 彻底不可用 → 前端 `useAnalysisStream` 降级为轮询 REST

前端还监听了 `visibilitychange`：iOS Safari 会冻结后台页面且不触发 `error`，
回到前台时要主动检查连接是否已死。

15 秒无新事件会发一条 SSE 注释行当心跳（`: keepalive`），
否则中间的 nginx / 运营商代理会掐断"看起来空闲"的连接。

## 缓存（app/data/cache.py）

进程内 TTL 缓存，带**单飞**（single-flight）：同一个 key 并发只真正取一次。
四位分析师并发启动时都要读行情，没有单飞就会对免费数据源发四份同样的请求。

TTL 分两档：行情 60 秒（`QUOTE_CACHE_TTL`），历史/财报 1 小时（`HISTORY_CACHE_TTL`）。
新闻单独压到最多 15 分钟 —— 盘中消息面变化快。

AKShare 的 `stock_zh_a_spot_em()` 一次返回 5000+ 行全市场快照，
搜索和行情两个接口共用它，必须走缓存，否则每次搜索都拉一次全市场。

## 决策的安全兜底

`_sanitize_decision()` 会夹紧模型给的数值：

- `conviction` 夹到 0-100
- `position_pct` 夹到 **0-50** —— 再看好也不该把身家压在一只 A股上
- `action` / `horizon` 不在枚举内 → 退回保守选项（`观望`）
- 结构化输出解析失败 → 返回一个 `action=观望, conviction=0` 的兜底决策，
  并把原始文本放进 `raw_text`，而不是抛异常让整次分析白跑

免责声明由程序无条件附加，不依赖模型记得写。

## 单进程限制与扩展路径

内存里的两处状态决定了当前**只能单 worker 跑**：

| 组件 | 现状 | 多进程方案 |
| --- | --- | --- |
| `services/run_store.py` | 事件列表在内存 | Redis Streams（`append`→XADD，`read_from`→XREAD）|
| `data/cache.py` | dict + TTL | Redis（`get_or_set` 接口不变，换实现即可）|

两者的接口都是为此预留的，换实现不用动调用方。

数据库用 `create_all()` 建表，够单人用。表结构一旦要演进就该引入 alembic：

```bash
cd backend && alembic init migrations
# 把 target_metadata 指向 app.db.models.Base.metadata
```

## 测试策略

89 个测试全部**离线**跑：`conftest.py` 在导入 app 之前就把环境变量设成
`LLM_PROVIDER=echo` + `DATA_PROVIDER_ORDER=mock`。不联网、不需要 Key、不花钱、结果确定。

重点覆盖的是容易出错且出错代价高的地方：

- `test_symbols.py` —— 代码规范化。这里错了后面全错（各板块的涨跌幅限制会影响止损计算）
- `test_indicators.py` —— 指标算法。用**手算的已知值**验证，不是"不报错就算过"；
  另外覆盖空数据、新股短历史、零波动等边界
- `test_fibonacci.py` —— 斐波那契。重点不是验证比例乘法，而是**方向判定**：
  上升段的回撤位是支撑、下降段是阻力，判反了给出的建议就完全相反。
  另有一条锁住"回撤率恒在 0-100"这个由取法决定的性质，
  避免以后有人误以为能从该字段读出突破
- `test_packaging.py` —— 发布完整性。直接比对 git 追踪状态与磁盘文件，
  本地有文件也骗不过去（曾因 .gitignore 规则误伤丢过整个数据层）
- `test_registry.py` —— 降级路由。构造"整体挂掉""缺能力""返回空""探活失败"四种源
- `test_agents.py` —— 尾标解析、简报完整性、单个智能体失败不拖垮流水线、决策夹紧
- `test_api.py` —— **SSE 断线续传**（读 8 条后断开，带 `Last-Event-ID` 重连，
  验证拼接起来正好是完整且无重复的事件流）

`MockProvider` 的随机游走从固定锚点（2021-01-04）生成再切片，
保证 `quote()` 和任意区间的 `daily_bars()` 对同一天给出同一个价格 ——
否则 demo 里会出现"最新价和 K 线最后一根收盘价不一致"。
