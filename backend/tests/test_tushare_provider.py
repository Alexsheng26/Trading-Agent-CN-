"""Tushare 适配器。不联网：用一个假的 tushare 包替身。"""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import date
from typing import Any

import pytest

from app.data.symbols import normalize

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None, reason="未安装 pandas（随 tushare 一起装）"
)


def _fake_tushare(monkeypatch) -> tuple[dict[str, dict[str, Any]], object]:  # type: ignore[no-untyped-def]
    """装一个假 tushare：pro_api() 和真的一样，把任何属性名都当接口名发给"服务端"。"""
    import pandas as pd

    frame = pd.DataFrame(
        {
            "trade_date": ["20261009", "20260930"],  # tushare 返回倒序
            "open": [10.0, 9.0],
            "high": [11.0, 10.0],
            "low": [9.5, 8.5],
            "close": [10.5, 9.5],
            "vol": [100.0, 200.0],
            "amount": [1000.0, 2000.0],
            "pct_chg": [10.53, -1.0],
        }
    )
    calls: dict[str, dict[str, Any]] = {}

    class FakeApi:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            def query(**kwargs):  # type: ignore[no-untyped-def]
                if name == "pro_bar":
                    # 真服务端对不存在的接口名就是这么回的
                    raise RuntimeError("请指定正确的接口名")
                calls[name] = kwargs
                return frame

            return query

    api = FakeApi()

    def pro_bar(api=None, **kwargs):  # type: ignore[no-untyped-def]
        calls["pro_bar"] = {"api": api, **kwargs}
        return frame

    fake = types.ModuleType("tushare")
    fake.set_token = lambda token: None  # type: ignore[attr-defined]
    fake.pro_api = lambda: api  # type: ignore[attr-defined]
    fake.pro_bar = pro_bar  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tushare", fake)
    return calls, api


async def test_adjusted_daily_bars_call_pro_bar_as_package_function(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """pro_bar 是 tushare 包里的函数（本地拼 daily + adj_factor），不是服务端接口。

    以前当成 `pro_api().pro_bar(...)` 调，用户诊断里 Tushare 日线一直是
    "请指定正确的接口名"。
    """
    from app.data.tushare_provider import TushareProvider

    calls, api = _fake_tushare(monkeypatch)
    provider = TushareProvider(token="x")

    bars = await provider.daily_bars(normalize("600519"), date(2026, 9, 25), date(2026, 10, 9))

    assert calls["pro_bar"] == {
        "api": api,
        "adj": "qfq",
        "freq": "D",
        "asset": "E",
        "ts_code": "600519.SH",
        "start_date": "20260925",
        "end_date": "20261009",
    }
    assert [b.date for b in bars] == [date(2026, 9, 30), date(2026, 10, 9)]
    assert bars[-1].close == 10.5
    assert bars[-1].amount == 1000.0 * 1e3  # 千元 -> 元


async def test_unadjusted_daily_bars_use_daily_api(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.data.tushare_provider import TushareProvider

    calls, _ = _fake_tushare(monkeypatch)
    provider = TushareProvider(token="x")

    await provider.daily_bars(normalize("600519"), date(2026, 9, 25), date(2026, 10, 9), adjust="")

    assert "pro_bar" not in calls
    assert calls["daily"]["ts_code"] == "600519.SH"
