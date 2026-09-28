"""A股交易时段。

前端据此决定要不要轮询行情 —— 收盘后每 5 秒拉一次价格毫无意义，
既浪费请求又把免费数据源打得更快挂掉。

A股交易时间（北京时间，周一至周五）：
    09:15-09:25  集合竞价（不成交，但会有参考价）
    09:30-11:30  上午连续竞价
    11:30-13:00  午间休市
    13:00-15:00  下午连续竞价

⚠️ 不含节假日。完整交易日历需要外部数据（AKShare 的
`tool_trade_date_hist_sina` 能取），节假日会被误判为交易日 ——
后果只是多轮询几次、价格不变，可以接受；反过来漏判交易日才严重，
所以这里刻意宁松不严。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from app.core.clock import CST, now_cst

Phase = Literal["盘前", "集合竞价", "连续竞价", "午间休市", "已收盘", "非交易日"]

_AUCTION_OPEN = time(9, 15)
_AUCTION_CLOSE = time(9, 25)
_MORNING_OPEN = time(9, 30)
_MORNING_CLOSE = time(11, 30)
_AFTERNOON_OPEN = time(13, 0)
_AFTERNOON_CLOSE = time(15, 0)


@dataclass(slots=True)
class SessionState:
    phase: Phase
    #: 是否处于价格会变动的时段（连续竞价或集合竞价）
    is_live: bool
    #: 建议的轮询间隔（秒）。非交易时段为 None，表示不必轮询。
    poll_interval: int | None
    #: 下一次状态变化的时间点（北京时间），用于前端显示"距开盘还有 X"
    next_change: datetime | None
    #: 判断所用的北京时间，便于排查时区问题
    as_of: datetime
    note: str = ""


def session_state(at: datetime | None = None) -> SessionState:
    """判断给定时刻（默认当前）处于哪个交易时段。"""
    now = at.astimezone(CST) if at is not None else now_cst()
    t = now.time()
    note = "未计入法定节假日，节假日会被当作交易日"

    if now.weekday() >= 5:  # 周六 / 周日
        return SessionState(
            phase="非交易日",
            is_live=False,
            poll_interval=None,
            next_change=_next_weekday_open(now),
            as_of=now,
            note="周末休市",
        )

    if t < _AUCTION_OPEN:
        return SessionState("盘前", False, None, _at(now, _AUCTION_OPEN), now, note)
    if t < _AUCTION_CLOSE:
        # 集合竞价期间有参考价变化，但成交要等 9:25 撮合，轮询可以慢一些
        return SessionState("集合竞价", True, 15, _at(now, _MORNING_OPEN), now, note)
    if t < _MORNING_OPEN:
        return SessionState("盘前", False, None, _at(now, _MORNING_OPEN), now, note)
    if t < _MORNING_CLOSE:
        return SessionState("连续竞价", True, 5, _at(now, _MORNING_CLOSE), now, note)
    if t < _AFTERNOON_OPEN:
        return SessionState("午间休市", False, None, _at(now, _AFTERNOON_OPEN), now, note)
    if t < _AFTERNOON_CLOSE:
        return SessionState("连续竞价", True, 5, _at(now, _AFTERNOON_CLOSE), now, note)

    return SessionState("已收盘", False, None, _next_weekday_open(now), now, note)


def _at(day: datetime, t: time) -> datetime:
    return day.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


def _next_weekday_open(now: datetime) -> datetime:
    """下一个工作日的集合竞价开始时间。"""
    day = now + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return _at(day, _AUCTION_OPEN)
