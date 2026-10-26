"""雪球/新浪备份数据源的字段映射。

由来：用户机器上 `push2*.eastmoney.com` 这一组主机不可达
（同域名下的 search-api-web 却是通的，换浏览器指纹也没用），
所以补了两个主机完全无关的源。

和 test_akshare_mapping.py 一样，这里不联网：把 `_call` 换成返回构造好的
DataFrame，只校验"接口返回的那张表 -> 我们的 Quote/Bar/IndexQuote"这段翻译。
"""

from __future__ import annotations

import importlib.util
from datetime import date

import pytest

from app.core.errors import ProviderUnavailable
from app.data.symbols import normalize

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None, reason="未安装 pandas（随 akshare 一起装）"
)

SYMBOL = normalize("300741")


def _kv(mapping: dict):  # type: ignore[no-untyped-def]
    """item/value 两列的竖表，雪球接口就是这个形状。"""
    import pandas as pd

    return pd.DataFrame(list(mapping.items()), columns=["item", "value"])


def _stub(provider, responses: dict):  # type: ignore[no-untyped-def]
    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        if func_name not in responses:
            raise AssertionError(f"没预料到的调用：{func_name}({kwargs})")
        result = responses[func_name]
        if isinstance(result, Exception):
            raise result
        return result

    provider._call = fake_call  # type: ignore[assignment, method-assign]
    return provider


# ---------------------------------------------------------------- 雪球行情

XQ = {
    "名称": "华宝股份",
    "现价": 28.66,
    "昨收": 28.30,
    "今开": 28.30,
    "最高": 29.10,
    "最低": 28.02,
    "涨幅": 1.27,
    "涨跌": 0.36,
    "成交量": 12345600,
    "成交额": 3.5e8,
    "周转率": 2.14,
    "市盈率(TTM)": 21.3,
    "市净率": 2.13,
    "资产净值/总市值": 1.76e10,
    "流通值": 1.02e10,
    "涨停": 34.39,
    "跌停": 22.64,
}


async def test_xueqiu_quote_mapping() -> None:
    from app.data.backup_providers import XueqiuProvider

    p = _stub(XueqiuProvider(), {"stock_individual_spot_xq": _kv(XQ)})
    q = await p.quote(SYMBOL)

    assert q.symbol == "300741.SZ"
    assert q.name == "华宝股份"
    assert q.price == 28.66
    assert q.prev_close == 28.30
    assert (q.open, q.high, q.low) == (28.30, 29.10, 28.02)
    assert q.change_pct == 1.27
    assert q.volume == 12345600
    assert q.amount == 3.5e8
    assert q.turnover_rate == 2.14, "雪球把换手率叫「周转率」"
    assert q.pe_ttm == 21.3
    assert q.pb == 2.13
    assert q.total_market_cap == 1.76e10
    assert q.float_market_cap == 1.02e10
    assert q.updated_at is not None and q.updated_at.tzinfo is not None


async def test_xueqiu_uses_prefixed_symbol() -> None:
    """雪球要的是 SZ300741 这种带市场前缀的写法，传 6 位数字会查不到。"""
    from app.data.backup_providers import XueqiuProvider

    seen: dict = {}

    p = XueqiuProvider()

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return _kv(XQ)

    p._call = fake_call  # type: ignore[assignment, method-assign]
    await p.quote(SYMBOL)
    assert seen["symbol"] == "SZ300741"


async def test_xueqiu_computes_change_pct_when_missing() -> None:
    from app.data.backup_providers import XueqiuProvider

    p = _stub(XueqiuProvider(), {"stock_individual_spot_xq": _kv({**XQ, "涨幅": None})})
    q = await p.quote(SYMBOL)
    assert q.change_pct == pytest.approx(1.27, abs=0.01)


async def test_xueqiu_falls_back_to_prev_close_when_suspended() -> None:
    from app.data.backup_providers import XueqiuProvider

    p = _stub(XueqiuProvider(), {"stock_individual_spot_xq": _kv({**XQ, "现价": 0})})
    assert (await p.quote(SYMBOL)).price == 28.30


async def test_xueqiu_rejects_empty() -> None:
    import pandas as pd

    from app.data.backup_providers import XueqiuProvider

    p = _stub(XueqiuProvider(), {"stock_individual_spot_xq": pd.DataFrame()})
    with pytest.raises(ProviderUnavailable):
        await p.quote(SYMBOL)


# ---------------------------------------------------------------- 新浪日线


async def test_sina_daily_bars_mapping() -> None:
    import pandas as pd

    from app.data.backup_providers import SinaProvider

    df = pd.DataFrame(
        [
            {
                "date": date(2026, 9, 25),
                "open": 28.0,
                "high": 29.1,
                "low": 27.9,
                "close": 28.66,
                "volume": 12345600,
                "amount": 3.5e8,
                "outstanding_share": 6.1e8,
                "turnover": 2.14,
            }
        ]
    )
    p = _stub(SinaProvider(), {"stock_zh_a_daily": df})
    bars = await p.daily_bars(SYMBOL, date(2026, 9, 1), date(2026, 9, 28))

    assert len(bars) == 1
    b = bars[0]
    assert b.date == date(2026, 9, 25)
    assert (b.open, b.high, b.low, b.close) == (28.0, 29.1, 27.9, 28.66)
    assert b.volume == 12345600
    assert b.turnover_rate == 2.14
    # 新浪这个接口不给涨跌幅，必须是 None 而不是 0
    assert b.change_pct is None


async def test_sina_uses_lowercase_prefixed_symbol() -> None:
    from app.data.backup_providers import SinaProvider

    seen: dict = {}
    p = SinaProvider()

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        import pandas as pd

        return pd.DataFrame()

    p._call = fake_call  # type: ignore[assignment, method-assign]
    await p.daily_bars(SYMBOL, date(2026, 9, 1), date(2026, 9, 28))
    assert seen["symbol"] == "sz300741", "新浪要的是 sz300741 这种小写前缀写法"
    assert seen["start_date"] == "20260901"


async def test_sina_skips_rows_without_close() -> None:
    import pandas as pd

    from app.data.backup_providers import SinaProvider

    df = pd.DataFrame(
        [
            {"date": date(2026, 9, 25), "close": None, "open": 1},
            {"date": date(2026, 9, 26), "close": 28.66, "open": 28.0},
        ]
    )
    p = _stub(SinaProvider(), {"stock_zh_a_daily": df})
    bars = await p.daily_bars(SYMBOL, date(2026, 9, 1), date(2026, 9, 28))
    assert [b.date.day for b in bars] == [26]


# ---------------------------------------------------------------- 新浪指数


async def test_sina_index_quotes_mapping() -> None:
    import pandas as pd

    from app.data.backup_providers import SinaProvider

    df = pd.DataFrame(
        [
            {"代码": "sh000001", "名称": "上证指数", "最新价": 3821.5, "涨跌幅": 0.42},
            {"代码": "sz399001", "名称": "深证成指", "最新价": 12000.0, "涨跌幅": -0.18},
            {"代码": "sh000016", "名称": "上证50", "最新价": 2900.0, "涨跌幅": 0.1},
        ]
    )
    p = _stub(SinaProvider(), {"stock_zh_index_spot_sina": df})
    out = await p.index_quotes()

    assert [i.code for i in out] == ["000001", "399001"], "上证50 不在展示清单里"
    assert out[0].price == 3821.5
    assert out[1].change_pct == -0.18


async def test_sina_index_accepts_english_columns() -> None:
    """新浪那个接口的列名在不同 akshare 版本里中英文都出现过，两种都要认。"""
    import pandas as pd

    from app.data.backup_providers import SinaProvider

    df = pd.DataFrame(
        [{"code": "sh000300", "name": "沪深300", "trade": 4512.3, "changepercent": -0.5}]
    )
    p = _stub(SinaProvider(), {"stock_zh_index_spot_sina": df})
    out = await p.index_quotes()
    assert out[0].code == "000300"
    assert out[0].price == 4512.3
    assert out[0].change_pct == -0.5


async def test_sina_index_reports_when_nothing_matches() -> None:
    import pandas as pd

    from app.data.backup_providers import SinaProvider

    df = pd.DataFrame([{"代码": "sh000016", "名称": "上证50", "最新价": 1.0, "涨跌幅": 0.1}])
    p = _stub(SinaProvider(), {"stock_zh_index_spot_sina": df})
    with pytest.raises(ProviderUnavailable):
        await p.index_quotes()


# ---------------------------------------------------------------- 能力声明


def test_capabilities_are_narrow() -> None:
    """这两个源只声明自己真能提供的接口，registry 才会正确地逐接口降级。"""
    from app.data.backup_providers import SinaProvider, XueqiuProvider

    # 雪球的行情接口股票和场内基金通用
    assert XueqiuProvider().capabilities == {"quote", "etf_quote"}
    # sina 的 search 只给后台建代码目录用（它要翻 60 多页且有封 IP 风险），
    # 请求路径上的搜索查的是本地表。
    # 实时价走 hq.sinajs.cn（免 token、批量），股票和 ETF 都声明。
    # 金价只声明伦敦金：上金所的数据在上金所官网，不在新浪
    assert SinaProvider().capabilities == {
        "quote",
        "daily_bars",
        "minute_bars",
        "index_quotes",
        "search",
        "etf_quote",
        "etf_bars",
        "etf_minute_bars",
        "etf_list",
        "gold_london_quote",
        "gold_london_bars",
    }


async def test_degrades_to_backup_when_eastmoney_quote_fails() -> None:
    """**关键断言**：东财行情挂掉时，registry 要能落到雪球上。

    这正是用户遇到的场景 —— push2.eastmoney.com 不可达。
    """
    from app.data.base import ALL_CAPABILITIES, CAP_QUOTE
    from app.data.registry import ProviderRegistry

    class _DeadEastmoney:
        name = "akshare"
        capabilities = frozenset(ALL_CAPABILITIES)

        async def healthcheck(self) -> bool:
            return True

        def __getattr__(self, _name):  # type: ignore[no-untyped-def]
            async def _boom(*a, **k):
                raise ProviderUnavailable(
                    "('Connection aborted.', RemoteDisconnected("
                    "'Remote end closed connection without response'))"
                )

            return _boom

    from app.data.backup_providers import XueqiuProvider

    xq = _stub(XueqiuProvider(), {"stock_individual_spot_xq": _kv(XQ)})
    registry = ProviderRegistry([_DeadEastmoney(), xq], timeout=5)

    quote = await registry.quote(SYMBOL)
    assert quote.name == "华宝股份"
    assert quote.price == 28.66
    assert registry.last_sources[CAP_QUOTE] == "xueqiu", "溯源要如实说是雪球给的"


# ---------------------------------------------------------------- 雪球 token


async def test_xueqiu_passes_token_when_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings
    from app.data.backup_providers import XueqiuProvider

    monkeypatch.setenv("XUEQIU_TOKEN", "abc123")
    get_settings.cache_clear()

    seen: dict = {}
    p = XueqiuProvider()

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return _kv(XQ)

    p._call = fake_call  # type: ignore[assignment, method-assign]
    await p.quote(SYMBOL)
    assert seen["token"] == "abc123"
    get_settings.cache_clear()


async def test_xueqiu_omits_token_when_not_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """不配 token 时不能传空串过去 —— 那会被当成一个无效凭证。"""
    from app.core.config import get_settings
    from app.data.backup_providers import XueqiuProvider

    monkeypatch.setenv("XUEQIU_TOKEN", "")
    get_settings.cache_clear()

    seen: dict = {}
    p = XueqiuProvider()

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return _kv(XQ)

    p._call = fake_call  # type: ignore[assignment, method-assign]
    await p.quote(SYMBOL)
    assert "token" not in seen
    get_settings.cache_clear()


async def test_xueqiu_login_error_says_how_to_fix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """雪球未登录返回 400016。原样透出去用户看不懂，要补上怎么配。"""
    from app.core.config import get_settings
    from app.data.backup_providers import XueqiuProvider

    monkeypatch.setenv("XUEQIU_TOKEN", "")
    get_settings.cache_clear()

    p = _stub(
        XueqiuProvider(),
        {
            "stock_individual_spot_xq": ProviderUnavailable(
                "API Error: Xueqiu quote endpoint requires a valid xq_a_token or login "
                "session; upstream returned 400016"
            )
        },
    )
    with pytest.raises(ProviderUnavailable) as err:
        await p.quote(SYMBOL)

    msg = str(err.value)
    assert "xq_a_token" in msg
    assert "XUEQIU_TOKEN" in msg
    assert "自动降级" in msg, "要说明不配也能用，别让人以为必须配"
    get_settings.cache_clear()
