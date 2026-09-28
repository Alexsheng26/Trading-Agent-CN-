"""数据库会话管理。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import Base

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict[str, object] = {"echo": False, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            # SQLite 要先把目录建出来，否则 aiosqlite 直接报 unable to open database file
            db_path = url.split("///", 1)[-1]
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)

            # SQLite 不用连接池（默认是 AsyncAdaptedQueuePool，池大小 5）。
            #
            # 原因是 asyncio 的一个经典陷阱：**aiosqlite 的连接绑死在创建它的
            # 事件循环上**（它内部有一个线程和一堆 Future）。连接被放回池里、
            # 之后在另一个事件循环里被取出来复用时，await 会挂在一个属于已关闭
            # 循环的 Future 上 —— 表现是**永久卡住，没有任何报错**。
            # 本项目的测试套件就踩到了：TestClient 有自己的 portal 循环，
            # pytest-asyncio 又给每个用例一个新循环，跑着跑着就随机卡死。
            #
            # SQLite 开连接就是打开一个本地文件，很便宜，池化省不了多少；
            # 顺带还避免了池里那条长命连接一直握着锁、让写入报
            # "database is locked"。PostgreSQL 仍用默认池 —— 那边池化是有意义的。
            kwargs["poolclass"] = NullPool
            kwargs.pop("pool_pre_ping")  # NullPool 每次都是新连接，无需预检
            # 等锁的时间：默认只有 5 秒，写入撞上就直接报 database is locked。
            # 后台建代码目录（一次写 5000+ 行）和请求里的写入是会并发的。
            kwargs["connect_args"] = {"timeout": 30}
        _engine = create_async_engine(url, **kwargs)  # type: ignore[arg-type]
        if url.startswith("sqlite") and ":memory:" not in url:
            _apply_sqlite_pragmas(_engine)
        log.info("数据库已连接：%s", _mask(url))
    return _engine


def _apply_sqlite_pragmas(engine: AsyncEngine) -> None:
    """每条 SQLite 连接建立时设好 PRAGMA。

    **WAL 是这里的关键。** 默认的 rollback journal 模式下，写事务会把所有
    读也挡住；本项目引入"后台建全市场代码目录"之后就真的撞上了 ——
    后台任务在写 5000 多行，同时请求在读，于是随机报
    `database is locked`（测试套件里稳定复现了这个错）。
    WAL 下读写互不阻塞，这类冲突基本消失。

    - journal_mode=WAL —— 读写不互斥。这个设置写在数据库文件里，一次即永久
    - synchronous=NORMAL —— WAL 下的推荐值，比 FULL 快很多，
      掉电最多丢最后几个事务（对一个本地投研工具完全可以接受）
    - busy_timeout —— 和上面的 connect timeout 双保险
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


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


def reset_db_state() -> None:
    """丢掉引擎与会话工厂，不做 await。

    给测试用。测试里同一个进程会有好几个事件循环：TestClient 有自己的
    portal 循环，pytest-asyncio 又给每个用例一个新循环。全局引擎一旦被
    跨循环复用，aiosqlite 那些绑在原循环上的 Future 就会让 await 永久挂住。
    每个用例都从新引擎开始，这类问题直接不存在。

    不调 dispose() 是因为 SQLite 走的是 NullPool —— 引擎本身不持有连接，
    连接在单次操作结束时就关掉了，没有东西需要异步回收。
    """
    global _engine, _sessionmaker
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
