"""AKShare 数据源适配器。

AKShare 免费、免注册、覆盖面广，是本项目的默认数据源。
它底层是爬各家财经站点，所以：
  1. 全部接口都是同步阻塞的 —— 这里统一用 `asyncio.to_thread` 包起来；
  2. 接口偶发失败或字段改名很常见 —— 取字段一律走 `_pick()` 容错，
     拿不到就抛 `ProviderUnavailable` 让 registry 降级到 Tushare。

安装：pip install -e ".[akshare]"
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from app.core.errors import ProviderUnavailable
from app.data.base import (
    CAP_BARS,
    CAP_BASIC,
    CAP_FINANCIALS,
    CAP_INDEXES,
    CAP_MONEY_FLOW,
    CAP_NEWS,
    CAP_QUOTE,
    CAP_SEARCH,
    Bar,
    FinancialReport,
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
        return self._ak

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
        df = await self._spot()
        match = df[df["代码"].astype(str).str.zfill(6) == symbol.code]
        if match.empty:
            raise ProviderUnavailable(
                f"akshare 实时行情里没有 {symbol.canonical}（可能停牌或已退市）"
            )
        row = match.iloc[0]
        return Quote(
            symbol=symbol.canonical,
            name=str(_pick(row, "名称") or ""),
            price=_to_float(_pick(row, "最新价")) or 0.0,
            prev_close=_to_float(_pick(row, "昨收")),
            open=_to_float(_pick(row, "今开")),
            high=_to_float(_pick(row, "最高")),
            low=_to_float(_pick(row, "最低")),
            change_pct=_to_float(_pick(row, "涨跌幅")),
            volume=_to_float(_pick(row, "成交量")),
            amount=_to_float(_pick(row, "成交额")),
            turnover_rate=_to_float(_pick(row, "换手率")),
            pe_ttm=_to_float(_pick(row, "市盈率-动态", "市盈率")),
            pb=_to_float(_pick(row, "市净率")),
            total_market_cap=_to_float(_pick(row, "总市值")),
            float_market_cap=_to_float(_pick(row, "流通市值")),
            updated_at=datetime.now(),
        )

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        df = await self._call(
            "stock_zh_a_hist",
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

    async def news(self, symbol: Symbol, limit: int = 20) -> list[NewsItem]:
        df = await self._call("stock_news_em", symbol=symbol.akshare)
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

    async def index_quotes(self) -> list[IndexQuote]:
        df = await self._call("stock_zh_index_spot_em", symbol="沪深重要指数")
        wanted = {"上证指数", "深证成指", "创业板指", "沪深300", "科创50"}
        out: list[IndexQuote] = []
        for _, row in df.iterrows():
            name = str(_pick(row, "名称") or "")
            if name not in wanted:
                continue
            out.append(
                IndexQuote(
                    code=str(_pick(row, "代码") or ""),
                    name=name,
                    price=_to_float(_pick(row, "最新价")) or 0.0,
                    change_pct=_to_float(_pick(row, "涨跌幅")),
                )
            )
        return out

    # ---------------- 全市场快照（多个接口共用） ----------------

    async def _spot(self) -> Any:
        """A股实时行情全表。一次请求 5000+ 行，务必走缓存。"""
        from app.core.config import get_settings  # noqa: PLC0415 —— 避免循环导入
        from app.data.cache import cache

        ttl = get_settings().quote_cache_ttl
        return await cache.get_or_set(
            "akshare:spot_em", ttl, lambda: self._call("stock_zh_a_spot_em")
        )


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
