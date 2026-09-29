"""A股代码规范化。

各家数据源对同一只股票的写法完全不同，这里统一成内部 `Symbol`，
再由各适配器转成自己要的格式：

    输入      600519 / sh600519 / SH600519 / 600519.SH / 600519.sh
    Tushare   600519.SH
    AKShare   600519          （多数接口用 6 位纯代码）
    东财 secid 1.600519        （AKShare 部分接口透传东财参数时用到）
    Baostock  sh.600519
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.core.errors import SymbolError

Exchange = Literal["SH", "SZ", "BJ"]

_CLEAN_RE = re.compile(r"[^0-9A-Za-z]")

# 板块前缀 -> 交易所。按最长前缀优先匹配。
_PREFIX_RULES: tuple[tuple[str, Exchange], ...] = (
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
    def board(self) -> str:
        """所属板块中文名，用于给模型的上下文和前端展示。"""
        for prefix, name in _BOARD_NAMES:
            if self.code.startswith(prefix):
                return name
        return "未知板块"

    @property
    def price_limit_pct(self) -> float:
        """涨跌幅限制（%）。风控智能体会用到——A股不同板块规则不一样。

        注意：这里只覆盖常规交易日的普通股票。新股上市前 5 日、ST 股（±5%）、
        退市整理期等特殊情形需要结合实时数据另行判断。
        """
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
