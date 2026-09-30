"""离线 Mock 数据源。

用途：没网 / 没 token / 跑单测时，让整条链路（数据 -> 智能体 -> API -> 前端）
依然能跑通。数据由代码种子确定性生成，同一只票每次结果一致，方便比对。

⚠️ 纯属造数，绝不可用于任何真实决策。
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from app.core.clock import now_utc
from app.core.errors import CapabilityNotSupported
from app.data.base import (
    ALL_CAPABILITIES,
    Bar,
    FinancialReport,
    FundHolding,
    FundProfile,
    IndexQuote,
    MoneyFlow,
    NewsItem,
    Quote,
    StockBasic,
)
from app.data.symbols import Symbol, normalize

# 几只常见票，让 demo 看起来像那么回事
_KNOWN: dict[str, tuple[str, str]] = {
    "600519": ("贵州茅台", "白酒"),
    "000001": ("平安银行", "股份制银行"),
    "300750": ("宁德时代", "电池"),
    "601318": ("中国平安", "保险"),
    "000858": ("五粮液", "白酒"),
    "002594": ("比亚迪", "汽车整车"),
    "688981": ("中芯国际", "半导体"),
    "600036": ("招商银行", "股份制银行"),
}

# 几只常见 ETF：名称、跟踪标的
_KNOWN_ETF: dict[str, tuple[str, str]] = {
    "512480": ("半导体ETF", "中证全指半导体产品与设备指数"),
    "512400": ("有色金属ETF", "中证申万有色金属指数"),
    "510300": ("沪深300ETF", "沪深300指数"),
    "159915": ("创业板ETF", "创业板指数"),
    "518880": ("黄金ETF", "上海黄金交易所 Au99.99 现货合约"),
}

# 模拟持仓用的成分股池
_HOLDING_POOL = (
    ("688981", "中芯国际"),
    ("603501", "韦尔股份"),
    ("002371", "北方华创"),
    ("688012", "中微公司"),
    ("603986", "兆易创新"),
    ("688008", "澜起科技"),
    ("600584", "长电科技"),
    ("688256", "寒武纪"),
    ("300661", "圣邦股份"),
    ("002049", "紫光国微"),
    ("688126", "沪硅产业"),
)


class MockProvider:
    name = "mock"
    capabilities = frozenset(ALL_CAPABILITIES)

    async def healthcheck(self) -> bool:
        return True

    # ---------------- 内部工具 ----------------

    def _rng(self, symbol: Symbol, salt: str = "") -> random.Random:
        return random.Random(f"{symbol.canonical}:{salt}")

    def _name(self, symbol: Symbol) -> str:
        if symbol.is_etf:
            return _KNOWN_ETF.get(symbol.code, (f"模拟ETF{symbol.code[-3:]}", ""))[0]
        return _KNOWN.get(symbol.code, (f"模拟股份{symbol.code[-3:]}", "综合"))[0]

    def _industry(self, symbol: Symbol) -> str:
        if symbol.is_etf:
            return _KNOWN_ETF.get(symbol.code, ("", "模拟指数"))[1]
        return _KNOWN.get(symbol.code, ("", "综合"))[1]

    def _base_price(self, symbol: Symbol) -> float:
        rng = self._rng(symbol, "price")
        if symbol.is_etf:
            # ETF 净值通常在 1 元上下，价格精确到厘
            return round(rng.uniform(0.6, 4.5), 3)
        return round(rng.uniform(8, 180), 2)

    # ---------------- 接口实现 ----------------

    async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]:
        keyword = keyword.strip()
        hits: list[StockBasic] = []
        for code, (name, industry) in {**_KNOWN, **_KNOWN_ETF}.items():
            if keyword in code or keyword in name:
                sym = normalize(code)
                hits.append(
                    StockBasic(
                        symbol=sym.canonical,
                        name=name,
                        exchange=sym.exchange,
                        board=sym.board,
                        industry=industry,
                    )
                )
        return hits[:limit]

    async def stock_basic(self, symbol: Symbol) -> StockBasic:
        rng = self._rng(symbol, "basic")
        return StockBasic(
            symbol=symbol.canonical,
            name=self._name(symbol),
            exchange=symbol.exchange,
            board=symbol.board,
            industry=self._industry(symbol),
            list_date=date(2010, 1, 1) + timedelta(days=rng.randint(0, 4000)),
            total_share=rng.uniform(5e8, 2e10),
            float_share=rng.uniform(3e8, 1.5e10),
        )

    async def quote(self, symbol: Symbol) -> Quote:
        bars = await self.daily_bars(symbol, date.today() - timedelta(days=10), date.today())
        last = bars[-1]
        prev = bars[-2].close if len(bars) > 1 else last.close
        rng = self._rng(symbol, "quote")
        shares = rng.uniform(5e8, 2e10)
        etf = symbol.is_etf
        return Quote(
            symbol=symbol.canonical,
            name=self._name(symbol),
            price=last.close,
            prev_close=prev,
            open=last.open,
            high=last.high,
            low=last.low,
            change_pct=round((last.close / prev - 1) * 100, 2) if prev else None,
            volume=last.volume,
            amount=last.amount,
            turnover_rate=round(rng.uniform(0.3, 6.0), 2),
            # ETF 没有估值和市值的概念
            pe_ttm=None if etf else round(rng.uniform(8, 60), 2),
            pb=None if etf else round(rng.uniform(0.8, 12), 2),
            total_market_cap=None if etf else last.close * shares,
            float_market_cap=None if etf else last.close * shares * 0.8,
            updated_at=now_utc(),
        )

    # 随机游走的锚点：所有窗口都从这天开始生成再切片，
    # 保证 quote() 和任意区间的 daily_bars() 对同一天给出同一个价格。
    _ANCHOR = date(2021, 1, 4)

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        """生成带趋势 + 噪声的几何随机游走，看起来像 K 线。"""
        return [b for b in self._full_walk(symbol, end) if start <= b.date <= end]

    def _full_walk(self, symbol: Symbol, end: date) -> list[Bar]:
        """从固定锚点走到 `end`，结果只取决于 (symbol, end)。"""
        rng = self._rng(symbol, "bars")
        digits = symbol.price_decimals
        drift = rng.uniform(-0.0012, 0.0018)
        vol = rng.uniform(0.014, 0.032)
        limit = symbol.price_limit_pct / 100

        bars: list[Bar] = []
        cursor = self._ANCHOR
        prev_close = self._base_price(symbol)
        while cursor <= end:
            # 跳过周末（不含节假日，Mock 够用了）
            if cursor.weekday() >= 5:
                cursor += timedelta(days=1)
                continue
            shock = rng.gauss(drift, vol)
            # 加一点周期成分，让均线有交叉，技术面分析师才有东西可说
            shock += 0.004 * math.sin(len(bars) / 9.0)
            shock = max(-limit * 0.95, min(limit * 0.95, shock))
            close = round(prev_close * (1 + shock), digits)
            open_ = round(prev_close * (1 + rng.gauss(0, vol / 3)), digits)
            high = round(max(open_, close) * (1 + abs(rng.gauss(0, vol / 4))), digits)
            low = round(min(open_, close) * (1 - abs(rng.gauss(0, vol / 4))), digits)
            volume = round(rng.uniform(3e5, 9e6), 0)
            bars.append(
                Bar(
                    date=cursor,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    amount=round(volume * close * 100, 0),
                    change_pct=round(shock * 100, 2),
                    turnover_rate=round(rng.uniform(0.3, 5.0), 2),
                )
            )
            prev_close = close
            cursor += timedelta(days=1)
        return bars

    async def financials(self, symbol: Symbol, periods: int = 8) -> list[FinancialReport]:
        if symbol.is_etf:
            raise CapabilityNotSupported("ETF 没有财报")
        rng = self._rng(symbol, "fin")
        reports: list[FinancialReport] = []
        revenue = rng.uniform(2e9, 8e10)
        margin = rng.uniform(0.08, 0.45)
        today = date.today()
        for i in range(periods):
            # 往前推报告期：3/31、6/30、9/30、12/31
            quarter_end = _previous_report_period(today, i)
            growth = rng.gauss(0.09, 0.12)
            revenue = revenue / (1 + growth)
            net = revenue * margin * rng.uniform(0.85, 1.15)
            reports.append(
                FinancialReport(
                    symbol=symbol.canonical,
                    period=quarter_end.isoformat(),
                    revenue=round(revenue, 0),
                    revenue_yoy=round(growth * 100, 2),
                    net_profit=round(net, 0),
                    net_profit_yoy=round(rng.gauss(growth * 1.3, 0.15) * 100, 2),
                    gross_margin=round(margin * 100 + rng.uniform(5, 18), 2),
                    net_margin=round(margin * 100, 2),
                    roe=round(rng.uniform(4, 28), 2),
                    debt_ratio=round(rng.uniform(15, 68), 2),
                    eps=round(net / rng.uniform(8e8, 5e9), 3),
                    bps=round(rng.uniform(3, 45), 2),
                    operating_cashflow=round(net * rng.uniform(0.6, 1.6), 0),
                )
            )
        return reports

    async def news(
        self, symbol: Symbol, limit: int = 20, keyword: str | None = None
    ) -> list[NewsItem]:
        rng = self._rng(symbol, "news")
        # 按关键词搜时（ETF），模板里的主语换成关键词，看起来像板块新闻
        name = keyword or self._name(symbol)
        templates = [
            f"{name}发布半年度业绩预告，净利润同比预增",
            f"机构调研｜多家公募近期集中调研{name}",
            f"{name}拟投资建设新产能项目",
            f"行业观察：{self._industry(symbol)}板块景气度回升",
            f"{name}获北向资金连续加仓",
            f"{name}公告：控股股东拟减持不超过 2% 股份",
            f"券商研报：维持{name}“增持”评级",
        ]
        rng.shuffle(templates)
        now = now_utc()
        return [
            NewsItem(
                title=t,
                published_at=now - timedelta(hours=rng.randint(1, 240)),
                source="模拟数据源",
                url="",
                summary="（离线 Mock 数据，仅用于打通链路，不具备任何信息价值）",
            )
            for t in templates[:limit]
        ]

    async def money_flow(self, symbol: Symbol, days: int = 10) -> list[MoneyFlow]:
        rng = self._rng(symbol, "flow")
        out: list[MoneyFlow] = []
        cursor = date.today()
        while len(out) < days:
            if cursor.weekday() < 5:
                main = rng.gauss(0, 1.2e8)
                out.append(
                    MoneyFlow(
                        date=cursor,
                        main_net_inflow=round(main, 0),
                        main_net_inflow_pct=round(rng.gauss(0, 6), 2),
                        super_large_net=round(main * rng.uniform(0.3, 0.7), 0),
                        large_net=round(main * rng.uniform(0.2, 0.5), 0),
                        medium_net=round(-main * rng.uniform(0.1, 0.4), 0),
                        small_net=round(-main * rng.uniform(0.1, 0.4), 0),
                    )
                )
            cursor -= timedelta(days=1)
        return list(reversed(out))

    async def index_quotes(self) -> list[IndexQuote]:
        rng = random.Random("mock-index")
        specs = [
            ("000001", "上证指数", 3200.0),
            ("399001", "深证成指", 10500.0),
            ("399006", "创业板指", 2100.0),
            ("000300", "沪深300", 3800.0),
        ]
        return [
            IndexQuote(
                code=code,
                name=name,
                price=round(base * rng.uniform(0.97, 1.03), 2),
                change_pct=round(rng.gauss(0, 0.9), 2),
            )
            for code, name, base in specs
        ]


    # ---------------- ETF ----------------

    async def etf_list(self) -> list[StockBasic]:
        out: list[StockBasic] = []
        for code, (name, _index) in _KNOWN_ETF.items():
            sym = normalize(code)
            out.append(
                StockBasic(symbol=sym.canonical, name=name, exchange=sym.exchange, board=sym.board)
            )
        return out

    async def fund_profile(self, symbol: Symbol) -> FundProfile:
        if not symbol.is_etf:
            raise CapabilityNotSupported(f"{symbol.canonical} 不是 ETF")
        rng = self._rng(symbol, "fund")
        index = self._industry(symbol)
        return FundProfile(
            symbol=symbol.canonical,
            name=self._name(symbol),
            full_name=f"模拟{index}交易型开放式指数证券投资基金",
            fund_type="指数型-股票",
            tracking_index=index,
            benchmark=f"{index}收益率",
            manager="模拟基金管理有限公司",
            inception_date=date(2015, 1, 1) + timedelta(days=rng.randint(0, 2500)),
            asset_size=round(rng.uniform(5e8, 3e10), 0),
            asset_size_date=_previous_report_period(date.today(), 0),
            share_size=round(rng.uniform(3e8, 2e10), 0),
            management_fee=0.5,
            custody_fee=0.1,
        )

    async def fund_holdings(self, symbol: Symbol) -> list[FundHolding]:
        if not symbol.is_etf:
            raise CapabilityNotSupported(f"{symbol.canonical} 不是 ETF")
        rng = self._rng(symbol, "holdings")
        pool = list(_HOLDING_POOL)
        rng.shuffle(pool)
        period_end = _previous_report_period(date.today(), 0)
        period = f"{period_end.year}年{(period_end.month - 1) // 3 + 1}季度"
        weights = sorted((round(rng.uniform(2.5, 12.0), 2) for _ in range(10)), reverse=True)
        return [
            FundHolding(code=code, name=name, weight_pct=w, period=period)
            for (code, name), w in zip(pool[:10], weights, strict=True)
        ]


def _previous_report_period(today: date, offset: int) -> date:
    """返回倒数第 offset 个已过去的报告期末。"""
    quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    candidates: list[date] = []
    for year in range(today.year, today.year - 6, -1):
        for month, day in reversed(quarter_ends):
            d = date(year, month, day)
            if d < today:
                candidates.append(d)
    return candidates[min(offset, len(candidates) - 1)]
