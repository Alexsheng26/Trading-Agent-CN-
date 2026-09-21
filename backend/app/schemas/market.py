"""行情相关的响应模型。"""

from __future__ import annotations

from datetime import date, datetime

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


class BarOut(BaseModel):
    date: date
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
    patterns: list[str] = Field(default_factory=list)


class ProviderStatusOut(BaseModel):
    name: str
    healthy: bool
    capabilities: list[str]
