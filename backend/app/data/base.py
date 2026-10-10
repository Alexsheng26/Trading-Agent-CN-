"""数据层契约：统一的数据结构 + DataProvider 协议。

新增一个数据源 = 实现 `DataProvider` + 在 `registry.py` 注册，
上层（智能体、API）完全不需要改动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from app.data.symbols import Symbol

# ---------------------------------------------------------------- 数据结构


@dataclass(slots=True)
class StockBasic:
    """股票基础信息。"""

    symbol: str
    name: str
    exchange: str
    board: str = ""
    industry: str = ""
    list_date: date | None = None
    total_share: float | None = None  # 总股本（股）
    float_share: float | None = None  # 流通股本（股）


@dataclass(slots=True)
class Quote:
    """实时（或最近收盘）行情快照。"""

    symbol: str
    name: str
    price: float
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    change_pct: float | None = None  # 涨跌幅 %
    volume: float | None = None  # 成交量（手）
    amount: float | None = None  # 成交额（元）
    turnover_rate: float | None = None  # 换手率 %
    pe_ttm: float | None = None
    pb: float | None = None
    total_market_cap: float | None = None  # 总市值（元）
    float_market_cap: float | None = None  # 流通市值（元）
    updated_at: datetime | None = None
    #: 是否来自实时接口。False 表示这是"最近一根日线的收盘价"——
    #: 在所有实时接口都不可用时的兜底。界面必须显著标注，
    #: 绝不能让盘中看到的其实是昨天的收盘价却毫不知情。
    is_realtime: bool = True
    #: 这份价格对应的交易日。非实时时一定要有，用来告诉用户"这是哪天的价"
    quote_date: date | None = None


@dataclass(slots=True)
class Bar:
    """日线（或其他周期）K线。价格默认前复权。"""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    change_pct: float | None = None
    turnover_rate: float | None = None
    #: 分钟级 K 线这根走完的时刻（北京时间，不带时区）。日线、周线为 None
    time: datetime | None = None


@dataclass(slots=True)
class FinancialReport:
    """单期财务摘要。"""

    symbol: str
    period: str  # 报告期，如 "2025-06-30"
    revenue: float | None = None  # 营业总收入（元）
    revenue_yoy: float | None = None  # 营收同比 %
    net_profit: float | None = None  # 归母净利润（元）
    net_profit_yoy: float | None = None  # 净利润同比 %
    gross_margin: float | None = None  # 毛利率 %
    net_margin: float | None = None  # 净利率 %
    roe: float | None = None  # 净资产收益率 %
    debt_ratio: float | None = None  # 资产负债率 %
    eps: float | None = None  # 每股收益（元）
    bps: float | None = None  # 每股净资产（元）
    operating_cashflow: float | None = None  # 经营活动现金流净额（元）


@dataclass(slots=True)
class NewsItem:
    """个股新闻 / 公告 / 研报摘要。"""

    title: str
    published_at: datetime | None = None
    source: str = ""
    url: str = ""
    summary: str = ""


@dataclass(slots=True)
class MoneyFlow:
    """单日资金流向（单位：元）。"""

    date: date
    main_net_inflow: float | None = None  # 主力净流入
    main_net_inflow_pct: float | None = None  # 主力净流入占成交额 %
    super_large_net: float | None = None  # 超大单净额
    large_net: float | None = None  # 大单净额
    medium_net: float | None = None  # 中单净额
    small_net: float | None = None  # 小单净额


@dataclass(slots=True)
class FundProfile:
    """ETF 概况。ETF 没有财报，这些才是它的"基本面"。"""

    symbol: str
    name: str = ""
    full_name: str = ""
    fund_type: str = ""  # 基金类型，如 "指数型-股票"
    tracking_index: str = ""  # 跟踪标的
    benchmark: str = ""  # 业绩比较基准
    manager: str = ""  # 基金管理人
    inception_date: date | None = None  # 成立日期
    asset_size: float | None = None  # 资产规模（元）
    asset_size_date: date | None = None  # 规模截止日期
    share_size: float | None = None  # 份额规模（份）
    management_fee: float | None = None  # 管理费率（%/年）
    custody_fee: float | None = None  # 托管费率（%/年）


@dataclass(slots=True)
class FundHolding:
    """ETF 的一只重仓股。"""

    code: str
    name: str
    weight_pct: float | None = None  # 占净值比例 %
    period: str = ""  # 披露期，如 "2025年2季度"


@dataclass(slots=True)
class IndexQuote:
    """大盘指数快照，用于给智能体提供市场环境。"""

    code: str
    name: str
    price: float
    change_pct: float | None = None


@dataclass(slots=True)
class MarketSnapshot:
    """一次分析所需的全部原始数据。由 MarketDataService 组装。"""

    symbol: Symbol
    basic: StockBasic | None = None
    quote: Quote | None = None
    bars: list[Bar] = field(default_factory=list)
    financials: list[FinancialReport] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    money_flow: list[MoneyFlow] = field(default_factory=list)
    indexes: list[IndexQuote] = field(default_factory=list)
    # 以下只对 ETF 有意义（股票为空）
    fund_profile: FundProfile | None = None
    holdings: list[FundHolding] = field(default_factory=list)
    #: ETF 的新闻按板块关键词检索（按代码几乎搜不到东西），记下用的是哪个词
    news_keyword: str = ""
    # 记录每个字段实际由哪个数据源提供，便于前端展示与排查降级
    sources: dict[str, str] = field(default_factory=dict)
    # 组装过程中失败但不致命的部分，前端可提示"该维度数据缺失"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 能力标识

CAP_SEARCH = "search"
CAP_BASIC = "stock_basic"
CAP_QUOTE = "quote"
CAP_BARS = "daily_bars"
CAP_FINANCIALS = "financials"
CAP_NEWS = "news"
CAP_MONEY_FLOW = "money_flow"
CAP_INDEXES = "index_quotes"
# 分钟级 K 线（2 小时图由 60 分钟线合成）
CAP_MINUTE_BARS = "minute_bars"

# ETF 单独成一组能力。同一个 `quote()` / `daily_bars()` 方法，股票和 ETF
# 走的是不同能力标识 —— 因为"能取股票行情"不代表"能取 ETF 行情"：
# 东财盘口接口按 6 开头判断沪市，5 开头的沪市 ETF 会被查成深市；
# Tushare 的 daily 根本不含基金。按能力分开声明，registry 才不会把 ETF
# 派给一个只会查股票的源，报错里也能如实写"未声明支持 ETF"。
CAP_ETF_QUOTE = "etf_quote"
CAP_ETF_BARS = "etf_bars"
CAP_ETF_MINUTE_BARS = "etf_minute_bars"
CAP_ETF_LIST = "etf_list"
CAP_FUND_PROFILE = "fund_profile"
CAP_FUND_HOLDINGS = "fund_holdings"

ALL_CAPABILITIES = (
    CAP_SEARCH,
    CAP_BASIC,
    CAP_QUOTE,
    CAP_BARS,
    CAP_FINANCIALS,
    CAP_NEWS,
    CAP_MONEY_FLOW,
    CAP_INDEXES,
    CAP_MINUTE_BARS,
    CAP_ETF_QUOTE,
    CAP_ETF_BARS,
    CAP_ETF_MINUTE_BARS,
    CAP_ETF_LIST,
    CAP_FUND_PROFILE,
    CAP_FUND_HOLDINGS,
)


# ---------------------------------------------------------------- Provider 协议


@runtime_checkable
class DataProvider(Protocol):
    """A股数据源适配器。

    实现要点：
    - `name` 要和 `DATA_PROVIDER_ORDER` 里的名字一致；
    - `capabilities` 声明自己支持哪些接口，registry 只会调用声明过的；
    - 底层库（akshare/tushare）是同步阻塞的，实现里请用
      `asyncio.to_thread` 包一层，不要阻塞事件循环。
    """

    name: str
    capabilities: frozenset[str]

    async def healthcheck(self) -> bool:
        """依赖是否装好、token 是否配好。返回 False 时 registry 会跳过该源。"""
        ...

    async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]: ...

    async def stock_basic(self, symbol: Symbol) -> StockBasic: ...

    async def quote(self, symbol: Symbol) -> Quote:
        """股票（CAP_QUOTE）和 ETF（CAP_ETF_QUOTE）共用。

        只声明了 CAP_QUOTE 的源不会收到 ETF 代码，反之亦然。
        """
        ...

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]: ...

    async def minute_bars(self, symbol: Symbol, period: int, start: date, end: date) -> list[Bar]:
        """`period` 分钟一根的 K 线（目前只用 60），每根带 `time`（这根走完的时刻）。

        股票（CAP_MINUTE_BARS）和 ETF（CAP_ETF_MINUTE_BARS）共用。
        """
        ...

    async def financials(self, symbol: Symbol, periods: int = 8) -> list[FinancialReport]: ...

    async def news(
        self, symbol: Symbol, limit: int = 20, keyword: str | None = None
    ) -> list[NewsItem]:
        """`keyword` 非空时按关键词搜（ETF 用板块词，按代码搜几乎没有结果）。"""
        ...

    async def money_flow(self, symbol: Symbol, days: int = 10) -> list[MoneyFlow]: ...

    async def index_quotes(self) -> list[IndexQuote]: ...

    async def etf_list(self) -> list[StockBasic]:
        """全部场内 ETF 的代码和名称，建本地代码目录用。"""
        ...

    async def fund_profile(self, symbol: Symbol) -> FundProfile: ...

    async def fund_holdings(self, symbol: Symbol) -> list[FundHolding]:
        """最近一期披露的重仓股，按占净值比例降序。"""
        ...
