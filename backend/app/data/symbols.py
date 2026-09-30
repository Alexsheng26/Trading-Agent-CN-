"""A股代码规范化。

各家数据源对同一只股票的写法完全不同，这里统一成内部 `Symbol`，
再由各适配器转成自己要的格式：

    输入      600519 / sh600519 / SH600519 / 600519.SH / 600519.sh
    Tushare   600519.SH
    AKShare   600519          （多数接口用 6 位纯代码）
    东财 secid 1.600519        （AKShare 部分接口透传东财参数时用到）
    Baostock  sh.600519

除了股票，也认场内 ETF（见 `ETF_PREFIXES`）：

    沪市  51xxxx / 52xxxx / 56xxxx / 58xxxx   如 512480 半导体ETF
    深市  159xxx                              如 159915 创业板ETF

ETF 的代码写法和股票一样，但很多数据接口不通用（没有财报、估值；
部分行情接口按"6 开头"判断沪市，会把 5 开头的沪市 ETF 当成深市），
所以 `Symbol.asset_type` 要一路带到数据层和分析层。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.core.errors import SymbolError

Exchange = Literal["SH", "SZ", "BJ"]
AssetType = Literal["stock", "etf"]

_CLEAN_RE = re.compile(r"[^0-9A-Za-z]")

#: 场内 ETF 的代码段。LOF（沪市 50、深市 16 开头）和封闭式基金不在其中 ——
#: 它们的数据接口又是另一套，没接入之前宁可明确报"不支持"，也不要当成股票去查。
ETF_PREFIXES: dict[str, tuple[str, ...]] = {
    "SH": ("51", "52", "56", "58"),
    "SZ": ("159",),
}

# 板块前缀 -> 交易所。按最长前缀优先匹配。
_PREFIX_RULES: tuple[tuple[str, Exchange], ...] = (
    ("51", "SH"),  # ETF
    ("52", "SH"),
    ("56", "SH"),
    ("58", "SH"),  # 含科创板 ETF（588/589）
    ("159", "SZ"),  # 深市 ETF
    ("920", "BJ"),  # 北交所新代码段
    ("688", "SH"),  # 科创板
    ("689", "SH"),  # 科创板 CDR
    ("605", "SH"),  # 主板
    ("603", "SH"),
    ("601", "SH"),
    ("600", "SH"),
    ("900", "SH"),  # 沪市B股
    ("300", "SZ"),  # 创业板
    ("301", "SZ"),
    ("302", "SZ"),
    ("000", "SZ"),  # 深市主板
    ("001", "SZ"),
    ("002", "SZ"),  # 原中小板
    ("003", "SZ"),
    ("004", "SZ"),
    ("200", "SZ"),  # 深市B股
    ("83", "BJ"),   # 北交所
    ("87", "BJ"),
    ("88", "BJ"),
    ("43", "BJ"),
)

_BOARD_NAMES: tuple[tuple[str, str], ...] = (
    ("688", "科创板"),
    ("689", "科创板"),
    ("300", "创业板"),
    ("301", "创业板"),
    ("302", "创业板"),
    ("002", "深证主板"),
    ("003", "深证主板"),
    ("000", "深证主板"),
    ("001", "深证主板"),
    ("60", "上证主板"),
    ("920", "北交所"),
    ("83", "北交所"),
    ("87", "北交所"),
    ("88", "北交所"),
    ("43", "北交所"),
    ("900", "B股"),
    ("200", "B股"),
)


@dataclass(frozen=True, slots=True)
class Symbol:
    """内部统一的股票标识。"""

    code: str  # 6 位数字代码，如 "600519"
    exchange: Exchange  # SH / SZ / BJ

    def __str__(self) -> str:
        return self.canonical

    @property
    def canonical(self) -> str:
        """对外 API 与数据库里使用的规范形式，如 `600519.SH`。"""
        return f"{self.code}.{self.exchange}"

    @property
    def tushare(self) -> str:
        return self.canonical

    @property
    def akshare(self) -> str:
        return self.code

    @property
    def em_secid(self) -> str:
        """东方财富 secid：沪市/北交所 1.，深市 0.。"""
        market = "1" if self.exchange in ("SH", "BJ") else "0"
        return f"{market}.{self.code}"

    @property
    def baostock(self) -> str:
        return f"{self.exchange.lower()}.{self.code}"

    @property
    def xueqiu(self) -> str:
        """雪球格式：`SH600519`。"""
        return f"{self.exchange}{self.code}"

    @property
    def sina(self) -> str:
        """新浪/腾讯格式：`sh600519`。"""
        return f"{self.exchange.lower()}{self.code}"

    @property
    def asset_type(self) -> AssetType:
        """品种：股票还是 ETF。决定走哪套数据接口、简报怎么写。"""
        if self.code.startswith(ETF_PREFIXES.get(self.exchange, ())):
            return "etf"
        return "stock"

    @property
    def is_etf(self) -> bool:
        return self.asset_type == "etf"

    @property
    def price_decimals(self) -> int:
        """价格精度。ETF 最小变动 0.001 元，股票 0.01 元。

        ETF 价格常在 1 元上下，按两位小数显示的话 0.873 和 0.878 看起来一样，
        支撑阻力位、止损价全都失真。
        """
        return 3 if self.is_etf else 2

    @property
    def board(self) -> str:
        """所属板块中文名，用于给模型的上下文和前端展示。"""
        if self.is_etf:
            return "ETF"
        for prefix, name in _BOARD_NAMES:
            if self.code.startswith(prefix):
                return name
        return "未知板块"

    @property
    def price_limit_pct(self) -> float:
        """涨跌幅限制（%）。风控智能体会用到——A股不同板块规则不一样。

        注意：这里只覆盖常规交易日的普通股票。新股上市前 5 日、ST 股（±5%）、
        退市整理期等特殊情形需要结合实时数据另行判断。

        ETF 跟随其跟踪的市场：科创板 ETF（588/589）±20%；创业板 ETF 也是 ±20%，
        但它们和其他深市 ETF 共用 159 代码段，光看代码分不出来 ——
        这里按 ±10% 给，简报里会另外写明这条规则让模型自己判断。
        """
        if self.is_etf:
            return 20.0 if self.code.startswith(("588", "589")) else 10.0
        if self.exchange == "BJ":
            return 30.0
        if self.code.startswith(("688", "689", "300", "301", "302")):
            return 20.0
        return 10.0


def normalize(raw: str) -> Symbol:
    """把用户/数据源的任意写法解析成 `Symbol`。

    >>> normalize("sh600519").canonical
    '600519.SH'
    >>> normalize("000001").canonical
    '000001.SZ'
    """
    if raw is None:
        raise SymbolError("股票代码不能为空")

    token = _CLEAN_RE.sub("", str(raw)).upper()
    if not token:
        raise SymbolError(f"无法识别的股票代码：{raw!r}")

    explicit: Exchange | None = None

    # 形如 600519SH（来自 600519.SH）
    if token[-2:] in ("SH", "SZ", "BJ") and token[:-2].isdigit():
        explicit = token[-2:]  # type: ignore[assignment]
        token = token[:-2]
    # 形如 SH600519
    elif token[:2] in ("SH", "SZ", "BJ") and token[2:].isdigit():
        explicit = token[:2]  # type: ignore[assignment]
        token = token[2:]

    if not token.isdigit():
        raise SymbolError(f"无法识别的股票代码：{raw!r}")

    code = token.zfill(6)
    if len(code) != 6:
        raise SymbolError(f"A股代码应为 6 位数字，收到 {raw!r}")

    if explicit is not None:
        return Symbol(code=code, exchange=explicit)

    inferred = _infer_exchange(code)
    if inferred is None:
        if code.startswith(("50", "16")):
            raise SymbolError(
                f"{code} 看起来是 LOF 或封闭式基金，目前只支持股票和场内 ETF"
            )
        raise SymbolError(
            f"无法从代码 {code} 推断交易所，"
            f"请显式写成 {code}.SH / {code}.SZ / {code}.BJ"
        )
    return Symbol(code=code, exchange=inferred)


def _infer_exchange(code: str) -> Exchange | None:
    for prefix, exchange in sorted(_PREFIX_RULES, key=lambda r: -len(r[0])):
        if code.startswith(prefix):
            return exchange
    return None


def try_normalize(raw: str) -> Symbol | None:
    """解析失败返回 None，用于搜索等容错场景。"""
    try:
        return normalize(raw)
    except SymbolError:
        return None
