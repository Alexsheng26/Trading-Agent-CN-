"""金价：首页卡片和金价页用的报价、分时、日K / 周K。

只看行情，不进 AI 分析 —— 分析师是按股票设计的（财报、估值），黄金要看的是美元、
利率、央行购金，得另做一套。想让 AI 分析黄金，分析黄金 ETF。

两个品种各取各的、各自失败：上金所挂了不影响伦敦金，反之亦然。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from app.core.clock import now_cst
from app.core.errors import DataNotFound
from app.data.base import Bar, GoldIntraday, GoldQuote
from app.data.gold import (
    GOLD_SPECS,
    SGE_AU9999,
    GoldSpec,
    resolve_prev_close,
    with_session_bar,
)
from app.data.registry import ProviderRegistry
from app.services.bar_periods import to_weekly

#: 拼报价时往回看多少天的日线：够跨过国庆、春节长假找到上一个交易日
_PREV_CLOSE_LOOKBACK = timedelta(days=20)


@dataclass(slots=True)
class GoldItem:
    spec: GoldSpec
    quote: GoldQuote | None
    #: 取不到时的原因（直接显示给用户）
    error: str | None = None


async def gold_overview(registry: ProviderRegistry) -> list[GoldItem]:
    """全部黄金品种的报价，并发取。"""

    async def one(spec: GoldSpec) -> GoldItem:
        try:
            return GoldItem(spec=spec, quote=await gold_quote(registry, spec.code))
        except DataNotFound as exc:
            return GoldItem(spec=spec, quote=None, error=str(exc))

    return list(await asyncio.gather(*(one(spec) for spec in GOLD_SPECS.values())))


async def gold_quote(registry: ProviderRegistry, code: str) -> GoldQuote:
    if code == SGE_AU9999:
        return await _sge_quote(registry)
    return await registry.gold_quote(code)


async def _sge_quote(registry: ProviderRegistry) -> GoldQuote:
    """上金所没有单独的报价接口：最新价取分时的最后一个点，昨收取日线。

    分时取不到时，退回最近一根日线的收盘价，并标成非实时。
    """
    today = now_cst().date()
    intraday, bars = await asyncio.gather(
        gold_intraday(registry, SGE_AU9999),
        registry.gold_bars(SGE_AU9999, today - _PREV_CLOSE_LOOKBACK, today),
        return_exceptions=True,
    )
    for result in (intraday, bars):
        if isinstance(result, BaseException) and not isinstance(result, DataNotFound):
            raise result

    if isinstance(intraday, GoldIntraday):
        prices = [p.price for p in intraday.points]
        return _quote(
            SGE_AU9999,
            price=prices[-1],
            prev_close=intraday.prev_close,
            open_=prices[0],
            high=max(prices),
            low=min(prices),
            as_of=intraday.as_of,
        )

    if isinstance(bars, list) and bars:
        last = bars[-1]
        prev = bars[-2].close if len(bars) > 1 else None
        quote = _quote(SGE_AU9999, price=last.close, prev_close=prev)
        quote.open, quote.high, quote.low = last.open, last.high, last.low
        quote.is_realtime = False
        quote.quote_date = last.date
        return quote

    reasons = [str(r) for r in (intraday, bars) if isinstance(r, DataNotFound)]
    raise DataNotFound("；".join(reasons) or "上金所金价取不到")


def _quote(
    code: str,
    *,
    price: float,
    prev_close: float | None,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    as_of: datetime | None = None,
) -> GoldQuote:
    return GoldQuote(
        code=code,
        price=price,
        prev_close=prev_close,
        change=round(price - prev_close, 4) if prev_close else None,
        change_pct=round((price / prev_close - 1) * 100, 2) if prev_close else None,
        open=open_,
        high=high,
        low=low,
        as_of=as_of,
    )


async def gold_intraday(registry: ProviderRegistry, code: str) -> GoldIntraday:
    """当前交易日的分时，昨收按日线核对过（见 `resolve_prev_close`）。"""
    intraday = await registry.gold_intraday(code)
    try:
        bars = await registry.gold_bars(
            code, intraday.trading_day - _PREV_CLOSE_LOOKBACK, intraday.trading_day
        )
    except DataNotFound:
        bars = []
    # 复制一份再改：数据源可能把同一个对象缓存着
    return replace(
        intraday,
        prev_close=resolve_prev_close(bars, intraday.trading_day, intraday.prev_close),
    )


async def gold_bars(
    registry: ProviderRegistry, code: str, period: Literal["day", "week"], days: int
) -> list[Bar]:
    """日K / 周K。上金所盘中的那一天由分时合成，补在日线最后（见 `with_session_bar`）。"""
    end = now_cst().date()
    bars = await registry.gold_bars(code, end - timedelta(days=days), end)
    if code == SGE_AU9999:
        try:
            intraday: GoldIntraday | None = await gold_intraday(registry, code)
        except DataNotFound:
            intraday = None  # 分时取不到就只画到上一根官方日线，不影响 K 线本身
        bars = with_session_bar(bars, intraday)
    return to_weekly(bars) if period == "week" else bars
