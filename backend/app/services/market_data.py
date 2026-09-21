"""把多个数据源的零散数据组装成一次分析所需的 `MarketSnapshot`。

设计要点：
- 八个维度并发抓取，互不阻塞；
- 单个维度失败不影响整体 —— 记进 `warnings`，分析师会看到"该维度缺失"，
  而不是整个分析直接失败（免费数据源某个接口挂掉是常态）；
- 行情走短 TTL 缓存，历史/财报走长 TTL。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from app.core.config import get_settings
from app.core.errors import DataNotFound
from app.data.base import (
    Bar,
    FinancialReport,
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

        tasks = {
            "basic": self._basic(symbol),
            "quote": self._quote(symbol),
            "bars": self._bars(symbol, start, end),
            "financials": self._financials(symbol, financial_periods),
            "news": self._news(symbol, news_limit),
            "money_flow": self._money_flow(symbol, flow_days),
            "indexes": self._indexes(),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for key, result in zip(tasks.keys(), results, strict=True):
            if isinstance(result, BaseException):
                msg = f"{_LABELS[key]}获取失败：{result}"
                snap.warnings.append(msg)
                log.warning("%s %s", symbol.canonical, msg)
                continue
            setattr(snap, key, result)

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
            lambda: self._registry.quote(symbol),
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

    async def _news(self, symbol: Symbol, limit: int) -> list[NewsItem]:
        return await cache.get_or_set(
            f"news:{symbol.canonical}:{limit}",
            # 新闻用较短 TTL，盘中消息面变化快
            min(self._settings.history_cache_ttl, 900),
            lambda: self._registry.news(symbol, limit),
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


_LABELS = {
    "basic": "基础信息",
    "quote": "实时行情",
    "bars": "历史K线",
    "financials": "财务数据",
    "news": "新闻舆情",
    "money_flow": "资金流向",
    "indexes": "大盘指数",
}
