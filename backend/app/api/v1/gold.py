"""金价接口：首页的金价卡片，金价页的分时和日K / 周K。

品种：AU9999（上金所 Au99.99，元/克）、XAU（伦敦金，美元/盎司），见 app/data/gold.py。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.core.errors import DataNotFound
from app.data.gold import GOLD_SPECS, GoldSpec, gold_spec
from app.data.registry import get_registry
from app.schemas.gold import GoldIntradayOut, GoldItemOut
from app.schemas.market import BarOut
from app.services import gold as gold_service

router = APIRouter(prefix="/gold", tags=["金价"])


def _spec(code: str) -> GoldSpec:
    spec = gold_spec(code)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"没有这个黄金品种：{code}（可选 {'、'.join(GOLD_SPECS)}）"
        )
    return spec


@router.get("", response_model=list[GoldItemOut], summary="金价（上金所 Au99.99 + 伦敦金）")
async def gold() -> list[GoldItemOut]:
    """每个品种各取各的：一个取不到时它的 `error` 写原因，另一个照常返回。"""
    items = await gold_service.gold_overview(get_registry())
    return [GoldItemOut.build(item) for item in items]


@router.get("/{code}/bars", response_model=list[BarOut], summary="金价日K / 周K")
async def gold_bars(
    code: str,
    period: Literal["day", "week"] = Query(default="day", description="日K / 周K"),
    days: int = Query(default=250, ge=5, le=3000, description="回溯自然日数"),
) -> list[BarOut]:
    spec = _spec(code)
    try:
        rows = await gold_service.gold_bars(get_registry(), spec.code, period, days)
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


@router.get("/{code}/intraday", response_model=GoldIntradayOut, summary="金价分时（当前交易日）")
async def gold_intraday(code: str) -> GoldIntradayOut:
    spec = _spec(code)
    if not spec.has_intraday:
        raise HTTPException(
            status_code=404,
            detail=f"{spec.name}没有分时：免费数据源里找不到可靠的{spec.name}分时，只有日K和周K",
        )
    try:
        data = await gold_service.gold_intraday(get_registry(), spec.code)
    except DataNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return GoldIntradayOut.build(data)
