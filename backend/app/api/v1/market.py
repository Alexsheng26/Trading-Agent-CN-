"""行情接口。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.config import get_settings
from app.core.errors import DataNotFound, SymbolError
from app.data.registry import get_registry, short_error
from app.data.symbols import normalize, try_normalize
from app.schemas.market import BarOut, IndexQuoteOut, IndicatorsOut, QuoteOut, StockBasicOut
from app.services import symbol_index
from app.services.indicators import compute_snapshot
from app.services.market_data import MarketDataService
from app.services.market_session import session_state

log = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["行情"])

#: StockBasicOut 接受的字段。asdict 会把 total_share 这些一起带出来，需要过滤。
_BASIC_FIELDS = frozenset(StockBasicOut.model_fields)


def _symbol(raw: str):  # type: ignore[no-untyped-def]
    try:
        return normalize(raw)
    except SymbolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/search", response_model=list[StockBasicOut], summary="搜索股票")
async def search(
    q: str = Query(min_length=1, max_length=32, description="代码或名称关键词"),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[StockBasicOut]:
    """按代码或名称搜索。三层保障，任何一层单独都能出结果：

    1. **本地代码目录**（`symbol_directory` 表）。建好之后搜索是纯本地查询，
       毫秒级返回，数据源挂了也照样能搜 —— 这是"一直卡在搜索中"的根治办法。
    2. **现场问数据源**。仅在目录还没建起来时走这条路，并顺手在后台建目录，
       下一次就不用等了。
    3. **代码直达**。输入合法代码时一定放行，代码规范化是纯本地计算，
       不依赖任何数据源。
    """
    direct = try_normalize(q)
    hits: list[StockBasicOut] = []

    if await symbol_index.is_ready():
        # 目录已建好，完全信任本地结果，不再走网络
        results = await symbol_index.lookup(q, limit)
        if not results and direct is None:
            # 按名称搜但目录里没有：可能是目录建好之后才上市的新股。
            # 在后台补一次目录（不阻塞本次请求），下次就能搜到。
            await symbol_index.refresh_if_stale()
    else:
        # 目录还是空的：现场问一次（有 provider_timeout 兜底），
        # 同时在后台把目录建起来
        symbol_index.schedule_refresh()
        try:
            results = list(await get_registry().search(q, limit))
        except DataNotFound as exc:
            # 所有数据源都拿不到。若输入是合法代码，仍然放行，让用户能进详情页；
            # 否则才报错。
            if direct is None:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            results = []

    hits = [
        StockBasicOut(**{k: v for k, v in asdict(r).items() if k in _BASIC_FIELDS})
        for r in results
    ]

    # 代码直达：确保它出现在结果里，且排在第一位
    if direct is not None and not any(h.symbol == direct.canonical for h in hits):
        hits.insert(
            0,
            StockBasicOut(
                symbol=direct.canonical,
                # 目录里有名称就补上，否则留空让前端显示代码
                name=await symbol_index.name_of(direct.canonical),
                exchange=direct.exchange,
                board=direct.board,
            ),
        )
    return hits[:limit]


@router.get("/symbols/status", summary="代码目录状态")
async def symbols_status() -> dict:
    """按名称搜索能不能用，看这个接口。"""
    st = await symbol_index.status()
    return asdict(st)


@router.post("/symbols/refresh", summary="重建代码目录")
async def symbols_refresh(
    wait: bool = Query(default=False, description="true 则等它跑完再返回"),
) -> dict:
    """手动重建全市场代码目录。

    正常不用管 —— 启动时会自动建、过期会自动补。
    数据源刚修好、想立刻让搜索可用时手动点一次。
    """
    if wait:
        try:
            changed = await symbol_index.refresh()
        except DataNotFound as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "started": True,
            "waited": True,
            "changed": changed,
            **asdict(await symbol_index.status()),
        }

    started = symbol_index.schedule_refresh()
    return {"started": started, "waited": False, **asdict(await symbol_index.status())}


@router.get("/quote/{symbol}", response_model=QuoteOut, summary="实时行情")
async def quote(symbol: str) -> QuoteOut:
    sym = _symbol(symbol)
    try:
        q = await get_registry().quote(sym)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return QuoteOut(**{k: v for k, v in asdict(q).items() if k != "volume"})


@router.get("/bars/{symbol}", response_model=list[BarOut], summary="日线K线")
async def bars(
    symbol: str,
    days: int = Query(default=120, ge=5, le=1200, description="回溯自然日数"),
    adjust: str = Query(default="qfq", pattern="^(qfq|hfq|none)$", description="复权方式"),
) -> list[BarOut]:
    sym = _symbol(symbol)
    end = date.today()
    try:
        rows = await get_registry().daily_bars(
            sym, end - timedelta(days=days), end, "" if adjust == "none" else adjust
        )
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        BarOut(
            date=b.date,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            amount=b.amount,
            change_pct=b.change_pct,
        )
        for b in rows
    ]


@router.get("/indicators/{symbol}", response_model=IndicatorsOut, summary="技术指标快照")
async def indicators(symbol: str) -> IndicatorsOut:
    sym = _symbol(symbol)
    end = date.today()
    try:
        rows = await get_registry().daily_bars(sym, end - timedelta(days=500), end)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return IndicatorsOut(**asdict(compute_snapshot(rows)))


@router.get("/indexes", response_model=list[IndexQuoteOut], summary="大盘指数")
async def indexes() -> list[IndexQuoteOut]:
    try:
        rows = await get_registry().index_quotes()
    except DataNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [IndexQuoteOut(**asdict(i)) for i in rows]


@router.get("/snapshot/{symbol}", summary="一次性取齐个股全维度数据")
async def snapshot(symbol: str) -> dict:
    """前端个股详情页用这一个接口就够了，省得串行发七八个请求。"""
    sym = _symbol(symbol)
    try:
        snap = await MarketDataService().snapshot(sym)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ind = compute_snapshot(snap.bars)
    return {
        "symbol": sym.canonical,
        "board": sym.board,
        "price_limit_pct": sym.price_limit_pct,
        "basic": asdict(snap.basic) if snap.basic else None,
        "quote": asdict(snap.quote) if snap.quote else None,
        "bars": [asdict(b) for b in snap.bars[-250:]],
        "indicators": asdict(ind),
        "financials": [asdict(f) for f in snap.financials],
        "news": [asdict(n) for n in snap.news],
        "money_flow": [asdict(m) for m in snap.money_flow],
        "indexes": [asdict(i) for i in snap.indexes],
        "sources": snap.sources,
        "warnings": snap.warnings,
    }


@router.get("/session", summary="当前交易时段")
async def session() -> dict:
    """前端据此决定要不要轮询行情。

    收盘后每 5 秒拉一次价格毫无意义 —— 既浪费请求，也会把免费数据源
    打得更快挂掉。
    """
    st = session_state()
    return {
        "phase": st.phase,
        "is_live": st.is_live,
        "poll_interval": st.poll_interval,
        "next_change": st.next_change.isoformat() if st.next_change else None,
        "as_of": st.as_of.isoformat(),
        "note": st.note,
    }


@router.get("/quotes", response_model=list[QuoteOut], summary="批量行情")
async def quotes(
    symbols: str = Query(description="逗号分隔的代码，最多 50 个"),
) -> list[QuoteOut]:
    """一次取多只票的行情。

    给自选股列表和实时轮询用 —— 逐只发请求的话，20 只自选股每轮就是
    20 个 HTTP 请求，移动网络下很不划算。
    取不到的票直接略过，不让一只坏票毁掉整个列表。
    """
    raw = [s.strip() for s in symbols.split(",") if s.strip()][:50]
    if not raw:
        return []

    syms = [s for s in (try_normalize(x) for x in raw) if s is not None]
    if not syms:
        return []
    registry = get_registry()
    failures: list[str] = []

    async def one(sym):  # type: ignore[no-untyped-def]
        try:
            return await registry.quote(sym)
        except (DataNotFound, SymbolError) as exc:
            failures.append(str(exc))
            return None

    results = await asyncio.gather(*(one(s) for s in syms))
    quotes = [q for q in results if q is not None]

    # 全军覆没要明确报错，不能返回 200 + 空数组。
    # 返回空数组的话前端会以为"取数成功、只是没有数据"，
    # 于是画出一排"—"还附上一句"行情更新于 22:04" —— 比报错更误导人。
    # 个别票取不到（停牌、退市）仍然只是跳过，不影响其余。
    if not quotes and failures:
        raise HTTPException(status_code=503, detail=failures[0])

    return [
        QuoteOut(**{k: v for k, v in asdict(q).items() if k != "volume"}) for q in quotes
    ]


@router.get("/diagnose", summary="数据源逐接口探测")
async def diagnose() -> dict:
    """逐个真实调用每个数据源的关键接口，报告成败与失败原因。

    存在的理由：healthcheck 只检查依赖能否导入，接口全挂时依然返回可用。
    出问题时想知道"到底哪一步失败、为什么"，只能翻容器日志 ——
    这个接口把答案直接摆出来。

    ⚠️ 会真的发网络请求，比较慢（每个源最多 12 秒 × 若干接口），
    只在排查时手动调用，不要放进轮询。
    """
    from app.data.base import (  # noqa: PLC0415
        CAP_BARS,
        CAP_INDEXES,
        CAP_NEWS,
        CAP_QUOTE,
        CAP_SEARCH,
    )

    registry = get_registry()
    probe_symbol = normalize("600519")
    end = date.today()

    probes: dict[str, tuple[str, object]] = {
        CAP_SEARCH: ("搜索", lambda p: p.search("600519", 1)),
        CAP_QUOTE: ("实时行情", lambda p: p.quote(probe_symbol)),
        CAP_BARS: ("日线", lambda p: p.daily_bars(probe_symbol, end - timedelta(days=10), end)),
        CAP_NEWS: ("新闻", lambda p: p.news(probe_symbol, 1)),
        CAP_INDEXES: ("大盘指数", lambda p: p.index_quotes()),
    }

    out: list[dict] = []
    for provider in registry._providers:  # noqa: SLF001 —— 诊断用途
        entry: dict = {"provider": provider.name, "interfaces": []}
        try:
            entry["dependency_ok"] = await provider.healthcheck()
        except Exception as exc:  # pragma: no cover
            entry["dependency_ok"] = False
            entry["dependency_error"] = short_error(exc)

        for cap, (label, call) in probes.items():
            if cap not in provider.capabilities:
                entry["interfaces"].append(
                    {"name": label, "status": "不支持", "detail": "该数据源未声明此能力"}
                )
                continue
            if not entry.get("dependency_ok"):
                entry["interfaces"].append(
                    {"name": label, "status": "跳过", "detail": "依赖不可用"}
                )
                continue
            try:
                async with asyncio.timeout(12):
                    result = await call(provider)  # type: ignore[operator]
            except TimeoutError:
                entry["interfaces"].append(
                    {"name": label, "status": "超时", "detail": "12 秒无响应"}
                )
            except Exception as exc:
                entry["interfaces"].append(
                    {"name": label, "status": "失败", "detail": short_error(exc)}
                )
            else:
                n = len(result) if isinstance(result, list) else 1
                entry["interfaces"].append(
                    {
                        "name": label,
                        "status": "正常" if n > 0 else "空结果",
                        "detail": f"返回 {n} 条",
                    }
                )
        out.append(entry)

    from app.core.version import APP_VERSION  # noqa: PLC0415
    from app.data.browser_transport import is_enabled  # noqa: PLC0415
    from app.services.host_probe import probe_hosts  # noqa: PLC0415

    settings = get_settings()
    # 主机可达性：分清"连不上这个域名"和"客户端被按指纹拒绝"这两种情况。
    # 只看接口报错是分不清的，而两者的处理方式完全不同。
    # 探测本身出问题不能让整个诊断挂掉 —— 这是用户排查时唯一的工具。
    try:
        hosts = await probe_hosts(settings.akshare_impersonate)
        host_results = [asdict(r) for r in hosts.results]
        host_summary = hosts.summary
    except Exception as exc:  # pragma: no cover —— 防御性
        log.warning("主机可达性探测失败：%s", exc)
        host_results = []
        host_summary = f"主机探测本身失败了：{short_error(exc)}"

    return {
        "checked_at": session_state().as_of.isoformat(),
        # 版本写进结果里：用户截图一发过来就知道跑的是哪一版，不用反推
        "app_version": APP_VERSION,
        "transport": {
            "browser_impersonation": settings.akshare_browser_transport,
            "impersonate": settings.akshare_impersonate,
            "active": is_enabled(),
        },
        # 尝试顺序：前面的源失败才会降级到后面，排查时这个顺序很关键
        "order": [p.name for p in registry._providers],  # noqa: SLF001 —— 诊断用途
        "providers": out,
        "hosts": host_results,
        "host_summary": host_summary,
    }
