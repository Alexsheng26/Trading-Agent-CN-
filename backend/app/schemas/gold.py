"""金价接口的响应模型。时刻一律是北京时间、不带时区（和 K 线的 `time` 一样）。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.data.base import GoldIntraday, GoldQuote
from app.services.gold import GoldItem


class GoldQuoteOut(BaseModel):
    price: float
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    as_of: datetime | None = None
    #: False = 分时取不到，这是最近一根日线的收盘价。前端必须标注
    is_realtime: bool = True
    quote_date: date | None = None

    @classmethod
    def build(cls, quote: GoldQuote) -> GoldQuoteOut:
        return cls(
            price=quote.price,
            prev_close=quote.prev_close,
            change=quote.change,
            change_pct=quote.change_pct,
            open=quote.open,
            high=quote.high,
            low=quote.low,
            as_of=quote.as_of,
            is_realtime=quote.is_realtime,
            quote_date=quote.quote_date,
        )


class GoldItemOut(BaseModel):
    code: str
    name: str
    short_name: str
    unit: str
    decimals: int
    has_intraday: bool
    source: str
    quote: GoldQuoteOut | None = None
    #: 取不到时的原因
    error: str | None = None

    @classmethod
    def build(cls, item: GoldItem) -> GoldItemOut:
        spec = item.spec
        return cls(
            code=spec.code,
            name=spec.name,
            short_name=spec.short_name,
            unit=spec.unit,
            decimals=spec.decimals,
            has_intraday=spec.has_intraday,
            source=spec.source,
            quote=GoldQuoteOut.build(item.quote) if item.quote else None,
            error=item.error,
        )


class PricePointOut(BaseModel):
    time: datetime
    price: float


class GoldIntradayOut(BaseModel):
    code: str
    #: 这批分时属于哪个交易日（上金所 20:00 开始的夜盘算下一个交易日）
    trading_day: date
    as_of: datetime | None = None
    #: 上一交易日收盘价，分时图上的基准线
    prev_close: float | None = None
    points: list[PricePointOut]

    @classmethod
    def build(cls, intraday: GoldIntraday) -> GoldIntradayOut:
        return cls(
            code=intraday.code,
            trading_day=intraday.trading_day,
            as_of=intraday.as_of,
            prev_close=intraday.prev_close,
            points=[PricePointOut(time=p.time, price=p.price) for p in intraday.points],
        )
