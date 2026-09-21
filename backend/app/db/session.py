"""数据库会话管理。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.models import Base

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite"):
            # SQLite 要先把目录建出来，否则 aiosqlite 直接报 unable to open database file
            db_path = url.split("///", 1)[-1]
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(url, echo=False, pool_pre_ping=True)
        log.info("数据库已连接：%s", _mask(url))
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def init_db() -> None:
    """建表。

    单人使用的小工具用 create_all 足够；表结构一旦要演进，
    引入 alembic 做迁移（见 docs/ARCHITECTURE.md）。
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖。"""
    async with get_sessionmaker()() as session:
        yield session


def _mask(url: str) -> str:
    """日志里别打出密码。"""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
