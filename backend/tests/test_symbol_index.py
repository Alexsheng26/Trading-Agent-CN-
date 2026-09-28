"""本地代码目录 —— 按名称搜索的根治方案。

由来：用户搜"华宝股份"一直卡在"搜索中"。加了超时之后不卡了，但也只是
12 秒后报错，照样搜不出结果 —— 因为按名称搜索每次都要现场拉全市场列表，
而 AKShare 那个接口慢或挂是常态。

现在改成拉到一次就落库，之后搜索是纯本地查询。这个文件要钉死的核心断言是：
**数据源全挂时，搜索依然能出结果。**
"""

from __future__ import annotations

import asyncio

import pytest

from app.data.base import ALL_CAPABILITIES, StockBasic
from app.data.mock_provider import MockProvider
from app.data.registry import ProviderRegistry
from app.services import symbol_index


class _Dead:
    """所有接口都立刻失败的数据源。"""

    name = "dead"
    capabilities = frozenset(ALL_CAPABILITIES)

    async def healthcheck(self) -> bool:
        return False

    def __getattr__(self, _name):  # type: ignore[no-untyped-def]
        async def _boom(*a, **k):
            raise RuntimeError("数据源挂了")

        return _boom


def _wipe() -> None:
    """同步清空目录表。

    这个文件里既有 async 单元测试、也有走 TestClient 的同步端到端测试，
    两者共用同一个 SQLite 文件。用同步 sqlite3 连接清理最省事 ——
    不用去操心哪个事件循环持有 async engine。
    """
    import sqlite3

    from app.core.config import get_settings

    path = get_settings().database_url.split("///", 1)[-1]
    try:
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM symbol_directory")
    except sqlite3.OperationalError:
        pass  # 表还没建出来


@pytest.fixture(autouse=True)
def _clean_directory():  # type: ignore[no-untyped-def]
    """每个用例都从空目录开始 —— 否则前一个用例建好的目录会让后面的断言失真。"""
    _wipe()
    symbol_index.reset_state()
    yield
    _wipe()
    symbol_index.reset_state()


@pytest.fixture
def _db():  # type: ignore[no-untyped-def]
    """建表并清空目录。返回一个协程工厂，测试里 `await _db()` 调用。"""

    async def setup() -> None:
        from sqlalchemy import delete

        from app.db.models import SymbolDirectory
        from app.db.session import get_sessionmaker, init_db

        await init_db()
        async with get_sessionmaker()() as session:
            await session.execute(delete(SymbolDirectory))
            await session.commit()
        symbol_index.reset_state()

    return setup


def _use(providers):  # type: ignore[no-untyped-def]
    """替换全局 registry —— 目录刷新走的是 get_registry()。"""
    import app.data.registry as reg

    reg._registry = ProviderRegistry(providers, timeout=1)


# ---------------------------------------------------------------- 建目录


async def test_refresh_populates_directory(_db) -> None:  # type: ignore[no-untyped-def]
    await _db()
    _use([MockProvider()])
    assert await symbol_index.is_ready() is False

    changed = await symbol_index.refresh()
    assert changed > 0
    assert await symbol_index.is_ready() is True

    st = await symbol_index.status()
    assert st.count == changed
    assert st.stale is False
    assert st.sources == ["mock"]


async def test_lookup_by_name_and_code(_db) -> None:  # type: ignore[no-untyped-def]
    await _db()
    _use([MockProvider()])
    await symbol_index.refresh()

    by_name = await symbol_index.lookup("茅台")
    assert [r.symbol for r in by_name] == ["600519.SH"]

    by_code = await symbol_index.lookup("600519")
    assert by_code[0].symbol == "600519.SH"
    assert by_code[0].name == "贵州茅台"


async def test_lookup_ranks_exact_code_first(_db) -> None:  # type: ignore[no-untyped-def]
    """搜 600519 时它必须是第一条，不能被"名称里带 600519 的"挤下去。"""
    await _db()
    _use([MockProvider()])
    await symbol_index.refresh()
    await _insert(symbol="600001.SH", code="600001", name="测试600519壳公司")

    hits = await symbol_index.lookup("600519")
    assert hits[0].symbol == "600519.SH"


async def test_lookup_escapes_like_wildcards(_db) -> None:  # type: ignore[no-untyped-def]
    """搜 "%" 不该把整个目录都匹配出来。"""
    await _db()
    _use([MockProvider()])
    await symbol_index.refresh()

    assert await symbol_index.lookup("%") == []
    assert await symbol_index.lookup("_") == []

    await _insert(symbol="600002.SH", code="600002", name="百分之100%公司")
    assert [r.symbol for r in await symbol_index.lookup("100%")] == ["600002.SH"]


async def test_lookup_empty_query_returns_nothing(_db) -> None:  # type: ignore[no-untyped-def]
    await _db()
    _use([MockProvider()])
    await symbol_index.refresh()
    assert await symbol_index.lookup("   ") == []


# ---------------------------------------------------------------- 增量与容错


async def test_refresh_is_incremental_and_never_wipes(_db) -> None:  # type: ignore[no-untyped-def]
    """数据源返回空列表时，目录必须保持原样 —— 一次抖动不能毁掉好数据。"""
    await _db()
    _use([MockProvider()])
    before = await symbol_index.refresh()
    assert before > 0

    class _Empty(MockProvider):
        name = "empty"

        async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]:
            return []

    _use([_Empty()])
    assert await symbol_index.refresh() == 0
    assert (await symbol_index.status()).count == before


async def test_refresh_updates_renamed_stock(_db) -> None:  # type: ignore[no-untyped-def]
    """ST / 重组改名要跟上。"""
    await _db()
    _use([MockProvider()])
    await symbol_index.refresh()

    class _Renamed(MockProvider):
        name = "renamed"

        async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]:
            return [
                StockBasic(symbol="600519.SH", name="ST茅台", exchange="SH", board="主板")
            ]

    _use([_Renamed()])
    assert await symbol_index.refresh() == 1
    assert await symbol_index.name_of("600519.SH") == "ST茅台"


async def test_refresh_raises_when_all_providers_dead(_db) -> None:  # type: ignore[no-untyped-def]
    from app.core.errors import DataNotFound

    await _db()
    _use([_Dead()])
    with pytest.raises(DataNotFound):
        await symbol_index.refresh()


async def test_schedule_refresh_does_not_duplicate(_db) -> None:  # type: ignore[no-untyped-def]
    """并发搜索不该排出一堆刷新任务。"""
    await _db()
    _use([MockProvider()])
    assert symbol_index.schedule_refresh() is True
    assert symbol_index.schedule_refresh() is False
    await asyncio.sleep(0.2)  # 让后台任务跑完
    assert await symbol_index.is_ready() is True


async def test_schedule_refresh_swallows_failures(_db) -> None:  # type: ignore[no-untyped-def]
    """后台刷新失败不能把异常泄漏出去 —— 它只是个可选的加速手段。"""
    await _db()
    _use([_Dead()])
    assert symbol_index.schedule_refresh() is True
    await asyncio.sleep(0.2)
    assert await symbol_index.is_ready() is False


async def test_name_of_unknown_symbol_is_empty(_db) -> None:  # type: ignore[no-untyped-def]
    await _db()
    assert await symbol_index.name_of("600519.SH") == ""


# ---------------------------------------------------------------- 辅助


async def _insert(*, symbol: str, code: str, name: str) -> None:
    from app.db.models import SymbolDirectory
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        session.add(
            SymbolDirectory(symbol=symbol, code=code, name=name, exchange="SH", board="主板")
        )
        await session.commit()
    symbol_index.reset_state()


# ---------------------------------------------------------------- 端到端


def test_search_survives_dead_data_source(client) -> None:  # type: ignore[no-untyped-def]
    """**本文件最重要的一条断言。**

    目录建好之后把数据源全部打死，按名称搜索必须照样出结果 ——
    这正是用户遇到的场景：akshare 挂了，搜什么都出不来。
    """
    built = client.post("/api/v1/market/symbols/refresh?wait=true").json()
    assert built["count"] > 0

    _use([_Dead()])  # 数据源全挂

    hits = client.get("/api/v1/market/search", params={"q": "茅台"}).json()
    assert any(h["name"] == "贵州茅台" for h in hits)

    # 代码搜索同样不受影响，而且名称从目录里补齐了
    by_code = client.get("/api/v1/market/search", params={"q": "600519"}).json()
    assert by_code[0]["symbol"] == "600519.SH"
    assert by_code[0]["name"] == "贵州茅台"


def test_search_falls_back_to_provider_before_index_exists(client) -> None:  # type: ignore[no-untyped-def]
    """目录还没建好时仍然现场问数据源，不能因为"还没建索引"就搜不到。"""
    status = client.get("/api/v1/market/symbols/status").json()
    assert status["count"] == 0

    hits = client.get("/api/v1/market/search", params={"q": "茅台"}).json()
    assert any(h["name"] == "贵州茅台" for h in hits)


def test_symbols_status_endpoint(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/market/symbols/status").json()
    for key in ("count", "updated_at", "stale", "refreshing", "sources"):
        assert key in body


def test_code_direct_works_with_empty_index_and_dead_source(client) -> None:  # type: ignore[no-untyped-def]
    """目录空 + 数据源全挂：合法代码仍要放行，否则用户连详情页都进不去。"""
    _use([_Dead()])
    hits = client.get("/api/v1/market/search", params={"q": "600519"}).json()
    assert [h["symbol"] for h in hits] == ["600519.SH"]
    assert hits[0]["name"] == ""  # 目录空，没有名称可补


def test_search_reports_503_when_nothing_can_answer(client) -> None:  # type: ignore[no-untyped-def]
    """既不是合法代码、目录也空、数据源也挂 —— 必须明确报错，不能假装成空结果。"""
    _use([_Dead()])
    resp = client.get("/api/v1/market/search", params={"q": "茅台"})
    assert resp.status_code == 503
    assert "dead" in resp.json()["detail"]
