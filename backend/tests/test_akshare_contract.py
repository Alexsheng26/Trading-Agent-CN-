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
    "stock_individual_info_em": ("symbol",),
    "stock_zh_a_hist": ("symbol", "period", "start_date", "end_date", "adjust"),
    "stock_financial_abstract": ("symbol",),
    "stock_news_em": ("symbol",),
    "stock_individual_fund_flow": ("stock", "market"),
    "stock_zh_index_spot_em": ("symbol",),
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
