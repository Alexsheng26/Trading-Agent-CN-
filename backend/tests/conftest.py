"""测试固件。

所有测试都跑在离线模式下（mock 数据源 + echo 桩模型），
不联网、不需要任何 API Key、不产生费用。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# 必须在导入 app 之前设好环境变量
_TMP = Path(tempfile.mkdtemp(prefix="trading-agent-test-"))
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{_TMP / 'test.db'}",
    LLM_PROVIDER="echo",
    DATA_PROVIDER_ORDER="mock",
    DEBATE_ROUNDS="1",
    APP_ENV="dev",
    LOG_LEVEL="WARNING",
    # 后台建目录的任务会和断言抢时间，测试里显式关掉，
    # 需要目录的测试自己调 symbol_index.refresh()
    SYMBOL_INDEX_AUTOREFRESH="false",
)


@pytest.fixture(autouse=True)
def _reset_singletons(request, monkeypatch) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """每个测试用例都从干净的单例开始，每个测试模块用独立的库文件。

    **为什么要按模块分库**：SQLite 的写锁是**文件级**的。只要有一个用例
    留下了没提交的写事务（后台任务被打断、连接没关干净都会造成），
    同一个文件上后面所有的写入都会一直等锁 —— 表现是测试套件随机卡死，
    而且卡在一个和肇事者完全无关的用例上，极难排查。
    按模块分库之后谁也拖不了谁。

    （生产上不存在这个问题：uvicorn 只有一个事件循环，后台任务也在
    关停时被统一取消，见 `run_store.cancel_all`。）
    """
    module = request.node.module.__name__.rsplit(".", 1)[-1]
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP / f'{module}.db'}")

    from app.core.config import get_settings
    from app.data.cache import cache
    from app.data.registry import reset_registry
    from app.db.session import reset_db_state
    from app.llm.factory import reset_llm
    from app.services.symbol_index import reset_state

    get_settings.cache_clear()
    reset_registry()
    reset_llm()
    reset_state()
    # 每个用例一套新引擎：这个进程里有多个事件循环（TestClient 一个、
    # pytest-asyncio 每个用例一个），全局引擎被跨循环复用时会永久挂住。
    reset_db_state()
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client() -> Iterator:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_registry():  # type: ignore[no-untyped-def]
    from app.data.mock_provider import MockProvider
    from app.data.registry import ProviderRegistry

    return ProviderRegistry([MockProvider()])


@pytest.fixture
async def snapshot(mock_registry):  # type: ignore[no-untyped-def]
    from app.data.symbols import normalize
    from app.services.market_data import MarketDataService

    return await MarketDataService(mock_registry).snapshot(normalize("600519"))
