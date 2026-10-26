"""金价：上金所分时的整理、报价的拼法、两个数据源的字段映射、接口。

最容易错的是上金所分时：官网给的是一张按"一天中的时刻"排、新数据覆盖旧数据的网格，
同一张表里混着两个交易日（见 app/data/gold.py 的模块注释）。整理错了，分时图上
就会把昨天下午接在今天上午后面，或者把前一晚的夜盘丢掉。
"""

from __future__ import annotations

import importlib.util
import random
from datetime import date, datetime, time, timedelta

import pytest

from app.core.errors import CapabilityNotSupported, DataNotFound, ProviderUnavailable
from app.data.base import Bar, GoldIntraday, GoldQuote, PricePoint
from app.data.gold import (
    LONDON_XAU,
    SGE_AU9999,
    arrange_sge_grid,
    parse_sge_as_of,
    resolve_prev_close,
    sge_slots,
    sge_trading_day,
    with_session_bar,
)

MON, TUE, WED = date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)
FRI, SAT, SUN, NEXT_MON = (
    date(2026, 10, 23),
    date(2026, 10, 24),
    date(2026, 10, 25),
    date(2026, 10, 26),
)


def _grid(price_of) -> tuple[list[str], list[float]]:  # type: ignore[no-untyped-def]
    """一整张网格：每个格子的价格由 price_of(时刻) 给出。"""
    slots = sge_slots()
    return [t.strftime("%H:%M") for t in slots], [price_of(t) for t in slots]


def _evening(t: time) -> bool:
    return t >= time(20, 0)


def _small_hours(t: time) -> bool:
    return t <= time(2, 30)


# ---------------------------------------------------------------- 整理网格


def test_daytime_keeps_last_nights_session_and_drops_yesterday_afternoon() -> None:
    """周二 10:00：前一晚（周一）的夜盘是本交易日的开头，必须留着；
    10:01 以后的格子还是周一白天的旧数据，不能画进来。"""

    def price(t: time) -> float:
        if _evening(t):
            return 200.0  # 周一晚上（本交易日）
        if _small_hours(t):
            return 201.0  # 周二凌晨（本交易日）
        if t <= time(10, 0):
            return 202.0  # 周二上午（本交易日）
        return 150.0 if t == time(15, 30) else 100.0  # 周一白天（上一交易日）

    session = arrange_sge_grid(*_grid(price), datetime.combine(TUE, time(10, 0)))

    assert session.trading_day == TUE
    assert {p.price for p in session.points} == {200.0, 201.0, 202.0}
    assert session.points[0].time == datetime.combine(MON, time(20, 0))
    assert session.points[-1].time == datetime.combine(TUE, time(10, 0))
    times = [p.time for p in session.points]
    assert times == sorted(times), "必须按时间先后排：夜盘 → 凌晨 → 白天"
    assert len(session.points) == 240 + 151 + 61
    # 还没被覆盖的周一 15:30 就是上一交易日的收盘价
    assert session.prev_close == 150.0


def test_evening_starts_a_new_trading_day() -> None:
    """周二 22:28：20:00 起的夜盘属于周三。凌晨和白天的格子都是周二的，不算今天。"""

    def price(t: time) -> float:
        if _evening(t):
            return 300.0 if t <= time(22, 28) else 200.0  # 22:28 以后还是周一晚上的
        if _small_hours(t):
            return 201.0
        return 250.0 if t == time(15, 30) else 202.0

    session = arrange_sge_grid(*_grid(price), datetime.combine(TUE, time(22, 28)))

    assert session.trading_day == WED
    assert {p.price for p in session.points} == {300.0}
    assert len(session.points) == 149  # 20:00 – 22:28
    assert session.points[0].time == datetime.combine(TUE, time(20, 0))
    assert session.prev_close == 250.0  # 周二收盘


def test_after_close_shows_the_whole_trading_day() -> None:
    session = arrange_sge_grid(*_grid(lambda t: 200.0), datetime.combine(TUE, time(17, 0)))
    assert session.trading_day == TUE
    assert len(session.points) == 240 + 151 + 151 + 121
    assert session.points[-1].time == datetime.combine(TUE, time(15, 30))
    # 白天的格子全是今天的，网格里读不出昨收（交给日线）
    assert session.prev_close is None


def test_weekend_shows_friday_night_which_belongs_to_monday() -> None:
    """周末网格不更新。最近的一段是周五晚上的夜盘 —— 它属于下周一这个交易日。"""

    def price(t: time) -> float:
        if _evening(t) or _small_hours(t):
            return 300.0
        return 260.0 if t == time(15, 30) else 250.0  # 周五白天

    session = arrange_sge_grid(*_grid(price), datetime.combine(SUN, time(15, 0)))

    assert session.trading_day == NEXT_MON
    assert {p.price for p in session.points} == {300.0}
    assert session.points[0].time == datetime.combine(FRI, time(20, 0))
    assert session.points[-1].time == datetime.combine(SAT, time(2, 30))
    assert session.prev_close == 260.0  # 周五收盘


def test_lunch_break_blank_and_zero_slots_are_dropped() -> None:
    times = ["11:30", "11:45", "13:30:00", "14:00", "14:01", "14:02", "14:03", "bad"]
    prices = [1.0, 2.0, 3.0, "", 0, float("nan"), "4.5", 5.0]
    session = arrange_sge_grid(times, prices, datetime.combine(TUE, time(17, 0)))
    assert [(p.time.time(), p.price) for p in session.points] == [
        (time(11, 30), 1.0),
        (time(13, 30), 3.0),
        (time(14, 3), 4.5),
    ]


def test_server_order_does_not_matter() -> None:
    times, prices = _grid(lambda t: t.hour * 100 + t.minute + 0.5)
    pairs = list(zip(times, prices, strict=True))
    random.Random(7).shuffle(pairs)
    as_of = datetime.combine(TUE, time(10, 0))
    shuffled = arrange_sge_grid([t for t, _ in pairs], [p for _, p in pairs], as_of)
    assert shuffled == arrange_sge_grid(times, prices, as_of)


def test_akshare_documented_sample() -> None:
    """akshare 文档里的样例：2025-04-14（周一）22:28:55 取的数据。"""
    as_of = parse_sge_as_of("2025年04月14日 22:28:55")
    assert as_of == datetime(2025, 4, 14, 22, 28, 55)
    session = arrange_sge_grid(*_grid(lambda t: 755.0), as_of)
    assert session.trading_day == date(2025, 4, 15)
    assert session.points[-1].time == datetime(2025, 4, 14, 22, 28)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025年04月14日 22:28:55", datetime(2025, 4, 14, 22, 28, 55)),
        ("2025-04-14 09:01", datetime(2025, 4, 14, 9, 1)),
        ("更新时间：2026年10月20日 10:00:00", datetime(2026, 10, 20, 10, 0)),
        ("", None),
        (None, None),
        ("2026年13月40日 10:00:00", None),
    ],
)
def test_parse_sge_as_of(text, expected) -> None:  # type: ignore[no-untyped-def]
    assert parse_sge_as_of(text) == expected


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (datetime.combine(TUE, time(19, 59)), TUE),
        (datetime.combine(TUE, time(20, 0)), WED),
        (datetime.combine(FRI, time(21, 0)), NEXT_MON),  # 周五夜盘算下周一
        (datetime.combine(SAT, time(1, 0)), NEXT_MON),
        (datetime.combine(SUN, time(12, 0)), NEXT_MON),
        (datetime.combine(NEXT_MON, time(1, 0)), NEXT_MON),
    ],
)
def test_sge_trading_day(at: datetime, expected: date) -> None:
    assert sge_trading_day(at) == expected


# ---------------------------------------------------------------- 昨收、补日线


def _bar(d: date, close: float) -> Bar:
    return Bar(date=d, open=close, high=close, low=close, close=close)


def test_prev_close_prefers_the_official_daily_bar() -> None:
    bars = [_bar(date(2026, 10, 16), 90.0), _bar(MON, 100.0)]
    assert resolve_prev_close(bars, TUE, grid_prev=99.0) == 100.0


def test_prev_close_uses_the_grid_when_daily_bars_lag() -> None:
    """晚上日线常常还没更新到刚收盘的那天：周三的交易日，日线只到周一。"""
    bars = [_bar(date(2026, 10, 16), 90.0), _bar(MON, 100.0)]
    assert resolve_prev_close(bars, WED, grid_prev=105.0) == 105.0
    assert resolve_prev_close(bars, WED, grid_prev=None) == 100.0
    assert resolve_prev_close([], WED, grid_prev=None) is None


def test_prev_close_after_a_holiday() -> None:
    """国庆后第一个交易日（10-08）：上一个工作日 10-07 在放假，没有日线 —— 照样找得到。"""
    bars = [_bar(date(2026, 9, 30), 88.0)]
    assert resolve_prev_close(bars, date(2026, 10, 8), grid_prev=None) == 88.0
    assert resolve_prev_close(bars, date(2026, 10, 8), grid_prev=88.0) == 88.0


def _intraday(day: date, prices: list[float], prev: float | None) -> GoldIntraday:
    start = datetime.combine(day, time(9, 0))
    return GoldIntraday(
        code=SGE_AU9999,
        trading_day=day,
        as_of=start + timedelta(minutes=len(prices)),
        points=[
            PricePoint(time=start + timedelta(minutes=i), price=p) for i, p in enumerate(prices)
        ],
        prev_close=prev,
    )


def test_session_bar_is_appended_while_trading() -> None:
    bars = [_bar(MON, 100.0)]
    out = with_session_bar(bars, _intraday(TUE, [101.0, 99.5, 103.0, 102.0], prev=100.0))
    assert out[:-1] == bars
    last = out[-1]
    assert (last.date, last.open, last.high, last.low, last.close) == (
        TUE,
        101.0,
        103.0,
        99.5,
        102.0,
    )
    assert last.change_pct == 2.0


def test_session_bar_never_duplicates_an_official_bar() -> None:
    bars = [_bar(MON, 100.0), _bar(TUE, 102.0)]
    assert with_session_bar(bars, _intraday(TUE, [101.0], prev=100.0)) == bars
    assert with_session_bar(bars, None) == bars
    assert with_session_bar(bars, _intraday(WED, [], prev=102.0)) == bars


# ---------------------------------------------------------------- 上金所（akshare 适配器）

PAYLOAD_TUE_10 = {
    "heyue": "Au99.99",
    "times": _grid(lambda t: 0)[0],
    "data": _grid(
        lambda t: 980.0 if t <= time(10, 0) or t >= time(20, 0) or t <= time(2, 30) else 975.5
    )[1],
    "delaystr": "2026年10月20日 10:00:00",
}


async def test_akshare_gold_intraday_maps_the_grid() -> None:
    from app.data.akshare_provider import AkshareProvider

    provider = AkshareProvider()

    async def fake_grid(instrument: str):  # type: ignore[no-untyped-def]
        assert instrument == "Au99.99"
        return PAYLOAD_TUE_10

    provider._sge_grid = fake_grid  # type: ignore[method-assign]
    data = await provider.gold_intraday(SGE_AU9999)

    assert data.code == SGE_AU9999
    assert data.trading_day == TUE
    assert data.as_of == datetime(2026, 10, 20, 10, 0)
    assert len(data.points) == 240 + 151 + 61
    assert data.prev_close == 975.5


async def test_akshare_gold_intraday_rejects_other_codes() -> None:
    from app.data.akshare_provider import AkshareProvider

    with pytest.raises(CapabilityNotSupported):
        await AkshareProvider().gold_intraday(LONDON_XAU)


async def test_sge_grid_request_copies_akshare(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """照抄 akshare 的请求：GET 带表单 instid=Au99.99，并且要有超时（akshare 自己没有）。"""
    import app.data.browser_transport as transport
    from app.data.akshare_provider import SGE_GRID_URL, AkshareProvider

    calls: list[tuple[str, dict]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return PAYLOAD_TUE_10

    class FakeHttp:
        def get(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(transport, "http", lambda: FakeHttp())
    provider = AkshareProvider()
    provider._module = lambda: None  # type: ignore[method-assign] —— 不需要真的装 akshare

    assert await provider._sge_grid("Au99.99") == PAYLOAD_TUE_10
    [(url, kwargs)] = calls
    assert url == SGE_GRID_URL
    assert kwargs["data"] == {"instid": "Au99.99"}
    assert kwargs["timeout"] > 0
    assert kwargs["headers"]["Referer"].startswith("https://www.sge.com.cn")


async def test_sge_grid_reports_a_changed_format(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.data.browser_transport as transport
    from app.data.akshare_provider import AkshareProvider

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"heyue": "Au99.99"}

    class FakeHttp:
        def get(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
            return FakeResponse()

    monkeypatch.setattr(transport, "http", lambda: FakeHttp())
    provider = AkshareProvider()
    provider._module = lambda: None  # type: ignore[method-assign]
    with pytest.raises(ProviderUnavailable, match="格式变了"):
        await provider._sge_grid("Au99.99")


requires_pandas = pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None, reason="未安装 pandas（随 akshare 一起装）"
)


@requires_pandas
async def test_akshare_sge_bars_mapping_and_cache() -> None:
    import pandas as pd

    from app.data.akshare_provider import AkshareProvider

    frame = pd.DataFrame(
        {
            "date": [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)],
            "open": [960.0, 965.0, 970.0],
            "close": [964.0, 969.0, 966.0],
            "low": [955.0, 960.0, 961.0],
            "high": [966.0, 972.0, 975.0],
        }
    )
    provider = AkshareProvider()
    calls: list[tuple[str, dict]] = []

    async def fake_call(name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((name, kwargs))
        return frame

    provider._call = fake_call  # type: ignore[method-assign]
    bars = await provider.gold_bars(SGE_AU9999, MON, TUE)

    assert calls == [("spot_hist_sge", {"symbol": "Au99.99"})]
    assert [(b.date, b.open, b.high, b.low, b.close) for b in bars] == [
        (MON, 965.0, 972.0, 960.0, 969.0),
        (TUE, 970.0, 975.0, 961.0, 966.0),
    ]
    assert bars[-1].change_pct == round((966 / 969 - 1) * 100, 2)
    # 全部历史一次取回、缓存起来：换个区间不再发请求
    await provider.gold_bars(SGE_AU9999, date(2026, 10, 1), TUE)
    assert len(calls) == 1


# ---------------------------------------------------------------- 伦敦金（新浪）


class _Frame:
    """够 parse 用的最小 DataFrame 替身（不需要装 pandas）。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.empty = not rows
        self.iloc = rows

    def iterrows(self):  # type: ignore[no-untyped-def]
        return enumerate(self._rows)


def test_london_quote_mapping() -> None:
    from app.data.backup_providers import parse_london_quote

    q = parse_london_quote(
        LONDON_XAU,
        {
            "名称": "伦敦金",
            "最新价": 4375.5,
            "昨日结算价": 4350.0,
            "开盘价": 4360.0,
            "最高价": 4380.0,
            "最低价": 0.0,  # 没开盘时外盘常常给 0
            "行情时间": "22:28:00",
            "日期": "2026-10-20",
        },
    )
    assert (q.code, q.price, q.prev_close, q.open, q.high) == (
        LONDON_XAU,
        4375.5,
        4350.0,
        4360.0,
        4380.0,
    )
    assert q.low is None
    assert q.change == 25.5
    assert q.change_pct == round((4375.5 / 4350 - 1) * 100, 2)
    assert q.as_of == datetime(2026, 10, 20, 22, 28)


def test_london_quote_without_price_fails() -> None:
    from app.data.backup_providers import parse_london_quote

    with pytest.raises(ProviderUnavailable):
        parse_london_quote(LONDON_XAU, {"最新价": None, "昨日结算价": 4350.0})


async def test_sina_gold_quote_calls_the_xau_feed() -> None:
    from app.data.backup_providers import SinaProvider

    provider = SinaProvider()
    calls: list[tuple[str, dict]] = []

    async def fake_call(name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((name, kwargs))
        return _Frame(
            [
                {
                    "最新价": "4375.5",
                    "昨日结算价": "4350",
                    "行情时间": "09:30:00",
                    "日期": "2026-10-20",
                }
            ]
        )

    provider._call = fake_call  # type: ignore[method-assign]
    q = await provider.gold_quote(LONDON_XAU)
    assert calls == [("futures_foreign_commodity_realtime", {"symbol": "XAU"})]
    assert q.price == 4375.5
    with pytest.raises(CapabilityNotSupported):
        await provider.gold_quote(SGE_AU9999)


async def test_sina_london_bars_mapping() -> None:
    """日期列可能是 pandas 的 Timestamp：要转成 date，否则和区间比较会出错。"""
    from app.data.backup_providers import SinaProvider

    rows = [
        {
            "date": datetime(2026, 10, 20),
            "open": 4350,
            "high": 4390,
            "low": 4340,
            "close": 4375.5,
            "volume": 1200,
        },
        {
            "date": "2026-10-19",
            "open": 4300,
            "high": 4360,
            "low": 4290,
            "close": 4350,
            "volume": 1000,
        },
        {
            "date": "2026-09-01",
            "open": 4000,
            "high": 4010,
            "low": 3990,
            "close": 4005,
            "volume": 900,
        },
    ]
    provider = SinaProvider()

    async def fake_call(name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        assert (name, kwargs) == ("futures_foreign_hist", {"symbol": "XAU"})
        return _Frame(rows)

    provider._call = fake_call  # type: ignore[method-assign]
    bars = await provider.gold_bars(LONDON_XAU, MON, TUE)
    assert [b.date for b in bars] == [MON, TUE]
    assert all(type(b.date) is date for b in bars)
    assert bars[-1].volume == 1200
    assert bars[-1].change_pct == round((4375.5 / 4350 - 1) * 100, 2)


# ---------------------------------------------------------------- 报价怎么拼（services/gold.py）


class _FakeRegistry:
    def __init__(
        self,
        intraday: GoldIntraday | Exception,
        bars: list[Bar] | Exception,
        london: GoldQuote | Exception | None = None,
    ) -> None:
        self._intraday, self._bars, self._london = intraday, bars, london
        self.intraday_calls = 0

    async def gold_intraday(self, code: str) -> GoldIntraday:
        self.intraday_calls += 1
        if isinstance(self._intraday, Exception):
            raise self._intraday
        return self._intraday

    async def gold_bars(self, code: str, start: date, end: date) -> list[Bar]:
        if isinstance(self._bars, Exception):
            raise self._bars
        return [b for b in self._bars if start <= b.date <= end]

    async def gold_quote(self, code: str) -> GoldQuote:
        if isinstance(self._london, Exception):
            raise self._london
        assert self._london is not None
        return self._london


def _recent_bars(day: date) -> list[Bar]:
    """`day` 之前的几根日线（到上一个工作日为止），最后一根收 100。"""
    from app.data.gold import previous_business_day

    prev = previous_business_day(day)
    return [_bar(previous_business_day(prev), 95.0), _bar(prev, 100.0)]


async def test_sge_quote_is_built_from_intraday_and_daily_bars() -> None:
    from app.core.clock import now_cst
    from app.services.gold import gold_quote

    day = now_cst().date()
    registry = _FakeRegistry(
        _intraday(day, [101.0, 99.0, 104.0, 103.0], prev=None), _recent_bars(day)
    )
    q = await gold_quote(registry, SGE_AU9999)  # type: ignore[arg-type]

    assert (q.price, q.prev_close, q.open, q.high, q.low) == (103.0, 100.0, 101.0, 104.0, 99.0)
    assert q.change == 3.0 and q.change_pct == 3.0
    assert q.is_realtime is True
    assert q.as_of is not None


async def test_sge_quote_falls_back_to_last_close_and_says_so() -> None:
    from app.core.clock import now_cst
    from app.services.gold import gold_quote

    day = now_cst().date()
    registry = _FakeRegistry(DataNotFound("上金所分时取不到"), _recent_bars(day))
    q = await gold_quote(registry, SGE_AU9999)  # type: ignore[arg-type]
    assert q.price == 100.0 and q.prev_close == 95.0
    assert q.is_realtime is False
    assert q.quote_date == _recent_bars(day)[-1].date


async def test_sge_quote_reports_both_reasons_when_everything_fails() -> None:
    from app.services.gold import gold_quote

    registry = _FakeRegistry(DataNotFound("分时挂了"), DataNotFound("日线挂了"))
    with pytest.raises(DataNotFound, match="分时挂了.*日线挂了"):
        await gold_quote(registry, SGE_AU9999)  # type: ignore[arg-type]


async def test_one_instrument_failing_does_not_hide_the_other() -> None:
    from app.core.clock import now_cst
    from app.services.gold import gold_overview

    day = now_cst().date()
    registry = _FakeRegistry(
        _intraday(day, [101.0], prev=None), _recent_bars(day), london=DataNotFound("新浪连不上")
    )
    items = {i.spec.code: i for i in await gold_overview(registry)}  # type: ignore[arg-type]
    assert items[SGE_AU9999].quote is not None and items[SGE_AU9999].error is None
    assert items[LONDON_XAU].quote is None
    assert "新浪连不上" in (items[LONDON_XAU].error or "")


async def test_london_bars_do_not_touch_intraday() -> None:
    from app.core.clock import now_cst
    from app.services.gold import gold_bars

    day = now_cst().date()
    registry = _FakeRegistry(DataNotFound("不该被调用"), _recent_bars(day))
    await gold_bars(registry, LONDON_XAU, "day", 30)  # type: ignore[arg-type]
    assert registry.intraday_calls == 0


# ---------------------------------------------------------------- 接口（离线模式）


@pytest.fixture
def frozen_mock_clock(monkeypatch):  # type: ignore[no-untyped-def]
    """把"现在"定在周二 10:00（模拟数据源和金价服务都是），结果不随跑测试的时刻变化。"""
    import app.data.mock_provider as mock
    import app.services.gold as gold_service
    from app.core.clock import CST

    now = datetime.combine(TUE, time(10, 0))
    monkeypatch.setattr(mock, "_now_naive", lambda: now)
    monkeypatch.setattr(gold_service, "now_cst", lambda: now.replace(tzinfo=CST))


def test_gold_api_lists_both_instruments(client, frozen_mock_clock) -> None:  # type: ignore[no-untyped-def]
    items = {i["code"]: i for i in client.get("/api/v1/gold").json()}
    assert set(items) == {SGE_AU9999, LONDON_XAU}
    sge, london = items[SGE_AU9999], items[LONDON_XAU]
    assert (sge["unit"], sge["has_intraday"]) == ("元/克", True)
    assert (london["unit"], london["has_intraday"]) == ("美元/盎司", False)
    for item in (sge, london):
        assert item["error"] is None
        assert item["quote"]["price"] > 0
        assert item["quote"]["prev_close"] > 0


def test_gold_api_daily_chart_ends_at_the_quoted_price(client, frozen_mock_clock) -> None:  # type: ignore[no-untyped-def]
    """日K 最后一根（盘中由分时合成）要和页面顶上的价格对得上。"""
    quote = next(i for i in client.get("/api/v1/gold").json() if i["code"] == SGE_AU9999)["quote"]
    bars = client.get(f"/api/v1/gold/{SGE_AU9999}/bars", params={"days": 30}).json()
    assert bars[-1]["date"] == TUE.isoformat()
    assert bars[-1]["close"] == quote["price"]
    assert bars[-2]["close"] == quote["prev_close"]
    dates = [b["date"] for b in bars]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


def test_gold_api_weekly(client, frozen_mock_clock) -> None:  # type: ignore[no-untyped-def]
    daily = client.get(f"/api/v1/gold/{LONDON_XAU}/bars", params={"days": 200}).json()
    weekly = client.get(
        f"/api/v1/gold/{LONDON_XAU}/bars", params={"period": "week", "days": 200}
    ).json()
    assert 20 <= len(weekly) < len(daily)
    closes = {b["date"]: b["close"] for b in daily}
    assert all(closes[w["date"]] == w["close"] for w in weekly)


def test_gold_api_intraday(client, frozen_mock_clock) -> None:  # type: ignore[no-untyped-def]
    body = client.get(f"/api/v1/gold/{SGE_AU9999}/intraday").json()
    assert body["trading_day"] == TUE.isoformat()
    assert body["prev_close"] > 0
    times = [p["time"] for p in body["points"]]
    assert times == sorted(times)
    assert times[0] == "2026-10-19T20:00:00"  # 前一晚的夜盘开头
    assert times[-1] == "2026-10-20T10:00:00"


def test_gold_api_errors(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get(f"/api/v1/gold/{LONDON_XAU}/intraday")
    assert resp.status_code == 404
    assert "只有日K和周K" in resp.json()["detail"]
    assert client.get("/api/v1/gold/AG/bars").status_code == 404
    assert (
        client.get(f"/api/v1/gold/{SGE_AU9999}/bars", params={"period": "120m"}).status_code == 422
    )


def test_diagnose_probes_gold(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.services.host_probe as hp

    async def no_hosts(_impersonate):  # type: ignore[no-untyped-def]
        return hp.ProbeReport()  # 主机探测要联网，这里不测它

    monkeypatch.setattr(hp, "probe_hosts", no_hosts)
    body = client.get("/api/v1/market/diagnose").json()
    mock = next(p for p in body["providers"] if p["provider"] == "mock")
    gold = {i["name"]: i for i in mock["interfaces"] if i["name"].startswith("金价")}
    assert set(gold) == {"金价·上金所日线", "金价·上金所分时", "金价·伦敦金行情", "金价·伦敦金日线"}
    assert all(i["status"] == "正常" for i in gold.values())


def test_host_probe_covers_gold_sources() -> None:
    from app.services.host_probe import HOSTS

    hosts = {h for h, _ in HOSTS}
    assert {"www.sge.com.cn", "hq.sinajs.cn", "stock2.finance.sina.com.cn"} <= hosts
