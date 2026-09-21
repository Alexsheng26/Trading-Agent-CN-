"""技术指标。

纯 Python 实现，不依赖 numpy/pandas —— 这样核心逻辑在没装 akshare 的环境
（CI、轻量容器）里也能跑测试。数据量是日线级别（几千根），性能完全够。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.data.base import Bar


def sma(values: list[float], period: int) -> list[float | None]:
    """简单移动平均。前 period-1 个位置是 None，保持与输入等长。"""
    if period <= 0:
        raise ValueError("period 必须为正数")
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    """指数移动平均。用前 period 个值的均值做种子。"""
    if period <= 0:
        raise ValueError("period 必须为正数")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD：返回 (DIF, DEA, MACD柱)。柱子按国内习惯放大 2 倍。"""
    fast_line = ema(values, fast)
    slow_line = ema(values, slow)
    dif: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_line, slow_line, strict=True)
    ]
    # DEA 是 DIF 的 EMA，只能在 DIF 有值的那段上算
    valid_from = next((i for i, v in enumerate(dif) if v is not None), len(dif))
    dif_valid = [v for v in dif[valid_from:] if v is not None]
    dea_valid = ema(dif_valid, signal)
    dea: list[float | None] = [None] * valid_from + list(dea_valid)
    dea += [None] * (len(values) - len(dea))

    hist: list[float | None] = [
        ((d - e) * 2) if (d is not None and e is not None) else None
        for d, e in zip(dif, dea[: len(dif)], strict=True)
    ]
    return dif, dea, hist


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """RSI（Wilder 平滑）。"""
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def boll(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """布林带：返回 (上轨, 中轨, 下轨)。"""
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        m = mid[i]
        if m is None:
            continue
        variance = sum((v - m) ** 2 for v in window) / period
        sd = variance**0.5
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return upper, mid, lower


def kdj(
    bars: list[Bar], period: int = 9, k_period: int = 3, d_period: int = 3
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """KDJ。国内看盘最常用的指标之一。"""
    n = len(bars)
    k_list: list[float | None] = [None] * n
    d_list: list[float | None] = [None] * n
    j_list: list[float | None] = [None] * n
    k_prev, d_prev = 50.0, 50.0
    for i in range(n):
        if i < period - 1:
            continue
        window = bars[i - period + 1 : i + 1]
        high = max(b.high for b in window)
        low = min(b.low for b in window)
        rsv = 50.0 if high == low else (bars[i].close - low) / (high - low) * 100.0
        k_prev = (rsv + (k_period - 1) * k_prev) / k_period
        d_prev = (k_prev + (d_period - 1) * d_prev) / d_period
        k_list[i] = k_prev
        d_list[i] = d_prev
        j_list[i] = 3 * k_prev - 2 * d_prev
    return k_list, d_list, j_list


def atr(bars: list[Bar], period: int = 14) -> list[float | None]:
    """ATR 真实波幅均值，风控智能体用它算止损距离。"""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < 2:
        return out
    trs: list[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        prev_close = bars[i - 1].close
        trs.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - prev_close),
                abs(prev_close - bars[i].low),
            )
        )
    if n < period:
        return out
    prev = sum(trs[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


# ---------------------------------------------------------------- 汇总


@dataclass(slots=True)
class IndicatorSnapshot:
    """最新一根 K 线上的指标读数 + 形态判断，直接喂给技术面分析师。"""

    close: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_hist: float | None = None
    rsi6: float | None = None
    rsi14: float | None = None
    boll_upper: float | None = None
    boll_mid: float | None = None
    boll_lower: float | None = None
    kdj_k: float | None = None
    kdj_d: float | None = None
    kdj_j: float | None = None
    atr14: float | None = None
    atr_pct: float | None = None  # ATR / 收盘价 %
    # 区间统计
    high_60: float | None = None
    low_60: float | None = None
    pct_from_high_60: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    volatility_20d: float | None = None  # 20 日收益率年化波动 %
    volume_ratio_5_20: float | None = None  # 5 日均量 / 20 日均量
    # 形态标签
    patterns: list[str] = field(default_factory=list)


def compute_snapshot(bars: list[Bar]) -> IndicatorSnapshot:
    """算出最新一根 K 线的全部指标。bars 需按日期升序。"""
    snap = IndicatorSnapshot()
    if not bars:
        return snap

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    last = len(bars) - 1
    snap.close = closes[last]

    for period, attr in ((5, "ma5"), (10, "ma10"), (20, "ma20"), (60, "ma60"), (120, "ma120")):
        setattr(snap, attr, _tail(sma(closes, period)))

    dif, dea, hist = macd(closes)
    snap.macd_dif, snap.macd_dea, snap.macd_hist = _tail(dif), _tail(dea), _tail(hist)
    snap.rsi6, snap.rsi14 = _tail(rsi(closes, 6)), _tail(rsi(closes, 14))

    upper, mid, lower = boll(closes)
    snap.boll_upper, snap.boll_mid, snap.boll_lower = _tail(upper), _tail(mid), _tail(lower)

    k, d, j = kdj(bars)
    snap.kdj_k, snap.kdj_d, snap.kdj_j = _tail(k), _tail(d), _tail(j)

    snap.atr14 = _tail(atr(bars))
    if snap.atr14 and snap.close:
        snap.atr_pct = round(snap.atr14 / snap.close * 100, 2)

    window60 = bars[-60:]
    snap.high_60 = max(b.high for b in window60)
    snap.low_60 = min(b.low for b in window60)
    if snap.high_60 and snap.close:
        snap.pct_from_high_60 = round((snap.close / snap.high_60 - 1) * 100, 2)

    snap.return_20d = _period_return(closes, 20)
    snap.return_60d = _period_return(closes, 60)
    snap.volatility_20d = _annualized_volatility(closes, 20)

    vol5 = _tail(sma(volumes, 5))
    vol20 = _tail(sma(volumes, 20))
    if vol5 and vol20 and vol20 > 0:
        snap.volume_ratio_5_20 = round(vol5 / vol20, 2)

    snap.patterns = _detect_patterns(snap, closes, dif, dea)
    return snap


def _tail(series: list[float | None]) -> float | None:
    for value in reversed(series):
        if value is not None:
            return round(value, 4)
    return None


def _period_return(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    start = closes[-days - 1]
    if start == 0:
        return None
    return round((closes[-1] / start - 1) * 100, 2)


def _annualized_volatility(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    rets = [
        closes[i] / closes[i - 1] - 1
        for i in range(len(closes) - days, len(closes))
        if closes[i - 1]
    ]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    # 一年约 244 个交易日
    return round(var**0.5 * (244**0.5) * 100, 2)


def _detect_patterns(
    snap: IndicatorSnapshot,
    closes: list[float],
    dif: list[float | None],
    dea: list[float | None],
) -> list[str]:
    """输出人类可读的形态标签，让分析师的 prompt 更有抓手。"""
    tags: list[str] = []

    if snap.ma5 and snap.ma20:
        tags.append(
            "MA5 上穿 MA20（短期偏强）" if snap.ma5 > snap.ma20 else "MA5 低于 MA20（短期偏弱）"
        )
    if snap.close and snap.ma60:
        tags.append("站上 60 日线" if snap.close > snap.ma60 else "位于 60 日线下方")
    if snap.ma20 and snap.ma60 and snap.ma120:
        if snap.ma20 > snap.ma60 > snap.ma120:
            tags.append("均线多头排列")
        elif snap.ma20 < snap.ma60 < snap.ma120:
            tags.append("均线空头排列")

    # MACD 金叉/死叉：看最近两根 DIF-DEA 的符号变化
    if len(dif) >= 2 and all(v is not None for v in (dif[-1], dif[-2], dea[-1], dea[-2])):
        prev_gap = dif[-2] - dea[-2]  # type: ignore[operator]
        curr_gap = dif[-1] - dea[-1]  # type: ignore[operator]
        if prev_gap <= 0 < curr_gap:
            tags.append("MACD 金叉")
        elif prev_gap >= 0 > curr_gap:
            tags.append("MACD 死叉")

    if snap.rsi14 is not None:
        if snap.rsi14 >= 70:
            tags.append(f"RSI14={snap.rsi14:.0f} 超买区")
        elif snap.rsi14 <= 30:
            tags.append(f"RSI14={snap.rsi14:.0f} 超卖区")

    if snap.close and snap.boll_upper and snap.close > snap.boll_upper:
        tags.append("突破布林上轨")
    elif snap.close and snap.boll_lower and snap.close < snap.boll_lower:
        tags.append("跌破布林下轨")

    if snap.volume_ratio_5_20 is not None:
        if snap.volume_ratio_5_20 >= 1.5:
            tags.append(f"近 5 日放量（量比 {snap.volume_ratio_5_20}）")
        elif snap.volume_ratio_5_20 <= 0.7:
            tags.append(f"近 5 日缩量（量比 {snap.volume_ratio_5_20}）")

    if snap.pct_from_high_60 is not None and snap.pct_from_high_60 > -2:
        tags.append("接近 60 日新高")
    if len(closes) >= 60 and snap.low_60 and closes[-1] <= snap.low_60 * 1.02:
        tags.append("接近 60 日新低")

    return tags
