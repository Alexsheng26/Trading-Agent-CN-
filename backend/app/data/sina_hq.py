"""新浪实时行情（hq.sinajs.cn）：不需要 token，一个请求能取多只。

**为什么要它**

用户机器上股票的实时价一直拿不到，盘中看到的是上一交易日的收盘价：

    东财盘口（akshare）   push2.eastmoney.com 返回空响应
    雪球                 要登录凭证（xq_a_token），而且会过期
    Tushare              日线接口，本来就不是实时的

而新浪的主机在那台机器上是通的（日线、ETF 行情都走新浪）。这个接口：
  - 不需要任何 token；
  - 股票、ETF 同一种格式；
  - `list=` 后面可以跟一串代码，一次请求取多只。

**批量是刻意的**：首页自选股每 5 秒轮询一次，20 只票逐只请求就是每秒 4 个请求，
新浪对高频访问会封 IP。`Batcher` 把同一时刻到达的请求合并成一个。

**响应格式**（GBK 编码，每只一行）：

    var hq_str_sh600519="贵州茅台,1430.00,1428.00,1432.50,...,2026-09-30,15:00:03,00,";

字段按下标：0 名称 / 1 今开 / 2 昨收 / 3 现价 / 4 最高 / 5 最低 / 6 买一 / 7 卖一 /
8 成交量（股）/ 9 成交额（元）/ 10-29 五档盘口 / 30 日期 / 31 时间。
代码不存在时返回空串：`var hq_str_sh999999="";`
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.core.clock import CST
from app.core.errors import ProviderUnavailable
from app.data.base import Quote
from app.data.symbols import Symbol

log = logging.getLogger(__name__)

URL = "https://hq.sinajs.cn/list="
#: 不带 Referer 会直接 403
HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
#: 单次请求超时（秒）。这是交互路径上的请求，比 registry 的 12 秒预算短得多
TIMEOUT = 6.0
#: 一个请求最多带多少只。再多 URL 会过长（自选股 50 只封顶，一般用不到）
MAX_BATCH = 80

_LINE_RE = re.compile(r'var hq_str_([a-z]{2}\d{6})="([^"]*)"')
#: A股这一行至少有 32 个字段（到日期、时间为止）；少于这个数说明格式变了或被截断
_MIN_FIELDS = 32


# ---------------------------------------------------------------- 解析


def parse(text: str) -> dict[str, list[str]]:
    """响应文本 -> {新浪代码: 字段列表}。空行（代码不存在）不收录。"""
    out: dict[str, list[str]] = {}
    for code, payload in _LINE_RE.findall(text or ""):
        if payload.strip():
            out[code] = payload.split(",")
    return out


def to_quote(symbol: Symbol, fields: list[str]) -> Quote | None:
    """一行字段 -> `Quote`。字段不全或拿不到价格时返回 None，不猜。

    新浪不给换手率、市盈率、市净率、市值 —— 如实留空，界面和简报会标"数据缺失"。
    """
    if len(fields) < _MIN_FIELDS:
        return None

    prev_close = _positive(fields[2])
    price = _positive(fields[3])
    if price is None:
        # 停牌、开盘前：现价是 0。用昨收兜底，别显示 0.00
        price = prev_close
    if price is None:
        return None

    change_pct = None
    if prev_close:
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    return Quote(
        symbol=symbol.canonical,
        name=fields[0].strip(),
        price=price,
        prev_close=prev_close,
        open=_positive(fields[1]),
        high=_positive(fields[4]),
        low=_positive(fields[5]),
        change_pct=change_pct,
        volume=_number(fields[8]),
        amount=_number(fields[9]),
        # 用交易所给的时间戳，而不是"现在"：收盘后看到的就是 15:00 那一笔
        updated_at=_timestamp(fields[30], fields[31]),
    )


def _number(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _positive(text: str) -> float | None:
    """价格字段：0 表示"没有"（未开盘 / 停牌），不能当成真实的 0 元。"""
    value = _number(text)
    return value if value is not None and value > 0 else None


def _timestamp(day: str, clock: str) -> datetime | None:
    try:
        return datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    except ValueError:
        return None


# ---------------------------------------------------------------- 请求


async def fetch(codes: list[str]) -> dict[str, list[str]]:
    """一次请求取多只。网络失败、被拒（403）都抛 `ProviderUnavailable`。"""
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
            resp = await client.get(URL + ",".join(codes))
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"新浪实时行情请求失败：{type(exc).__name__}: {exc}") from exc
    # 新浪这个接口是 GBK 编码，按 UTF-8 解会把名称变成乱码
    return parse(resp.content.decode("gbk", errors="replace"))


class Batcher:
    """把同一时刻到达的行情请求合并成一个 HTTP 请求。

    `/market/quotes` 对每只票并发调一次 registry.quote()，到达这里的时间
    只差几十毫秒。等一个很短的窗口再统一发，20 只票就从 20 个请求变成 1 个。
    代价是每次多等 `window` 秒 —— 对 5 秒一次的轮询无关紧要。
    """

    def __init__(
        self,
        fetcher: Callable[[list[str]], Awaitable[dict[str, list[str]]]] = fetch,
        window: float = 0.05,
        max_batch: int = MAX_BATCH,
    ) -> None:
        self._fetch = fetcher
        self._window = window
        self._max_batch = max_batch
        self._pending: dict[str, list[asyncio.Future[list[str] | None]]] = {}
        # "已经排了一次发送、还没取走待发列表"。不能用"发送任务还在跑"来判断：
        # 任务取走列表之后、请求还没回来的那段时间里新到的请求，
        # 会以为有人负责而干等到超时
        self._scheduled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        # 持有任务引用，免得还没跑完就被垃圾回收（asyncio 文档的要求）
        self._tasks: set[asyncio.Task[None]] = set()

    async def get(self, code: str) -> list[str] | None:
        """取一只的字段。新浪没有这只（代码不存在）返回 None；请求失败抛异常。"""
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            # 换了事件循环（测试里每个用例一个循环）：上一个循环里排的发送永远不会跑了
            self._loop, self._pending, self._scheduled = loop, {}, False
        future: asyncio.Future[list[str] | None] = loop.create_future()
        self._pending.setdefault(code, []).append(future)
        if not self._scheduled:
            self._scheduled = True
            task = loop.create_task(self._flush_soon())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return await future

    async def _flush_soon(self) -> None:
        await asyncio.sleep(self._window)
        pending, self._pending, self._scheduled = self._pending, {}, False
        codes = list(pending)
        for start in range(0, len(codes), self._max_batch):
            chunk = codes[start : start + self._max_batch]
            try:
                rows = await self._fetch(chunk)
            except Exception as exc:
                for code in chunk:
                    _settle(pending[code], error=exc)
                continue
            for code in chunk:
                _settle(pending[code], result=rows.get(code))


def _settle(
    futures: list[asyncio.Future[list[str] | None]],
    *,
    result: list[str] | None = None,
    error: BaseException | None = None,
) -> None:
    # 调用方可能已经因为超时被取消了（registry 给每次尝试限了时），
    # 对已完成的 future 再赋值会抛 InvalidStateError
    for future in futures:
        if future.done():
            continue
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)
