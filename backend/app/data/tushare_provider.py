"""Tushare Pro 数据源适配器。

相比 AKShare：字段规范、稳定性好、适合量化；代价是要注册拿 token，
部分接口按积分开放。没配 `TUSHARE_TOKEN` 时 healthcheck 直接返回 False，
registry 会自动跳过。

Tushare 没有个股新闻接口（新闻类需要较高积分且覆盖有限），
所以 `capabilities` 里不声明 CAP_NEWS —— registry 会把新闻这一项降级到 AKShare。

安装：pip install -e ".[tushare]"
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
    CAP_QUOTE,
    CAP_SEARCH,
    Bar,
    FinancialReport,
    IndexQuote,
    Quote,
    StockBasic,
)
from app.data.symbols import Symbol, try_normalize

log = logging.getLogger(__name__)

_INDEX_CODES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
}


class TushareProvider:
    name = "tushare"
    # 不含 CAP_NEWS / CAP_MONEY_FLOW：这两类交给 AKShare
    capabilities = frozenset(
        {CAP_SEARCH, CAP_BASIC, CAP_QUOTE, CAP_BARS, CAP_FINANCIALS, CAP_INDEXES}
    )

    def __init__(self, token: str) -> None:
        self._token = token.strip()
        self._api: Any | None = None

    # ---------------- 依赖与调用 ----------------

    def _client(self) -> Any:
        if self._api is None:
            if not self._token:
                raise ProviderUnavailable("未配置 TUSHARE_TOKEN")
            try:
                import tushare as ts  # noqa: PLC0415 —— 延迟导入
            except ImportError as exc:  # pragma: no cover
                raise ProviderUnavailable(
                    'tushare 未安装，执行 pip install -e ".[tushare]" 或把它从 '
                    "DATA_PROVIDER_ORDER 中移除"
                ) from exc
            ts.set_token(self._token)
            self._api = ts.pro_api()
        return self._api

    async def _call(self, api_name: str, /, **kwargs: Any) -> Any:
        api = self._client()
        try:
            return await asyncio.to_thread(lambda: getattr(api, api_name)(**kwargs))
        except Exception as exc:
            raise ProviderUnavailable(f"tushare.{api_name}() 调用失败：{exc}") from exc

    async def healthcheck(self) -> bool:
        if not self._token:
            return False
        try:
            self._client()
        except ProviderUnavailable:
            return False
        return True

    # ---------------- 接口实现 ----------------

    async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]:
        df = await self._stock_list()
        keyword = keyword.strip()
        out: list[StockBasic] = []
        for _, row in df.iterrows():
            code = str(row.get("ts_code", ""))
            name = str(row.get("name", ""))
            if keyword and keyword not in code and keyword not in name:
                continue
            sym = try_normalize(code)
            if sym is None:
                continue
            out.append(
                StockBasic(
                    symbol=sym.canonical,
                    name=name,
                    exchange=sym.exchange,
                    board=sym.board,
                    industry=str(row.get("industry") or ""),
                    list_date=_parse_date(row.get("list_date")),
                )
            )
            if len(out) >= limit:
                break
        return out

    async def stock_basic(self, symbol: Symbol) -> StockBasic:
        df = await self._call(
            "stock_basic",
            ts_code=symbol.tushare,
            fields="ts_code,name,industry,list_date,total_share,float_share",
        )
        if df.empty:
            raise ProviderUnavailable(f"tushare 没有 {symbol.canonical} 的基础信息")
        row = df.iloc[0]
        return StockBasic(
            symbol=symbol.canonical,
            name=str(row.get("name") or ""),
            exchange=symbol.exchange,
            board=symbol.board,
            industry=str(row.get("industry") or ""),
            list_date=_parse_date(row.get("list_date")),
            # tushare 单位是万股
            total_share=_scale(row.get("total_share"), 1e4),
            float_share=_scale(row.get("float_share"), 1e4),
        )

    async def quote(self, symbol: Symbol) -> Quote:
        """用最近一个交易日的 daily + daily_basic 拼快照。

        注意：Tushare 的 daily 是日线收盘数据，不是盘中 tick。
        盘中要真实时价格，用 AKShare（放在 DATA_PROVIDER_ORDER 前面）。
        """
        daily = await self._call("daily", ts_code=symbol.tushare, limit=2)
        if daily.empty:
            raise ProviderUnavailable(f"tushare 没有 {symbol.canonical} 的日线数据")
        row = daily.iloc[0]
        basic_df = await self._call("daily_basic", ts_code=symbol.tushare, limit=1)
        b = basic_df.iloc[0] if not basic_df.empty else {}
        name_info = await self.stock_basic(symbol)

        return Quote(
            symbol=symbol.canonical,
            name=name_info.name,
            price=_to_float(row.get("close")) or 0.0,
            prev_close=_to_float(row.get("pre_close")),
            open=_to_float(row.get("open")),
            high=_to_float(row.get("high")),
            low=_to_float(row.get("low")),
            change_pct=_to_float(row.get("pct_chg")),
            volume=_scale(row.get("vol"), 1.0),  # 手
            amount=_scale(row.get("amount"), 1e3),  # 千元 -> 元
            turnover_rate=_to_float(_get(b, "turnover_rate")),
            pe_ttm=_to_float(_get(b, "pe_ttm")),
            pb=_to_float(_get(b, "pb")),
            total_market_cap=_scale(_get(b, "total_mv"), 1e4),  # 万元 -> 元
            float_market_cap=_scale(_get(b, "circ_mv"), 1e4),
            updated_at=datetime.now(),
        )

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        adj = {"qfq": "qfq", "hfq": "hfq", "": None, "none": None}.get(adjust, "qfq")
        df = await self._call(
            "pro_bar" if adj else "daily",
            ts_code=symbol.tushare,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            **({"adj": adj, "freq": "D", "asset": "E"} if adj else {}),
        )
        bars: list[Bar] = []
        for _, row in df.iterrows():
            d = _parse_date(row.get("trade_date"))
            close = _to_float(row.get("close"))
            if d is None or close is None:
                continue
            bars.append(
                Bar(
                    date=d,
                    open=_to_float(row.get("open")) or close,
                    high=_to_float(row.get("high")) or close,
                    low=_to_float(row.get("low")) or close,
                    close=close,
                    volume=_to_float(row.get("vol")) or 0.0,
                    amount=_scale(row.get("amount"), 1e3) or 0.0,
                    change_pct=_to_float(row.get("pct_chg")),
                )
            )
        bars.sort(key=lambda b: b.date)  # tushare 返回倒序
        return bars

    async def financials(self, symbol: Symbol, periods: int = 8) -> list[FinancialReport]:
        indicators = await self._call(
            "fina_indicator",
            ts_code=symbol.tushare,
            limit=periods,
            fields=(
                "ts_code,end_date,roe,grossprofit_margin,netprofit_margin,debt_to_assets,"
                "eps,bps,or_yoy,netprofit_yoy"
            ),
        )
        income = await self._call(
            "income",
            ts_code=symbol.tushare,
            limit=periods,
            fields="ts_code,end_date,total_revenue,n_income_attr_p",
        )
        cashflow = await self._call(
            "cashflow",
            ts_code=symbol.tushare,
            limit=periods,
            fields="ts_code,end_date,n_cashflow_act",
        )

        by_period: dict[str, dict[str, Any]] = {}
        for df in (indicators, income, cashflow):
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                key = str(row.get("end_date") or "")
                if key:
                    by_period.setdefault(key, {}).update(row.to_dict())

        reports: list[FinancialReport] = []
        for key in sorted(by_period, reverse=True)[:periods]:
            row = by_period[key]
            d = _parse_date(key)
            if d is None:
                continue
            reports.append(
                FinancialReport(
                    symbol=symbol.canonical,
                    period=d.isoformat(),
                    revenue=_to_float(row.get("total_revenue")),
                    revenue_yoy=_to_float(row.get("or_yoy")),
                    net_profit=_to_float(row.get("n_income_attr_p")),
                    net_profit_yoy=_to_float(row.get("netprofit_yoy")),
                    gross_margin=_to_float(row.get("grossprofit_margin")),
                    net_margin=_to_float(row.get("netprofit_margin")),
                    roe=_to_float(row.get("roe")),
                    debt_ratio=_to_float(row.get("debt_to_assets")),
                    eps=_to_float(row.get("eps")),
                    bps=_to_float(row.get("bps")),
                    operating_cashflow=_to_float(row.get("n_cashflow_act")),
                )
            )
        return reports

    async def index_quotes(self) -> list[IndexQuote]:
        out: list[IndexQuote] = []
        for code, name in _INDEX_CODES.items():
            try:
                df = await self._call("index_daily", ts_code=code, limit=1)
            except ProviderUnavailable as exc:
                log.warning("tushare 指数 %s 获取失败：%s", code, exc)
                continue
            if df.empty:
                continue
            row = df.iloc[0]
            out.append(
                IndexQuote(
                    code=code,
                    name=name,
                    price=_to_float(row.get("close")) or 0.0,
                    change_pct=_to_float(row.get("pct_chg")),
                )
            )
        return out

    # ---------------- 共用 ----------------

    async def _stock_list(self) -> Any:
        from app.core.config import get_settings  # noqa: PLC0415
        from app.data.cache import cache

        ttl = get_settings().history_cache_ttl
        return await cache.get_or_set(
            "tushare:stock_basic_all",
            ttl,
            lambda: self._call(
                "stock_basic", exchange="", list_status="L", fields="ts_code,name,industry,list_date"
            ),
        )


# ---------------------------------------------------------------- 工具


def _get(row: Any, key: str) -> Any:
    try:
        return row.get(key)  # type: ignore[union-attr]
    except AttributeError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-", "nan", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _scale(value: Any, factor: float) -> float | None:
    v = _to_float(value)
    return v * factor if v is not None else None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
