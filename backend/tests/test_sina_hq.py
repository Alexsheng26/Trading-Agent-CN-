"""新浪实时行情（hq.sinajs.cn）。

由来：用户机器上股票的实时价一直拿不到 —— 东财返回空响应、雪球要 token、
Tushare 不是实时的 —— 盘中看到的是上一交易日的收盘价。新浪的主机在那台机器上
是通的，这个接口又不要 token、一个请求能取多只。

要钉死的几件事：
  1. 字段映射对（下标错一位，价格就成了昨收）；
  2. 0 是"没有"而不是 0 元（停牌、开盘前）；
  3. 并发的多只请求合成一个 HTTP 请求（逐只请求会被新浪封 IP）；
  4. 合并逻辑里的竞态：发送途中新到的请求不能被晾到超时。

不联网：HTTP 用 httpx.MockTransport，Provider 里的请求函数换成假的。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.core.clock import CST
from app.core.errors import ProviderUnavailable
from app.data import sina_hq
from app.data.base import CAP_QUOTE, Quote
from app.data.symbols import normalize

MOUTAI = normalize("600519")
SH_ETF = normalize("512480")


def _fields(
    name: str = "贵州茅台",
    open_: str = "1430.000",
    prev: str = "1428.000",
    price: str = "1432.500",
    high: str = "1440.000",
    low: str = "1425.000",
    vol: str = "2345678",
    amount: str = "3356789012.000",
    day: str = "2026-09-30",
    clock: str = "14:35:03",
) -> list[str]:
    """按新浪的真实格式拼一行：10 个行情字段 + 20 个五档字段 + 日期 / 时间 / 状态。"""
    depth = ["100", "1432.400"] * 5 + ["200", "1432.600"] * 5
    return [name, open_, prev, price, high, low, "1432.400", "1432.600", vol, amount,
            *depth, day, clock, "00", ""]  # fmt: skip


def _line(code: str, fields: list[str]) -> str:
    return f'var hq_str_{code}="{",".join(fields)}";'


# ---------------------------------------------------------------- 解析


def test_parse_multiple_lines_and_skips_unknown_codes() -> None:
    text = "\n".join(
        [
            _line("sh600519", _fields()),
            _line("sh512480", _fields(name="半导体ETF", price="0.873")),
            'var hq_str_sh999999="";',  # 代码不存在
        ]
    )
    rows = sina_hq.parse(text)
    assert set(rows) == {"sh600519", "sh512480"}
    assert rows["sh512480"][0] == "半导体ETF"


def test_quote_mapping() -> None:
    q = sina_hq.to_quote(MOUTAI, _fields())
    assert q is not None
    assert (q.name, q.price, q.prev_close, q.open, q.high, q.low) == (
        "贵州茅台", 1432.5, 1428.0, 1430.0, 1440.0, 1425.0,
    )  # fmt: skip
    assert q.change_pct == 0.32
    assert (q.volume, q.amount) == (2345678.0, 3356789012.0)
    # 交易所给的时间，不是"现在"
    assert q.updated_at == datetime(2026, 9, 30, 14, 35, 3, tzinfo=CST)
    assert q.is_realtime is True


def test_quote_leaves_unknown_fields_empty() -> None:
    """新浪不给换手率和估值 —— 留空，不能填 0 冒充真实数值。"""
    q = sina_hq.to_quote(MOUTAI, _fields())
    assert q is not None
    assert (q.turnover_rate, q.pe_ttm, q.pb, q.total_market_cap) == (None, None, None, None)


def test_zero_price_means_no_trade_yet() -> None:
    """停牌 / 开盘前现价是 0：用昨收兜底，开高低留空，涨跌幅为 0。"""
    q = sina_hq.to_quote(MOUTAI, _fields(open_="0.000", price="0.000", high="0.000", low="0.000"))
    assert q is not None
    assert q.price == 1428.0
    assert (q.open, q.high, q.low) == (None, None, None)
    assert q.change_pct == 0.0


def test_no_price_at_all_is_not_a_quote() -> None:
    assert sina_hq.to_quote(MOUTAI, _fields(prev="0.000", price="0.000")) is None


def test_truncated_line_is_rejected() -> None:
    """格式变了或者被截断：字段数不够就不认，免得把别的字段当成价格。"""
    assert sina_hq.to_quote(MOUTAI, _fields()[:20]) is None


def test_etf_keeps_three_decimals() -> None:
    q = sina_hq.to_quote(SH_ETF, _fields(name="半导体ETF", prev="0.869", price="0.873"))
    assert q is not None
    assert q.price == 0.873
    assert q.change_pct == 0.46


# ---------------------------------------------------------------- 请求


def _mock_client(monkeypatch, handler) -> None:  # type: ignore[no-untyped-def]
    import httpx

    real = httpx.AsyncClient

    def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_fetch_sends_referer_and_decodes_gbk(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["referer"] = request.headers.get("referer")
        body = _line("sh600519", _fields()) + "\n" + _line("sz000001", _fields(name="平安银行"))
        return httpx.Response(200, content=body.encode("gbk"))

    _mock_client(monkeypatch, handler)
    rows = await sina_hq.fetch(["sh600519", "sz000001"])

    assert seen["url"].endswith("list=sh600519,sz000001")
    assert seen["referer"] == "https://finance.sina.com.cn/", "不带 Referer 新浪直接 403"
    assert rows["sz000001"][0] == "平安银行", "GBK 要正确解码，否则名称是乱码"


async def test_fetch_turns_403_into_provider_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    _mock_client(monkeypatch, lambda request: httpx.Response(403, text="Forbidden"))
    with pytest.raises(ProviderUnavailable, match="新浪实时行情请求失败"):
        await sina_hq.fetch(["sh600519"])


async def test_fetch_turns_network_error_into_provider_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连不上", request=request)

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderUnavailable, match="ConnectError"):
        await sina_hq.fetch(["sh600519"])


# ---------------------------------------------------------------- 合并请求


class _Recorder:
    def __init__(self, rows: dict | None = None, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.rows = rows or {}
        self.error = error

    async def __call__(self, codes: list[str]) -> dict[str, list[str]]:
        self.calls.append(list(codes))
        if self.error is not None:
            raise self.error
        return {c: self.rows[c] for c in codes if c in self.rows}


async def test_concurrent_requests_share_one_http_call() -> None:
    """首页 20 只自选是并发取的：必须合成一个请求，逐只请求会被新浪封 IP。"""
    fetch = _Recorder({"sh600519": _fields(), "sz000001": _fields(name="平安银行")})
    batcher = sina_hq.Batcher(fetcher=fetch, window=0.01)

    got = await asyncio.gather(
        batcher.get("sh600519"),
        batcher.get("sz000001"),
        batcher.get("sh600519"),  # 同一只被要了两次
        batcher.get("sh999999"),  # 新浪没有
    )

    assert len(fetch.calls) == 1
    assert sorted(fetch.calls[0]) == ["sh600519", "sh999999", "sz000001"], "去重"
    assert got[0] is not None and got[0][0] == "贵州茅台"
    assert got[2] == got[0]
    assert got[3] is None


async def test_failure_reaches_every_waiter() -> None:
    fetch = _Recorder(error=ProviderUnavailable("新浪 403"))
    batcher = sina_hq.Batcher(fetcher=fetch, window=0.01)
    results = await asyncio.gather(
        batcher.get("sh600519"), batcher.get("sz000001"), return_exceptions=True
    )
    assert all(isinstance(r, ProviderUnavailable) for r in results)


async def test_large_batches_are_split() -> None:
    fetch = _Recorder()
    batcher = sina_hq.Batcher(fetcher=fetch, window=0.01, max_batch=2)
    await asyncio.gather(*(batcher.get(f"sh60000{i}") for i in range(5)))
    assert [len(c) for c in fetch.calls] == [2, 2, 1]


async def test_request_arriving_mid_flight_is_not_stranded() -> None:
    """**竞态**：第一批已经发出去、还没回来时又来了一只。

    如果用"发送任务还在跑"来判断要不要再排一次，这只会以为有人负责，
    干等到 registry 的超时（12 秒）—— 表现就是偶尔某只票的价格刷不出来。
    """
    release = asyncio.Event()
    started = asyncio.Event()
    calls: list[list[str]] = []

    async def slow_fetch(codes: list[str]) -> dict[str, list[str]]:
        calls.append(list(codes))
        started.set()
        await release.wait()
        return {c: _fields() for c in codes}

    batcher = sina_hq.Batcher(fetcher=slow_fetch, window=0.01)
    first = asyncio.ensure_future(batcher.get("sh600519"))
    await started.wait()  # 第一批在途
    second = asyncio.ensure_future(batcher.get("sz000001"))
    release.set()

    async with asyncio.timeout(2):
        a, b = await asyncio.gather(first, second)
    assert a is not None and b is not None
    assert calls == [["sh600519"], ["sz000001"]]


async def test_cancelled_waiter_does_not_break_others() -> None:
    """registry 给每次尝试限了时，超时的调用会被取消；别的等待者照常拿结果。"""
    fetch = _Recorder({"sz000001": _fields(name="平安银行")})
    batcher = sina_hq.Batcher(fetcher=fetch, window=0.05)
    doomed = asyncio.ensure_future(batcher.get("sh600519"))
    survivor = asyncio.ensure_future(batcher.get("sz000001"))
    await asyncio.sleep(0)
    doomed.cancel()
    row = await survivor
    assert row is not None and row[0] == "平安银行"


# ---------------------------------------------------------------- 接入数据源


def _sina(rows: dict | None = None, error: Exception | None = None):  # type: ignore[no-untyped-def]
    from app.data.backup_providers import SinaProvider

    provider = SinaProvider()
    fetch = _Recorder(rows, error)
    provider._hq = sina_hq.Batcher(fetcher=fetch, window=0.01)  # type: ignore[attr-defined]

    async def no_akshare(func_name: str, /, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(f"实时价不该走到 akshare：{func_name}")

    async def healthy() -> bool:
        # 真的 healthcheck 要能 import akshare；CI 的后端任务不装 akshare，
        # 而这里的实时价只走 hq（httpx），和装没装 akshare 无关
        return True

    provider._call = no_akshare  # type: ignore[assignment, method-assign]
    provider.healthcheck = healthy  # type: ignore[method-assign]
    return provider, fetch


async def test_sina_provider_serves_stock_quotes() -> None:
    provider, fetch = _sina({"sh600519": _fields()})
    q = await provider.quote(MOUTAI)
    assert (q.symbol, q.name, q.price) == ("600519.SH", "贵州茅台", 1432.5)
    assert fetch.calls == [["sh600519"]]


async def test_sina_provider_serves_etf_quotes_from_hq() -> None:
    """ETF 也先走 hq：取到了就不必下载整张 ETF 列表。"""
    provider, _ = _sina({"sh512480": _fields(name="半导体ETF", prev="0.869", price="0.873")})
    q = await provider.quote(SH_ETF)
    assert (q.name, q.price) == ("半导体ETF", 0.873)


async def test_sina_provider_reports_hq_failure_for_stocks() -> None:
    provider, _ = _sina(error=ProviderUnavailable("新浪实时行情请求失败：ConnectError"))
    with pytest.raises(ProviderUnavailable, match="ConnectError"):
        await provider.quote(MOUTAI)


class _Down:
    """东财 / 雪球在用户机器上的样子：实时价取不到。"""

    capabilities = frozenset({CAP_QUOTE})

    def __init__(self, name: str) -> None:
        self.name = name

    async def healthcheck(self) -> bool:
        return True

    async def quote(self, symbol):  # type: ignore[no-untyped-def]
        raise ProviderUnavailable(f"{self.name} 取不到")


async def test_registry_falls_through_to_sina_for_stocks() -> None:
    """**本文件最重要的一条**：东财、雪球都取不到时，股票实时价落到新浪，
    而不是退回昨天的收盘价。"""
    from app.data.registry import ProviderRegistry

    provider, _ = _sina({"sh600519": _fields()})
    registry = ProviderRegistry([_Down("akshare"), _Down("xueqiu"), provider], timeout=5)

    q = await registry.quote(MOUTAI)
    assert isinstance(q, Quote)
    assert q.price == 1432.5
    assert registry.last_sources["quote"] == "sina"


async def test_watchlist_poll_is_one_request() -> None:
    """自选股批量行情走 registry 逐只并发 —— 到了新浪这里仍然只发一个请求。"""
    from app.data.registry import ProviderRegistry

    codes = ["600519", "000001", "300750", "601318", "512480"]
    provider, fetch = _sina({normalize(c).sina: _fields() for c in codes})
    registry = ProviderRegistry([_Down("akshare"), provider], timeout=5)

    quotes = await asyncio.gather(*(registry.quote(normalize(c)) for c in codes))
    assert len(quotes) == 5
    assert len(fetch.calls) == 1
