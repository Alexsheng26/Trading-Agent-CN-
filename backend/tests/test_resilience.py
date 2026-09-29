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


# ---------------------------------------------------------------- 绝不偷偷用假数据


def test_unusable_provider_order_fails_loudly(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """配置里没有一个可用数据源时必须抛错，不能静默塞一个 mock 进去。

    以前的行为是回落到 mock：`DATA_PROVIDER_ORDER` 写错一个字母，
    用户就会拿到一整套程序编出来的行情，而界面上看起来一切正常 ——
    在一个会输出买卖点位的工具里这是不可接受的。
    """
    import pytest as _pytest

    from app.core.config import get_settings
    from app.data.registry import ProviderRegistry

    monkeypatch.setenv("DATA_PROVIDER_ORDER", "akshre,tushre")  # 故意拼错
    get_settings.cache_clear()
    with _pytest.raises(RuntimeError, match="没有一个可用的数据源"):
        ProviderRegistry.from_settings(get_settings())
    get_settings.cache_clear()


def test_mock_is_not_in_default_provider_order(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """默认配置里不能有 mock —— 取不到真数据就该报错，不该编一个。"""
    from app.core.config import Settings

    monkeypatch.delenv("DATA_PROVIDER_ORDER", raising=False)
    assert "mock" not in Settings().data_provider_order


def test_short_error_strips_url_noise() -> None:
    """数据源报错要能读。

    requests 的连接错误会把整条 URL 连同上百个查询参数一起抛出来，
    原样透给前端就是一屏乱码 —— 而现在取不到数据就会报错（不再降级到
    假数据），这些提示是用户唯一的线索，必须人能看懂。
    """
    from app.data.registry import short_error

    raw = (
        "akshare.stock_bid_ask_em() 调用失败：HTTPSConnectionPool("
        "host='push2.eastmoney.com', port=443): Max retries exceeded with url: "
        "/api/qt/stock/get?fltt=2&invt=2&fields=" + "f120%2C" * 40
        + " (Caused by NewConnectionError('unable to connect'))"
    )
    out = short_error(Exception(raw))

    assert len(out) <= 181, f"太长了：{len(out)}"
    assert "push2.eastmoney.com" in out, "必须保留是哪个域名连不上"
    assert "stock_bid_ask_em" in out, "必须保留是哪个接口失败"
    assert "f120%2C" not in out, "长查询串要被砍掉"


def test_short_error_keeps_short_messages_intact() -> None:
    from app.data.registry import short_error

    assert short_error(Exception("akshare 未安装")) == "akshare 未安装"


def test_short_error_collapses_newlines() -> None:
    from app.data.registry import short_error

    assert short_error(Exception("第一行\n  第二行\t第三行")) == "第一行 第二行 第三行"


# ---------------------------------------------------------------- 报错里不能带凭证


def test_short_error_redacts_tushare_style_token() -> None:
    """数据源报错会直接显示在界面上，上游有时把 token 原样回显。

    Tushare 的 token 是 40-64 位十六进制，必须擦掉。
    """
    from app.data.registry import short_error

    fake = "a1b2c3d4e5f6" * 5  # 60 位十六进制，形状和真 token 一致
    out = short_error(Exception(f"tushare 调用失败：token {fake} 无效"))
    assert fake not in out
    assert "***" in out
    assert "tushare 调用失败" in out, "该保留的信息不能一起擦掉"


def test_short_error_redacts_token_in_query_string() -> None:
    from app.data.registry import short_error

    fake = "f" * 48
    out = short_error(Exception(f"https://api.tushare.pro/?token={fake}&api=daily"))
    assert fake not in out
    assert "token=***" in out
    assert "api.tushare.pro" in out, "域名要留着，那是排查线索"


def test_short_error_redacts_sk_style_keys() -> None:
    from app.data.registry import short_error

    for key in ("sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx", "sk-abcdefghijklmnopqrstuvwx"):
        out = short_error(Exception(f"鉴权失败: {key}"))
        assert key not in out
        assert "***" in out


def test_short_error_leaves_normal_messages_alone() -> None:
    """别擦过头 —— 正常报错要原样保留，否则排查线索就没了。"""
    from app.data.registry import short_error

    msg = "连不上 push2.eastmoney.com（ConnectionError）"
    assert short_error(Exception(msg)) == msg


def test_redact_keeps_short_hex_like_words() -> None:
    """短的十六进制串（比如颜色值、commit 前缀）不该被当成密钥。"""
    from app.data.registry import redact_secrets

    assert redact_secrets("背景色 #1a2b3c，提交 a1b2c3d") == "背景色 #1a2b3c，提交 a1b2c3d"
