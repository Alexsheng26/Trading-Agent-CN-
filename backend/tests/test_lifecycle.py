"""启动与关停。

由来：关停时没人管那些还在跑的后台分析任务。事件循环一关，任务正卡在
"写数据库"那一步，底下的 aiosqlite 连接就带着一个**没提交的写事务**留在
那儿 —— SQLite 的写锁从此不放，之后任何写入都要等锁等到超时。
本项目的测试套件因此随机卡死过（`database is locked`），排查了很久。

生产上的后果是一样的：`docker compose restart` 正好撞上一次分析，
那条记录会永远停在 "running"，还可能把库锁住。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.services.run_store import store


@pytest.fixture
def _slow_echo(monkeypatch) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """把桩模型放慢，确保关停时分析确实还在跑 —— 否则这个用例是空跑。"""
    from app.core.config import get_settings
    from app.llm.factory import reset_llm

    monkeypatch.setenv("ECHO_DELAY", "0.5")
    get_settings.cache_clear()
    reset_llm()
    yield
    get_settings.cache_clear()
    reset_llm()


def test_shutdown_cancels_inflight_analysis(_slow_echo) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        run_id = client.post(
            "/api/v1/analysis/runs", json={"symbol": "600519", "horizon": "短线"}
        ).json()["run_id"]
        buffer = store.get(run_id)
        assert buffer is not None
        assert buffer.task is not None and not buffer.task.done(), "前提：分析应当还在跑"
    # 退出 with 触发 lifespan 关停

    buffer = store.get(run_id)
    assert buffer is not None
    assert buffer.task is not None
    assert buffer.task.done(), "关停后不该还留着在跑的分析任务"


def test_shutdown_is_clean_with_nothing_running() -> None:
    """没有在跑的任务时，关停不该有任何额外开销或异常。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}


async def test_cancel_all_returns_zero_when_idle() -> None:
    assert await store.cancel_all() == 0
