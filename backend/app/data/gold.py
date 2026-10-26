"""黄金品种，以及上金所分时的整理（纯计算，不联网）。

两个品种：
    AU9999  上海黄金交易所 Au99.99，元/克。国内金价的基准 —— 银行纸黄金、积存金、
            黄金 ETF 的价格基本都跟着它走（再加上各自的点差、费用）
    XAU     伦敦金（现货黄金），美元/盎司。国际金价，工作日几乎 24 小时在动

**上金所的交易日**

上金所有夜盘：20:00 – 次日 02:30，然后白天 09:00 – 11:30、13:30 – 15:30。
晚上 20:00 开始的夜盘算**下一个交易日**（周五晚上的夜盘算下周一）。

**分时为什么要自己整理**

上金所官网的分时（akshare 的 spot_quotations_sge 取的也是它）是一张按"一天中的
时刻"排的固定网格：00:00–02:30、09:00–15:30、20:00–23:59 每分钟一格，新数据把同一
时刻的旧数据覆盖掉。所以同一张表里混着两个交易日 —— 比如周二 10:00 去取：

    20:00–23:59  周一晚上的夜盘          属于周二这个交易日
    00:00–02:30  周二凌晨的夜盘          属于周二
    09:00–10:00  周二上午                属于周二
    10:01–15:30  还没被覆盖的周一白天     上一个交易日，不能画进今天的图

akshare 按时刻排序、再丢掉"晚于更新时间"的格子，结果白天去取时，前一晚的夜盘
（本交易日的开头）被一起丢掉了。这里拿原始网格，按交易日的顺序
（夜盘 → 次日凌晨 → 白天）重排，只留当前交易日、截至数据时间的格子。
顺带：没被覆盖的上一交易日白天，最后一格（15:30）就是上一交易日的收盘价。

不认节假日：长假后第一个交易日，网格里节前的夜盘可能被当成当天的。一年几次，
而且只影响分时图开头那一段，不值得为它维护一份节假日表。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.data.base import Bar, GoldIntraday, PricePoint

SGE_AU9999 = "AU9999"
LONDON_XAU = "XAU"


@dataclass(frozen=True, slots=True)
class GoldSpec:
    code: str
    #: 详情页标题
    name: str
    #: 首页卡片上的短名
    short_name: str
    unit: str
    decimals: int
    #: 有没有当天分时
    has_intraday: bool
    #: 数据来自哪里，显示在页面上
    source: str


GOLD_SPECS: dict[str, GoldSpec] = {
    SGE_AU9999: GoldSpec(
        code=SGE_AU9999,
        name="上金所 Au99.99",
        short_name="上金所金价",
        unit="元/克",
        decimals=2,
        has_intraday=True,
        source="上海黄金交易所官网（延时行情）",
    ),
    LONDON_XAU: GoldSpec(
        code=LONDON_XAU,
        name="伦敦金",
        short_name="伦敦金",
        unit="美元/盎司",
        decimals=2,
        has_intraday=False,
        source="新浪财经",
    ),
}


def gold_spec(code: str) -> GoldSpec | None:
    return GOLD_SPECS.get(code.strip().upper())


# ---------------------------------------------------------------- 上金所交易时段

NIGHT_OPEN = time(20, 0)
NIGHT_CLOSE = time(2, 30)
DAY_OPEN = time(9, 0)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 30)
DAY_CLOSE = time(15, 30)
#: 凌晨夜盘（02:30 收）和白天（09:00 开）之间的分界
_DAWN = time(3, 0)

#: 交易日里的三段：晚上的夜盘、次日凌晨的夜盘、白天
_EVENING, _SMALL_HOURS, _DAYTIME = 0, 1, 2


def _segment(t: time) -> int | None:
    """这一分钟属于交易日的哪一段。不在交易时间（含午休）返回 None。"""
    if t >= NIGHT_OPEN:
        return _EVENING
    if t <= NIGHT_CLOSE:
        return _SMALL_HOURS
    if DAY_OPEN <= t <= MORNING_CLOSE or AFTERNOON_OPEN <= t <= DAY_CLOSE:
        return _DAYTIME
    return None


def _is_weekend_gap(at: datetime) -> bool:
    """周六 03:00 到下周一 03:00 之间没有任何交易（周日晚上没有夜盘）。"""
    weekday, t = at.weekday(), at.time()
    return (weekday == 5 and t >= _DAWN) or weekday == 6 or (weekday == 0 and t < _DAWN)


def _cutoff(as_of: datetime) -> tuple[int, time]:
    """当前交易日的格子截止到哪里（按交易日顺序比较）。"""
    if _is_weekend_gap(as_of):
        # 周末网格不再更新：最近的一段是周五晚上的夜盘（它属于下周一）
        return (_SMALL_HOURS, NIGHT_CLOSE)
    t = as_of.time()
    if t >= NIGHT_OPEN:
        return (_EVENING, t)
    if t < _DAWN:
        return (_SMALL_HOURS, t)
    return (_DAYTIME, t)


def next_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def previous_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def sge_trading_day(as_of: datetime) -> date:
    """`as_of` 所在的交易日：20:00 以后算下一个交易日，周末算下周一。"""
    d = as_of.date() + timedelta(days=1) if as_of.time() >= NIGHT_OPEN else as_of.date()
    return next_business_day(d)


def sge_slots() -> list[time]:
    """一个交易日里所有分时格子的时刻，按交易日顺序（夜盘 → 凌晨 → 白天）。"""
    out: list[time] = []
    for start, end in (
        (NIGHT_OPEN, time(23, 59)),
        (time(0, 0), NIGHT_CLOSE),
        (DAY_OPEN, MORNING_CLOSE),
        (AFTERNOON_OPEN, DAY_CLOSE),
    ):
        minute = start.hour * 60 + start.minute
        last = end.hour * 60 + end.minute
        while minute <= last:
            out.append(time(minute // 60, minute % 60))
            minute += 1
    return out


def _stamp(segment: int, t: time, trading_day: date) -> datetime:
    """格子的完整时刻。夜盘从上一个工作日晚上开始（周一的夜盘是上周五晚上）。"""
    if segment == _DAYTIME:
        return datetime.combine(trading_day, t)
    evening = previous_business_day(trading_day)
    if segment == _EVENING:
        return datetime.combine(evening, t)
    return datetime.combine(evening + timedelta(days=1), t)


@dataclass(slots=True)
class SgeSession:
    trading_day: date
    points: list[PricePoint]
    #: 网格里还没被覆盖的上一交易日白天的最后一格，即上一交易日收盘价。读不出来是 None
    prev_close: float | None


def arrange_sge_grid(times: Sequence[Any], prices: Sequence[Any], as_of: datetime) -> SgeSession:
    """把上金所的分时网格整理成当前交易日的分时（见模块注释）。"""
    trading_day = sge_trading_day(as_of)
    cutoff = _cutoff(as_of)
    current: dict[tuple[int, time], float] = {}
    stale_close: tuple[time, float] | None = None

    for raw_time, raw_price in zip(times, prices, strict=False):
        t, price = _slot_time(raw_time), _price(raw_price)
        if t is None or price is None:
            continue
        segment = _segment(t)
        if segment is None:
            continue
        key = (segment, t)
        if key <= cutoff:
            current.setdefault(key, price)
        elif segment == _DAYTIME and (stale_close is None or t > stale_close[0]):
            stale_close = (t, price)

    points = [
        PricePoint(time=_stamp(segment, t, trading_day), price=price)
        for (segment, t), price in sorted(current.items())
    ]
    return SgeSession(
        trading_day=trading_day,
        points=points,
        prev_close=stale_close[1] if stale_close else None,
    )


_AS_OF_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2}):(\d{2})(?::(\d{2}))?")


def parse_sge_as_of(text: Any) -> datetime | None:
    """上金所的数据时间，形如 "2025年04月14日 22:28:55"。"""
    m = _AS_OF_RE.search(str(text or ""))
    if not m:
        return None
    year, month, day, hour, minute, second = m.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
    except ValueError:
        return None


def _slot_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", str(value or "").strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    return time(hour, minute) if hour < 24 and minute < 60 else None


def _price(value: Any) -> float | None:
    """格子里的价格。空格子、0（从没写过的格子）都不算。"""
    try:
        price = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None  # NaN 也会在这里被筛掉


# ---------------------------------------------------------------- 拼报价、补日线


def resolve_prev_close(
    bars: Sequence[Bar], trading_day: date, grid_prev: float | None
) -> float | None:
    """上一交易日收盘价。

    优先用日线；晚上日线常常还没更新到刚收盘的那天，这时用分时网格里读出来的。
    """
    before = [b for b in bars if b.date < trading_day]
    if before and before[-1].date >= previous_business_day(trading_day):
        return before[-1].close
    if grid_prev is not None:
        return grid_prev
    return before[-1].close if before else None


def with_session_bar(bars: list[Bar], intraday: GoldIntraday | None) -> list[Bar]:
    """日线末尾补上当前交易日（由分时合成）。

    上金所的日线要收盘后才出，盘中图上最后一根会停在昨天，和页面顶上的价格对不上。
    官方日线已经有这一天了就不补。
    """
    if intraday is None or not intraday.points:
        return bars
    if bars and bars[-1].date >= intraday.trading_day:
        return bars
    prices = [p.price for p in intraday.points]
    bar = Bar(
        date=intraday.trading_day,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
    )
    prev = intraday.prev_close or (bars[-1].close if bars else None)
    if prev:
        bar.change_pct = round((bar.close / prev - 1) * 100, 2)
    return [*bars, bar]
