"""实时行情取不到时用日线兜底。

由来（用户机器上的真实组合）：

    日线       正常   （新浪）
    实时行情    全挂   —— 东财返回空响应、雪球要 token、tushare 没配

也就是 K线是真的、只有"最新价"拿不到。直接报错的话整个界面没有价格、
分析也跑不起来；而最近一根日线的收盘价是完全真实的数据，
收盘后它本来就等于"现价"。

这里要钉死两件事：
  1. 兜底能用；
  2. 兜底结果**必须**标明不是实时价、以及是哪一天的 ——
     盘中把昨天的收盘价当现价展示而不告知，比没有价格危险得多。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.core.errors import DataNotFound
from app.data.base import ALL_CAPABILITIES, Bar, Quote
from app.data.mock_provider import MockProvider
from app.data.registry import ProviderRegistry
from app.data.symbols import normalize
from app.services.quote_source import quote_with_fallback

SYMBOL = normalize("600519")

BARS = [
    Bar(
        date=date(2026, 9, 28),
        open=1400.0,
        high=1420.0,
        low=1390.0,
        close=1410.0,
        volume=1e6,
        amount=1.4e9,
        change_pct=None,
        turnover_rate=0.8,
    ),
    Bar(
        date=date(2026, 9, 29),
        open=1412.0,
        high=1435.0,
        low=1405.0,
        close=1430.0,
        volume=1.2e6,
        amount=1.7e9,
        change_pct=None,
        turnover_rate=0.9,
    ),
]


class _NoRealtime:
    """日线能取、实时不行 —— 用户遇到的正是这种组合。"""

    name = "bars-only"
    capabilities = frozenset(ALL_CAPABILITIES)

    def __init__(self, bars: list[Bar] | None = None) -> None:
        self._bars = BARS if bars is None else bars

    async def healthcheck(self) -> bool:
        return True

    async def quote(self, _symbol):  # type: ignore[no-untyped-def]
        raise DataNotFound("实时接口全挂：东财返回空响应、雪球要 token")

    async def daily_bars(self, _symbol, _start, _end, adjust: str = "qfq"):  # type: ignore[no-untyped-def]
        return list(self._bars)

    def __getattr__(self, _name):  # type: ignore[no-untyped-def]
        async def _unsupported(*a, **k):
            raise DataNotFound("该源只提供日线")

        return _unsupported


async def test_falls_back_to_last_bar() -> None:
    registry = ProviderRegistry([_NoRealtime()], timeout=5)
    q = await quote_with_fallback(registry, SYMBOL)

    assert q.price == 1430.0, "取最后一根日线的收盘价"
    assert q.prev_close == 1410.0, "昨收取倒数第二根"
    assert (q.open, q.high, q.low) == (1412.0, 1435.0, 1405.0)
    assert q.volume == 1.2e6
    assert q.turnover_rate == 0.9


async def test_fallback_is_clearly_marked_not_realtime() -> None:
    """**本文件最重要的一条。**

    不标记的话，盘中看到的其实是上一个交易日的收盘价，
    而界面看起来和实时价一模一样 —— 那比没有价格危险得多。
    """
    registry = ProviderRegistry([_NoRealtime()], timeout=5)
    q = await quote_with_fallback(registry, SYMBOL)

    assert q.is_realtime is False
    assert q.quote_date == date(2026, 9, 29)


async def test_fallback_computes_change_pct_from_prev_bar() -> None:
    registry = ProviderRegistry([_NoRealtime()], timeout=5)
    q = await quote_with_fallback(registry, SYMBOL)
    # (1430 - 1410) / 1410 = 1.418...
    assert q.change_pct == pytest.approx(1.42, abs=0.01)


async def test_fallback_keeps_provider_change_pct_when_present() -> None:
    # Bar 是 slots dataclass，没有 __dict__，改字段要用 replace
    bars = [BARS[0], replace(BARS[1], change_pct=1.39)]
    registry = ProviderRegistry([_NoRealtime(bars)], timeout=5)
    q = await quote_with_fallback(registry, SYMBOL)
    assert q.change_pct == 1.39, "数据源给了涨跌幅就用它的，别自己算"


async def test_fallback_leaves_valuation_empty() -> None:
    """日线不含估值。留空而不是填 0 —— 0 会被当成真实的"市盈率为零"。"""
    registry = ProviderRegistry([_NoRealtime()], timeout=5)
    q = await quote_with_fallback(registry, SYMBOL)
    assert q.pe_ttm is None
    assert q.pb is None
    assert q.total_market_cap is None


async def test_single_bar_has_no_prev_close() -> None:
    registry = ProviderRegistry([_NoRealtime([BARS[1]])], timeout=5)
    q = await quote_with_fallback(registry, SYMBOL)
    assert q.price == 1430.0
    assert q.prev_close is None
    assert q.change_pct is None, "没有昨收就别编涨跌幅"


async def test_realtime_wins_when_available() -> None:
    """实时接口能用时就不该走兜底。"""
    registry = ProviderRegistry([MockProvider()], timeout=5)
    q = await quote_with_fallback(registry, SYMBOL)
    assert q.is_realtime is True
    assert q.quote_date is None


async def test_raises_realtime_error_when_bars_also_missing() -> None:
    """两条路都不通时，抛的应该是实时接口的原始报错 —— 那个信息对排查更有用。"""

    class _Dead(_NoRealtime):
        name = "dead"

        async def daily_bars(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise DataNotFound("日线也没有")

    registry = ProviderRegistry([_Dead()], timeout=5)
    with pytest.raises(DataNotFound, match="quote"):
        await quote_with_fallback(registry, SYMBOL)


async def test_empty_bar_list_is_not_a_quote() -> None:
    registry = ProviderRegistry([_NoRealtime([])], timeout=5)
    with pytest.raises(DataNotFound):
        await quote_with_fallback(registry, SYMBOL)


def test_quote_defaults_to_realtime() -> None:
    """普通数据源构造的 Quote 默认就是实时的，不用每个适配器都写一遍。"""
    q = Quote(symbol="600519.SH", name="贵州茅台", price=1430.0)
    assert q.is_realtime is True
    assert q.quote_date is None
