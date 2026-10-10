"""行情相关的响应模型。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class StockBasicOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    board: str = ""
    industry: str = ""
    list_date: date | None = None


class QuoteOut(BaseModel):
    symbol: str
    name: str
    price: float
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    change_pct: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    total_market_cap: float | None = None
    float_market_cap: float | None = None
    updated_at: datetime | None = None
    #: False = 这是最近一根日线的收盘价，不是实时价。前端必须显著标注
    is_realtime: bool = True
    quote_date: date | None = None
    #: stock | etf。前端据此切换展示（ETF 没有市盈率，价格三位小数）
    asset_type: str = "stock"
    #: 价格小数位：股票 2，ETF 3
    price_decimals: int = 2

    @classmethod
    def from_quote(cls, quote: Any) -> QuoteOut:
        """由数据层的 `Quote` 构造，顺带补上品种和价格精度。"""
        from app.data.symbols import try_normalize  # noqa: PLC0415

        data = {k: v for k, v in asdict(quote).items() if k != "volume"}
        sym = try_normalize(quote.symbol)
        if sym is not None:
            data["asset_type"] = sym.asset_type
            data["price_decimals"] = sym.price_decimals
        return cls(**data)


class BarOut(BaseModel):
    date: date
    #: 分钟级 K 线（2 小时线等）这根走完的时刻，北京时间、不带时区。日线、周线为空
    time: datetime | None = None
    open: float
    high: float
    low: float
    close: float
    volume: float = 0
    amount: float = 0
    change_pct: float | None = None


class IndexQuoteOut(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float | None = None


class FibLevelOut(BaseModel):
    ratio: float
    label: str
    price: float
    kind: str  # swing | retracement | extension


class FibAnalysisOut(BaseModel):
    direction: str  # 上升 | 下降
    swing_high: float
    swing_low: float
    swing_high_date: date | None = None
    swing_low_date: date | None = None
    lookback: int = 0
    retracement_pct: float | None = None
    levels: list[FibLevelOut] = Field(default_factory=list)
    nearest_support: FibLevelOut | None = None
    nearest_resistance: FibLevelOut | None = None


class IndicatorsOut(BaseModel):
    """技术指标快照，字段与 IndicatorSnapshot 对应。"""

    close: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_hist: float | None = None
    rsi6: float | None = None
    rsi14: float | None = None
    boll_upper: float | None = None
    boll_mid: float | None = None
    boll_lower: float | None = None
    kdj_k: float | None = None
    kdj_d: float | None = None
    kdj_j: float | None = None
    atr14: float | None = None
    atr_pct: float | None = None
    high_60: float | None = None
    low_60: float | None = None
    pct_from_high_60: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    volatility_20d: float | None = None
    volume_ratio_5_20: float | None = None
    fib: FibAnalysisOut | None = None
    patterns: list[str] = Field(default_factory=list)


class ProviderStatusOut(BaseModel):
    name: str
    healthy: bool
    capabilities: list[str]
