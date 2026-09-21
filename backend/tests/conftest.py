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
)


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    """每个测试用例都从干净的单例开始。"""
    from app.core.config import get_settings
    from app.data.cache import cache
    from app.data.registry import reset_registry
    from app.llm.factory import reset_llm

    get_settings.cache_clear()
    reset_registry()
    reset_llm()
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
