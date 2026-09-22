"""斐波那契回撤。

重点不是验证比例算得对（那是乘法），而是验证**方向判定**：
上升段的回撤位是支撑、下降段的是阻力，判反了给出的交易建议就完全相反。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.data.base import Bar
from app.services.indicators import compute_snapshot, fib_analysis


def _bars(closes: list[float], highs: dict[int, float] | None = None,
          lows: dict[int, float] | None = None) -> list[Bar]:
    """按收盘价序列造 K 线，可在指定位置插入更极端的高/低点。"""
    highs = highs or {}
    lows = lows or {}
    start = date(2025, 1, 6)
    out: list[Bar] = []
    for i, c in enumerate(closes):
        h = max(highs.get(i, c), c)
        low = min(lows.get(i, c), c)
        out.append(Bar(date=start + timedelta(days=i), open=c, high=h, low=low, close=c))
    return out


# ---------------------------------------------------------------- 方向判定


def test_uptrend_leg_detected_when_low_precedes_high() -> None:
    """低点在前、高点在后 = 上升段。"""
    fib = fib_analysis(_bars([100] + [110] * 9 + [200] + [150] * 9))
    assert fib is not None
    assert fib.direction == "上升"
    assert fib.swing_low == 100
    assert fib.swing_high == 200


def test_downtrend_leg_detected_when_high_precedes_low() -> None:
    """高点在前、低点在后 = 下降段。"""
    fib = fib_analysis(_bars([200] + [190] * 9 + [100] + [150] * 9))
    assert fib is not None
    assert fib.direction == "下降"


def test_support_and_resistance_flip_with_direction() -> None:
    """同样的价位区间，方向不同时支撑/阻力必须互换。

    这是本模块最重要的一条：判反了，"逢低买入"就会变成"逢高买入"。
    """
    close = 150.0
    up = fib_analysis(_bars([100] + [110] * 9 + [200] + [close] * 9))
    down = fib_analysis(_bars([200] + [190] * 9 + [100] + [close] * 9))
    assert up is not None and down is not None

    # 两个方向下，支撑都必须低于现价、阻力都必须高于现价
    for fib in (up, down):
        assert fib.nearest_support is not None
        assert fib.nearest_resistance is not None
        assert fib.nearest_support.price < close < fib.nearest_resistance.price

    # 但具体是哪一档不同：上升段 50% 位就在现价，支撑落到 61.8%
    assert up.nearest_support.label == "61.8%"
    assert down.nearest_support.label == "38.2%"


# ---------------------------------------------------------------- 价位计算


def test_retracement_prices_match_hand_calculation() -> None:
    """用手算值校验，不是"不报错就算过"。

    上升段 low=100 high=200，区间 100：
      38.2% 位 = 200 - 100 × 0.382 = 161.8
      61.8% 位 = 200 - 100 × 0.618 = 138.2
    """
    fib = fib_analysis(_bars([100] + [110] * 9 + [200] + [150] * 9))
    assert fib is not None
    prices = {lv.label: lv.price for lv in fib.levels}
    assert prices["38.2%"] == pytest.approx(161.8)
    assert prices["61.8%"] == pytest.approx(138.2)
    assert prices["50.0%"] == pytest.approx(150.0)


def test_extension_targets_project_beyond_the_swing() -> None:
    """扩展位必须在摆动区间之外 —— 它是顺势目标，不是回撤位。"""
    fib = fib_analysis(_bars([100] + [110] * 9 + [200] + [150] * 9))
    assert fib is not None
    ext = [lv for lv in fib.levels if lv.kind == "extension"]
    assert len(ext) == 3
    # 上升段的扩展目标都在高点上方
    assert all(lv.price > fib.swing_high for lv in ext)
    prices = {lv.label: lv.price for lv in ext}
    assert prices["161.8%"] == pytest.approx(261.8)  # 100 + 100 × 1.618


def test_downtrend_extensions_project_below_the_low() -> None:
    fib = fib_analysis(_bars([200] + [190] * 9 + [100] + [150] * 9))
    assert fib is not None
    ext = [lv for lv in fib.levels if lv.kind == "extension"]
    assert all(lv.price < fib.swing_low for lv in ext)


def test_levels_include_both_swing_anchors() -> None:
    fib = fib_analysis(_bars([100] + [110] * 9 + [200] + [150] * 9))
    assert fib is not None
    swings = [lv for lv in fib.levels if lv.kind == "swing"]
    assert len(swings) == 2
    assert {round(lv.price) for lv in swings} == {100, 200}


# ---------------------------------------------------------------- 回撤深度


def test_retracement_pct_measures_pullback_depth() -> None:
    """上升段：回撤 % = (高点 - 现价) / 区间。"""
    fib = fib_analysis(_bars([100] + [110] * 9 + [200] + [150] * 9))
    assert fib is not None
    assert fib.retracement_pct == pytest.approx(50.0)


def test_retracement_pct_is_always_bounded_0_to_100() -> None:
    """回撤率恒在 0~100 之间 —— 这是取法决定的，不是夹取出来的。

    摆动端点就是窗口内的最高/最低价，现价必然落在两者之间。
    这一条锁住该性质，避免以后有人误以为能从这个字段读出"突破"。
    """
    cases = [
        [100] + [110] * 9 + [200] + [150] * 9,   # 上升后回撤一半
        [200] + [190] * 9 + [100] + [150] * 9,   # 下跌后反弹一半
        [100] + [110] * 9 + [200] + [199] * 9,   # 高位横盘
        [100] + [110] * 9 + [200] + [101] * 9,   # 几乎回到起点
        [float(i) for i in range(100, 140)],      # 单边上涨
        [float(i) for i in range(140, 100, -1)],  # 单边下跌
    ]
    for closes in cases:
        fib = fib_analysis(_bars(closes))
        assert fib is not None
        assert fib.retracement_pct is not None
        assert 0.0 <= fib.retracement_pct <= 100.0, (closes[:3], fib.retracement_pct)


def test_new_extreme_redefines_the_swing_and_flips_direction() -> None:
    """价格跌破原摆动低点后，该新低成为新端点，方向翻成下降段。

    这是自动斐波那契的固有行为，把它记录下来免得被当成 bug。
    """
    # 上升段 100 -> 200，随后跌到 90（低于原起点 100）
    fib = fib_analysis(_bars([100] + [110] * 9 + [200] + [90] * 9, lows={19: 90}))
    assert fib is not None
    assert fib.direction == "下降"      # 不再是上升段
    assert fib.swing_low == 90          # 新低成了新端点
    assert fib.swing_high == 200


# ---------------------------------------------------------------- 边界


@pytest.mark.parametrize("bars", [[], [10.0] * 5, [10.0] * 9])
def test_returns_none_when_too_few_bars(bars: list[float]) -> None:
    """不足 10 根 K 线没有摆动结构可谈。"""
    assert fib_analysis(_bars(bars)) is None


def test_returns_none_on_zero_range() -> None:
    """连续一字板 / 长期停牌，区间为零时不能除零。"""
    assert fib_analysis(_bars([10.0] * 30)) is None


def test_rejects_invalid_lookback() -> None:
    with pytest.raises(ValueError):
        fib_analysis(_bars([10.0] * 30), lookback=1)


def test_lookback_shorter_than_history_uses_recent_window() -> None:
    """只看最近的窗口 —— 早期的极值不该干扰当前摆动。"""
    old_spike = [500.0] + [100.0] * 9          # 很久以前的高点
    recent = [120.0] + [130.0] * 9 + [160.0] + [140.0] * 9
    fib = fib_analysis(_bars(old_spike + recent), lookback=20)
    assert fib is not None
    assert fib.swing_high == 160.0             # 没被 500 污染
    assert fib.lookback == 20


# ---------------------------------------------------------------- 接入快照


def test_snapshot_exposes_fib() -> None:
    snap = compute_snapshot(_bars([100] + [110] * 9 + [200] + [150] * 9))
    assert snap.fib is not None
    assert snap.fib.direction == "上升"


def test_snapshot_fib_is_none_on_empty_bars() -> None:
    assert compute_snapshot([]).fib is None


def test_pattern_tag_fires_only_when_price_is_near_a_level() -> None:
    """贴近关键位才提示；离得远不该产生噪声标签。"""
    # 现价正好落在 50% 位（150）
    near = compute_snapshot(_bars([100] + [110] * 9 + [200] + [150] * 9))
    assert any("斐波那契" in t and "50.0%" in t for t in near.patterns)

    # 现价 175.5，距最近的 23.6%(176.4) 约 0.5%，仍算贴近；
    # 取 168 则与 161.8/176.4 都差 4% 以上，不该有贴近标签
    far = compute_snapshot(_bars([100] + [110] * 9 + [200] + [168] * 9))
    assert not any("贴近斐波那契" in t for t in far.patterns)


def test_pattern_tag_names_support_or_resistance_correctly() -> None:
    snap = compute_snapshot(_bars([100] + [110] * 9 + [200] + [150] * 9))
    tag = next(t for t in snap.patterns if "贴近斐波那契" in t)
    # 50% 位 == 现价，按实现归为支撑（price <= close）
    assert "支撑" in tag
