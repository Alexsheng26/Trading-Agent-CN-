"""数据源不可用时的行为。

由来：用户搜索一只票时前端永久停在"搜索中…"。根因是 registry 对每次
provider 调用没有任何超时——AKShare 的搜索要先拉全市场 5000+ 行快照，
那个请求一挂，整个 HTTP 请求就永不返回。免费数据源慢或挂是常态，
必须时间盒住；同时输入合法代码时根本不该依赖数据源。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.errors import DataNotFound
from app.data.base import ALL_CAPABILITIES, CAP_QUOTE
from app.data.mock_provider import MockProvider
from app.data.registry import ProviderRegistry
from app.data.symbols import normalize

SYMBOL = normalize("600519")


class _Hanging:
    """永远不返回的数据源 —— 模拟 AKShare 卡死。"""

    name = "hanging"
    capabilities = frozenset(ALL_CAPABILITIES)
    entered = False

    async def healthcheck(self) -> bool:
        return True

    async def quote(self, symbol):  # type: ignore[no-untyped-def]
        _Hanging.entered = True
        await asyncio.sleep(3600)
        raise AssertionError("不该走到这里")

    def __getattr__(self, _name):  # type: ignore[no-untyped-def]
        async def _hang(*a, **k):
            await asyncio.sleep(3600)

        return _hang


# ---------------------------------------------------------------- 超时降级


async def test_hanging_provider_degrades_instead_of_blocking() -> None:
    """卡住的数据源必须被超时踢掉，并降级到下一个。"""
    registry = ProviderRegistry([_Hanging(), MockProvider()], timeout=0.3)
    quote = await asyncio.wait_for(registry.quote(SYMBOL), timeout=5)
    assert quote.name == "贵州茅台"
    assert registry.last_sources[CAP_QUOTE] == "mock"
    assert _Hanging.entered, "第一个源应当被真的尝试过"


async def test_timeout_is_reported_in_error_detail() -> None:
    """全都超时时，报错要说清是超时而不是别的原因，否则没法排查。"""
    registry = ProviderRegistry([_Hanging()], timeout=0.2)
    with pytest.raises(DataNotFound) as exc:
        await registry.quote(SYMBOL)
    assert "超过" in str(exc.value)
    assert "hanging" in str(exc.value)


async def test_timeout_applies_per_provider_not_per_request() -> None:
    """超时是"每个源"各算一次，不是整个请求共享一份预算 ——
    否则排在后面的健康数据源会被前面的慢源吃掉时间。"""
    registry = ProviderRegistry([_Hanging(), _Hanging(), MockProvider()], timeout=0.3)
    loop = asyncio.get_running_loop()
    start = loop.time()
    quote = await registry.quote(SYMBOL)
    elapsed = loop.time() - start
    assert quote.name == "贵州茅台"
    # 两个卡死的源各耗 0.3 秒，总耗时应在 0.6 秒上下而不是 0.3
    assert 0.5 < elapsed < 2.0, elapsed


async def test_healthy_provider_is_not_slowed_by_timeout() -> None:
    """正常数据源不该因为有超时机制而变慢。"""
    registry = ProviderRegistry([MockProvider()], timeout=5)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await registry.quote(SYMBOL)
    assert loop.time() - start < 1.0


# ---------------------------------------------------------------- 代码直达


def test_search_by_code_works_without_any_data_source(client) -> None:  # type: ignore[no-untyped-def]
    """输入合法代码时，即使数据源查不到这只票也要能返回。

    mock 数据源只认识 8 只硬编码的票，300741 不在其中 ——
    这正是用户遇到的情况。
    """
    results = client.get("/api/v1/market/search", params={"q": "300741"}).json()
    assert results, "合法代码必须有结果"
    assert results[0]["symbol"] == "300741.SZ"
    assert results[0]["board"] == "创业板"
    # 数据源没给名称时留空，由前端显示代码
    assert results[0]["name"] == ""


def test_search_by_code_puts_direct_hit_first(client) -> None:  # type: ignore[no-untyped-def]
    """代码直达要排在第一位。"""
    results = client.get("/api/v1/market/search", params={"q": "600519"}).json()
    assert results[0]["symbol"] == "600519.SH"
    # 数据源认识这只票，所以名称应该有
    assert results[0]["name"] == "贵州茅台"


def test_search_does_not_duplicate_direct_hit(client) -> None:  # type: ignore[no-untyped-def]
    """数据源已经返回该票时不要重复插一条。"""
    results = client.get("/api/v1/market/search", params={"q": "600519"}).json()
    assert [r["symbol"] for r in results].count("600519.SH") == 1


def test_search_by_name_still_works(client) -> None:  # type: ignore[no-untyped-def]
    results = client.get("/api/v1/market/search", params={"q": "茅台"}).json()
    assert any(r["name"] == "贵州茅台" for r in results)


def test_search_garbage_returns_empty_not_error(client) -> None:  # type: ignore[no-untyped-def]
    """无意义关键词返回空列表，不是 500。"""
    resp = client.get("/api/v1/market/search", params={"q": "乱码xyz"})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------- 状态透明度


def test_status_reports_actually_used_sources(client) -> None:  # type: ignore[no-untyped-def]
    """healthcheck 只说明依赖装好了。真正需要暴露的是"实际命中了哪个源"。

    AKShare 模块在、但接口全挂时 healthcheck 仍为 True，
    界面若只看它就会把模拟数据当成真实行情展示 —— 在金融工具里这很危险。
    """
    before = client.get("/api/v1/status").json()
    assert before["active_sources"] == {}
    assert before["using_mock_data"] is False

    client.get("/api/v1/market/quote/600519")

    after = client.get("/api/v1/status").json()
    assert after["active_sources"]["quote"] == "mock"
    assert after["using_mock_data"] is True, "用了模拟数据必须能被界面察觉"


def test_status_exposes_provider_timeout(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/status").json()
    assert body["pipeline"]["timeout_sec"] > 0
