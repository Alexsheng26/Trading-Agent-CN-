"""自选股接口。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DataNotFound, SymbolError
from app.data.registry import get_registry
from app.data.symbols import normalize
from app.db.models import WatchlistItem
from app.db.session import get_session
from app.schemas.market import QuoteOut
from app.schemas.watchlist import WatchlistCreate, WatchlistItemOut, WatchlistUpdate
from app.services import symbol_index
from app.services.quote_source import quote_with_fallback

log = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["自选股"])


@router.get("", response_model=list[WatchlistItemOut], summary="自选股列表")
async def list_items(
    with_quotes: bool = Query(default=True, description="是否一并返回行情"),
    session: AsyncSession = Depends(get_session),
) -> list[WatchlistItemOut]:
    rows = (
        (
            await session.execute(
                select(WatchlistItem).order_by(
                    WatchlistItem.group_name, WatchlistItem.sort_order, WatchlistItem.id
                )
            )
        )
        .scalars()
        .all()
    )

    items = [
        WatchlistItemOut(
            id=r.id,
            symbol=r.symbol,
            name=r.name,
            group_name=r.group_name,
            note=r.note,
            sort_order=r.sort_order,
            created_at=r.created_at,
        )
        for r in rows
    ]
    if not with_quotes or not items:
        return items

    # 并发取行情；个别失败就留空，不能因为一只票拖垮整个列表
    quotes = await asyncio.gather(
        *(_safe_quote(item.symbol) for item in items), return_exceptions=False
    )
    for item, quote in zip(items, quotes, strict=True):
        item.quote = quote
    return items


async def _safe_quote(symbol: str) -> QuoteOut | None:
    try:
        q = await quote_with_fallback(get_registry(), normalize(symbol))
    except (DataNotFound, SymbolError) as exc:
        log.info("自选股 %s 行情获取失败：%s", symbol, exc)
        return None
    return QuoteOut.from_quote(q)


@router.post("", response_model=WatchlistItemOut, status_code=201, summary="加入自选")
async def add_item(
    payload: WatchlistCreate, session: AsyncSession = Depends(get_session)
) -> WatchlistItemOut:
    try:
        symbol = normalize(payload.symbol)
    except SymbolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = (
        await session.execute(
            select(WatchlistItem).where(WatchlistItem.symbol == symbol.canonical)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"{symbol.canonical} 已在自选股中")

    # 取个名字存下来，列表页离线也能显示。
    # 先查本地代码目录：不走网络，而且 ETF 在那里是交易所简称（"半导体ETF"），
    # 基金档案里的是"国联安中证全指半导体ETF"这种长名字
    name = await symbol_index.name_of(symbol.canonical)
    if not name:
        try:
            basic = await get_registry().stock_basic(symbol)
            name = basic.name
        except (DataNotFound, SymbolError) as exc:
            log.info("取 %s 名称失败，先留空：%s", symbol.canonical, exc)

    item = WatchlistItem(
        symbol=symbol.canonical,
        name=name,
        group_name=payload.group_name or "默认",
        note=payload.note,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    return WatchlistItemOut(
        id=item.id,
        symbol=item.symbol,
        name=item.name,
        group_name=item.group_name,
        note=item.note,
        sort_order=item.sort_order,
        created_at=item.created_at,
        quote=await _safe_quote(item.symbol),
    )


@router.patch("/{item_id}", response_model=WatchlistItemOut, summary="修改分组/备注/排序")
async def update_item(
    item_id: int, payload: WatchlistUpdate, session: AsyncSession = Depends(get_session)
) -> WatchlistItemOut:
    item = await session.get(WatchlistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="自选股不存在")

    if payload.group_name is not None:
        item.group_name = payload.group_name or "默认"
    if payload.note is not None:
        item.note = payload.note
    if payload.sort_order is not None:
        item.sort_order = payload.sort_order
    await session.commit()
    await session.refresh(item)

    return WatchlistItemOut(
        id=item.id,
        symbol=item.symbol,
        name=item.name,
        group_name=item.group_name,
        note=item.note,
        sort_order=item.sort_order,
        created_at=item.created_at,
    )


@router.delete("/{item_id}", status_code=204, summary="移出自选")
async def delete_item(item_id: int, session: AsyncSession = Depends(get_session)) -> None:
    item = await session.get(WatchlistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="自选股不存在")
    await session.delete(item)
    await session.commit()
