"""AKShare 接口契约。

AKShare 迭代很快（版本号每周都在动），接口改名、参数改名都发生过。
一旦对不上，用户那边的表现就是"数据全是假的"或"搜索出不来" ——
而且只有在真机联网时才暴露，很难排查。

这里不联网，只检查两件事：
  1. 我们调用的函数在装好的 akshare 里**确实存在**；
  2. 我们传的关键字参数**确实被接受**。

装了 akshare 才跑（可选依赖）。CI 的 artifact 任务只装运行时依赖，会跳过。
"""

from __future__ import annotations

import importlib.util
import inspect

import pytest

requires_akshare = pytest.mark.skipif(
    importlib.util.find_spec("akshare") is None,
    reason="未安装可选依赖 akshare",
)

#: 函数名 -> 我们实际传入的关键字参数
CALLS: dict[str, tuple[str, ...]] = {
    "stock_zh_a_spot_em": (),
    # 单只行情：quote() 现在走这个，不再走全市场快照
    "stock_bid_ask_em": ("symbol",),
    # 市盈率/市净率（盘口接口不给估值）
    "stock_value_em": ("symbol",),
    "stock_individual_info_em": ("symbol",),
    "stock_zh_a_hist": ("symbol", "period", "start_date", "end_date", "adjust"),
    # 60 分钟线（2 小时图由它合成）
    "stock_zh_a_hist_min_em": ("symbol", "start_date", "end_date", "period", "adjust"),
    "stock_financial_abstract": ("symbol",),
    "stock_news_em": ("symbol",),
    "stock_individual_fund_flow": ("stock", "market"),
    "stock_zh_index_spot_em": ("symbol",),
    # ---- ETF ----
    "fund_etf_spot_em": (),
    "fund_etf_hist_em": ("symbol", "period", "start_date", "end_date", "adjust"),
    "fund_etf_hist_min_em": ("symbol", "start_date", "end_date", "period", "adjust"),
    "fund_overview_em": ("symbol",),
    "fund_portfolio_hold_em": ("symbol", "date"),
    # 新浪（backup_providers.SinaProvider）
    "fund_etf_category_sina": ("symbol",),
    "fund_etf_hist_sina": ("symbol",),
    "stock_zh_a_minute": ("symbol", "period", "adjust"),
    # 雪球（backup_providers.XueqiuProvider），股票和场内基金共用
    "stock_individual_spot_xq": ("symbol", "token"),
}


@requires_akshare
@pytest.mark.parametrize("name", sorted(CALLS))
def test_function_exists(name: str) -> None:
    import akshare

    assert hasattr(akshare, name), (
        f"akshare {akshare.__version__} 里没有 {name} —— 接口大概被改名了，"
        "检查 app/data/akshare_provider.py"
    )


@requires_akshare
@pytest.mark.parametrize(("name", "kwargs"), sorted(CALLS.items()))
def test_keyword_arguments_are_accepted(name: str, kwargs: tuple[str, ...]) -> None:
    import akshare

    fn = getattr(akshare, name)
    params = inspect.signature(fn).parameters
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    for kw in kwargs:
        assert kw in params or accepts_kwargs, f"{name} 不接受参数 {kw}=，签名是 {params.keys()}"


@requires_akshare
def test_index_symbol_choice_is_valid() -> None:
    """index_quotes 传的是 "沪深重要指数"，这是个枚举值，打错就整个大盘取不到。"""
    import akshare

    doc = inspect.getdoc(akshare.stock_zh_index_spot_em) or ""
    assert "沪深重要指数" in doc, "该取值已不在 akshare 支持的列表里"


# ---------------------------------------------------------------- ETF 列名

#: 函数 -> 我们按名字取的列。这些接口的列名是在 akshare 源码里硬编码 rename 的，
#: 所以能不联网核对：列名出现在源码里，才说明我们取得到。
ETF_COLUMNS: dict[str, tuple[str, ...]] = {
    "fund_etf_spot_em": ("代码", "名称"),
    "fund_etf_hist_em": ("日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌幅"),
    "fund_portfolio_hold_em": ("股票代码", "股票名称", "占净值比例", "季度"),
    "fund_etf_category_sina": (
        "代码", "名称", "最新价", "涨跌幅", "昨收", "今开", "最高", "最低", "成交量", "成交额",
    ),  # fmt: skip
    "fund_etf_hist_sina": ("date", "open", "high", "low", "close", "volume"),
}


@requires_akshare
@pytest.mark.parametrize(("name", "columns"), sorted(ETF_COLUMNS.items()))
def test_etf_columns_exist_in_source(name: str, columns: tuple[str, ...]) -> None:
    import akshare

    source = inspect.getsource(getattr(akshare, name))
    missing = [c for c in columns if f'"{c}"' not in source]
    assert not missing, f"{name} 的源码里找不到列 {missing} —— 上游改了列名"


@requires_akshare
def test_sina_etf_category_choice_is_valid() -> None:
    """ETF 列表传的是 "ETF基金"，这是个枚举值，打错就一只 ETF 都取不到。"""
    import akshare

    assert '"ETF基金"' in inspect.getsource(akshare.fund_etf_category_sina)


@requires_akshare
def test_eastmoney_bid_ask_still_misroutes_sh_etfs() -> None:
    """我们在 AkshareProvider.quote 里直接跳过沪市 ETF，前提是东财盘口接口
    仍然只按 "6 开头" 判断沪市。哪天它修好了，这条会失败 —— 那时就可以把跳过去掉。"""
    import akshare

    source = inspect.getsource(akshare.stock_bid_ask_em)
    assert 'startswith("6")' in source
