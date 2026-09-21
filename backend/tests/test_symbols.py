"""A股代码规范化。数据源格式各不相同，这里错了后面全错。"""

from __future__ import annotations

import pytest

from app.core.errors import SymbolError
from app.data.symbols import normalize, try_normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519.SH"),
        ("sh600519", "600519.SH"),
        ("SH600519", "600519.SH"),
        ("600519.SH", "600519.SH"),
        ("600519.sh", "600519.SH"),
        ("  600519  ", "600519.SH"),
        ("000001", "000001.SZ"),
        ("sz000001", "000001.SZ"),
        ("300750", "300750.SZ"),
        ("688981", "688981.SH"),
        ("002594", "002594.SZ"),
        ("430047", "430047.BJ"),
        ("920819", "920819.BJ"),
        ("1", "000001.SZ"),  # 补零
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw).canonical == expected


@pytest.mark.parametrize("raw", ["", "   ", "abcdef", "股票", None])
def test_normalize_rejects_garbage(raw) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SymbolError):
        normalize(raw)


def test_unknown_prefix_requires_explicit_exchange() -> None:
    # 999 不属于任何已知板块，必须显式指定交易所
    with pytest.raises(SymbolError):
        normalize("999999")
    assert normalize("999999.SH").canonical == "999999.SH"


def test_provider_specific_formats() -> None:
    sym = normalize("600519")
    assert sym.tushare == "600519.SH"
    assert sym.akshare == "600519"
    assert sym.em_secid == "1.600519"  # 沪市 -> 1.
    assert sym.baostock == "sh.600519"

    sz = normalize("000001")
    assert sz.em_secid == "0.000001"  # 深市 -> 0.


@pytest.mark.parametrize(
    ("code", "board", "limit"),
    [
        ("600519", "上证主板", 10.0),
        ("000001", "深证主板", 10.0),
        ("300750", "创业板", 20.0),
        ("688981", "科创板", 20.0),
        ("430047", "北交所", 30.0),
        ("920819", "北交所", 30.0),
    ],
)
def test_board_and_price_limit(code: str, board: str, limit: float) -> None:
    """涨跌幅限制会直接影响风控给的止损距离，不能算错。"""
    sym = normalize(code)
    assert sym.board == board
    assert sym.price_limit_pct == limit


def test_try_normalize_returns_none() -> None:
    assert try_normalize("乱码") is None
    assert try_normalize("600519") is not None
