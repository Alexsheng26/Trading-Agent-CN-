"""K 线周期：2 小时线（60 分钟合成）、周线（日线合成），以及分钟线的取数与映射。

用户要在 K 线上切「2 小时 / 日 K / 周 K」。数据源都不直接给 2 小时线，
周线各家口径又不一，所以都由我们自己合成 —— 合成错了，图上的每一根都是错的。
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, time

import pytest

from app.data.base import Bar
from app.data.symbols import normalize
from app.services.bar_periods import to_two_hour, to_weekly


def _hour(day: date, hh: int, mm: int, o: float, h: float, l: float, c: float, v: float) -> Bar:  # noqa: E741
    return Bar(
        date=day,
        time=datetime.combine(day, time(hh, mm)),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        amount=v * 10,
    )


D1, D2 = date(2026, 10, 9), date(2026, 10, 12)

HOURLY = [
    _hour(D1, 10, 30, 10.0, 10.5, 9.9, 10.2, 100),
    _hour(D1, 11, 30, 10.2, 10.3, 10.0, 10.1, 50),
    _hour(D1, 14, 0, 10.1, 10.8, 10.1, 10.7, 60),
    _hour(D1, 15, 0, 10.7, 10.9, 10.4, 10.5, 90),
    _hour(D2, 10, 30, 10.5, 10.6, 10.0, 10.0, 120),
]


# ---------------------------------------------------------------- 2 小时线


def test_two_hour_merges_each_half_day() -> None:
    bars = to_two_hour(HOURLY)

    assert [b.time for b in bars] == [
        datetime(2026, 10, 9, 11, 30),
        datetime(2026, 10, 9, 15, 0),
        datetime(2026, 10, 12, 11, 30),
    ]
    am, pm, _ = bars
    assert (am.open, am.high, am.low, am.close) == (10.0, 10.5, 9.9, 10.1)
    assert (pm.open, pm.high, pm.low, pm.close) == (10.1, 10.9, 10.1, 10.5)
    assert am.volume == 150 and pm.volume == 150
    assert am.amount == 1500


def test_two_hour_unfinished_half_keeps_its_closing_label() -> None:
    """盘中上午只走完 10:30 那根：合成出来仍标 11:30，不会一会儿 10:30 一会儿 11:30。"""
    last = to_two_hour(HOURLY)[-1]
    assert last.time == datetime(2026, 10, 12, 11, 30)
    assert (last.open, last.close, last.volume) == (10.5, 10.0, 120)


def test_two_hour_change_pct_is_against_previous_bar() -> None:
    am, pm, nxt = to_two_hour(HOURLY)
    assert am.change_pct is None  # 第一根没有前值，不拿 0 冒充
    assert pm.change_pct == round((10.5 / 10.1 - 1) * 100, 2)
    assert nxt.change_pct == round((10.0 / 10.5 - 1) * 100, 2)


def test_two_hour_sorts_input_and_skips_daily_bars() -> None:
    daily = Bar(date=D1, open=1, high=1, low=1, close=1)
    bars = to_two_hour([*reversed(HOURLY), daily])
    assert len(bars) == 3
    assert bars[0].open == 10.0


# ---------------------------------------------------------------- 周线


def _day(d: date, o: float, h: float, l: float, c: float) -> Bar:  # noqa: E741
    return Bar(date=d, open=o, high=h, low=l, close=c, volume=10, amount=100)


def test_weekly_groups_by_calendar_week_and_labels_last_trading_day() -> None:
    days = [
        _day(date(2026, 9, 28), 10, 11, 9.5, 10.5),  # 周一
        _day(date(2026, 9, 29), 10.5, 12, 10, 11.8),  # 周二；之后国庆休市
        _day(date(2026, 10, 9), 11.8, 12.2, 11, 11.2),  # 节后只有周五一天
        _day(date(2026, 10, 12), 11.2, 11.5, 10.8, 11.0),  # 下周一
    ]
    weeks = to_weekly(days)

    assert [w.date for w in weeks] == [date(2026, 9, 29), date(2026, 10, 9), date(2026, 10, 12)]
    first = weeks[0]
    assert (first.open, first.high, first.low, first.close) == (10, 12, 9.5, 11.8)
    assert first.volume == 20 and first.amount == 200
    assert first.time is None
    assert weeks[1].change_pct == round((11.2 / 11.8 - 1) * 100, 2)


def test_weekly_handles_year_boundary() -> None:
    """2025-12-29 ~ 2026-01-02 是同一个自然周（ISO 2026 年第 1 周），不能拆成两根。"""
    days = [
        _day(date(2025, 12, 29), 1, 2, 1, 1.5),
        _day(date(2025, 12, 31), 1.5, 1.6, 1.4, 1.4),
        _day(date(2026, 1, 2), 1.4, 1.7, 1.3, 1.6),
    ]
    weeks = to_weekly(days)
    assert len(weeks) == 1
    assert weeks[0].date == date(2026, 1, 2)
    assert (weeks[0].open, weeks[0].close, weeks[0].high, weeks[0].low) == (1, 1.6, 2, 1)


# ---------------------------------------------------------------- 模拟数据源


async def test_mock_minute_bars_agree_with_daily_bars() -> None:
    """离线模式的 60 分钟线拼回一天，必须和那天的日线一致，否则切周期时图会对不上。"""
    from app.data.mock_provider import MockProvider

    provider = MockProvider()
    sym = normalize("600519")
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    daily = {b.date: b for b in await provider.daily_bars(sym, start, end)}
    hourly = await provider.minute_bars(sym, 60, start, end)

    assert len(hourly) == 4 * len(daily)
    for d, bar in daily.items():
        chunk = [h for h in hourly if h.date == d]
        assert [h.time.time() for h in chunk if h.time] == [
            time(10, 30),
            time(11, 30),
            time(14, 0),
            time(15, 0),
        ]
        assert chunk[0].open == bar.open and chunk[-1].close == bar.close
        assert max(h.high for h in chunk) == bar.high
        assert min(h.low for h in chunk) == bar.low
        for h in chunk:
            assert h.low <= min(h.open, h.close) <= max(h.open, h.close) <= h.high


# ---------------------------------------------------------------- 真实数据源的字段映射（不联网）

requires_pandas = pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None, reason="未安装 pandas（随 akshare 一起装）"
)


def _recording(provider, frame):  # type: ignore[no-untyped-def]
    calls: list[tuple[str, dict]] = []

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((func_name, kwargs))
        return frame

    provider._call = fake_call
    return calls


def _em_frame():  # type: ignore[no-untyped-def]
    """东财分钟线（stock_zh_a_hist_min_em / fund_etf_hist_min_em）返回的形状。"""
    import pandas as pd

    return pd.DataFrame(
        {
            "时间": ["2026-10-09 10:30:00", "2026-10-09 11:30:00"],
            "开盘": [1453.0, 1455.2],
            "收盘": [1455.2, 1451.0],
            "最高": [1458.0, 1456.0],
            "最低": [1450.1, 1449.9],
            "涨跌幅": [0.15, -0.29],
            "涨跌额": [2.2, -4.2],
            "成交量": [12000.0, 8000.0],
            "成交额": [1.74e9, 1.16e9],
            "振幅": [0.54, 0.42],
            "换手率": [0.1, 0.06],
        }
    )


@requires_pandas
@pytest.mark.parametrize(
    ("code", "func"),
    [("600519", "stock_zh_a_hist_min_em"), ("512480", "fund_etf_hist_min_em")],
)
async def test_akshare_minute_bars_mapping(code: str, func: str) -> None:
    """ETF 必须走基金那个函数：个股的按"6 开头"判断沪市，会把 512480 查成深市。"""
    from app.data.akshare_provider import AkshareProvider

    provider = AkshareProvider()
    calls = _recording(provider, _em_frame())

    bars = await provider.minute_bars(normalize(code), 60, date(2026, 9, 25), date(2026, 10, 9))

    assert calls == [
        (
            func,
            {
                "symbol": code,
                "start_date": "2026-09-25 09:00:00",
                "end_date": "2026-10-09 15:30:00",
                "period": "60",
                "adjust": "qfq",
            },
        )
    ]
    assert [b.time for b in bars] == [datetime(2026, 10, 9, 10, 30), datetime(2026, 10, 9, 11, 30)]
    first = bars[0]
    assert (first.date, first.open, first.high, first.low, first.close) == (
        date(2026, 10, 9),
        1453.0,
        1458.0,
        1450.1,
        1455.2,
    )
    assert first.volume == 12000.0 and first.change_pct == 0.15


@requires_pandas
async def test_sina_minute_bars_mapping() -> None:
    """新浪分钟线的值都是字符串，而且一次给最近约 1970 根 —— 要按日期范围筛、按时间排。"""
    import pandas as pd

    from app.data.backup_providers import SinaProvider

    frame = pd.DataFrame(
        {
            "day": ["2026-10-09 11:30:00", "2026-10-09 10:30:00", "2026-09-01 15:00:00"],
            "open": ["1.020", "1.010", "0.900"],
            "high": ["1.030", "1.025", "0.910"],
            "low": ["1.000", "1.005", "0.890"],
            "close": ["1.005", "1.020", "0.905"],
            "volume": ["300000", "500000", "1"],
            "amount": ["301500.000", "507500.000", "1"],
        }
    )
    provider = SinaProvider()
    calls = _recording(provider, frame)

    bars = await provider.minute_bars(normalize("512480"), 60, date(2026, 9, 25), date(2026, 10, 9))

    assert calls == [("stock_zh_a_minute", {"symbol": "sh512480", "period": "60", "adjust": ""})]
    assert [b.time for b in bars] == [datetime(2026, 10, 9, 10, 30), datetime(2026, 10, 9, 11, 30)]
    assert (bars[0].open, bars[0].close, bars[0].volume) == (1.01, 1.02, 500000.0)
    assert bars[1].change_pct == round((1.005 / 1.02 - 1) * 100, 2)


# ---------------------------------------------------------------- 接口


def test_bars_api_two_hour(client) -> None:  # type: ignore[no-untyped-def]
    rows = client.get("/api/v1/market/bars/600519", params={"period": "120m", "days": 30}).json()
    assert rows, "离线模式也要有 2 小时线"
    assert {r["time"][11:16] for r in rows} <= {"11:30", "15:00"}
    assert all(r["time"].startswith(r["date"]) for r in rows)
    times = [r["time"] for r in rows]
    assert times == sorted(times)


def test_bars_api_two_hour_for_etf(client) -> None:  # type: ignore[no-untyped-def]
    rows = client.get("/api/v1/market/bars/512480", params={"period": "120m", "days": 30}).json()
    assert rows and rows[0]["close"] < 10  # ETF 价格量级，确认没被当成别的代码


def test_bars_api_weekly(client) -> None:  # type: ignore[no-untyped-def]
    daily = client.get("/api/v1/market/bars/600519", params={"days": 400}).json()
    weekly = client.get("/api/v1/market/bars/600519", params={"period": "week", "days": 400}).json()
    assert 50 <= len(weekly) < len(daily)
    assert all(r["time"] is None for r in weekly)
    # 周线的收盘价就是那周最后一个交易日的收盘价
    closes = {r["date"]: r["close"] for r in daily}
    assert all(closes[w["date"]] == w["close"] for w in weekly)


def test_bars_api_default_is_daily(client) -> None:  # type: ignore[no-untyped-def]
    rows = client.get("/api/v1/market/bars/600519", params={"days": 30}).json()
    assert rows and all(r["time"] is None for r in rows)


def test_bars_api_rejects_unknown_period(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/v1/market/bars/600519", params={"period": "3h"})
    assert resp.status_code == 422
