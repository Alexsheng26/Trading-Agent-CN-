"""离线 Mock 数据源。

用途：没网 / 没 token / 跑单测时，让整条链路（数据 -> 智能体 -> API -> 前端）
依然能跑通。数据由代码种子确定性生成，同一只票每次结果一致，方便比对。

⚠️ 纯属造数，绝不可用于任何真实决策。
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta

from app.core.clock import now_cst, now_utc
from app.core.errors import CapabilityNotSupported
from app.data.base import (
    ALL_CAPABILITIES,
    Bar,
    FinancialReport,
    FundHolding,
    FundProfile,
    GoldIntraday,
    GoldQuote,
    IndexQuote,
    MoneyFlow,
    NewsItem,
    Quote,
    StockBasic,
)
from app.data.gold import (
    LONDON_XAU,
    SGE_AU9999,
    arrange_sge_grid,
    previous_business_day,
    sge_slots,
    sge_trading_day,
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

    #: 60 分钟线每天四根，标在各自走完的时刻（A 股 9:30-11:30、13:00-15:00）
    _HOUR_ENDS = (time(10, 30), time(11, 30), time(14, 0), time(15, 0))
    #: 成交量的日内分布：开盘和收盘最活跃
    _HOUR_WEIGHTS = (0.35, 0.2, 0.2, 0.25)

    async def minute_bars(self, symbol: Symbol, period: int, start: date, end: date) -> list[Bar]:
        """把每根模拟日线拆成 4 根 60 分钟线，开高低收和那天的日线对得上。"""
        if period != 60:
            raise CapabilityNotSupported("模拟数据只有 60 分钟线")
        digits = symbol.price_decimals
        out: list[Bar] = []
        for day in await self.daily_bars(symbol, start, end):
            rng = self._rng(symbol, f"minute:{day.date.isoformat()}")
            # 日内走势：开盘价 -> 三个中间点 -> 收盘价，中间点落在当天高低之间
            path = [day.open]
            path += [round(rng.uniform(day.low, day.high), digits) for _ in range(3)]
            path.append(day.close)
            # 当天的最高、最低各落在某一根上，日线和合成后的 2 小时线才对得上
            high_at, low_at = rng.randrange(4), rng.randrange(4)
            for i, ends in enumerate(self._HOUR_ENDS):
                o, c = path[i], path[i + 1]
                hi = day.high if i == high_at else max(o, c)
                lo = day.low if i == low_at else min(o, c)
                volume = round(day.volume * self._HOUR_WEIGHTS[i], 0)
                out.append(
                    Bar(
                        date=day.date,
                        time=datetime.combine(day.date, ends),
                        open=o,
                        high=max(hi, o, c),
                        low=min(lo, o, c),
                        close=c,
                        volume=volume,
                        amount=round(volume * c * 100, 0),
                    )
                )
        return out

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


    # ---------------- 黄金 ----------------

    #: 模拟金价：锚点那天的价格、趋势线每天的增长率（年化约 15%）。
    #: 价格围着趋势线上下波动（均值回复），几年下来不会漂到离谱的位置
    _GOLD_TREND = {SGE_AU9999: (390.0, 0.00042), LONDON_XAU: (1900.0, 0.00037)}

    def _gold_walk(self, code: str, end: date) -> list[Bar]:
        """从固定锚点走到 `end`，结果只取决于 (code, end)。上金所没有成交量，这里也不给。"""
        if code not in self._GOLD_TREND:
            raise CapabilityNotSupported(f"模拟数据没有 {code}")
        start_price, growth = self._GOLD_TREND[code]
        rng = random.Random(f"gold:{code}:bars")
        bars: list[Bar] = []
        prev_close = start_price
        deviation = 0.0  # 偏离趋势线多少（对数）
        cursor = self._ANCHOR
        while cursor <= end:
            if cursor.weekday() >= 5:
                cursor += timedelta(days=1)
                continue
            deviation = 0.98 * deviation + rng.gauss(0, 0.009)
            trend = start_price * math.exp(growth * (cursor - self._ANCHOR).days)
            close = round(trend * math.exp(deviation), 2)
            open_ = round(prev_close * (1 + rng.gauss(0, 0.002)), 2)
            high = round(max(open_, close) * (1 + abs(rng.gauss(0, 0.003))), 2)
            low = round(min(open_, close) * (1 - abs(rng.gauss(0, 0.003))), 2)
            bars.append(
                Bar(
                    date=cursor,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=0.0 if code == SGE_AU9999 else round(rng.uniform(1e5, 3e5), 0),
                    change_pct=round((close / prev_close - 1) * 100, 2),
                )
            )
            prev_close = close
            cursor += timedelta(days=1)
        return bars

    async def gold_bars(self, code: str, start: date, end: date) -> list[Bar]:
        if code == SGE_AU9999:
            # 和真的上金所一样：当前交易日的日线要收盘后才有，盘中由分时合成
            end = min(end, previous_business_day(sge_trading_day(_now_naive())))
        return [b for b in self._gold_walk(code, end) if start <= b.date <= end]

    async def gold_intraday(self, code: str) -> GoldIntraday:
        """按上金所的网格造一天的分时，再走一遍真实数据用的整理逻辑。"""
        if code != SGE_AU9999:
            raise CapabilityNotSupported("模拟数据只有上金所的分时")
        as_of = _now_naive()
        trading_day = sge_trading_day(as_of)
        history = await self.gold_bars(code, trading_day - timedelta(days=20), trading_day)
        prev_close = history[-1].close if history else self._GOLD_TREND[code][0]

        rng = random.Random(f"gold:{code}:intraday:{trading_day.isoformat()}")
        slots = sge_slots()
        prices: list[float] = []
        price = prev_close
        for _ in slots:
            price = round(price * (1 + rng.gauss(0, 0.0005)), 2)
            prices.append(price)
        session = arrange_sge_grid([t.strftime("%H:%M") for t in slots], prices, as_of)
        return GoldIntraday(
            code=code,
            trading_day=session.trading_day,
            as_of=as_of,
            points=session.points,
            prev_close=prev_close,
        )

    async def gold_quote(self, code: str) -> GoldQuote:
        if code != LONDON_XAU:
            raise CapabilityNotSupported("模拟数据的实时报价只有伦敦金")
        today = date.today()
        bars = await self.gold_bars(code, today - timedelta(days=10), today)
        last, prev = bars[-1], bars[-2]
        return GoldQuote(
            code=code,
            price=last.close,
            prev_close=prev.close,
            change=round(last.close - prev.close, 2),
            change_pct=round((last.close / prev.close - 1) * 100, 2),
            open=last.open,
            high=last.high,
            low=last.low,
            as_of=_now_naive(),
        )

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


def _now_naive() -> datetime:
    """当前北京时间（不带时区，精确到分钟）—— 和真实数据源给的时刻同一种写法。"""
    return now_cst().replace(tzinfo=None, second=0, microsecond=0)


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
