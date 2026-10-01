"""场内 ETF（512480 半导体ETF、512400 有色金属ETF 这类）。

ETF 的代码写法和股票一样，但几乎每一层都有不同：
  - 代码段：沪市 5 开头、深市 159 开头 —— 原来的规则认不出来，直接报错；
  - 行情：东财盘口接口按"6 开头"判断沪市，会把沪市 ETF 查成深市；
  - 基本面：没有财报和市盈率，换成跟踪标的、规模、费率、重仓股；
  - 价格：最小变动 0.001 元，两位小数会把 0.873 和 0.878 显示成一样；
  - 交易规则：跨境 / 债券 / 黄金 / 货币 ETF 是 T+0。

不联网：数据源的 `_call` 换成查表返回构造好的 DataFrame。
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta

import pytest

from app.core.errors import DataNotFound, ProviderUnavailable, SymbolError
from app.data.base import CAP_ETF_QUOTE, CAP_QUOTE, Bar, Quote, StockBasic
from app.data.symbols import normalize

requires_pandas = pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None, reason="未安装 pandas（随 akshare 一起装）"
)

SH_ETF = normalize("512480")
SZ_ETF = normalize("159915")


# ---------------------------------------------------------------- 代码识别


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("512480", "512480.SH"),  # 半导体ETF
        ("512400", "512400.SH"),  # 有色金属ETF
        ("sh510300", "510300.SH"),
        ("SH518880", "518880.SH"),
        ("560050.SH", "560050.SH"),
        ("588000", "588000.SH"),  # 科创50ETF
        ("520830", "520830.SH"),
        ("159915", "159915.SZ"),  # 创业板ETF
    ],
)
def test_etf_codes_are_recognised(raw: str, canonical: str) -> None:
    sym = normalize(raw)
    assert sym.canonical == canonical
    assert sym.asset_type == "etf"
    assert sym.is_etf
    assert sym.board == "ETF"
    assert sym.price_decimals == 3


@pytest.mark.parametrize("raw", ["600519", "000001", "300750", "688981", "830799"])
def test_stocks_are_unaffected(raw: str) -> None:
    sym = normalize(raw)
    assert sym.asset_type == "stock"
    assert sym.price_decimals == 2
    assert sym.board != "ETF"


def test_etf_price_limits() -> None:
    assert normalize("588000").price_limit_pct == 20.0  # 科创板 ETF
    assert normalize("512480").price_limit_pct == 10.0


@pytest.mark.parametrize("raw", ["501050", "161725"])
def test_lof_is_rejected_with_a_clear_reason(raw: str) -> None:
    """LOF 的数据接口是另一套，没接入之前明确说不支持，不能当股票去查。"""
    with pytest.raises(SymbolError, match="LOF"):
        normalize(raw)


# ---------------------------------------------------------------- 按品种调度


class _StockQuoteOnly:
    """只会查股票行情的源 —— 比如东财盘口之于沪市 ETF、Tushare 的 daily。"""

    name = "stock-only"
    capabilities = frozenset({CAP_QUOTE})

    def __init__(self) -> None:
        self.called_with: list[str] = []

    async def healthcheck(self) -> bool:
        return True

    async def quote(self, symbol):  # type: ignore[no-untyped-def]
        self.called_with.append(symbol.canonical)
        return Quote(symbol=symbol.canonical, name="股票", price=10.0)


class _EtfQuoteOnly(_StockQuoteOnly):
    name = "etf-only"
    capabilities = frozenset({CAP_ETF_QUOTE})

    async def quote(self, symbol):  # type: ignore[no-untyped-def]
        self.called_with.append(symbol.canonical)
        return Quote(symbol=symbol.canonical, name="半导体ETF", price=0.873)


async def test_etf_quote_skips_stock_only_sources() -> None:
    from app.data.registry import ProviderRegistry

    stock, etf = _StockQuoteOnly(), _EtfQuoteOnly()
    registry = ProviderRegistry([stock, etf], timeout=5)

    q = await registry.quote(SH_ETF)
    assert q.price == 0.873
    assert stock.called_with == [], "只会查股票的源不该收到 ETF 代码"
    assert registry.last_sources["etf_quote"] == "etf-only"


async def test_stock_quote_skips_etf_only_sources() -> None:
    from app.data.registry import ProviderRegistry

    stock, etf = _StockQuoteOnly(), _EtfQuoteOnly()
    registry = ProviderRegistry([etf, stock], timeout=5)

    await registry.quote(normalize("600519"))
    assert etf.called_with == []


async def test_etf_failure_says_which_sources_do_not_support_it() -> None:
    from app.data.registry import ProviderRegistry

    registry = ProviderRegistry([_StockQuoteOnly()], timeout=5)
    with pytest.raises(DataNotFound, match="未声明支持 etf_quote"):
        await registry.quote(SH_ETF)


# ---------------------------------------------------------------- AKShare（东财 / 天天基金）


def _akshare(responses: dict):  # type: ignore[no-untyped-def]
    from app.data.akshare_provider import AkshareProvider

    provider = AkshareProvider()
    calls: list[tuple[str, dict]] = []

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((func_name, kwargs))
        if func_name not in responses:
            raise AssertionError(f"没预料到的调用：{func_name}({kwargs})")
        result = responses[func_name]
        if callable(result):
            result = result(**kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    provider._call = fake_call  # type: ignore[assignment, method-assign]
    provider.calls = calls  # type: ignore[attr-defined]
    return provider


def _kv(mapping: dict):  # type: ignore[no-untyped-def]
    import pandas as pd

    return pd.DataFrame(list(mapping.items()), columns=["item", "value"])


OVERVIEW = {
    "基金全称": "国联安中证全指半导体产品与设备交易型开放式指数证券投资基金",
    "基金简称": "国联安中证全指半导体ETF",
    "基金代码": "512480（主代码）",
    "基金类型": "指数型-股票",
    "成立日期/规模": "2019年05月08日 / 5.123亿份",
    "资产规模": "123.45亿元（截止至：2025年06月30日）",
    "份额规模": "98.7654亿份（截止至：2025年06月30日）",
    "基金管理人": "国联安基金",
    "管理费率": "0.50%（每年）",
    "托管费率": "0.10%（每年）",
    "业绩比较基准": "中证全指半导体产品与设备指数收益率",
    "跟踪标的": "中证全指半导体产品与设备指数",
}


def _overview_frame(overrides: dict | None = None):  # type: ignore[no-untyped-def]
    import pandas as pd

    return pd.DataFrame([{**OVERVIEW, **(overrides or {})}])


@requires_pandas
async def test_akshare_skips_sh_etf_quote_without_calling() -> None:
    """东财盘口接口会把 512480 查成 0.512480（深市）。与其发一个注定查错的请求，
    不如直接跳过，让 registry 降级到雪球 / 新浪。"""
    p = _akshare({})
    with pytest.raises(ProviderUnavailable, match="沪市 ETF"):
        await p.quote(SH_ETF)
    assert p.calls == []


@requires_pandas
async def test_akshare_sz_etf_quote_is_a_single_request() -> None:
    """深市 ETF 走东财盘口，**只发这一个请求**。

    不去个股估值接口（ETF 没有估值），也不去天天基金档案页查名称 ——
    akshare 请求那个页面不带超时，它一慢，每 5 秒一次的行情轮询就跟着超时。
    名称由 quote_with_fallback 从本地代码目录补。
    """
    p = _akshare(
        {
            "stock_bid_ask_em": _kv(
                {
                    "最新": 2.345,
                    "昨收": 2.300,
                    "今开": 2.310,
                    "最高": 2.350,
                    "最低": 2.301,
                    "涨幅": 1.96,
                    "总手": 1234567,
                    "金额": 2.9e8,
                    "换手": 3.2,
                }
            ),  # fmt: skip
        }
    )
    q = await p.quote(SZ_ETF)
    assert (q.price, q.prev_close, q.change_pct) == (2.345, 2.300, 1.96)
    assert (q.pe_ttm, q.pb, q.total_market_cap) == (None, None, None)
    assert q.name == ""
    assert [c[0] for c in p.calls] == ["stock_bid_ask_em"]


@requires_pandas
async def test_akshare_etf_bars_use_fund_interface() -> None:
    import pandas as pd

    df = pd.DataFrame(
        [{"日期": "2026-09-29", "开盘": 0.870, "收盘": 0.873, "最高": 0.880, "最低": 0.866,
          "成交量": 5e6, "成交额": 4.3e8, "涨跌幅": 0.35, "换手率": 2.1}]
    )  # fmt: skip
    p = _akshare({"fund_etf_hist_em": df})
    bars = await p.daily_bars(SH_ETF, date(2026, 9, 1), date(2026, 9, 30))

    assert bars[0].close == 0.873
    name, kwargs = p.calls[0]
    assert name == "fund_etf_hist_em"
    assert kwargs["symbol"] == "512480"
    assert kwargs["adjust"] == "qfq"


@requires_pandas
async def test_akshare_fund_profile() -> None:
    p = _akshare({"fund_overview_em": _overview_frame()})
    prof = await p.fund_profile(SH_ETF)

    assert prof.tracking_index == "中证全指半导体产品与设备指数"
    assert prof.asset_size == pytest.approx(123.45e8)
    assert prof.asset_size_date == date(2025, 6, 30)
    assert prof.share_size == pytest.approx(98.7654e8)
    assert prof.inception_date == date(2019, 5, 8)
    assert (prof.management_fee, prof.custody_fee) == (0.5, 0.1)
    assert prof.manager == "国联安基金"


@requires_pandas
async def test_akshare_profile_tolerates_missing_fields() -> None:
    """档案页的字段缺了就留空，不能整条失败，更不能填 0。"""
    p = _akshare(
        {"fund_overview_em": _overview_frame({"资产规模": "---", "跟踪标的": "该基金无跟踪标的"})}
    )
    prof = await p.fund_profile(SH_ETF)
    assert prof.asset_size is None
    assert prof.tracking_index == ""


@requires_pandas
async def test_akshare_etf_basic_uses_fund_archive() -> None:
    """个股信息接口查不到基金 —— 要走基金档案，行业一栏放跟踪标的。"""
    p = _akshare({"fund_overview_em": _overview_frame()})
    basic = await p.stock_basic(SH_ETF)
    assert basic.industry == "中证全指半导体产品与设备指数"
    assert basic.list_date == date(2019, 5, 8)
    assert [c[0] for c in p.calls] == ["fund_overview_em"]


def _holdings_frame(rows):  # type: ignore[no-untyped-def]
    import pandas as pd

    return pd.DataFrame(
        rows, columns=["序号", "股票代码", "股票名称", "占净值比例", "持股数", "持仓市值", "季度"]
    )  # noqa: E501


@requires_pandas
async def test_akshare_holdings_pick_latest_quarter() -> None:
    rows = [
        [1, "688981", "中芯国际", 9.1, 1, 1, "2026年1季度股票投资明细"],
        [2, "603501", "韦尔股份", 7.0, 1, 1, "2026年1季度股票投资明细"],
        # 最新一期放在后面，行序不代表新旧
        [1, "603501", "韦尔股份", 8.4, 1, 1, "2026年2季度股票投资明细"],
        [2, "688981", "中芯国际", 10.2, 1, 1, "2026年2季度股票投资明细"],
    ]
    p = _akshare({"fund_portfolio_hold_em": _holdings_frame(rows)})
    holdings = await p.fund_holdings(SH_ETF)

    assert [h.name for h in holdings] == ["中芯国际", "韦尔股份"], "按占比降序"
    assert holdings[0].weight_pct == 10.2
    assert {h.period for h in holdings} == {"2026年2季度"}


@requires_pandas
async def test_akshare_holdings_fall_back_to_last_year() -> None:
    """年初今年还没有季报：今年查不到就查去年。"""
    from app.core.clock import now_cst

    this_year = str(now_cst().year)
    last = _holdings_frame([[1, "688981", "中芯国际", 9.1, 1, 1, "2025年4季度股票投资明细"]])
    p = _akshare(
        {
            "fund_portfolio_hold_em": lambda symbol, date: (
                _holdings_frame([]) if date == this_year else last
            )
        }  # noqa: E501
    )
    holdings = await p.fund_holdings(SH_ETF)
    assert [h.code for h in holdings] == ["688981"]
    assert [c[1]["date"] for c in p.calls] == [this_year, str(int(this_year) - 1)]


@requires_pandas
async def test_akshare_holdings_fall_back_when_this_year_errors() -> None:
    """今年的页面还不存在时上游可能是解析报错而不是空表 —— 也要退回去年。"""
    from app.core.clock import now_cst

    this_year = str(now_cst().year)
    last = _holdings_frame([[1, "688981", "中芯国际", 9.1, 1, 1, "2025年4季度股票投资明细"]])
    p = _akshare(
        {
            "fund_portfolio_hold_em": lambda symbol, date: (
                ProviderUnavailable("No tables found") if date == this_year else last
            )
        }
    )
    assert [h.code for h in await p.fund_holdings(SH_ETF)] == ["688981"]


@requires_pandas
async def test_akshare_holdings_reports_error_when_both_years_fail() -> None:
    p = _akshare({"fund_portfolio_hold_em": ProviderUnavailable("连不上 fundf10")})
    with pytest.raises(ProviderUnavailable, match="fundf10"):
        await p.fund_holdings(SH_ETF)


@requires_pandas
async def test_akshare_etf_list_keeps_only_etfs() -> None:
    import pandas as pd

    df = pd.DataFrame(
        [
            {"代码": "512480", "名称": "半导体ETF"},
            {"代码": "159915", "名称": "创业板ETF"},
            {"代码": "501050", "名称": "某LOF"},  # 不是 ETF
            {"代码": "garbage", "名称": "?"},
        ]
    )
    p = _akshare({"fund_etf_spot_em": df})
    out = await p.etf_list()
    assert [(b.symbol, b.board) for b in out] == [("512480.SH", "ETF"), ("159915.SZ", "ETF")]


@requires_pandas
async def test_akshare_news_searches_by_keyword() -> None:
    import pandas as pd

    df = pd.DataFrame([{"新闻标题": "半导体板块走强", "发布时间": "2026-09-29 10:00:00"}])
    p = _akshare({"stock_news_em": df})
    await p.news(SH_ETF, 5, keyword="半导体")
    assert p.calls[0][1]["symbol"] == "半导体"


@requires_pandas
async def test_akshare_refuses_financials_for_etf() -> None:
    p = _akshare({})
    with pytest.raises(ProviderUnavailable, match="ETF 没有财报"):
        await p.financials(SH_ETF)
    assert p.calls == []


# ---------------------------------------------------------------- 新浪 / 雪球


def _sina(responses: dict, hq_rows: dict | None = None):  # type: ignore[no-untyped-def]
    """SinaProvider，akshare 调用查表返回；hq.sinajs.cn 也换成假的，绝不联网。

    `hq_rows` 为 None 时 hq 接口什么都没有（模拟它取不到），
    这样能单独测到"退回全部 ETF 列表"那条路。
    """
    from app.data import sina_hq
    from app.data.backup_providers import SinaProvider

    provider = SinaProvider()
    calls: list[str] = []

    async def fake_hq(codes):  # type: ignore[no-untyped-def]
        calls.append("hq:" + ",".join(codes))
        return {c: v for c, v in (hq_rows or {}).items() if c in codes}

    provider._hq = sina_hq.Batcher(fetcher=fake_hq, window=0)  # type: ignore[attr-defined]

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(func_name)
        if func_name not in responses:
            raise AssertionError(f"没预料到的调用：{func_name}({kwargs})")
        return responses[func_name]

    provider._call = fake_call  # type: ignore[assignment, method-assign]
    provider.calls = calls  # type: ignore[attr-defined]
    return provider


def _sina_etf_table():  # type: ignore[no-untyped-def]
    import pandas as pd

    return pd.DataFrame(
        [
            {"代码": "sh512480", "名称": "半导体ETF", "最新价": 0.873, "涨跌额": 0.004,
             "涨跌幅": 0.46, "买入": 0.872, "卖出": 0.873, "昨收": 0.869, "今开": 0.870,
             "最高": 0.880, "最低": 0.866, "成交量": 512345600, "成交额": 4.46e8},
            {"代码": "sz159915", "名称": "创业板ETF", "最新价": 0.0, "涨跌额": 0.0,
             "涨跌幅": 0.0, "买入": 0.0, "卖出": 0.0, "昨收": 2.300, "今开": 0.0,
             "最高": 0.0, "最低": 0.0, "成交量": 0, "成交额": 0},
        ]
    )  # fmt: skip


@requires_pandas
async def test_sina_etf_quote_falls_back_to_list() -> None:
    """hq 接口取不到这只 ETF 时，退回新浪的全部 ETF 列表。"""
    from app.data.cache import cache

    cache.clear()
    p = _sina({"fund_etf_category_sina": _sina_etf_table()})
    q = await p.quote(SH_ETF)
    assert (q.name, q.price, q.prev_close, q.change_pct) == ("半导体ETF", 0.873, 0.869, 0.46)
    assert q.pe_ttm is None
    assert p.calls == ["hq:sh512480", "fund_etf_category_sina"]


@requires_pandas
async def test_sina_etf_table_is_shared_across_quotes() -> None:
    """一次下载带齐所有 ETF 的价格：自选里有几只 ETF 也只该请求一次。"""
    from app.data.cache import cache

    cache.clear()
    p = _sina({"fund_etf_category_sina": _sina_etf_table()})
    await p.quote(SH_ETF)
    await p.quote(SZ_ETF)
    assert [c for c in p.calls if not c.startswith("hq:")] == ["fund_etf_category_sina"]


@requires_pandas
async def test_sina_etf_quote_falls_back_to_prev_close() -> None:
    """开盘前 / 停牌时最新价是 0，用昨收兜底，不能显示 0.000。"""
    from app.data.cache import cache

    cache.clear()
    p = _sina({"fund_etf_category_sina": _sina_etf_table()})
    q = await p.quote(SZ_ETF)
    assert q.price == 2.300


@requires_pandas
async def test_sina_stock_quote_never_touches_the_etf_list() -> None:
    """股票取不到实时价就如实失败，不能去 ETF 列表里瞎找。"""
    p = _sina({})
    with pytest.raises(ProviderUnavailable, match="没有 600519.SH"):
        await p.quote(normalize("600519"))
    assert p.calls == ["hq:sh600519"]


@requires_pandas
async def test_sina_etf_list() -> None:
    from app.data.cache import cache

    cache.clear()
    p = _sina({"fund_etf_category_sina": _sina_etf_table()})
    out = await p.etf_list()
    assert {(b.symbol, b.name) for b in out} == {
        ("512480.SH", "半导体ETF"),
        ("159915.SZ", "创业板ETF"),
    }  # noqa: E501


def _sina_hist(closes: list[float]):  # type: ignore[no-untyped-def]
    import pandas as pd

    start = date(2026, 9, 1)
    return pd.DataFrame(
        [
            {"date": start + timedelta(days=i), "open": c, "high": c, "low": c, "close": c,
             "volume": 1e6}
            for i, c in enumerate(closes)
        ]
    )  # fmt: skip


@requires_pandas
async def test_sina_etf_bars_fill_change_pct() -> None:
    p = _sina({"fund_etf_hist_sina": _sina_hist([1.000, 1.010, 0.999])})
    bars = await p.daily_bars(SH_ETF, date(2026, 9, 1), date(2026, 9, 30))
    assert [b.change_pct for b in bars] == [None, 1.0, -1.09]


@requires_pandas
async def test_sina_etf_bars_drop_history_before_a_split() -> None:
    """新浪的 ETF 日线不复权。份额折算后价格腰斩，技术指标会把它当成暴跌 ——
    只保留断层之后的数据。"""
    p = _sina({"fund_etf_hist_sina": _sina_hist([2.00, 2.02, 2.04, 1.03, 1.04])})
    bars = await p.daily_bars(SH_ETF, date(2026, 9, 1), date(2026, 9, 30))
    assert [b.close for b in bars] == [1.03, 1.04]


@requires_pandas
async def test_sina_etf_bars_keep_a_normal_limit_move() -> None:
    """20% 涨跌幅限制的 ETF 真的涨停一天，不能被当成拆分截掉。"""
    p = _sina({"fund_etf_hist_sina": _sina_hist([1.00, 1.20, 1.21])})
    bars = await p.daily_bars(normalize("588000"), date(2026, 9, 1), date(2026, 9, 30))
    assert len(bars) == 3


@requires_pandas
async def test_xueqiu_etf_quote_has_no_valuation() -> None:
    from app.data.backup_providers import XueqiuProvider

    p = XueqiuProvider()

    async def fake_call(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["symbol"] == "SH512480"
        return _kv({"名称": "半导体ETF", "现价": 0.873, "昨收": 0.869, "市盈率(TTM)": 35.2})

    p._call = fake_call  # type: ignore[assignment, method-assign]
    q = await p.quote(SH_ETF)
    assert q.price == 0.873
    assert q.pe_ttm is None


# ---------------------------------------------------------------- 快照与简报


async def test_snapshot_for_etf_has_fund_data_and_no_financials(mock_registry) -> None:  # type: ignore[no-untyped-def]
    from app.services.market_data import MarketDataService

    snap = await MarketDataService(mock_registry).snapshot(SH_ETF)

    assert snap.fund_profile is not None
    assert snap.fund_profile.tracking_index
    assert len(snap.holdings) == 10
    assert snap.financials == []
    assert not any("财务" in w for w in snap.warnings), "ETF 不该去拉财报，更不该报财报缺失"
    assert snap.news_keyword == "半导体"
    assert snap.basic is not None and snap.basic.name == "半导体ETF"


@pytest.mark.parametrize(
    ("name", "keyword"),
    [
        ("半导体ETF", "半导体"),
        ("有色金属ETF", "有色金属"),
        ("芯片ETF华夏", "芯片"),
        ("沪深300ETF", "沪深300"),
        ("A50ETF", "A50"),
        ("ETF", ""),  # 取不出板块词就不用，退回按代码搜
        ("", ""),
    ],
)
def test_etf_news_keyword(name: str, keyword: str) -> None:
    from app.services.market_data import etf_news_keyword

    assert etf_news_keyword(name) == keyword


async def _ctx(mock_registry, code: str):  # type: ignore[no-untyped-def]
    from app.agents.context import AnalysisContext
    from app.services.market_data import MarketDataService

    snap = await MarketDataService(mock_registry).snapshot(normalize(code))
    return AnalysisContext.build(snap)


async def test_etf_brief_replaces_stock_sections(mock_registry) -> None:  # type: ignore[no-untyped-def]
    brief = (await _ctx(mock_registry, "512480")).market_brief()

    assert "品种：**ETF" in brief
    assert "## ETF 概况" in brief
    assert "大重仓股" in brief
    assert "本标的是 ETF：分析要点" in brief
    assert "板块新闻（按关键词“半导体”" in brief
    # 个股专属的东西不该出现：ETF 没有这些，列出来只会让模型对着"数据缺失"硬解释
    assert "市盈率" not in brief.split("## 本标的是 ETF")[0]
    assert "财务数据" not in brief


async def test_etf_brief_prices_have_three_decimals(mock_registry) -> None:  # type: ignore[no-untyped-def]
    ctx = await _ctx(mock_registry, "512480")
    price = ctx.snapshot.quote.price
    assert f"- 最新价：{price:.3f}".rstrip("0").rstrip(".") in ctx.market_brief()
    assert all(round(lv.price, 3) == lv.price for lv in ctx.indicators.fib.levels)


async def test_stock_brief_is_unchanged(mock_registry) -> None:  # type: ignore[no-untyped-def]
    brief = (await _ctx(mock_registry, "600519")).market_brief()
    assert "市盈率(TTM)" in brief
    assert "## 财务数据" in brief
    assert "ETF" not in brief


async def test_t0_hint_for_gold_etf(mock_registry) -> None:  # type: ignore[no-untyped-def]
    brief = (await _ctx(mock_registry, "518880")).market_brief()
    assert "可能是 T+0 品种" in brief


async def test_equity_etf_is_t1(mock_registry) -> None:  # type: ignore[no-untyped-def]
    brief = (await _ctx(mock_registry, "512400")).market_brief()
    assert "本只为 **T+1**" in brief


@pytest.mark.parametrize(
    ("name", "t0"),
    [
        ("黄金ETF", True),
        ("国债ETF", True),
        ("纳指ETF", True),
        ("恒生科技ETF", True),
        # 持有 A股金矿股，是股票类 ETF —— 别被"黄金"两个字骗了
        ("黄金股ETF", False),
        ("半导体ETF", False),
    ],
)
def test_settlement_rule_hint(name: str, t0: bool) -> None:
    from app.agents.context import _etf_settlement_rule

    assert ("可能是 T+0" in _etf_settlement_rule(name, None)) is t0


async def test_snapshot_fetches_quote_once_without_directory_name() -> None:
    """目录里没有这只 ETF 时要先拿行情取名称 —— 但只能取一次。

    成功时第二次会命中缓存，看不出问题；**失败时缓存不存异常**，
    共用不了同一次结果的话，整条降级链（每个源最多 12 秒）会被跑两遍。
    """
    from app.data.cache import cache
    from app.data.mock_provider import MockProvider
    from app.data.registry import ProviderRegistry
    from app.services.market_data import MarketDataService

    class _Broken(MockProvider):
        quotes = 0

        async def quote(self, symbol):  # type: ignore[no-untyped-def]
            _Broken.quotes += 1
            raise ProviderUnavailable("行情挂了")

        async def daily_bars(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise ProviderUnavailable("日线也挂了")

    cache.clear()
    service = MarketDataService(ProviderRegistry([_Broken()], timeout=5))
    with pytest.raises(DataNotFound):
        await service.snapshot(SH_ETF)
    assert _Broken.quotes == 1


def test_fib_rounds_to_asset_precision() -> None:
    from app.services.indicators import compute_snapshot

    start = date(2026, 6, 1)
    bars = [
        Bar(date=start + timedelta(days=i), open=c, high=c + 0.0137, low=c - 0.0121, close=c)
        for i, c in enumerate(0.8 + 0.0031 * i for i in range(40))
    ]
    two = compute_snapshot(bars).fib
    three = compute_snapshot(bars, price_decimals=3).fib
    assert all(round(lv.price, 2) == lv.price for lv in two.levels)
    assert any(round(lv.price, 2) != lv.price for lv in three.levels), "三位小数时要保留第三位"


# ---------------------------------------------------------------- 智能体


async def test_fundamental_analyst_switches_prompt_for_etf(mock_registry) -> None:  # type: ignore[no-untyped-def]
    from app.agents.prompts import FUNDAMENTAL_ANALYST, FUNDAMENTAL_ANALYST_ETF
    from app.agents.roster import FundamentalAnalyst, TechnicalAnalyst
    from app.llm.echo_client import EchoLLM

    etf_ctx = await _ctx(mock_registry, "512480")
    stock_ctx = await _ctx(mock_registry, "600519")
    agent = FundamentalAnalyst(EchoLLM())

    assert agent.role_prompt_for(etf_ctx) is FUNDAMENTAL_ANALYST_ETF
    assert agent.role_prompt_for(stock_ctx) is FUNDAMENTAL_ANALYST
    # 其他角色不分品种
    tech = TechnicalAnalyst(EchoLLM())
    assert tech.role_prompt_for(etf_ctx) == tech.role_prompt_for(stock_ctx)


async def test_etf_pipeline_runs_end_to_end(mock_registry) -> None:  # type: ignore[no-untyped-def]
    from app.agents.orchestrator import AnalysisOrchestrator
    from app.core.config import Settings
    from app.llm.echo_client import EchoLLM

    ctx = await _ctx(mock_registry, "512480")
    settings = Settings(llm_provider="echo", debate_rounds=1, data_provider_order=["mock"])
    events = [e async for e in AnalysisOrchestrator(EchoLLM(), settings).run(ctx)]
    types = [e.type for e in events]
    assert types[-1] == "run_done"
    assert "error" not in types
    outputs = {e.data["agent"]: e.data["output"] for e in events if e.type == "agent_done"}
    assert outputs["fundamental_analyst"]["content"], "ETF 的基本面分析师要有产出"


# ---------------------------------------------------------------- 代码目录


class _ListOnly:
    """只提供一种全市场列表的源。"""

    def __init__(self, cap: str, items: list[StockBasic] | None, name: str) -> None:
        self.name = name
        self.capabilities = frozenset({cap})
        self._items = items

    async def healthcheck(self) -> bool:
        return True

    async def _list(self, *_a, **_k):  # type: ignore[no-untyped-def]
        if self._items is None:
            raise ProviderUnavailable("列表接口挂了")
        return list(self._items)

    search = _list
    etf_list = _list


STOCKS = [StockBasic(symbol="600519.SH", name="贵州茅台", exchange="SH")]
ETFS = [
    StockBasic(symbol="512480.SH", name="半导体ETF", exchange="SH", board="ETF"),
    StockBasic(symbol="512400.SH", name="有色金属ETF", exchange="SH", board="ETF"),
]


@pytest.fixture
def _directory():  # type: ignore[no-untyped-def]
    async def setup(providers):  # type: ignore[no-untyped-def]
        from sqlalchemy import delete

        import app.data.registry as reg
        from app.data.registry import ProviderRegistry
        from app.db.models import SymbolDirectory
        from app.db.session import get_sessionmaker, init_db
        from app.services import symbol_index

        await init_db()
        async with get_sessionmaker()() as session:
            await session.execute(delete(SymbolDirectory))
            await session.commit()
        symbol_index.reset_state()
        reg._registry = ProviderRegistry(providers, timeout=5)

    return setup


async def test_directory_includes_etfs(_directory) -> None:  # type: ignore[no-untyped-def]
    from app.services import symbol_index

    await _directory([_ListOnly("search", STOCKS, "s"), _ListOnly("etf_list", ETFS, "e")])
    await symbol_index.refresh()

    st = await symbol_index.status()
    assert (st.count, st.etf_count) == (3, 2)
    hits = await symbol_index.lookup("半导体")
    assert [(h.symbol, h.board) for h in hits] == [("512480.SH", "ETF")]


async def test_directory_keeps_stocks_when_etf_list_fails(_directory) -> None:  # type: ignore[no-untyped-def]
    from app.services import symbol_index

    await _directory([_ListOnly("search", STOCKS, "s"), _ListOnly("etf_list", None, "e")])
    await symbol_index.refresh()
    st = await symbol_index.status()
    assert (st.count, st.etf_count) == (1, 0)


async def test_directory_keeps_etfs_when_stock_list_fails(_directory) -> None:  # type: ignore[no-untyped-def]
    from app.services import symbol_index

    await _directory([_ListOnly("search", None, "s"), _ListOnly("etf_list", ETFS, "e")])
    await symbol_index.refresh()
    assert (await symbol_index.status()).etf_count == 2


async def test_directory_refresh_fails_only_when_both_lists_fail(_directory) -> None:  # type: ignore[no-untyped-def]
    from app.services import symbol_index

    await _directory([_ListOnly("search", None, "s"), _ListOnly("etf_list", None, "e")])
    with pytest.raises(DataNotFound, match="股票列表.*ETF 列表"):
        await symbol_index.refresh()


async def test_nameless_quote_gets_directory_name(_directory) -> None:  # type: ignore[no-untyped-def]
    """东财的 ETF 行情不带名称（刻意不去查档案页），名称从本地目录补。"""
    from app.services import symbol_index
    from app.services.quote_source import quote_with_fallback

    class _Nameless(_EtfQuoteOnly):
        async def quote(self, symbol):  # type: ignore[no-untyped-def]
            return Quote(symbol=symbol.canonical, name="", price=0.873)

    await _directory([_ListOnly("etf_list", ETFS, "e")])
    await symbol_index.refresh()

    from app.data.registry import ProviderRegistry

    q = await quote_with_fallback(ProviderRegistry([_Nameless()], timeout=5), SH_ETF)
    assert q.name == "半导体ETF"


async def test_upgrade_backfills_etfs_on_startup(_directory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """老版本建的目录没有 ETF，也还没过期 —— 启动时要补，不能等 24 小时。"""
    from app.core.config import get_settings
    from app.services import symbol_index

    await _directory([_ListOnly("search", STOCKS, "s")])
    await symbol_index.refresh()
    assert (await symbol_index.status()).etf_count == 0

    scheduled: list[bool] = []
    monkeypatch.setattr(symbol_index, "schedule_refresh", lambda: scheduled.append(True) or True)
    monkeypatch.setenv("SYMBOL_INDEX_AUTOREFRESH", "true")
    get_settings.cache_clear()
    try:
        await symbol_index.refresh_if_needed()
    finally:
        get_settings.cache_clear()
    assert scheduled == [True]


# ---------------------------------------------------------------- HTTP 接口


def test_search_finds_etf_by_code(client) -> None:  # type: ignore[no-untyped-def]
    hits = client.get("/api/v1/market/search", params={"q": "512480"}).json()
    assert hits[0]["symbol"] == "512480.SH"
    assert hits[0]["board"] == "ETF"


def test_quote_marks_etf(client) -> None:  # type: ignore[no-untyped-def]
    q = client.get("/api/v1/market/quote/512480").json()
    assert q["asset_type"] == "etf"
    assert q["price_decimals"] == 3
    assert q["pe_ttm"] is None

    stock = client.get("/api/v1/market/quote/600519").json()
    assert (stock["asset_type"], stock["price_decimals"]) == ("stock", 2)


def test_fund_endpoint(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/market/fund/512480").json()
    assert body["profile"]["tracking_index"]
    assert len(body["holdings"]) == 10
    assert body["warnings"] == []


def test_fund_endpoint_rejects_stock(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/v1/market/fund/600519")
    assert resp.status_code == 400
    assert "不是 ETF" in resp.json()["detail"]


def test_watchlist_accepts_etf(client) -> None:  # type: ignore[no-untyped-def]
    created = client.post("/api/v1/watchlist", json={"symbol": "512400"})
    assert created.status_code == 201
    assert created.json()["name"] == "有色金属ETF"
    items = client.get("/api/v1/watchlist").json()
    etf = next(i for i in items if i["symbol"] == "512400.SH")
    assert etf["quote"]["asset_type"] == "etf"
    client.delete(f"/api/v1/watchlist/{created.json()['id']}")


def test_analysis_runs_for_etf(client) -> None:  # type: ignore[no-untyped-def]
    import time

    run_id = client.post("/api/v1/analysis/runs", json={"symbol": "512480"}).json()["run_id"]
    deadline = time.time() + 20
    while time.time() < deadline:
        run = client.get(f"/api/v1/analysis/runs/{run_id}").json()
        if run["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert run["status"] == "done", run["error"]
    assert run["name"] == "半导体ETF"
    assert run["decision"]["action"]
