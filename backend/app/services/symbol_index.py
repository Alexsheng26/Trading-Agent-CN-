"""全市场代码-名称目录。

**这是"搜索一直卡在搜索中"的根治方案。**

按名称搜索必须有全市场列表。之前每次搜索都现场去问数据源，于是：
AKShare 那个全市场接口 5000+ 行，慢；它靠抓第三方网页，挂了就整体失效；
超时保护只能让它 12 秒后报错，没法让它出结果。

改成：成功拉到一次就落库，之后搜索纯本地查询（毫秒级），
数据源事后挂掉也照样能搜。启动时在后台建目录，不阻塞任何请求。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.core.clock import now_utc
from app.core.config import get_settings
from app.data.base import StockBasic
from app.data.registry import get_registry
from app.data.symbols import try_normalize
from app.db.models import SymbolDirectory
from app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

#: 一次拉全市场的上限。A股总数 5400 左右，留足余量。
_FETCH_LIMIT = 8000
#: 一次 SQL 取回的候选行数，再在 Python 里排序截断
_CANDIDATES = 300

_refresh_task: asyncio.Task | None = None
#: 目录里有多少条。None = 还没查过。缓存起来，避免每次搜索都 COUNT。
_count: int | None = None


@dataclass(slots=True)
class IndexStatus:
    count: int
    updated_at: str | None
    stale: bool
    refreshing: bool
    sources: list[str]


# ---------------------------------------------------------------- 查询


def _escape_like(text: str) -> str:
    """转义 LIKE 的通配符 —— 用户搜 "100%" 不该匹配到所有东西。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def lookup(query: str, limit: int = 20) -> list[StockBasic]:
    """在本地目录里搜。不发任何网络请求。"""
    q = query.strip()
    if not q:
        return []

    pattern = f"%{_escape_like(q)}%"
    async with get_sessionmaker()() as session:
        rows = (
            await session.scalars(
                select(SymbolDirectory)
                .where(
                    or_(
                        SymbolDirectory.code.like(pattern, escape="\\"),
                        SymbolDirectory.name.like(pattern, escape="\\"),
                    )
                )
                .limit(_CANDIDATES)
            )
        ).all()

    ranked = sorted(rows, key=lambda r: (_rank(r, q), r.code))
    return [
        StockBasic(
            symbol=r.symbol,
            name=r.name,
            exchange=r.exchange,
            board=r.board,
        )
        for r in ranked[:limit]
    ]


def _rank(row: SymbolDirectory, q: str) -> int:
    """相关度。数字越小越靠前。

    搜"600" 时 600000 应该比"名称里带 600 的"更靠前；
    搜"茅台"时"贵州茅台"应该比"茅台机场"（假设有）更靠前。
    """
    if row.code == q:
        return 0
    if row.name == q:
        return 1
    if row.code.startswith(q):
        return 2
    if row.name.startswith(q):
        return 3
    if q in row.code:
        return 4
    return 5


async def name_of(symbol: str) -> str:
    """查代码对应的名称。代码直达的结果靠它补上名字，否则界面只显示一串数字。"""
    async with get_sessionmaker()() as session:
        row = await session.get(SymbolDirectory, symbol)
        return row.name if row else ""


async def is_ready() -> bool:
    """目录里有数据吗？

    有就完全信任本地结果、绝不再走网络；没有才回退到现场问数据源。
    这个判断必须便宜 —— 每次搜索都要问一次。
    """
    global _count
    if _count is None:
        async with get_sessionmaker()() as session:
            _count = int((await session.scalar(select(func.count(SymbolDirectory.symbol)))) or 0)
    return _count > 0


# ---------------------------------------------------------------- 刷新


async def status() -> IndexStatus:
    async with get_sessionmaker()() as session:
        count = int((await session.scalar(select(func.count(SymbolDirectory.symbol)))) or 0)
        newest = await session.scalar(select(func.max(SymbolDirectory.updated_at)))
        sources = list(
            (await session.scalars(select(SymbolDirectory.source).distinct())).all()
        )

    global _count
    _count = count

    ttl = timedelta(hours=get_settings().symbol_index_ttl_hours)
    # 空目录也算过期 —— 启动时才会去建
    stale = newest is None or now_utc() - newest > ttl

    return IndexStatus(
        count=count,
        updated_at=newest.isoformat() if newest is not None else None,
        stale=stale,
        refreshing=_refresh_task is not None and not _refresh_task.done(),
        sources=[s for s in sources if s],
    )


async def refresh() -> int:
    """拉一次全市场列表并增量写入。返回新增+更新的条数。

    只增不删：一次拉漏了不该把已有的好数据删掉。
    """
    items = await get_registry().search("", _FETCH_LIMIT)
    source = get_registry().last_sources.get("search", "") or ""
    if not items:
        log.warning("代码目录刷新：数据源返回空列表，目录保持不变")
        return 0

    changed = 0
    async with get_sessionmaker()() as session:
        existing = {
            row.symbol: row for row in (await session.scalars(select(SymbolDirectory))).all()
        }
        for basic in items:
            sym = try_normalize(basic.symbol)
            if sym is None:
                continue
            row = existing.get(sym.canonical)
            if row is None:
                session.add(
                    SymbolDirectory(
                        symbol=sym.canonical,
                        code=sym.code,
                        name=basic.name,
                        exchange=sym.exchange,
                        board=sym.board or "",
                        source=source,
                    )
                )
                changed += 1
            elif basic.name and row.name != basic.name:
                # 改名的股票（ST、重组）要跟上
                row.name = basic.name
                row.source = source
                row.updated_at = now_utc()
                changed += 1
        await session.commit()

    global _count
    _count = None  # 下次 is_ready() 重新数
    log.info("代码目录刷新完成：源=%s 收到 %d 条，写入 %d 条", source or "?", len(items), changed)
    return changed


def schedule_refresh() -> bool:
    """在后台刷新目录。已经在刷就不重复排。

    返回是否真的排了一个新任务。绝不阻塞调用方 ——
    搜索请求不该为了建目录而等 5000 行下载。
    """
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return False

    async def _run() -> None:
        try:
            await refresh()
        except Exception as exc:  # 目录建不起来不该影响任何其他功能
            log.warning("代码目录刷新失败（搜索将退回实时查询）：%s", exc)

    _refresh_task = asyncio.create_task(_run())
    return True


async def refresh_if_needed() -> None:
    """启动时调用：目录空了或过期了就在后台补。"""
    if not get_settings().symbol_index_autorefresh:
        return
    st = await status()
    if st.count == 0 or st.stale:
        schedule_refresh()


def reset_state() -> None:
    """测试用：清掉进程内的计数缓存与任务句柄。"""
    global _count, _refresh_task
    _count = None
    _refresh_task = None
