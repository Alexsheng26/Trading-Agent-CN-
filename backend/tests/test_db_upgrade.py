"""老数据库升级：新版本给表加了列，用户机器上的库必须能自动补上。

`create_all` 只建不存在的表，对已存在的表一个字都不改。
没有补列这一步的话，用户装上新版本后读写分析记录会直接报
`no such column: analysis_runs.config` —— 升级即挂。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine

from app.db.models import Base
from app.db.session import init_db, reset_db_state
from app.services.analysis_service import AnalysisService


def _make_old_db(path: Path) -> None:
    """按当前模型建表，再删掉 config 列 —— 等同于档位功能之前的库。"""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("ALTER TABLE analysis_runs DROP COLUMN config")
        conn.execute(
            "INSERT INTO analysis_runs (id, symbol, name, horizon, user_note, status, error,"
            " llm_provider, created_at) VALUES ('old1', '600519.SH', '贵州茅台', '波段', '',"
            " 'done', '', 'openai_compat', '2026-09-01 02:00:00.000000')"
        )
        conn.commit()


def _columns(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(analysis_runs)")}


async def test_init_db_adds_missing_column(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "old.db"
    _make_old_db(db)
    assert "config" not in _columns(db)

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from app.core.config import get_settings

    get_settings.cache_clear()
    reset_db_state()

    await init_db()
    assert "config" in _columns(db)

    # 老记录照样能读，只是没有档位信息
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        run = await AnalysisService(session).get("old1")
    assert run is not None
    assert run.name == "贵州茅台"
    assert run.config is None


async def test_init_db_is_idempotent(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "old.db"
    _make_old_db(db)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from app.core.config import get_settings

    get_settings.cache_clear()
    reset_db_state()

    await init_db()
    await init_db()  # 第二次启动不能因为"列已存在"报错
    assert "config" in _columns(db)
