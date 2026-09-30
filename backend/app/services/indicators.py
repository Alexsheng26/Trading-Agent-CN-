"""技术指标。

纯 Python 实现，不依赖 numpy/pandas —— 这样核心逻辑在没装 akshare 的环境
（CI、轻量容器）里也能跑测试。数据量是日线级别（几千根），性能完全够。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

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


# ---------------------------------------------------------------- 斐波那契

# 回撤比例。0.5 与 2.0 严格来说不是斐波那契数列比值，
# 但国内外看盘软件都会带上，实际交易中也确实被当作关键位，所以保留。
_FIB_RETRACEMENTS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)
# 扩展比例，用于顺势的目标位
_FIB_EXTENSIONS: tuple[float, ...] = (1.272, 1.618, 2.618)


@dataclass(slots=True)
class FibLevel:
    """一条斐波那契价位。"""

    ratio: float  # 0.382
    label: str  # "38.2%"
    price: float
    #: swing=摆动端点 / retracement=回撤位 / extension=扩展目标位
    kind: str


@dataclass(slots=True)
class FibAnalysis:
    """斐波那契回撤分析结果。"""

    #: 最近一段摆动的方向：上升 = 先低后高，下降 = 先高后低
    direction: str
    swing_high: float
    swing_low: float
    swing_high_date: date | None = None
    swing_low_date: date | None = None
    lookback: int = 0
    #: 当前价相对这段摆动已回撤的百分比，恒在 0~100 之间。
    #: 之所以不会越界：摆动端点取的就是窗口内的最高/最低价，
    #: 现价必然落在两者之间。价格一旦创出新极值，该极值就成为新的摆动端点，
    #: 方向也会随之翻转 —— 所以"突破摆动区间"这件事要看
    #: `IndicatorSnapshot.pct_from_high_60` 与 60 日新高/新低标签，
    #: 不要指望从这个字段读出来。
    retracement_pct: float | None = None
    levels: list[FibLevel] = field(default_factory=list)
    #: 当前价下方最近的斐波那契位（潜在支撑）
    nearest_support: FibLevel | None = None
    #: 当前价上方最近的斐波那契位（潜在阻力）
    nearest_resistance: FibLevel | None = None


def fib_analysis(
    bars: list[Bar], lookback: int = 60, decimals: int = 2
) -> FibAnalysis | None:
    """按最近 `lookback` 根 K 线的摆动区间计算斐波那契位。

    摆动点取法：窗口内的最高价与最低价。两者的**先后顺序**决定方向 ——
    低点在前、高点在后，说明这是一段上涨，回撤位在高点下方充当支撑；
    反之是一段下跌，回撤位在低点上方充当阻力。
    方向判反的话支撑阻力会完全颠倒，所以这一步比算比例重要得多。

    这是"自动斐波那契"的常见做法（端点随窗口滚动），优点是无需人工挑摆动点、
    始终反映最近的结构；代价是端点会随新极值移动，因此价格不会"跌破"这组位 ——
    判断突破请用 60 日新高/新低相关字段。

    返回 None 的情况：K 线不足、或区间为零（连续一字板/长期停牌）。

    `decimals` 是价位的小数位：股票 2 位，ETF 3 位（最小变动 0.001 元）——
    1 元左右的 ETF 按两位小数算，相邻两条回撤位可能被舍入成同一个价。
    """
    if lookback <= 1:
        raise ValueError("lookback 必须大于 1")

    window = bars[-lookback:]
    # 少于 10 根没有摆动结构可谈，算出来也是噪声
    if len(window) < 10:
        return None

    hi_idx = max(range(len(window)), key=lambda i: window[i].high)
    lo_idx = min(range(len(window)), key=lambda i: window[i].low)
    high = window[hi_idx].high
    low = window[lo_idx].low
    span = high - low
    if span <= 0:
        return None

    uptrend = hi_idx > lo_idx
    close = window[-1].close

    levels: list[FibLevel] = []

    # 摆动端点本身也是关键位，一并给出
    if uptrend:
        levels.append(FibLevel(0.0, "0%（高点）", round(high, decimals), "swing"))
        levels += [
            FibLevel(r, f"{r * 100:.1f}%", round(high - span * r, decimals), "retracement")
            for r in _FIB_RETRACEMENTS
        ]
        levels.append(FibLevel(1.0, "100%（低点）", round(low, decimals), "swing"))
        levels += [
            FibLevel(r, f"{r * 100:.1f}%", round(low + span * r, decimals), "extension")
            for r in _FIB_EXTENSIONS
        ]
        retraced = (high - close) / span * 100
    else:
        levels.append(FibLevel(0.0, "0%（低点）", round(low, decimals), "swing"))
        levels += [
            FibLevel(r, f"{r * 100:.1f}%", round(low + span * r, decimals), "retracement")
            for r in _FIB_RETRACEMENTS
        ]
        levels.append(FibLevel(1.0, "100%（高点）", round(high, decimals), "swing"))
        levels += [
            FibLevel(r, f"{r * 100:.1f}%", round(high - span * r, decimals), "extension")
            for r in _FIB_EXTENSIONS
        ]
        retraced = (close - low) / span * 100

    below = [lv for lv in levels if lv.price < close]
    above = [lv for lv in levels if lv.price > close]

    return FibAnalysis(
        direction="上升" if uptrend else "下降",
        swing_high=round(high, decimals),
        swing_low=round(low, decimals),
        swing_high_date=window[hi_idx].date,
        swing_low_date=window[lo_idx].date,
        lookback=len(window),
        retracement_pct=round(retraced, 1),
        levels=levels,
        nearest_support=max(below, key=lambda lv: lv.price) if below else None,
        nearest_resistance=min(above, key=lambda lv: lv.price) if above else None,
    )


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
    # 斐波那契回撤（60 日摆动区间）
    fib: FibAnalysis | None = None
    # 形态标签
    patterns: list[str] = field(default_factory=list)


def compute_snapshot(bars: list[Bar], price_decimals: int = 2) -> IndicatorSnapshot:
    """算出最新一根 K 线的全部指标。bars 需按日期升序。

    `price_decimals` 只影响斐波那契价位的舍入（ETF 传 3）。
    """
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

    snap.fib = fib_analysis(bars, decimals=price_decimals)

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

    tags += _fib_tags(snap)

    return tags


def _fib_tags(snap: IndicatorSnapshot) -> list[str]:
    """斐波那契形态标签。

    只在价格**确实贴近**某条关键位时才输出 —— 否则每只票都会挂一堆
    "存在斐波那契位" 的废话标签，反而淹没真正的信号。
    """
    fib = snap.fib
    if fib is None or snap.close is None:
        return []

    tags: list[str] = []

    # 贴近阈值取 1.2%：A股日内波动通常 1-3%，再宽就失去指示意义了
    threshold = 0.012
    for level in fib.levels:
        if level.kind == "swing" or level.price <= 0:
            continue
        if abs(snap.close - level.price) / level.price <= threshold:
            role = "支撑" if level.price <= snap.close else "阻力"
            kind_cn = "回撤" if level.kind == "retracement" else "扩展"
            tags.append(
                f"贴近斐波那契{kind_cn} {level.label} 位（{level.price}，{role}）"
            )

    # 注意：retracement_pct 恒在 0~100（端点即窗口极值），
    # 所以这里只按深度分档，不存在"越界"的情况。
    pct = fib.retracement_pct
    if pct is not None:
        if 55 <= pct <= 68:
            # 61.8% 是市场关注度最高的一档，落在这个带里值得单独点出
            tags.append(f"回撤 {pct:.0f}%，处于 61.8% 黄金分割带")
        elif pct >= 78:
            tags.append(f"回撤 {pct:.0f}%，已深度回吐{fib.direction}段行情")
        elif pct <= 24:
            tags.append(f"回撤仅 {pct:.0f}%，仍处{fib.direction}段末端")

    return tags
