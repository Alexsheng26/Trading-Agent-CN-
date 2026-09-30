"""把多个数据源的零散数据组装成一次分析所需的 `MarketSnapshot`。

设计要点：
- 八个维度并发抓取，互不阻塞；
- 单个维度失败不影响整体 —— 记进 `warnings`，分析师会看到"该维度缺失"，
  而不是整个分析直接失败（免费数据源某个接口挂掉是常态）；
- 行情走短 TTL 缓存，历史/财报走长 TTL。

ETF 的维度不一样：没有财报，换成基金档案（跟踪标的、规模、费率）和
重仓股；新闻按板块关键词搜（按代码几乎搜不到东西）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from datetime import date, timedelta

from app.core.config import get_settings
from app.core.errors import DataNotFound
from app.data.base import (
    Bar,
    FinancialReport,
    FundHolding,
    FundProfile,
    IndexQuote,
    MarketSnapshot,
    MoneyFlow,
    NewsItem,
    Quote,
    StockBasic,
)
from app.data.cache import cache
from app.data.registry import ProviderRegistry, get_registry
from app.data.symbols import Symbol
from app.services.quote_source import quote_with_fallback

log = logging.getLogger(__name__)

# 抓 500 个自然日 ≈ 340 个交易日，足够算 120 日均线和 60 日区间
_HISTORY_DAYS = 500


class MarketDataService:
    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self._registry = registry or get_registry()
        self._settings = get_settings()

    async def snapshot(
        self,
        symbol: Symbol,
        *,
        history_days: int = _HISTORY_DAYS,
        news_limit: int = 15,
        financial_periods: int = 8,
        flow_days: int = 10,
    ) -> MarketSnapshot:
        end = date.today()
        start = end - timedelta(days=history_days)
        snap = MarketSnapshot(symbol=symbol)

        # 行情包成 Task：ETF 那边可能要提前拿它取名称，
        # 共用同一个 Task 才不会把整条降级链跑两遍（失败时尤其慢）
        quote_task = asyncio.ensure_future(self._quote(symbol))
        tasks = {
            "basic": self._basic(symbol),
            "quote": quote_task,
            "bars": self._bars(symbol, start, end),
            "money_flow": self._money_flow(symbol, flow_days),
            "indexes": self._indexes(),
        }
        short_name = ""
        if symbol.is_etf:
            # 交易所简称（"半导体ETF"）。基金档案里的简称是"国联安中证全指半导体ETF"
            # 这种长名字，用户认的是短的，拿来当新闻关键词也只有短的能用
            short_name = await _directory_name(symbol)
            if not short_name:
                # 目录还没收录它：先等行情 —— 新浪 / 雪球的行情里带的就是简称。
                # 行情失败的话这里吞掉，下面 gather 会把同一个异常记进 warnings
                with suppress(Exception):
                    short_name = (await quote_task).name
            snap.news_keyword = etf_news_keyword(short_name)
            tasks["fund_profile"] = self._fund_profile(symbol)
            tasks["holdings"] = self._holdings(symbol)
            tasks["news"] = self._news(symbol, news_limit, snap.news_keyword or None)
        else:
            tasks["financials"] = self._financials(symbol, financial_periods)
            tasks["news"] = self._news(symbol, news_limit)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for key, result in zip(tasks.keys(), results, strict=True):
            if isinstance(result, BaseException):
                msg = f"{_LABELS[key]}获取失败：{result}"
                snap.warnings.append(msg)
                log.warning("%s %s", symbol.canonical, msg)
                continue
            setattr(snap, key, result)

        if symbol.is_etf:
            _apply_etf_names(snap, short_name)

        snap.sources = dict(self._registry.last_sources)

        # 行情和 K 线全都没有，这次分析没有意义，直接失败
        if snap.quote is None and not snap.bars:
            raise DataNotFound(
                f"{symbol.canonical} 既拿不到行情也拿不到 K 线，无法分析。"
                f"已尝试的数据源：{', '.join(self._registry.provider_names)}"
            )
        return snap

    # ---------------- 各维度（带缓存） ----------------

    async def _basic(self, symbol: Symbol) -> StockBasic:
        return await cache.get_or_set(
            f"basic:{symbol.canonical}",
            self._settings.history_cache_ttl,
            lambda: self._registry.stock_basic(symbol),
        )

    async def _quote(self, symbol: Symbol) -> Quote:
        return await cache.get_or_set(
            f"quote:{symbol.canonical}",
            self._settings.quote_cache_ttl,
            lambda: quote_with_fallback(self._registry, symbol),
        )

    async def _bars(self, symbol: Symbol, start: date, end: date) -> list[Bar]:
        return await cache.get_or_set(
            f"bars:{symbol.canonical}:{start}:{end}",
            self._settings.history_cache_ttl,
            lambda: self._registry.daily_bars(symbol, start, end),
        )

    async def _financials(self, symbol: Symbol, periods: int) -> list[FinancialReport]:
        return await cache.get_or_set(
            f"fin:{symbol.canonical}:{periods}",
            self._settings.history_cache_ttl,
            lambda: self._registry.financials(symbol, periods),
        )

    async def _news(self, symbol: Symbol, limit: int, keyword: str | None = None) -> list[NewsItem]:
        return await cache.get_or_set(
            f"news:{symbol.canonical}:{limit}:{keyword or ''}",
            # 新闻用较短 TTL，盘中消息面变化快
            min(self._settings.history_cache_ttl, 900),
            lambda: self._registry.news(symbol, limit, keyword),
        )

    async def fund_info(
        self, symbol: Symbol
    ) -> tuple[FundProfile | None, list[FundHolding], list[str]]:
        """ETF 档案 + 重仓股，各自失败互不影响。返回 (档案, 重仓股, 失败说明)。"""
        profile, holdings = await asyncio.gather(
            self._fund_profile(symbol), self._holdings(symbol), return_exceptions=True
        )
        warnings: list[str] = []
        if isinstance(profile, BaseException):
            warnings.append(f"{_LABELS['fund_profile']}获取失败：{profile}")
            profile = None
        if isinstance(holdings, BaseException):
            warnings.append(f"{_LABELS['holdings']}获取失败：{holdings}")
            holdings = []
        return profile, holdings, warnings

    async def _fund_profile(self, symbol: Symbol) -> FundProfile:
        return await cache.get_or_set(
            f"fund_profile:{symbol.canonical}",
            self._settings.history_cache_ttl,
            lambda: self._registry.fund_profile(symbol),
        )

    async def _holdings(self, symbol: Symbol) -> list[FundHolding]:
        return await cache.get_or_set(
            f"holdings:{symbol.canonical}",
            self._settings.history_cache_ttl,
            lambda: self._registry.fund_holdings(symbol),
        )

    async def _money_flow(self, symbol: Symbol, days: int) -> list[MoneyFlow]:
        return await cache.get_or_set(
            f"flow:{symbol.canonical}:{days}",
            self._settings.quote_cache_ttl,
            lambda: self._registry.money_flow(symbol, days),
        )

    async def _indexes(self) -> list[IndexQuote]:
        return await cache.get_or_set(
            "indexes",
            self._settings.quote_cache_ttl,
            self._registry.index_quotes,
        )


async def _directory_name(symbol: Symbol) -> str:
    """本地代码目录里的名称（交易所简称）。目录没建好就是空串。"""
    from app.services import symbol_index  # noqa: PLC0415 —— 避免循环导入

    try:
        return await symbol_index.name_of(symbol.canonical)
    except Exception as exc:  # 目录只是锦上添花，查不到不能影响分析
        log.info("查 %s 的目录名称失败：%s", symbol.canonical, exc)
        return ""


def _apply_etf_names(snap: MarketSnapshot, short_name: str) -> None:
    """ETF 统一用交易所简称展示；目录里没有时退回行情接口给的名称。"""
    name = short_name or (snap.quote.name if snap.quote else "")
    if not name:
        return
    if snap.basic is None:
        snap.basic = StockBasic(
            symbol=snap.symbol.canonical,
            name=name,
            exchange=snap.symbol.exchange,
            board=snap.symbol.board,
        )
    else:
        snap.basic.name = name
    if snap.quote is not None and not snap.quote.name:
        snap.quote.name = name


_ETF_SUFFIX_RE = re.compile(r"ETF.*$", re.IGNORECASE)


def etf_news_keyword(name: str) -> str:
    """从 ETF 简称里取板块词："半导体ETF" -> "半导体"，"芯片ETF华夏" -> "芯片"。

    取出来太短（少于 2 个字）就不用，退回按代码搜。
    """
    keyword = _ETF_SUFFIX_RE.sub("", name or "").strip()
    return keyword if len(keyword) >= 2 else ""


_LABELS = {
    "basic": "基础信息",
    "quote": "实时行情",
    "bars": "历史K线",
    "financials": "财务数据",
    "news": "新闻舆情",
    "money_flow": "资金流向",
    "indexes": "大盘指数",
    "fund_profile": "ETF 概况",
    "holdings": "ETF 重仓股",
}
