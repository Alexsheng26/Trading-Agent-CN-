"""取行情：实时接口拿不到时，用最近一根日线兜底。

**为什么需要它**

用户机器上出现过这样的组合（诊断截图）：

    日线       正常   （新浪）
    大盘指数    正常   （东财）
    新闻       正常   （东财）
    实时行情    全挂   —— 东财返回空响应、雪球要 token、tushare 没配

也就是说：**K线是真的、只有"最新价"拿不到。**
这种情况下如果直接报错，整个界面就没有价格，分析也跑不起来 ——
而实际上最近一根日线的收盘价是完全真实、且大部分时间就是"最新价"
（收盘后到次日开盘前，收盘价就是现价）。

所以这里兜一层：实时接口全失败时，取最近一根日线，按收盘价造一份行情，
并**明确标记 `is_realtime=False` 和对应的交易日**。

标记是这件事的关键。盘中把昨天的收盘价当成现价展示而不告知，
比没有价格危险得多 —— 界面会显著标注"收盘价 · X月X日（非实时）"，
分析用的行情简报里也会写明。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.core.clock import now_utc
from app.core.errors import DataNotFound
from app.data.base import Quote
from app.data.registry import ProviderRegistry
from app.data.symbols import Symbol

log = logging.getLogger(__name__)

#: 往回取多少天的日线来找最后一根。留足长假与停牌的余量
_LOOKBACK_DAYS = 30


async def quote_with_fallback(registry: ProviderRegistry, symbol: Symbol) -> Quote:
    """先试实时接口，全失败则用最近一根日线兜底。

    两条路都走不通才抛 `DataNotFound` —— 那时候确实没有任何真实数据，
    该让界面明确报错，而不是编一个数字。
    """
    try:
        quote = await registry.quote(symbol)
    except DataNotFound as realtime_error:
        log.info("%s 实时行情不可用，尝试用日线兜底：%s", symbol.canonical, realtime_error)
        fallback = await _from_last_bar(registry, symbol)
        if fallback is None:
            # 日线也没有：把实时接口的原始报错抛出去，那个信息对排查更有用
            raise
        quote = fallback
    if not quote.name:
        quote.name = await _directory_name(symbol)
    return quote


async def _directory_name(symbol: Symbol) -> str:
    """没名字的行情（日线兜底、部分 ETF 接口）从本地代码目录补名称。"""
    from app.services import symbol_index  # noqa: PLC0415 —— 避免循环导入

    try:
        return await symbol_index.name_of(symbol.canonical)
    except Exception as exc:  # 名称只是锦上添花，查不到也不能让行情失败
        log.info("查 %s 的目录名称失败：%s", symbol.canonical, exc)
        return ""


async def _from_last_bar(registry: ProviderRegistry, symbol: Symbol) -> Quote | None:
    today = now_utc().date()
    try:
        bars = await registry.daily_bars(symbol, today - timedelta(days=_LOOKBACK_DAYS), today)
    except DataNotFound:
        return None
    if not bars:
        return None

    last = bars[-1]
    prev_close = bars[-2].close if len(bars) >= 2 else None
    change_pct = last.change_pct
    if change_pct is None and prev_close:
        change_pct = round((last.close - prev_close) / prev_close * 100, 2)

    return Quote(
        symbol=symbol.canonical,
        # 日线接口不带名称。留空，quote_with_fallback 会从本地代码目录补
        name="",
        price=last.close,
        prev_close=prev_close,
        open=last.open,
        high=last.high,
        low=last.low,
        change_pct=change_pct,
        volume=last.volume,
        amount=last.amount,
        turnover_rate=last.turnover_rate,
        # 日线不含估值，如实留空
        pe_ttm=None,
        pb=None,
        total_market_cap=None,
        float_market_cap=None,
        updated_at=now_utc(),
        is_realtime=False,
        quote_date=last.date,
    )
