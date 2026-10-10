"""把 K 线合成更长的周期：60 分钟线 → 2 小时线，日线 → 周线。

为什么自己合成：
  - 2 小时线没有数据源直接给 —— 东财、新浪的分钟线只有 1/5/15/30/60 分钟；
  - 周线各家接口都有，但参数、复权方式、日期标在周几都不一样。
    从已经走通降级链的日线合成，换哪个数据源结果都一致，也不用多一套降级。

都是纯函数，不联网。
"""

from __future__ import annotations

from datetime import date, datetime, time

from app.data.base import Bar

#: A 股上午 9:30-11:30、下午 13:00-15:00，每天正好 4 小时 —— 2 小时线上午、下午各一根
MORNING_CLOSE = time(11, 30)
AFTERNOON_CLOSE = time(15, 0)


def to_two_hour(bars: list[Bar]) -> list[Bar]:
    """60 分钟线 → 2 小时线（上午一根、下午一根）。

    时间标在所属半天的收盘时刻（11:30 / 15:00）。盘中还没走完的那半天照样合成，
    标签不变 —— 这样同一根 K 线在一天里不会一会儿标 10:30、一会儿标 11:30。
    没有 `time` 的（不是分钟线）跳过。
    """
    groups: dict[tuple[date, int], list[Bar]] = {}
    for bar in sorted(bars, key=_bar_time):
        if bar.time is None:
            continue
        half = 0 if bar.time.time() <= MORNING_CLOSE else 1
        groups.setdefault((bar.time.date(), half), []).append(bar)

    out = [
        _merge(chunk, day, datetime.combine(day, MORNING_CLOSE if half == 0 else AFTERNOON_CLOSE))
        for (day, half), chunk in sorted(groups.items())
    ]
    return _fill_change_pct(out)


def to_weekly(bars: list[Bar]) -> list[Bar]:
    """日线 → 周线（按自然周）。日期标在这周最后一个交易日 —— 节假日所在的周也对。"""
    groups: dict[tuple[int, int], list[Bar]] = {}
    for bar in sorted(bars, key=lambda b: b.date):
        year, week, _ = bar.date.isocalendar()
        groups.setdefault((year, week), []).append(bar)

    out = [_merge(chunk, chunk[-1].date, None) for _, chunk in sorted(groups.items())]
    return _fill_change_pct(out)


def _bar_time(bar: Bar) -> datetime:
    return bar.time or datetime.combine(bar.date, time())


def _merge(chunk: list[Bar], day: date, at: datetime | None) -> Bar:
    return Bar(
        date=day,
        time=at,
        open=chunk[0].open,
        high=max(b.high for b in chunk),
        low=min(b.low for b in chunk),
        close=chunk[-1].close,
        volume=sum(b.volume for b in chunk),
        amount=sum(b.amount for b in chunk),
    )


def _fill_change_pct(bars: list[Bar]) -> list[Bar]:
    """涨跌幅按上一根的收盘价算。第一根没有前值，留空 —— 不拿 0 冒充。"""
    for prev, cur in zip(bars, bars[1:], strict=False):
        if prev.close:
            cur.change_pct = round((cur.close / prev.close - 1) * 100, 2)
    return bars
