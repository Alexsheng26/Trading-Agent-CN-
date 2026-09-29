"""把 `MarketSnapshot` 渲染成给模型看的中文行情简报。

这段文本是整条流水线里最重要的东西之一：它决定了分析师"看到什么"。
两条原则：
1. **只给事实，不给结论** —— 判断留给智能体，这里不做任何主观描述；
2. **明确标注缺失** —— 数据源挂掉时要写"数据缺失"，不能让模型以为是 0，
   否则会基于空数据编出结论。

这份简报在一次分析中被所有智能体共享，作为 `system_blocks[0]` 命中提示缓存。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.data.base import MarketSnapshot
from app.data.symbols import Symbol
from app.services.indicators import IndicatorSnapshot, compute_snapshot

# 交易风格 -> 给模型的说明
HORIZONS: dict[str, str] = {
    "短线": "持仓 1-5 个交易日，看重量价配合、题材催化与短期技术形态",
    "波段": "持仓 2 周到 2 个月，看重趋势结构、资金流向与景气度拐点",
    "中线": "持仓 3-12 个月，看重基本面质量、估值水位与行业格局",
}


@dataclass(slots=True)
class AnalysisContext:
    """一次分析的全部输入。"""

    symbol: Symbol
    snapshot: MarketSnapshot
    indicators: IndicatorSnapshot
    horizon: str = "波段"
    as_of: datetime = field(default_factory=datetime.now)
    user_note: str = ""  # 用户补充的关注点，如"只关心是否该止损"

    @classmethod
    def build(
        cls, snapshot: MarketSnapshot, horizon: str = "波段", user_note: str = ""
    ) -> AnalysisContext:
        return cls(
            symbol=snapshot.symbol,
            snapshot=snapshot,
            indicators=compute_snapshot(snapshot.bars),
            horizon=horizon if horizon in HORIZONS else "波段",
            user_note=user_note.strip(),
        )

    @property
    def display_name(self) -> str:
        if self.snapshot.basic and self.snapshot.basic.name:
            return self.snapshot.basic.name
        if self.snapshot.quote and self.snapshot.quote.name:
            return self.snapshot.quote.name
        return self.symbol.canonical

    # ------------------------------------------------------------ 渲染

    def market_brief(self) -> str:
        """给所有智能体共享的行情简报。"""
        parts = [
            self._header(),
            self._quote_section(),
            self._indicator_section(),
            self._fib_section(),
            self._price_history_section(),
            self._financial_section(),
            self._money_flow_section(),
            self._news_section(),
            self._market_section(),
            self._meta_section(),
        ]
        return "\n\n".join(p for p in parts if p)

    def _header(self) -> str:
        basic = self.snapshot.basic
        lines = [
            "# 标的行情简报",
            "",
            f"- 名称：{self.display_name}",
            f"- 代码：{self.symbol.canonical}",
            f"- 板块：{self.symbol.board}",
            f"- 涨跌幅限制：±{self.symbol.price_limit_pct:g}%"
            "（常规交易日普通股票；ST 股为 ±5%）",
            "- 交易制度：T+1（当日买入次日才能卖出）",
            f"- 数据截止：{self.as_of:%Y-%m-%d %H:%M}",
            f"- 分析周期：{self.horizon}（{HORIZONS[self.horizon]}）",
        ]
        if basic:
            if basic.industry:
                lines.append(f"- 所属行业：{basic.industry}")
            if basic.list_date:
                years = (date.today() - basic.list_date).days / 365.25
                lines.append(f"- 上市日期：{basic.list_date}（已上市约 {years:.1f} 年）")
        return "\n".join(lines)

    def _quote_section(self) -> str:
        q = self.snapshot.quote
        if q is None:
            return "## 最新行情\n\n⚠️ 行情数据缺失，不要对当前价格做任何假设。"
        # 非实时价必须在简报里说清楚。不说的话模型会拿一个上一交易日的收盘价
        # 当现价去算入场区间和止损位，而且不会在结论里声明这个前提 ——
        # 用户看到的就是一套基于过期价格却毫无提示的点位。
        stale_warning = ""
        if getattr(q, "is_realtime", True) is False:
            when = q.quote_date.isoformat() if q.quote_date else "最近一个交易日"
            stale_warning = (
                f"\n⚠️ **以下价格不是实时价**，是 {when} 的收盘价"
                "（实时行情接口当前不可用）。"
                "涉及当前价位的判断（入场区间、止损、是否突破）必须据此降低置信度，"
                "并在结论里明确声明这一前提。\n"
            )

        lines = [
            "## 最新行情",
            stale_warning,
            f"- 最新价：{_num(q.price)}"
            + ("（收盘价，非实时）" if getattr(q, "is_realtime", True) is False else ""),
            f"- 涨跌幅：{_num(q.change_pct)}%",
            f"- 今开/最高/最低：{_num(q.open)} / {_num(q.high)} / {_num(q.low)}",
            f"- 昨收：{_num(q.prev_close)}",
            f"- 成交额：{_yi(q.amount)}",
            f"- 换手率：{_num(q.turnover_rate)}%",
            f"- 市盈率(TTM)：{_num(q.pe_ttm)}",
            f"- 市净率：{_num(q.pb)}",
            f"- 总市值：{_yi(q.total_market_cap)}",
            f"- 流通市值：{_yi(q.float_market_cap)}",
        ]
        return "\n".join(lines)

    def _indicator_section(self) -> str:
        i = self.indicators
        if i.close is None:
            return "## 技术指标\n\n⚠️ K线数据缺失，无法计算技术指标。"
        lines = [
            "## 技术指标（基于前复权日线）",
            "",
            f"- 均线：MA5：{_num(i.ma5)}　MA10：{_num(i.ma10)}　MA20：{_num(i.ma20)}"
            f"　MA60：{_num(i.ma60)}　MA120：{_num(i.ma120)}",
            f"- MACD：DIF：{_num(i.macd_dif)}　DEA：{_num(i.macd_dea)}　柱：{_num(i.macd_hist)}",
            f"- RSI6：{_num(i.rsi6)}　RSI14：{_num(i.rsi14)}",
            f"- 布林带：上轨：{_num(i.boll_upper)}　中轨：{_num(i.boll_mid)}"
            f"　下轨：{_num(i.boll_lower)}",
            f"- KDJ：K：{_num(i.kdj_k)}　D：{_num(i.kdj_d)}　J：{_num(i.kdj_j)}",
            f"- ATR14：{_num(i.atr14)}（占股价 {_num(i.atr_pct)}%）",
            f"- 60日区间：最高 {_num(i.high_60)}　最低 {_num(i.low_60)}"
            f"　距高点 {_num(i.pct_from_high_60)}%",
            f"- 区间涨幅：20日 {_num(i.return_20d)}%　60日 {_num(i.return_60d)}%",
            f"- 20日年化波动率：{_num(i.volatility_20d)}%",
            f"- 量比(5日/20日均量)：{_num(i.volume_ratio_5_20)}",
            f"- 形态：{'、'.join(i.patterns) if i.patterns else '无明显形态'}",
        ]
        return "\n".join(lines)

    def _fib_section(self) -> str:
        """斐波那契回撤。

        重点是把"方向"和"当前价处在哪两条位之间"讲清楚 ——
        只给一串价位，模型很容易把支撑说成阻力。
        """
        fib = self.indicators.fib
        if fib is None:
            return ""

        close = self.indicators.close
        lines = [
            f"## 斐波那契回撤（近 {fib.lookback} 个交易日摆动区间）",
            "",
            f"- 摆动方向：**{fib.direction}段**"
            + (
                f"（低点 {fib.swing_low} @ {fib.swing_low_date} → "
                f"高点 {fib.swing_high} @ {fib.swing_high_date}）"
                if fib.direction == "上升"
                else f"（高点 {fib.swing_high} @ {fib.swing_high_date} → "
                f"低点 {fib.swing_low} @ {fib.swing_low_date}）"
            ),
            f"- 当前已回撤：{_num(fib.retracement_pct)}%"
            "（0% = 仍在该段末端，100% = 已回到该段起点）",
        ]
        if fib.nearest_support:
            lines.append(
                f"- 下方最近关键位：{fib.nearest_support.label} "
                f"= {fib.nearest_support.price}"
            )
        if fib.nearest_resistance:
            lines.append(
                f"- 上方最近关键位：{fib.nearest_resistance.label} "
                f"= {fib.nearest_resistance.price}"
            )

        lines += ["", "| 比例 | 价位 | 类型 | 相对当前价 |", "| --- | --- | --- | --- |"]
        for lv in fib.levels:
            if close:
                delta = (lv.price / close - 1) * 100
                rel = f"{delta:+.2f}%"
            else:
                rel = "—"
            kind_cn = {
                "swing": "摆动端点",
                "retracement": "回撤位",
                "extension": "扩展目标",
            }[lv.kind]
            lines.append(f"| {lv.label} | {lv.price} | {kind_cn} | {rel} |")

        lines += [
            "",
            "说明：摆动端点取的是窗口内最高/最低价，会随新极值自动滚动，"
            "因此回撤率恒在 0~100% 之间 —— 判断是否突破区间请看"
            "「接近 60 日新高/新低」这类标签，不要从回撤率去推。",
            "",
            "读法提醒："
            + (
                "这是一段上涨后的回撤，回撤位在高点下方，起支撑作用；"
                "扩展位在高点上方，是继续上攻时的目标。"
                if fib.direction == "上升"
                else "这是一段下跌后的反弹，回撤位在低点上方，起阻力作用；"
                "扩展位在低点下方，是继续下跌时的目标。"
            ),
        ]
        return "\n".join(lines)

    def _price_history_section(self, days: int = 20) -> str:
        bars = self.snapshot.bars[-days:]
        if not bars:
            return ""
        lines = [
            f"## 最近 {len(bars)} 个交易日量价",
            "",
            "| 日期 | 开 | 高 | 低 | 收 | 涨跌% | 成交额(亿) |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for b in bars:
            amount_yi = f"{b.amount / 1e8:.2f}" if b.amount else "-"
            lines.append(
                f"| {b.date} | {_num(b.open)} | {_num(b.high)} | {_num(b.low)} "
                f"| {_num(b.close)} | {_num(b.change_pct)} | {amount_yi} |"
            )
        return "\n".join(lines)

    def _financial_section(self) -> str:
        reports = self.snapshot.financials
        if not reports:
            return "## 财务数据\n\n⚠️ 财务数据缺失，基本面判断请明确说明受此限制。"
        lines = [
            "## 财务数据（按报告期，单位：元 / %）",
            "",
            "| 报告期 | 营收 | 营收同比% | 归母净利 | 净利同比% "
            "| 毛利率% | 净利率% | ROE% | 资产负债率% | EPS |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in reports[:8]:
            lines.append(
                f"| {r.period} | {_yi(r.revenue, unit=False)} | {_num(r.revenue_yoy)} "
                f"| {_yi(r.net_profit, unit=False)} | {_num(r.net_profit_yoy)} "
                f"| {_num(r.gross_margin)} | {_num(r.net_margin)} | {_num(r.roe)} "
                f"| {_num(r.debt_ratio)} | {_num(r.eps)} |"
            )
        latest = reports[0]
        if latest.operating_cashflow is not None and latest.net_profit:
            ratio = latest.operating_cashflow / latest.net_profit
            lines.append("")
            lines.append(
                f"最新报告期经营现金流 / 归母净利润 = {ratio:.2f}"
                "（显著低于 1 通常意味着利润质量需要留意）"
            )
        return "\n".join(lines)

    def _money_flow_section(self) -> str:
        flows = self.snapshot.money_flow
        if not flows:
            return "## 资金流向\n\n⚠️ 资金流向数据缺失。"
        total = sum(f.main_net_inflow or 0 for f in flows)
        lines = [
            f"## 资金流向（近 {len(flows)} 个交易日）",
            "",
            "| 日期 | 主力净流入(万元) | 占成交额% | 超大单(万元) | 大单(万元) |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in flows:
            lines.append(
                f"| {f.date} | {_wan(f.main_net_inflow)} | {_num(f.main_net_inflow_pct)} "
                f"| {_wan(f.super_large_net)} | {_wan(f.large_net)} |"
            )
        lines.append("")
        lines.append(f"区间主力资金合计：{_wan(total)} 万元")
        return "\n".join(lines)

    def _news_section(self) -> str:
        news = self.snapshot.news
        if not news:
            return "## 新闻与公告\n\n⚠️ 新闻数据缺失，消息面判断请明确说明受此限制。"
        lines = [f"## 新闻与公告（近 {len(news)} 条）", ""]
        for n in news:
            when = f"{n.published_at:%m-%d %H:%M}" if n.published_at else "时间未知"
            source = f"｜{n.source}" if n.source else ""
            lines.append(f"- [{when}{source}] {n.title}")
        return "\n".join(lines)

    def _market_section(self) -> str:
        indexes = self.snapshot.indexes
        if not indexes:
            return ""
        cells = "　".join(f"{i.name} {_num(i.price)}（{_num(i.change_pct)}%）" for i in indexes)
        return f"## 大盘环境\n\n{cells}"

    def _meta_section(self) -> str:
        """数据来源与降级情况 —— 让模型知道哪些数据不可靠。"""
        lines = ["## 数据溯源"]
        if self.snapshot.sources:
            lines.append("")
            lines.append(
                "各维度实际数据源："
                + "、".join(f"{k}={v}" for k, v in sorted(self.snapshot.sources.items()))
            )
        if self.snapshot.warnings:
            lines.append("")
            lines.append("⚠️ 本次抓取的失败项（相关维度请勿臆测）：")
            for w in self.snapshot.warnings:
                lines.append(f"- {w}")
        if "mock" in self.snapshot.sources.values():
            lines.append("")
            lines.append(
                "🔴 **注意：本次使用了 mock 离线模拟数据，全部数字均为程序生成的假数据，"
                "不具备任何现实意义。请在结论开头明确声明这一点。**"
            )
        return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------- 格式化


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:,.0f}"


def _yi(value: float | None, *, unit: bool = True) -> str:
    """元 -> 亿元。"""
    if value is None:
        return "数据缺失"
    yi = value / 1e8
    return f"{yi:,.2f} 亿" if unit else f"{yi:,.2f}亿"


def _wan(value: float | None) -> str:
    """元 -> 万元。"""
    if value is None:
        return "数据缺失"
    return f"{value / 1e4:,.0f}"
