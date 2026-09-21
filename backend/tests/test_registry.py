"""数据源降级路由。这是"两个数据源可切换"的核心保障。"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import DataNotFound, ProviderUnavailable
from app.data.base import ALL_CAPABILITIES, CAP_NEWS, CAP_QUOTE, NewsItem, Quote
from app.data.mock_provider import MockProvider
from app.data.registry import ProviderRegistry
from app.data.symbols import normalize

SYMBOL = normalize("600519")


class _Broken:
    """声明支持一切，但每个调用都失败。"""

    name = "broken"
    capabilities = frozenset(ALL_CAPABILITIES)

    async def healthcheck(self) -> bool:
        return True

    async def quote(self, symbol):  # type: ignore[no-untyped-def]
        raise ProviderUnavailable("接口挂了")

    async def news(self, symbol, limit=20):  # type: ignore[no-untyped-def]
        raise ProviderUnavailable("接口挂了")

    def __getattr__(self, _name):  # type: ignore[no-untyped-def]
        async def _fail(*args, **kwargs):
            raise ProviderUnavailable("接口挂了")

        return _fail


class _QuoteOnly:
    """只支持行情，不支持新闻 —— 模拟 Tushare 没有个股新闻接口。"""

    name = "quote_only"
    capabilities = frozenset({CAP_QUOTE})

    async def healthcheck(self) -> bool:
        return True

    async def quote(self, symbol) -> Quote:  # type: ignore[no-untyped-def]
        return Quote(symbol=symbol.canonical, name="只有行情", price=100.0)


class _EmptyNews:
    """新闻接口返回空列表 —— 空结果也该降级。"""

    name = "empty_news"
    capabilities = frozenset({CAP_NEWS})

    async def healthcheck(self) -> bool:
        return True

    async def news(self, symbol, limit=20) -> list[NewsItem]:  # type: ignore[no-untyped-def]
        return []


class _Unhealthy:
    """探活失败 —— 模拟没配 TUSHARE_TOKEN。"""

    name = "unhealthy"
    capabilities = frozenset(ALL_CAPABILITIES)
    called = False

    async def healthcheck(self) -> bool:
        return False

    async def quote(self, symbol):  # type: ignore[no-untyped-def]
        _Unhealthy.called = True
        raise AssertionError("探活失败的数据源不该被调用")


async def test_first_healthy_provider_wins() -> None:
    registry = ProviderRegistry([_QuoteOnly(), MockProvider()])
    quote = await registry.quote(SYMBOL)
    assert quote.name == "只有行情"
    assert registry.last_sources[CAP_QUOTE] == "quote_only"


async def test_falls_back_when_provider_raises() -> None:
    registry = ProviderRegistry([_Broken(), MockProvider()])
    quote = await registry.quote(SYMBOL)
    assert quote.name == "贵州茅台"
    assert registry.last_sources[CAP_QUOTE] == "mock"


async def test_skips_provider_lacking_capability() -> None:
    """排在前面的数据源不支持新闻时，应直接跳到后面那个。"""
    registry = ProviderRegistry([_QuoteOnly(), MockProvider()])
    news = await registry.news(SYMBOL)
    assert news
    assert registry.last_sources[CAP_NEWS] == "mock"


async def test_empty_result_triggers_fallback() -> None:
    registry = ProviderRegistry([_EmptyNews(), MockProvider()])
    news = await registry.news(SYMBOL)
    assert len(news) > 0
    assert registry.last_sources[CAP_NEWS] == "mock"


async def test_unhealthy_provider_never_called() -> None:
    _Unhealthy.called = False
    registry = ProviderRegistry([_Unhealthy(), MockProvider()])
    quote = await registry.quote(SYMBOL)
    assert quote.name == "贵州茅台"
    assert _Unhealthy.called is False


async def test_all_providers_failing_raises_with_detail() -> None:
    registry = ProviderRegistry([_Broken()])
    with pytest.raises(DataNotFound) as exc:
        await registry.quote(SYMBOL)
    # 报错信息要说清每个数据源为什么失败，否则没法排查
    assert "broken" in str(exc.value)
    assert "接口挂了" in str(exc.value)


async def test_mixed_sources_recorded_per_capability() -> None:
    """一次分析里不同维度可能来自不同数据源，溯源要分别记录。"""
    registry = ProviderRegistry([_QuoteOnly(), MockProvider()])
    await registry.quote(SYMBOL)
    await registry.news(SYMBOL)
    await registry.daily_bars(SYMBOL, date(2025, 1, 1), date(2025, 6, 1))
    assert registry.last_sources[CAP_QUOTE] == "quote_only"
    assert registry.last_sources[CAP_NEWS] == "mock"
    assert registry.last_sources["daily_bars"] == "mock"


async def test_tushare_declares_no_news_capability() -> None:
    """Tushare 确实不该声明新闻能力 —— 否则会把新闻请求吞掉。"""
    from app.data.tushare_provider import TushareProvider

    provider = TushareProvider(token="")
    assert CAP_NEWS not in provider.capabilities
    assert await provider.healthcheck() is False  # 没 token 就该跳过
