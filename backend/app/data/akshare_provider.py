"""AKShare 数据源适配器。

AKShare 免费、免注册、覆盖面广，是本项目的默认数据源。
它底层是爬各家财经站点，所以：
  1. 全部接口都是同步阻塞的 —— 这里统一用 `asyncio.to_thread` 包起来；
  2. 接口偶发失败或字段改名很常见 —— 取字段一律走 `_pick()` 容错，
     拿不到就抛 `ProviderUnavailable` 让 registry 降级到 Tushare。

ETF 走另一组接口（东财 `fund_etf_*` + 天天基金档案），见各方法里的分支。

安装：pip install -e ".[akshare]"
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any

from app.core.clock import now_cst, now_utc
from app.core.errors import ProviderUnavailable
from app.data.base import (
    CAP_BARS,
    CAP_BASIC,
    CAP_ETF_BARS,
    CAP_ETF_LIST,
    CAP_ETF_QUOTE,
    CAP_FINANCIALS,
    CAP_FUND_HOLDINGS,
    CAP_FUND_PROFILE,
    CAP_INDEXES,
    CAP_MONEY_FLOW,
    CAP_NEWS,
    CAP_QUOTE,
    CAP_SEARCH,
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
from app.data.symbols import Symbol, try_normalize

log = logging.getLogger(__name__)


class AkshareProvider:
    name = "akshare"
    capabilities = frozenset(
        {
            CAP_SEARCH,
            CAP_BASIC,
            CAP_QUOTE,
            CAP_BARS,
            CAP_FINANCIALS,
            CAP_NEWS,
            CAP_MONEY_FLOW,
            CAP_INDEXES,
            CAP_ETF_QUOTE,
            CAP_ETF_BARS,
            CAP_ETF_LIST,
            CAP_FUND_PROFILE,
            CAP_FUND_HOLDINGS,
        }
    )

    def __init__(self) -> None:
        self._ak: Any | None = None
        self._spot_cache: Any | None = None

    # ---------------- 依赖与调用 ----------------

    def _module(self) -> Any:
        if self._ak is None:
            try:
                import akshare  # noqa: PLC0415  —— 延迟导入，没装也不影响其他数据源
            except ImportError as exc:  # pragma: no cover - 取决于安装情况
                raise ProviderUnavailable(
                    'akshare 未安装，执行 pip install -e ".[akshare]" 或把它从 '
                    "DATA_PROVIDER_ORDER 中移除"
                ) from exc
            self._ak = akshare
            self._enable_browser_transport()
        return self._ak

    @staticmethod
    def _enable_browser_transport() -> None:
        """把 akshare 的请求换成浏览器 TLS 指纹。

        东方财富会按 TLS 握手特征拦掉普通 Python requests（表现是
        "Remote end closed connection without response"）。
        详细原因见 app/data/browser_transport.py 的模块注释。
        """
        from app.core.config import get_settings  # noqa: PLC0415 —— 避免循环导入
        from app.data.browser_transport import enable  # noqa: PLC0415

        settings = get_settings()
        if not settings.akshare_browser_transport:
            log.info("AKSHARE_BROWSER_TRANSPORT=false，akshare 使用普通 requests")
            return
        enable(settings.akshare_impersonate)

    async def _call(self, func_name: str, /, **kwargs: Any) -> Any:
        """在线程池里调 AKShare，避免阻塞事件循环。"""
        ak = self._module()
        func = getattr(ak, func_name, None)
        if func is None:
            raise ProviderUnavailable(f"当前 akshare 版本没有 {func_name}()，请升级 akshare")
        try:
            return await asyncio.to_thread(lambda: func(**kwargs))
        except Exception as exc:
            raise ProviderUnavailable(f"akshare.{func_name}() 调用失败：{exc}") from exc

    async def healthcheck(self) -> bool:
        try:
            self._module()
        except ProviderUnavailable:
            return False
        return True

    # ---------------- 接口实现 ----------------

    async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]:
        df = await self._spot()
        keyword = keyword.strip()
        rows = []
        for _, row in df.iterrows():
            code = str(_pick(row, "代码") or "")
            name = str(_pick(row, "名称") or "")
            if keyword and keyword not in code and keyword not in name:
                continue
            sym = try_normalize(code)
            if sym is None:
                continue
            rows.append(
                StockBasic(
                    symbol=sym.canonical, name=name, exchange=sym.exchange, board=sym.board
                )
            )
            if len(rows) >= limit:
                break
        return rows

    async def stock_basic(self, symbol: Symbol) -> StockBasic:
        if symbol.is_etf:
            # 个股信息接口查不到基金。ETF 的"基础信息"来自基金档案：
            # 行业一栏放跟踪标的 —— 对 ETF 来说这才是它"属于什么"
            profile = await self.fund_profile(symbol)
            return StockBasic(
                symbol=symbol.canonical,
                name=profile.name,
                exchange=symbol.exchange,
                board=symbol.board,
                industry=profile.tracking_index or profile.fund_type,
                list_date=profile.inception_date,
            )
        df = await self._call("stock_individual_info_em", symbol=symbol.akshare)
        info = {str(r["item"]): r["value"] for _, r in df.iterrows()} if not df.empty else {}
        list_date = _parse_date(info.get("上市时间"))
        return StockBasic(
            symbol=symbol.canonical,
            name=str(info.get("股票简称") or ""),
            exchange=symbol.exchange,
            board=symbol.board,
            industry=str(info.get("行业") or ""),
            list_date=list_date,
            total_share=_to_float(info.get("总股本")),
            float_share=_to_float(info.get("流通股")),
        )

    async def quote(self, symbol: Symbol) -> Quote:
        """单只股票的实时行情。

        **刻意不走全市场快照。** `stock_zh_a_spot_em` 内部按每页 100 条翻页，
        A股 5400 多只要翻 54 页，而且 akshare 在每页之间会 sleep 0.5-1.5 秒 ——
        一次调用要 60-90 秒，在任何合理的请求超时下都不可能成功。
        （这正是"价格全是模拟数据"的真正原因：快照每次都超时，
        于是每次都降级到 mock。）

        改用 `stock_bid_ask_em`：单只股票、单个请求、不翻页不睡眠，一秒级返回。
        代价是它只给盘口和量价，不给名称和估值 ——
        名称走 `stock_individual_info_em`（单请求且强缓存），
        市盈率/市净率则确实拿不到，按"数据缺失"如实上报，不猜不编。

        注意：该接口判断市场只看代码是否以 6 开头，北交所（8/4 开头）会被
        当成深市，查不到数据 —— 那种情况下它会取值失败并抛出，registry 照常
        降级到下一个数据源，不会返回错的价格。

        ETF 同理：深市 ETF（159）能正确查到；沪市 ETF（5 开头）会被当成深市，
        所以直接跳过，由雪球 / 新浪提供 —— 发一个注定查错的请求没有意义。
        """
        if symbol.is_etf and symbol.exchange == "SH":
            raise ProviderUnavailable(
                "东财盘口接口按代码是否 6 开头判断沪市，沪市 ETF 会被查成深市，已跳过"
            )
        raw = await self._call("stock_bid_ask_em", symbol=symbol.code)
        if raw is None or raw.empty:
            raise ProviderUnavailable(
                f"akshare 盘口接口没有 {symbol.canonical}（可能停牌或已退市）"
            )

        # 该接口返回的是 item/value 两列的竖表，转成字典好取
        fields = {str(r["item"]): r["value"] for _, r in raw.iterrows()}
        price = _to_float(fields.get("最新"))
        prev_close = _to_float(fields.get("昨收"))
        if price is None or price <= 0:
            # 停牌时最新价可能是 0，用昨收兜底，免得界面显示 0.00
            price = prev_close
        if price is None:
            raise ProviderUnavailable(f"akshare 没返回 {symbol.canonical} 的价格")

        change_pct = _to_float(fields.get("涨幅"))
        if change_pct is None and prev_close:
            change_pct = round((price - prev_close) / prev_close * 100, 2)
        # 总手的单位是"手"，1 手 = 100 股（ETF 是 100 份）
        volume_lots = _to_float(fields.get("总手"))

        if symbol.is_etf:
            # ETF 没有市盈率、市净率；个股信息接口也查不到它的名称和市值。
            # 名称刻意不在这里查：唯一的来源是天天基金档案页，akshare 请求它时
            # 不带超时 —— 它一慢，这条每 5 秒轮询一次的行情就跟着超时，
            # 明明价格一秒就拿到了。名称留空，quote_with_fallback 会从本地代码目录补
            return Quote(
                symbol=symbol.canonical,
                name="",
                price=price,
                prev_close=prev_close,
                open=_to_float(fields.get("今开")),
                high=_to_float(fields.get("最高")),
                low=_to_float(fields.get("最低")),
                change_pct=change_pct,
                volume=volume_lots * 100 if volume_lots is not None else None,
                amount=_to_float(fields.get("金额")),
                turnover_rate=_to_float(fields.get("换手")),
                updated_at=now_utc(),
            )

        basic = await self._basic_info(symbol)
        valuation = await self._valuation(symbol)

        return Quote(
            symbol=symbol.canonical,
            name=str(basic.get("股票简称") or ""),
            price=price,
            prev_close=prev_close,
            open=_to_float(fields.get("今开")),
            high=_to_float(fields.get("最高")),
            low=_to_float(fields.get("最低")),
            change_pct=change_pct,
            volume=volume_lots * 100 if volume_lots is not None else None,
            amount=_to_float(fields.get("金额")),
            turnover_rate=_to_float(fields.get("换手")),
            # 盘口接口不含估值，另取一次估值表（单请求、按小时缓存）。
            # 取不到就是 None，不猜不编。
            pe_ttm=_scale_to_price(valuation.get("pe_ttm"), valuation.get("close"), price),
            pb=_scale_to_price(valuation.get("pb"), valuation.get("close"), price),
            total_market_cap=_to_float(basic.get("总市值")),
            float_market_cap=_to_float(basic.get("流通市值")),
            updated_at=now_utc(),
        )

    async def _valuation(self, symbol: Symbol) -> dict[str, float | None]:
        """市盈率 / 市净率。

        `stock_bid_ask_em` 不给估值，这里单独取一次东财的估值表
        （单请求、不翻页）。它是**日频**数据，最后一行是最近一个交易日收盘口径，
        所以按小时缓存就够，不必跟着行情每 5 秒刷。
        """
        from app.core.config import get_settings  # noqa: PLC0415 —— 避免循环导入
        from app.data.cache import cache  # noqa: PLC0415

        async def load() -> dict[str, float | None]:
            try:
                df = await self._call("stock_value_em", symbol=symbol.code)
            except ProviderUnavailable:
                return {}  # 估值取不到不该让整条行情失败
            if df is None or df.empty:
                return {}
            row = df.iloc[-1]  # 该接口按日期升序，最后一行最新
            return {
                "pe_ttm": _to_float(_pick(row, "PE(TTM)")),
                "pb": _to_float(_pick(row, "市净率")),
                "close": _to_float(_pick(row, "当日收盘价")),
            }

        return await cache.get_or_set(
            f"akshare:valuation:{symbol.code}", get_settings().history_cache_ttl, load
        )

    async def _basic_info(self, symbol: Symbol) -> dict[str, Any]:
        """个股基本信息（名称/市值/行业）。单请求，强缓存 —— 这些一天都不会变。"""
        from app.core.config import get_settings  # noqa: PLC0415 —— 避免循环导入
        from app.data.cache import cache  # noqa: PLC0415

        async def load() -> dict[str, Any]:
            try:
                df = await self._call("stock_individual_info_em", symbol=symbol.akshare)
            except ProviderUnavailable:
                return {}  # 名称拿不到不该让整个行情失败
            if df is None or df.empty:
                return {}
            return {str(r["item"]): r["value"] for _, r in df.iterrows()}

        return await cache.get_or_set(
            f"akshare:info:{symbol.code}", get_settings().history_cache_ttl, load
        )

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        # ETF 日线接口的参数和返回列与个股的完全一致，只是函数不同；
        # 它自己按 5/6 开头判断沪市，ETF 代码不会被查错
        df = await self._call(
            "fund_etf_hist_em" if symbol.is_etf else "stock_zh_a_hist",
            symbol=symbol.akshare,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adjust,
        )
        bars: list[Bar] = []
        for _, row in df.iterrows():
            d = _parse_date(_pick(row, "日期"))
            close = _to_float(_pick(row, "收盘"))
            if d is None or close is None:
                continue
            bars.append(
                Bar(
                    date=d,
                    open=_to_float(_pick(row, "开盘")) or close,
                    high=_to_float(_pick(row, "最高")) or close,
                    low=_to_float(_pick(row, "最低")) or close,
                    close=close,
                    volume=_to_float(_pick(row, "成交量")) or 0.0,
                    amount=_to_float(_pick(row, "成交额")) or 0.0,
                    change_pct=_to_float(_pick(row, "涨跌幅")),
                    turnover_rate=_to_float(_pick(row, "换手率")),
                )
            )
        return bars

    async def financials(self, symbol: Symbol, periods: int = 8) -> list[FinancialReport]:
        """用东财的财务摘要（按报告期一列一期）。"""
        if symbol.is_etf:
            raise ProviderUnavailable("ETF 没有财报，看 fund_profile / fund_holdings")
        df = await self._call("stock_financial_abstract", symbol=symbol.akshare)
        if df.empty:
            return []

        # 该接口返回宽表：首列是指标名，其余列是报告期
        index_col = df.columns[0]
        period_cols = [c for c in df.columns[1:] if _parse_date(c) is not None]
        period_cols.sort(key=lambda c: _parse_date(c) or date.min, reverse=True)

        metrics: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            key = str(row[index_col]).strip()
            metrics[key] = {c: row[c] for c in period_cols}

        def metric(names: tuple[str, ...], col: str) -> float | None:
            for n in names:
                for key, values in metrics.items():
                    if n in key:
                        v = _to_float(values.get(col))
                        if v is not None:
                            return v
            return None

        reports: list[FinancialReport] = []
        for col in period_cols[:periods]:
            d = _parse_date(col)
            if d is None:
                continue
            reports.append(
                FinancialReport(
                    symbol=symbol.canonical,
                    period=d.isoformat(),
                    revenue=metric(("营业总收入", "营业收入"), col),
                    revenue_yoy=metric(("营业总收入同比增长", "营业收入同比增长"), col),
                    net_profit=metric(("归母净利润", "净利润"), col),
                    net_profit_yoy=metric(("归母净利润同比增长", "净利润同比增长"), col),
                    gross_margin=metric(("毛利率",), col),
                    net_margin=metric(("销售净利率", "净利率"), col),
                    roe=metric(("净资产收益率(ROE)", "净资产收益率"), col),
                    debt_ratio=metric(("资产负债率",), col),
                    eps=metric(("基本每股收益", "每股收益"), col),
                    bps=metric(("每股净资产",), col),
                    operating_cashflow=metric(("经营活动产生的现金流量净额", "经营现金流"), col),
                )
            )
        return reports

    async def news(
        self, symbol: Symbol, limit: int = 20, keyword: str | None = None
    ) -> list[NewsItem]:
        # 这个接口本质是东财站内搜索，参数就是搜索词 ——
        # ETF 按代码几乎搜不到东西，上层会传"半导体"这类板块词进来
        df = await self._call("stock_news_em", symbol=keyword or symbol.akshare)
        items: list[NewsItem] = []
        for _, row in df.head(limit).iterrows():
            items.append(
                NewsItem(
                    title=str(_pick(row, "新闻标题", "标题") or "").strip(),
                    published_at=_parse_datetime(_pick(row, "发布时间", "时间")),
                    source=str(_pick(row, "文章来源", "来源") or ""),
                    url=str(_pick(row, "新闻链接", "链接") or ""),
                    summary=str(_pick(row, "新闻内容", "内容") or "")[:500],
                )
            )
        return [i for i in items if i.title]

    async def money_flow(self, symbol: Symbol, days: int = 10) -> list[MoneyFlow]:
        market = "sh" if symbol.exchange in ("SH", "BJ") else "sz"
        df = await self._call(
            "stock_individual_fund_flow", stock=symbol.akshare, market=market
        )
        flows: list[MoneyFlow] = []
        for _, row in df.tail(days).iterrows():
            d = _parse_date(_pick(row, "日期"))
            if d is None:
                continue
            flows.append(
                MoneyFlow(
                    date=d,
                    main_net_inflow=_to_float(_pick(row, "主力净流入-净额")),
                    main_net_inflow_pct=_to_float(_pick(row, "主力净流入-净占比")),
                    super_large_net=_to_float(_pick(row, "超大单净流入-净额")),
                    large_net=_to_float(_pick(row, "大单净流入-净额")),
                    medium_net=_to_float(_pick(row, "中单净流入-净额")),
                    small_net=_to_float(_pick(row, "小单净流入-净额")),
                )
            )
        return flows

    #: 首页要展示的大盘指数。**以代码为准**，名称只作兜底 ——
    #: 上游的指数名称写法会变（"沪深300" / "沪深 300"），代码不会。
    #: 之前只按名称精确匹配，一旦对不上就筛出空列表，registry 把空结果当失败，
    #: 于是整条大盘数据降级到假数据，而真实原因只是少了一个空格。
    _WANTED_INDEXES = {
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
        "000300": "沪深300",
        "000688": "科创50",
    }

    async def index_quotes(self) -> list[IndexQuote]:
        df = await self._call("stock_zh_index_spot_em", symbol="沪深重要指数")
        wanted_names = set(self._WANTED_INDEXES.values())
        out: list[IndexQuote] = []
        for _, row in df.iterrows():
            name = str(_pick(row, "名称") or "")
            code = str(_pick(row, "代码") or "").zfill(6)
            if code not in self._WANTED_INDEXES and name not in wanted_names:
                continue
            out.append(
                IndexQuote(
                    code=code,
                    # 上游没给名称时用我们自己的叫法，别显示一串代码
                    name=name or self._WANTED_INDEXES.get(code, code),
                    price=_to_float(_pick(row, "最新价")) or 0.0,
                    change_pct=_to_float(_pick(row, "涨跌幅")),
                )
            )
        if not out and not df.empty:
            # 接口有数据但一个都没匹配上：大概率是上游改了代码/名称。
            # 把实际拿到的前几个名字写进报错，排查时一眼就能看出问题。
            got = [
                str(_pick(r, "名称") or _pick(r, "代码") or "?")
                for _, r in df.head(6).iterrows()
            ]
            raise ProviderUnavailable(
                f"akshare 指数列表里没有需要的指数，实际返回：{'、'.join(got)}"
            )
        return out

    # ---------------- ETF ----------------

    async def etf_list(self) -> list[StockBasic]:
        """全部场内 ETF。

        ⚠️ 和股票全市场快照一样要翻页（1000 多只，每页 100 条，页间 sleep），
        单次 15-20 秒，只能在后台建目录时用。
        """
        df = await self._call("fund_etf_spot_em")
        out: list[StockBasic] = []
        for _, row in df.iterrows():
            sym = try_normalize(str(_pick(row, "代码") or ""))
            if sym is None or not sym.is_etf:
                continue
            out.append(
                StockBasic(
                    symbol=sym.canonical,
                    name=str(_pick(row, "名称") or ""),
                    exchange=sym.exchange,
                    board=sym.board,
                )
            )
        return out

    async def fund_profile(self, symbol: Symbol) -> FundProfile:
        """天天基金的基金档案（跟踪标的、规模、费率……）。一天变不了几次，强缓存。"""
        from app.core.config import get_settings  # noqa: PLC0415 —— 避免循环导入
        from app.data.cache import cache  # noqa: PLC0415

        async def load() -> FundProfile:
            df = await self._call("fund_overview_em", symbol=symbol.code)
            if df is None or df.empty:
                raise ProviderUnavailable(f"天天基金没有 {symbol.canonical} 的档案")
            info = {str(k).strip(): v for k, v in df.iloc[0].items()}
            return parse_fund_overview(symbol, info)

        return await cache.get_or_set(
            f"akshare:fund_profile:{symbol.code}", get_settings().history_cache_ttl, load
        )

    async def fund_holdings(self, symbol: Symbol) -> list[FundHolding]:
        """最近一期披露的前十大重仓股。

        接口按年份查。年初时今年还没有季报，所以今年查不到就退回去年。
        """
        df = None
        error: ProviderUnavailable | None = None
        this_year = now_cst().year
        for year in (this_year, this_year - 1):
            try:
                df = await self._call(
                    "fund_portfolio_hold_em", symbol=symbol.code, date=str(year)
                )
            except ProviderUnavailable as exc:
                # 今年的页面可能还不存在（解析失败而不是返回空表），照样退回去年
                error, df = exc, None
                continue
            if df is not None and not df.empty:
                break
        if df is None or df.empty:
            if error is not None:
                raise error
            return []
        return pick_latest_holdings(
            [
                (
                    str(_pick(r, "股票代码") or ""),
                    str(_pick(r, "股票名称") or ""),
                    _to_float(_pick(r, "占净值比例")),
                    str(_pick(r, "季度") or ""),
                )
                for _, r in df.iterrows()
            ]
        )

    # ---------------- 全市场快照（多个接口共用） ----------------

    async def _spot(self) -> Any:
        """A股实时行情全表。

        ⚠️ **极慢，只能在后台任务里用。** akshare 内部按每页 100 条翻页，
        5400 多只股票要翻 54 页，且每页之间 sleep 0.5-1.5 秒 ——
        单次调用 60-90 秒。放在请求路径上必然超时。
        目前只有"按名称搜索"需要它，而那条路已经改成后台建本地代码目录
        （见 services/symbol_index.py），请求侧查的是本地表。
        """
        from app.core.config import get_settings  # noqa: PLC0415 —— 避免循环导入
        from app.data.cache import cache

        ttl = get_settings().quote_cache_ttl
        return await cache.get_or_set(
            "akshare:spot_em", ttl, lambda: self._call("stock_zh_a_spot_em")
        )


# ---------------------------------------------------------------- ETF 解析


def parse_fund_overview(symbol: Symbol, info: dict[str, Any]) -> FundProfile:
    """把天天基金档案页的键值表转成 `FundProfile`。

    这张表的值是给人看的字符串，例如：
        资产规模      123.45亿元（截止至：2025年06月30日）
        成立日期/规模  2019年05月08日 / 12.345亿份
        管理费率      0.50%（每年）
        跟踪标的      中证全指半导体产品与设备指数
    解析不出来的字段留空，不猜。
    """

    def text(key: str) -> str:
        value = info.get(key)
        if value is None:
            return ""
        s = str(value).strip()
        return "" if s in ("", "nan", "None", "--", "---") else s

    size_text = text("资产规模")
    tracking = text("跟踪标的")
    if "无跟踪标的" in tracking:
        tracking = ""
    inception = text("成立日期/规模").split("/")[0].strip() or text("成立日期")

    return FundProfile(
        symbol=symbol.canonical,
        name=text("基金简称"),
        full_name=text("基金全称"),
        fund_type=text("基金类型"),
        tracking_index=tracking,
        benchmark=text("业绩比较基准"),
        manager=text("基金管理人"),
        inception_date=_parse_date(inception),
        asset_size=_amount_with_unit(size_text),
        asset_size_date=_parse_date(_date_in_parens(size_text)),
        share_size=_amount_with_unit(text("份额规模")),
        management_fee=_leading_number(text("管理费率")),
        custody_fee=_leading_number(text("托管费率")),
    )


_QUARTER_RE = re.compile(r"(\d{4})\s*年\s*(\d)\s*季度")


def pick_latest_holdings(
    rows: list[tuple[str, str, float | None, str]], top: int = 10
) -> list[FundHolding]:
    """从多期持仓里挑出最近一期，按占净值比例降序取前 `top` 只。

    接口一次返回一整年的几个季度，而且不保证顺序 —— 按季度标签解析出
    (年, 季) 比较，而不是信任行序。
    """

    def key(label: str) -> tuple[int, int]:
        m = _QUARTER_RE.search(label)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    if not rows:
        return []
    latest = max((r[3] for r in rows), key=key)
    m = _QUARTER_RE.search(latest)
    period = f"{m.group(1)}年{m.group(2)}季度" if m else latest

    picked = [
        FundHolding(code=code.strip(), name=name.strip(), weight_pct=weight, period=period)
        for code, name, weight, label in rows
        if label == latest and code.strip()
    ]
    picked.sort(key=lambda h: h.weight_pct if h.weight_pct is not None else -1, reverse=True)
    return picked[:top]


def _amount_with_unit(text: str) -> float | None:
    """"123.45亿元" / "5,678.9万份" -> 数值（元 / 份）。"""
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(亿|万)?", text or "")
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return value * {"亿": 1e8, "万": 1e4}.get(m.group(2) or "", 1.0)


def _leading_number(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text or "")
    return float(m.group(0)) if m else None


def _date_in_parens(text: str) -> str:
    m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2})", text or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 字段容错工具


def _pick(row: Any, *names: str) -> Any:
    """按候选列名依次取值，取到非空就返回。

    AKShare 上游站点改字段名是常态，多给几个候选比直接 KeyError 好。
    """
    for name in names:
        try:
            if name in row:
                value = row[name]
                if value is not None and str(value).strip() not in ("", "nan", "None", "-"):
                    return value
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _scale_to_price(ratio: float | None, base_price: float | None, price: float) -> float | None:
    """把按收盘价算的估值倍数折算到当前价。

    估值表是日频的，PE 用的是那天的收盘价。盘中价格动了，PE 也该跟着动 ——
    盈利和股本不变，倍数就与价格成正比。
    拿不到基准价就原样返回（宁可略旧，也不要没有）。
    """
    if ratio is None:
        return None
    if not base_price or base_price <= 0 or price <= 0:
        return ratio
    return round(ratio * price / base_price, 2)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "nan", "None", "--"):
        return None
    # 处理"1.23亿"/"4.56万"这类中文单位
    multiplier = 1.0
    for suffix, factor in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4)):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = factor
            break
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    dt = _parse_datetime(value)
    return dt.date() if dt else None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text or text in ("nan", "None"):
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d",
        "%Y/%m/%d",
        "%Y年%m月%d日",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
