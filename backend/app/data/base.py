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

ALL_CAPABILITIES = (
    CAP_SEARCH,
    CAP_BASIC,
    CAP_QUOTE,
    CAP_BARS,
    CAP_FINANCIALS,
    CAP_NEWS,
    CAP_MONEY_FLOW,
    CAP_INDEXES,
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

    async def quote(self, symbol: Symbol) -> Quote: ...

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]: ...

    async def financials(self, symbol: Symbol, periods: int = 8) -> list[FinancialReport]: ...

    async def news(self, symbol: Symbol, limit: int = 20) -> list[NewsItem]: ...

    async def money_flow(self, symbol: Symbol, days: int = 10) -> list[MoneyFlow]: ...

    async def index_quotes(self) -> list[IndexQuote]: ...
