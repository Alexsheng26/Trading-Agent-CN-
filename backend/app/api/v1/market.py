"""行情接口。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.errors import DataNotFound, SymbolError
from app.data.registry import get_registry
from app.data.symbols import normalize, try_normalize
from app.schemas.market import BarOut, IndexQuoteOut, IndicatorsOut, QuoteOut, StockBasicOut
from app.services.indicators import compute_snapshot
from app.services.market_data import MarketDataService

router = APIRouter(prefix="/market", tags=["行情"])


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
    """按代码或名称搜索。

    输入合法股票代码时，**不依赖数据源也一定能返回** —— 代码规范化是纯本地
    计算。这很重要：按名称搜索需要拉全市场快照（AKShare 那个接口 5000+ 行，
    慢或挂都是常态），但用户输入 600519 这种明确代码时没理由陪着一起等。
    """
    direct = try_normalize(q)
    hits: list[StockBasicOut] = []

    try:
        results = await get_registry().search(q, limit)
    except DataNotFound as exc:
        # 所有数据源都拿不到。若输入是合法代码，仍然放行，让用户能进详情页；
        # 否则才报错。
        if direct is None:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        results = []

    hits = [StockBasicOut(**asdict(r)) for r in results]

    # 代码直达：确保它出现在结果里，且排在第一位
    if direct is not None and not any(h.symbol == direct.canonical for h in hits):
        hits.insert(
            0,
            StockBasicOut(
                symbol=direct.canonical,
                name="",  # 数据源没返回名称时留空，前端显示代码
                exchange=direct.exchange,
                board=direct.board,
            ),
        )
    return hits[:limit]


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
