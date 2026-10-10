"""数据源注册与降级路由。

按 `DATA_PROVIDER_ORDER` 的顺序尝试每个数据源，逐接口降级：
比如 Tushare 排第一但不支持个股新闻，新闻这一项就自动落到 AKShare。
每次调用都记录实际命中的数据源，写进 `MarketSnapshot.sources`，
前端能直接看到"这块数据来自哪儿"。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, TypeVar

from app.core.config import Settings, get_settings
from app.core.errors import CapabilityNotSupported, DataNotFound, ProviderUnavailable
from app.data.akshare_provider import AkshareProvider
from app.data.backup_providers import SinaProvider, XueqiuProvider
from app.data.base import (
    CAP_BARS,
    CAP_BASIC,
    CAP_ETF_BARS,
    CAP_ETF_LIST,
    CAP_ETF_MINUTE_BARS,
    CAP_ETF_QUOTE,
    CAP_FINANCIALS,
    CAP_FUND_HOLDINGS,
    CAP_FUND_PROFILE,
    CAP_INDEXES,
    CAP_MINUTE_BARS,
    CAP_MONEY_FLOW,
    CAP_NEWS,
    CAP_QUOTE,
    CAP_SEARCH,
    Bar,
    DataProvider,
    FinancialReport,
    FundHolding,
    FundProfile,
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


#: 单个数据源的报错在提示里最多保留多少字
_ERROR_MAX_CHARS = 180


#: 看起来像密钥的片段。数据源报错里可能把 token 原样带出来，
#: 而这些报错现在会直接显示在界面上 —— 必须先擦掉。
_SECRET_PATTERNS = (
    # Tushare 那种 40-64 位十六进制
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    # sk-... 形式（Anthropic / OpenAI / DeepSeek）
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    # URL 查询参数里的 token/key/apikey
    re.compile(r"(?i)\b(token|api_?key|secret)=[^&\s\'\"]+"),
)


def redact_secrets(text: str) -> str:
    """擦掉文本里像密钥的片段。

    数据源的报错会被显示在界面上（自 v11 起取不到数据就明确报错），
    而上游有时会把凭证原样回显在错误信息里。
    宁可多擦几个十六进制串，也不能让 token 出现在屏幕上。
    """
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.endswith(r"=[^&\s\'\"]+"):
            text = pattern.sub(lambda m: m.group(0).split("=", 1)[0] + "=***", text)
        else:
            text = pattern.sub("***", text)
    return text


def short_error(exc: object) -> str:
    """把数据源的原始异常压成一句人能读的话。

    requests 的连接错误会把整条 URL 连同上百个查询参数一起抛出来
    （`fields=f120%2Cf121%2C...` 那种），原样透给前端就是一屏乱码，
    真正有用的只有"连不上 push2.eastmoney.com"这一句。
    完整信息仍然写日志，不丢。

    同时擦掉像密钥的片段 —— 这些报错会直接显示在界面上。
    """
    text = re.sub(r"\s+", " ", str(exc)).strip()
    text = redact_secrets(text)
    # 砍掉长查询串，只留问号
    text = re.sub(r"\?[^\s)\'\"]{40,}", "?…", text)
    if len(text) > _ERROR_MAX_CHARS:
        text = text[:_ERROR_MAX_CHARS].rstrip() + "…"
    return text


def build_provider(name: str, settings: Settings) -> DataProvider | None:
    match name.lower():
        case "akshare":
            return AkshareProvider()
        case "tushare":
            return TushareProvider(settings.tushare_token)
        case "xueqiu":
            return XueqiuProvider()
        case "sina":
            return SinaProvider()
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
            # 以前这里会静默塞一个 mock 进去。那是个很坑的行为：
            # DATA_PROVIDER_ORDER 写错一个字母（akshre），用户就会拿到一整套
            # 程序编出来的行情，而界面上看起来一切正常。
            # 在一个会输出买卖点位的工具里，宁可起不来也不能编数据。
            raise RuntimeError(
                "DATA_PROVIDER_ORDER 里没有一个可用的数据源："
                f"{settings.data_provider_order!r}。"
                "可选值为 akshare / tushare / mock（mock 是假数据，仅用于离线看界面）。"
            )
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
        attempt_timeout: float | None = None,
    ) -> T:
        """按顺序尝试支持该能力的数据源，第一个成功的胜出。

        `attempt_timeout` 按次覆盖"每个数据源单次尝试"的预算。
        注意它不是整个调用的超时 —— 从外面包一层 asyncio.timeout 会把
        整条降级链一起掐掉，那就失去降级的意义了。少数接口天生就慢
        （比如 AKShare 的全市场快照要翻 54 页、每页还 sleep 1 秒，
        单次 60-90 秒），用默认的 12 秒去调等于永远失败。
        这类调用只应出现在后台任务里，并显式给一个大超时。
        """
        await self.health()
        attempts: list[str] = []  # 真尝试过、失败了的
        skipped: list[str] = []  # 压根没尝试的

        budget = self._timeout if attempt_timeout is None else attempt_timeout

        for provider in self._providers:
            if capability not in provider.capabilities:
                skipped.append(f"{provider.name}: 未声明支持 {capability}")
                continue
            if not self._healthy.get(provider.name, False):
                # 以前这里静默跳过，报错就成了"没有数据源声明支持该能力" ——
                # 明明是探活没过，却指向完全不相干的原因，排查时非常误导。
                skipped.append(f"{provider.name}: 探活未通过（依赖未安装或配置缺失）")
                continue
            try:
                # 时间盒住每次尝试：卡住的数据源会拖死整个请求，
                # 超时就当它失败，继续降级。
                async with asyncio.timeout(budget):
                    result = await call(provider)
            except TimeoutError:
                msg = f"超过 {budget} 秒无响应"
                attempts.append(f"{provider.name}: {msg}")
                log.warning("%s 的 %s %s，降级尝试下一个", provider.name, capability, msg)
                continue
            except (ProviderUnavailable, CapabilityNotSupported) as exc:
                attempts.append(f"{provider.name}: {short_error(exc)}")
                log.info("%s 的 %s 不可用，降级尝试下一个：%s", provider.name, capability, exc)
                continue
            except Exception as exc:
                attempts.append(f"{provider.name}: {type(exc).__name__}: {short_error(exc)}")
                log.warning("%s 的 %s 抛出未预期异常：%s", provider.name, capability, exc)
                continue

            # 空结果也算失败，继续往下找（新闻/资金流经常某个源就是空的）
            if not allow_empty and _is_empty(result):
                attempts.append(f"{provider.name}: 返回空结果")
                continue

            self.last_sources[capability] = provider.name
            return result

        detail = "；".join(attempts + skipped) if (attempts or skipped) else "未配置任何数据源"
        raise DataNotFound(f"所有数据源都拿不到 {capability} —— {detail}")

    # ---------------- 对上层的统一接口 ----------------

    async def search(
        self, keyword: str, limit: int = 20, attempt_timeout: float | None = None
    ) -> list[StockBasic]:
        return await self._dispatch(
            CAP_SEARCH,
            lambda p: p.search(keyword, limit),
            allow_empty=True,
            attempt_timeout=attempt_timeout,
        )

    async def stock_basic(self, symbol: Symbol) -> StockBasic:
        return await self._dispatch(CAP_BASIC, lambda p: p.stock_basic(symbol))

    async def quote(self, symbol: Symbol) -> Quote:
        # 股票和 ETF 走不同的能力标识，见 base.py 里 CAP_ETF_* 的说明
        cap = CAP_ETF_QUOTE if symbol.is_etf else CAP_QUOTE
        return await self._dispatch(cap, lambda p: p.quote(symbol))

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        cap = CAP_ETF_BARS if symbol.is_etf else CAP_BARS
        return await self._dispatch(cap, lambda p: p.daily_bars(symbol, start, end, adjust))

    async def minute_bars(self, symbol: Symbol, period: int, start: date, end: date) -> list[Bar]:
        cap = CAP_ETF_MINUTE_BARS if symbol.is_etf else CAP_MINUTE_BARS
        return await self._dispatch(cap, lambda p: p.minute_bars(symbol, period, start, end))

    async def financials(self, symbol: Symbol, periods: int = 8) -> list[FinancialReport]:
        return await self._dispatch(CAP_FINANCIALS, lambda p: p.financials(symbol, periods))

    async def news(
        self, symbol: Symbol, limit: int = 20, keyword: str | None = None
    ) -> list[NewsItem]:
        return await self._dispatch(CAP_NEWS, lambda p: p.news(symbol, limit, keyword=keyword))

    async def money_flow(self, symbol: Symbol, days: int = 10) -> list[MoneyFlow]:
        return await self._dispatch(CAP_MONEY_FLOW, lambda p: p.money_flow(symbol, days))

    async def index_quotes(self) -> list[IndexQuote]:
        return await self._dispatch(CAP_INDEXES, lambda p: p.index_quotes())

    # ---------------- ETF 专用 ----------------

    async def etf_list(self, attempt_timeout: float | None = None) -> list[StockBasic]:
        return await self._dispatch(
            CAP_ETF_LIST, lambda p: p.etf_list(), attempt_timeout=attempt_timeout
        )

    async def fund_profile(self, symbol: Symbol) -> FundProfile:
        return await self._dispatch(CAP_FUND_PROFILE, lambda p: p.fund_profile(symbol))

    async def fund_holdings(self, symbol: Symbol) -> list[FundHolding]:
        return await self._dispatch(CAP_FUND_HOLDINGS, lambda p: p.fund_holdings(symbol))


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
