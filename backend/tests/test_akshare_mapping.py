"""AKShare 字段映射。

之前这层完全没有测试 —— 沙箱里连不上东方财富，只能靠读源码核对列名，
一旦上游改名或者我自己写错一个字段，只有用户那边才会暴露，
而且表现是"价格变成模拟数据"这种很难联想到根因的样子。

这里不联网：把 `_call` 换成返回构造好的 DataFrame，专门校验
"接口返回的那张表 -> 我们的 Quote/Bar" 这段翻译。
"""

from __future__ import annotations

import importlib.util

import pytest

from app.data.symbols import normalize

requires_pandas = pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None, reason="未安装 pandas（随 akshare 一起装）"
)

pytestmark = requires_pandas

SYMBOL = normalize("300741")


def _kv_frame(mapping: dict) -> object:
    """造一张 item/value 两列的竖表，`stock_bid_ask_em` 就是这个形状。"""
    import pandas as pd

    return pd.DataFrame(list(mapping.items()), columns=["item", "value"])


def _valuation_frame(rows=None):  # type: ignore[no-untyped-def]
    import pandas as pd

    return pd.DataFrame(VALUATION_ROWS if rows is None else rows)


def _provider(responses: dict):  # type: ignore[no-untyped-def]
    """AkshareProvider，但 `_call` 改为查表返回预设结果。"""
    from app.data.akshare_provider import AkshareProvider

    provider = AkshareProvider()

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        if func_name not in responses:
            raise AssertionError(f"没预料到的调用：{func_name}({kwargs})")
        return responses[func_name]

    provider._call = fake_call  # type: ignore[assignment, method-assign]
    return provider


BID_ASK = {
    "最新": 28.66,
    "均价": 28.41,
    "涨幅": 1.29,
    "涨跌": 0.36,
    "总手": 123456,     # 单位是"手"
    "金额": 3.5e8,
    "换手": 2.14,
    "量比": 0.95,
    "最高": 29.10,
    "最低": 28.02,
    "今开": 28.30,
    "昨收": 28.30,
    "涨停": 33.96,
    "跌停": 22.64,
}

VALUATION_ROWS = [
    {"数据日期": "2026-09-25", "当日收盘价": 28.30, "PE(TTM)": 20.0, "市净率": 2.0},
    {"数据日期": "2026-09-26", "当日收盘价": 28.30, "PE(TTM)": 21.0, "市净率": 2.1},
]

INFO = {
    "股票代码": "300741",
    "股票简称": "华宝股份",
    "行业": "食品饮料",
    "总市值": 1.76e10,
    "流通市值": 1.02e10,
    "上市时间": "20180224",
}


async def test_quote_maps_bid_ask_fields() -> None:
    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame(BID_ASK),
            "stock_individual_info_em": _kv_frame(INFO),
            "stock_value_em": _valuation_frame(),
        }
    )
    q = await provider.quote(SYMBOL)

    assert q.symbol == "300741.SZ"
    assert q.name == "华宝股份"
    assert q.price == 28.66
    assert q.prev_close == 28.30
    assert q.open == 28.30
    assert q.high == 29.10
    assert q.low == 28.02
    assert q.change_pct == 1.29
    assert q.amount == 3.5e8
    assert q.turnover_rate == 2.14
    # 总手 -> 股：1 手 = 100 股
    assert q.volume == 12345600
    assert q.total_market_cap == 1.76e10
    assert q.float_market_cap == 1.02e10
    # 估值来自日频的估值表（取最后一行），并按当前价折算：
    # 收盘 28.30 时 PE=21，现价 28.66 -> 21 * 28.66 / 28.30
    assert q.pe_ttm == pytest.approx(21.27, abs=0.01)
    assert q.pb == pytest.approx(2.13, abs=0.01)
    assert q.updated_at is not None and q.updated_at.tzinfo is not None


async def test_quote_computes_change_pct_when_missing() -> None:
    """涨幅字段偶尔是空的，用 (最新 - 昨收) / 昨收 兜底。"""
    fields = {**BID_ASK, "涨幅": None}
    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame(fields),
            "stock_individual_info_em": _kv_frame(INFO),
            "stock_value_em": _valuation_frame(),
        }
    )
    q = await provider.quote(SYMBOL)
    assert q.change_pct == pytest.approx(1.27, abs=0.01)  # (28.66-28.30)/28.30


async def test_quote_falls_back_to_prev_close_when_suspended() -> None:
    """停牌时最新价可能是 0，显示 0.00 会让人以为跌到零。"""
    fields = {**BID_ASK, "最新": 0}
    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame(fields),
            "stock_individual_info_em": _kv_frame(INFO),
            "stock_value_em": _valuation_frame(),
        }
    )
    q = await provider.quote(SYMBOL)
    assert q.price == 28.30


async def test_quote_survives_missing_basic_info() -> None:
    """名称取不到不该让整个行情失败 —— 有价格比什么都没有好。"""
    import pandas as pd

    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame(BID_ASK),
            "stock_individual_info_em": pd.DataFrame(),
            "stock_value_em": _valuation_frame(),
        }
    )
    q = await provider.quote(SYMBOL)
    assert q.price == 28.66
    assert q.name == ""


async def test_quote_rejects_empty_response() -> None:
    """空表要抛出让 registry 降级，不能返回一个价格为 0 的假行情。"""
    import pandas as pd

    from app.core.errors import ProviderUnavailable

    provider = _provider({"stock_bid_ask_em": pd.DataFrame()})
    with pytest.raises(ProviderUnavailable):
        await provider.quote(SYMBOL)


async def test_quote_does_not_touch_full_market_snapshot() -> None:
    """**关键断言**：单只行情绝不能去调全市场快照。

    那个接口要翻 54 页、每页 sleep 1 秒，60-90 秒才回来，
    在任何合理超时下都必然失败 —— 也正是"价格全变成模拟数据"的根因。
    """
    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame(BID_ASK),
            "stock_individual_info_em": _kv_frame(INFO),
            "stock_value_em": _valuation_frame(),
        }
    )
    await provider.quote(SYMBOL)  # 预设里没有 stock_zh_a_spot_em，调了就会断言失败


async def test_bars_maps_hist_fields() -> None:
    import pandas as pd

    from app.data.akshare_provider import AkshareProvider

    df = pd.DataFrame(
        [
            {
                "日期": "2026-09-25",
                "开盘": 28.0,
                "收盘": 28.66,
                "最高": 29.1,
                "最低": 27.9,
                "成交量": 123456,
                "成交额": 3.5e8,
                "涨跌幅": 1.29,
                "换手率": 2.14,
            }
        ]
    )
    provider = AkshareProvider()

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        assert func_name == "stock_zh_a_hist"
        return df

    provider._call = fake_call  # type: ignore[assignment, method-assign]

    from datetime import date

    bars = await provider.daily_bars(SYMBOL, date(2026, 9, 1), date(2026, 9, 28))
    assert len(bars) == 1
    b = bars[0]
    assert (b.open, b.close, b.high, b.low) == (28.0, 28.66, 29.1, 27.9)
    assert b.date.isoformat() == "2026-09-25"
    assert b.change_pct == 1.29


# ---------------------------------------------------------------- 大盘指数


async def test_index_quotes_match_by_code_not_only_name() -> None:
    """按代码匹配，名称写法变了也能认出来。

    之前只按名称精确匹配，上游把"沪深300"写成"沪深 300"就会筛出空列表，
    registry 把空结果当失败 —— 整条大盘数据降级到假数据，
    而真实原因只是多了一个空格。
    """
    import pandas as pd

    df = pd.DataFrame(
        [
            {"代码": "000001", "名称": "上证指数", "最新价": 3821.5, "涨跌幅": 0.42},
            {"代码": "000300", "名称": "沪深 300", "最新价": 4512.3, "涨跌幅": -0.18},
            {"代码": "399006", "名称": "创业板指", "最新价": 2730.1, "涨跌幅": 1.02},
            {"代码": "000016", "名称": "上证50", "最新价": 2900.0, "涨跌幅": 0.1},
        ]
    )
    provider = _provider({"stock_zh_index_spot_em": df})
    out = await provider.index_quotes()

    codes = [i.code for i in out]
    assert codes == ["000001", "000300", "399006"], "上证50 不在展示清单里，应被过滤"
    assert out[1].name == "沪深 300"  # 名称原样保留，不改写用户看到的内容
    assert out[0].price == 3821.5


async def test_index_quotes_report_when_nothing_matches() -> None:
    """有数据但一个都没匹配上，要报错并说出实际拿到了什么。"""
    import pandas as pd

    from app.core.errors import ProviderUnavailable

    df = pd.DataFrame([{"代码": "899050", "名称": "北证50", "最新价": 1400.0, "涨跌幅": 0.5}])
    provider = _provider({"stock_zh_index_spot_em": df})
    with pytest.raises(ProviderUnavailable, match="北证50"):
        await provider.index_quotes()


async def test_index_quotes_fills_missing_name_from_code() -> None:
    import pandas as pd

    df = pd.DataFrame([{"代码": "399001", "名称": None, "最新价": 12000.0, "涨跌幅": 0.3}])
    provider = _provider({"stock_zh_index_spot_em": df})
    out = await provider.index_quotes()
    assert out[0].name == "深证成指"


# ---------------------------------------------------------------- 估值


async def test_valuation_missing_leaves_pe_none_not_zero() -> None:
    """估值取不到必须是 None。写成 0 会被当成真实的"市盈率为零"，比缺失更糟。"""
    import pandas as pd

    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame(BID_ASK),
            "stock_individual_info_em": _kv_frame(INFO),
            "stock_value_em": pd.DataFrame(),
        }
    )
    q = await provider.quote(SYMBOL)
    assert q.price == 28.66  # 行情本身不受影响
    assert q.pe_ttm is None
    assert q.pb is None


async def test_valuation_without_base_price_is_used_as_is() -> None:
    """没有基准收盘价时原样返回，宁可略旧也不要没有。"""
    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame(BID_ASK),
            "stock_individual_info_em": _kv_frame(INFO),
            "stock_value_em": _valuation_frame(
                [{"数据日期": "2026-09-26", "当日收盘价": None, "PE(TTM)": 21.0, "市净率": 2.1}]
            ),
        }
    )
    q = await provider.quote(SYMBOL)
    assert q.pe_ttm == 21.0
    assert q.pb == 2.1


async def test_valuation_takes_latest_row() -> None:
    """估值表按日期升序，必须取最后一行，不能取第一行。"""
    provider = _provider(
        {
            "stock_bid_ask_em": _kv_frame({**BID_ASK, "最新": 28.30}),
            "stock_individual_info_em": _kv_frame(INFO),
            "stock_value_em": _valuation_frame(),
        }
    )
    q = await provider.quote(SYMBOL)
    assert q.pe_ttm == 21.0, "取到的是 9-26 那行（21.0），不是 9-25 那行（20.0）"
