"""技术指标。用手算得出的已知值验证，不是"不报错就算过"。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.data.base import Bar
from app.services.indicators import (
    atr,
    boll,
    compute_snapshot,
    ema,
    kdj,
    macd,
    rsi,
    sma,
)


def _bars(closes: list[float]) -> list[Bar]:
    start = date(2025, 1, 6)  # 周一
    return [
        Bar(
            date=start + timedelta(days=i),
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1_000_000,
            amount=c * 1e8,
        )
        for i, c in enumerate(closes)
    ]


def test_sma_known_values() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = sma(values, 3)
    assert result[:2] == [None, None]  # 前 period-1 个无值
    assert result[2] == pytest.approx(2.0)  # (1+2+3)/3
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)
    assert len(result) == len(values)  # 与输入等长


def test_ema_seeded_with_sma() -> None:
    values = [float(i) for i in range(1, 11)]
    result = ema(values, 3)
    assert result[1] is None
    assert result[2] == pytest.approx(2.0)  # 种子 = (1+2+3)/3
    # k = 2/(3+1) = 0.5 -> 4*0.5 + 2*0.5 = 3.0
    assert result[3] == pytest.approx(3.0)


def test_ema_too_short_returns_all_none() -> None:
    assert ema([1.0, 2.0], 5) == [None, None]


def test_rsi_extremes() -> None:
    rising = [float(i) for i in range(1, 21)]
    assert rsi(rising, 14)[-1] == pytest.approx(100.0)

    falling = [float(i) for i in range(20, 0, -1)]
    assert rsi(falling, 14)[-1] == pytest.approx(0.0)

    flat = [10.0] * 20
    assert rsi(flat, 14)[-1] == pytest.approx(50.0)  # 无涨无跌


def test_macd_lengths_and_crossover() -> None:
    closes = [10.0 + i * 0.5 for i in range(60)]
    dif, dea, hist = macd(closes)
    assert len(dif) == len(dea) == len(hist) == len(closes)
    # 单边上涨时 DIF 应为正
    assert dif[-1] is not None and dif[-1] > 0


def test_boll_bands_ordered() -> None:
    closes = [10.0, 11.0, 9.0, 12.0, 8.0] * 6
    upper, mid, lower = boll(closes, period=20)
    assert upper[-1] > mid[-1] > lower[-1]  # type: ignore[operator]


def test_boll_zero_variance() -> None:
    """价格完全不动时上下轨应与中轨重合，不能崩。"""
    closes = [10.0] * 30
    upper, mid, lower = boll(closes, period=20)
    assert upper[-1] == pytest.approx(10.0)
    assert lower[-1] == pytest.approx(10.0)


def test_kdj_bounded() -> None:
    bars = _bars([10.0 + (i % 7) for i in range(40)])
    k, d, _ = kdj(bars)
    assert 0 <= k[-1] <= 100  # type: ignore[operator]
    assert 0 <= d[-1] <= 100  # type: ignore[operator]


def test_atr_positive() -> None:
    bars = _bars([10.0 + i * 0.3 for i in range(30)])
    result = atr(bars, 14)
    assert result[-1] is not None and result[-1] > 0


def test_snapshot_on_empty_bars() -> None:
    """数据源挂掉时会传空列表进来，不能抛异常。"""
    snap = compute_snapshot([])
    assert snap.close is None
    assert snap.patterns == []


def test_snapshot_on_short_history() -> None:
    """新股只有几根 K 线，长周期指标应为 None 而不是报错。"""
    snap = compute_snapshot(_bars([10.0, 10.5, 11.0]))
    assert snap.close == pytest.approx(11.0)
    assert snap.ma120 is None
    assert snap.volatility_20d is None


def test_snapshot_detects_bullish_alignment() -> None:
    """持续上涨应识别出均线多头排列。"""
    snap = compute_snapshot(_bars([10.0 + i * 0.2 for i in range(150)]))
    assert "均线多头排列" in snap.patterns
    assert snap.return_20d is not None and snap.return_20d > 0


def test_snapshot_detects_bearish_alignment() -> None:
    snap = compute_snapshot(_bars([50.0 - i * 0.2 for i in range(150)]))
    assert "均线空头排列" in snap.patterns
