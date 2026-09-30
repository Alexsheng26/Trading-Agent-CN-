"""全市场代码-名称目录。

**这是"搜索一直卡在搜索中"的根治方案。**

按名称搜索必须有全市场列表。之前每次搜索都现场去问数据源，于是：
AKShare 那个全市场接口 5000+ 行，慢；它靠抓第三方网页，挂了就整体失效；
超时保护只能让它 12 秒后报错，没法让它出结果。

改成：成功拉到一次就落库，之后搜索纯本地查询（毫秒级），
数据源事后挂掉也照样能搜。启动时在后台建目录，不阻塞任何请求。

目录同时收录股票和场内 ETF，两份列表分别拉取、互不拖累：
ETF 列表挂了不影响股票，反之亦然。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.core.clock import now_utc
from app.core.config import get_settings
from app.core.errors import DataNotFound
from app.data.base import StockBasic
from app.data.registry import get_registry
from app.data.symbols import try_normalize
from app.db.models import SymbolDirectory
from app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

#: 一次拉全市场的上限。A股总数 5400 左右，留足余量。
_FETCH_LIMIT = 8000

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
    #: 其中 ETF 有多少只。0 说明 ETF 列表还没拉成功，按名称搜不到 ETF
    etf_count: int = 0


# ---------------------------------------------------------------- 查询


def _escape_like(text: str) -> str:
    """转义 LIKE 的通配符 —— 用户搜 "100%" 不该匹配到所有东西。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def lookup(query: str, limit: int = 20) -> list[StockBasic]:
    """在本地目录里搜。不发任何网络请求。

    分三档查、按档次拼结果：完全相等 → 前缀匹配 → 包含。
    分档是必须的：如果只发一条 `LIKE '%关键词%'` 再在 Python 里排序，
    就得先限制候选行数（否则搜"科技"会捞回几百行），而一旦截断，
    真正想要的那条精确匹配完全可能落在截断之外。
    分档之后每档自带 LIMIT，既有界又不会漏掉精确匹配。
    """
    q = query.strip()
    if not q:
        return []

    esc = _escape_like(q)
    code, name = SymbolDirectory.code, SymbolDirectory.name
    tiers = (
        or_(code == q, name == q),
        or_(code.like(f"{esc}%", escape="\\"), name.like(f"{esc}%", escape="\\")),
        or_(code.like(f"%{esc}%", escape="\\"), name.like(f"%{esc}%", escape="\\")),
    )

    out: list[StockBasic] = []
    seen: set[str] = set()
    async with get_sessionmaker()() as session:
        for where in tiers:
            if len(out) >= limit:
                break
            rows = (
                await session.scalars(
                    # 同一档内按代码排序，保证结果稳定 —— 翻译成人话就是
                    # 同样的关键词每次出来的顺序都一样
                    select(SymbolDirectory).where(where).order_by(code).limit(limit * 2)
                )
            ).all()
            for r in rows:
                if r.symbol in seen:
                    continue
                seen.add(r.symbol)
                out.append(
                    StockBasic(
                        symbol=r.symbol, name=r.name, exchange=r.exchange, board=r.board
                    )
                )
                if len(out) >= limit:
                    break
    return out[:limit]


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
        etf_count = int(
            (
                await session.scalar(
                    select(func.count(SymbolDirectory.symbol)).where(
                        SymbolDirectory.board == "ETF"
                    )
                )
            )
            or 0
        )

    # 顺手把 is_ready() 的计数缓存刷新一下，省掉下次搜索的那次 COUNT
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
        etf_count=etf_count,
    )


async def refresh() -> int:
    """拉一次全市场列表（股票 + ETF）并增量写入。返回新增+更新的条数。

    只增不删：一次拉漏了不该把已有的好数据删掉。
    两份列表都拉不到才报错；拉到一份就先写一份。
    """
    registry = get_registry()
    # 全市场列表天生很慢（AKShare 要翻 54 页、每页 sleep 1 秒，60-90 秒），
    # 这里是后台任务，给足预算。默认的 12 秒会让它永远失败。
    budget = get_settings().symbol_index_timeout
    batches: list[tuple[list[StockBasic], str]] = []
    errors: list[str] = []

    try:
        stocks = await registry.search("", _FETCH_LIMIT, attempt_timeout=budget)
        batches.append((stocks, registry.last_sources.get("search", "") or ""))
    except DataNotFound as exc:
        errors.append(f"股票列表：{exc}")
    try:
        etfs = await registry.etf_list(attempt_timeout=budget)
        batches.append((etfs, registry.last_sources.get("etf_list", "") or ""))
    except DataNotFound as exc:
        errors.append(f"ETF 列表：{exc}")

    for err in errors:
        log.warning("代码目录刷新：%s", err)
    if not batches:
        raise DataNotFound("；".join(errors))

    total = 0
    changed = 0
    for items, source in batches:
        total += len(items)
        changed += await _write(items, source)

    global _count
    _count = None  # 下次 is_ready() 重新数
    log.info(
        "代码目录刷新完成：源=%s 收到 %d 条，写入 %d 条",
        "、".join(src or "?" for _, src in batches),
        total,
        changed,
    )
    return changed


async def _write(items: list[StockBasic], source: str) -> int:
    """增量写入一批代码。返回新增+更新的条数。"""
    if not items:
        log.warning("代码目录刷新：%s 返回空列表，跳过", source or "数据源")
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
                row = SymbolDirectory(
                    symbol=sym.canonical,
                    code=sym.code,
                    name=basic.name,
                    exchange=sym.exchange,
                    board=sym.board or "",
                    source=source,
                )
                session.add(row)
                # 记下来：同一批里再出现同一个代码时走"更新"分支，而不是重复插入撞主键
                existing[sym.canonical] = row
                changed += 1
            elif basic.name and row.name != basic.name:
                # 改名的股票（ST、重组）要跟上
                row.name = basic.name
                row.source = source
                row.updated_at = now_utc()
                changed += 1
        await session.commit()
    return changed


def schedule_refresh() -> bool:
    """在后台刷新目录。已经在刷就不重复排。

    返回是否真的排了一个新任务。绝不阻塞调用方 ——
    搜索请求不该为了建目录而等 5000 行下载。
    """
    global _refresh_task
    # 关掉自动刷新就一条路都不许偷偷去拉：
    # 漏了这个检查的后果不止是"设置项不生效" —— 每次搜索都可能派一个后台
    # 写库任务，任务和请求的生命周期不一致，测试里表现为随机卡死。
    if not get_settings().symbol_index_autorefresh:
        return False
    if _refresh_task is not None and not _refresh_task.done():
        return False

    async def _run() -> None:
        try:
            await refresh()
        except Exception as exc:  # 目录建不起来不该影响任何其他功能
            log.warning("代码目录刷新失败（搜索将退回实时查询）：%s", exc)

    _refresh_task = asyncio.create_task(_run())
    return True


async def refresh_if_stale() -> bool:
    """目录过期了就在后台补一次。返回是否排了刷新任务。

    给"按名称搜不到"这条路用：可能是目录建好之后才上市的新股。
    刻意不阻塞调用方 —— 本次搜索照常返回空，下次就能搜到了。
    """
    if not get_settings().symbol_index_autorefresh:
        return False  # 提前返回，省掉下面那几次查询
    if (await status()).stale:
        return schedule_refresh()
    return False


async def refresh_if_needed() -> None:
    """启动时调用：目录空了或过期了就在后台补。"""
    if not get_settings().symbol_index_autorefresh:
        return
    st = await status()
    # etf_count == 0：老版本建的目录里没有 ETF，升级后第一次启动就补上，
    # 不用等 24 小时过期
    if st.count == 0 or st.stale or st.etf_count == 0:
        schedule_refresh()


async def cancel_refresh() -> None:
    """关停时取消未跑完的后台刷新。

    不取消的话，进程退出时会留下 "Task was destroyed but it is pending"，
    更糟的是任务可能在数据库连接被关掉之后才醒过来。
    """
    global _refresh_task
    task = _refresh_task
    _refresh_task = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def reset_state() -> None:
    """测试用：清掉进程内的计数缓存与任务句柄。"""
    global _count, _refresh_task
    _count = None
    _refresh_task = None
