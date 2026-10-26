"""v1 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import analysis, gold, health, market, watchlist

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(market.router)
api_router.include_router(gold.router)
api_router.include_router(analysis.router)
api_router.include_router(watchlist.router)
