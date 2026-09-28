"""数据源注册与降级路由。

按 `DATA_PROVIDER_ORDER` 的顺序尝试每个数据源，逐接口降级：
比如 Tushare 排第一但不支持个股新闻，新闻这一项就自动落到 AKShare。
每次调用都记录实际命中的数据源，写进 `MarketSnapshot.sources`，
前端能直接看到"这块数据来自哪儿"。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, TypeVar

from app.core.config import Settings, get_settings
from app.core.errors import CapabilityNotSupported, DataNotFound, ProviderUnavailable
from app.data.akshare_provider import AkshareProvider
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
    DataProvider,
    FinancialReport,
    IndexQuote,
    MoneyFlow,
    NewsItem,
    Quote,
    StockBasic,
)
from app.data.mock_provider import MockProvider
from app.data.symbols import Symbol
from app.data.tushare_provider import TushareProvider

log = logging.getLogger(__name__)

T = TypeVar("T")


def build_provider(name: str, settings: Settings) -> DataProvider | None:
    match name.lower():
        case "akshare":
            return AkshareProvider()
        case "tushare":
            return TushareProvider(settings.tushare_token)
        case "mock":
            return MockProvider()
        case _:
            log.warning("未知数据源 %r，已忽略", name)
            return None


class ProviderRegistry:
    """多数据源路由器。"""

    def __init__(self, providers: list[DataProvider], timeout: float | None = None) -> None:
        self._providers = providers
        self._healthy: dict[str, bool] = {}
        # 记录最近一次每个能力实际由谁提供
        self.last_sources: dict[str, str] = {}
        self._timeout = timeout if timeout is not None else get_settings().provider_timeout

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ProviderRegistry:
        settings = settings or get_settings()
        providers: list[DataProvider] = []
        for name in settings.data_provider_order:
            provider = build_provider(name, settings)
            if provider is not None:
                providers.append(provider)
        if not providers:
            log.warning("DATA_PROVIDER_ORDER 为空，回落到 mock 数据源")
            providers.append(MockProvider())
        return cls(providers, timeout=settings.provider_timeout)

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    async def health(self) -> dict[str, bool]:
        """逐个探活，结果缓存在内存里（重启后重新探测）。"""
        for provider in self._providers:
            if provider.name not in self._healthy:
                try:
                    self._healthy[provider.name] = await provider.healthcheck()
                except Exception as exc:  # pragma: no cover - 防御性
                    log.warning("%s 探活异常：%s", provider.name, exc)
                    self._healthy[provider.name] = False
        return dict(self._healthy)

    async def _dispatch(
        self,
        capability: str,
        call: Callable[[DataProvider], Awaitable[T]],
        *,
        allow_empty: bool = False,
    ) -> T:
        """按顺序尝试支持该能力的数据源，第一个成功的胜出。"""
        await self.health()
        attempts: list[str] = []

        for provider in self._providers:
            if capability not in provider.capabilities:
                continue
            if not self._healthy.get(provider.name, False):
                continue
            try:
                # 时间盒住每次尝试：卡住的数据源会拖死整个请求，
                # 超时就当它失败，继续降级。
                async with asyncio.timeout(self._timeout):
                    result = await call(provider)
            except TimeoutError:
                msg = f"超过 {self._timeout} 秒无响应"
                attempts.append(f"{provider.name}: {msg}")
                log.warning("%s 的 %s %s，降级尝试下一个", provider.name, capability, msg)
                continue
            except (ProviderUnavailable, CapabilityNotSupported) as exc:
                attempts.append(f"{provider.name}: {exc}")
                log.info("%s 的 %s 不可用，降级尝试下一个：%s", provider.name, capability, exc)
                continue
            except Exception as exc:
                attempts.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                log.warning("%s 的 %s 抛出未预期异常：%s", provider.name, capability, exc)
                continue

            # 空结果也算失败，继续往下找（新闻/资金流经常某个源就是空的）
            if not allow_empty and _is_empty(result):
                attempts.append(f"{provider.name}: 返回空结果")
                continue

            self.last_sources[capability] = provider.name
            return result

        detail = "；".join(attempts) if attempts else "没有数据源声明支持该能力"
        raise DataNotFound(f"所有数据源都拿不到 {capability} —— {detail}")

    # ---------------- 对上层的统一接口 ----------------

    async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]:
        return await self._dispatch(
            CAP_SEARCH, lambda p: p.search(keyword, limit), allow_empty=True
        )

    async def stock_basic(self, symbol: Symbol) -> StockBasic:
        return await self._dispatch(CAP_BASIC, lambda p: p.stock_basic(symbol))

    async def quote(self, symbol: Symbol) -> Quote:
        return await self._dispatch(CAP_QUOTE, lambda p: p.quote(symbol))

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        return await self._dispatch(CAP_BARS, lambda p: p.daily_bars(symbol, start, end, adjust))

    async def financials(self, symbol: Symbol, periods: int = 8) -> list[FinancialReport]:
        return await self._dispatch(CAP_FINANCIALS, lambda p: p.financials(symbol, periods))

    async def news(self, symbol: Symbol, limit: int = 20) -> list[NewsItem]:
        return await self._dispatch(CAP_NEWS, lambda p: p.news(symbol, limit))

    async def money_flow(self, symbol: Symbol, days: int = 10) -> list[MoneyFlow]:
        return await self._dispatch(CAP_MONEY_FLOW, lambda p: p.money_flow(symbol, days))

    async def index_quotes(self) -> list[IndexQuote]:
        return await self._dispatch(CAP_INDEXES, lambda p: p.index_quotes())


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) == 0
    return False


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry.from_settings()
    return _registry


def reset_registry() -> None:
    """测试用：清掉单例。"""
    global _registry
    _registry = None
