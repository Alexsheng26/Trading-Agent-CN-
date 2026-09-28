"""FastAPI 应用入口。

启动：
    uvicorn app.main:app --reload --port 8000
接口文档：
    http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import (
    DataNotFound,
    LLMError,
    ProviderUnavailable,
    SymbolError,
    TradingAgentError,
)
from app.core.logging import setup_logging
from app.db.session import dispose_db, init_db
from app.services.run_store import store
from app.services.symbol_index import cancel_refresh, refresh_if_needed

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info("启动中：环境=%s 模型=%s", settings.app_env, settings.llm_provider)
    if settings.llm_provider == "echo":
        log.warning("⚠️  当前为离线桩模型（LLM_PROVIDER=echo），分析结果无参考价值")
    await init_db()
    # 建"全市场代码-名称目录"：按名称搜索靠它。
    # 这里只查一下目录状态（本地 SQL，很快）并派一个后台任务 ——
    # 真正拉 5000 行不在启动路径上，失败也只是退回实时查询。
    await refresh_if_needed()
    yield
    # 关停顺序很重要：先把还在跑的后台任务收干净，再关数据库。
    # 反过来的话，任务会在连接被关掉之后才醒过来 —— 更糟的是它可能正卡在
    # 一个没提交的写事务上，把 SQLite 的写锁一直握着。
    cancelled = await store.cancel_all()
    if cancelled:
        log.info("已取消 %d 个进行中的分析", cancelled)
    await cancel_refresh()
    await dispose_db()
    log.info("已关闭")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Trading-Agent-CN",
        description=(
            "A股 AI 多智能体投研助手。\n\n"
            "⚠️ 本服务输出由 AI 生成，仅供研究参考，不构成投资建议。"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # 让前端能读到 SSE 的 Last-Event-ID
        expose_headers=["Last-Event-ID"],
    )

    app.include_router(api_router)

    # ---------- 领域异常 -> HTTP 状态码 ----------

    @app.exception_handler(SymbolError)
    async def _symbol_error(request: Request, exc: SymbolError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(DataNotFound)
    async def _data_not_found(request: Request, exc: DataNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ProviderUnavailable)
    async def _provider_unavailable(
        request: Request, exc: ProviderUnavailable
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(LLMError)
    async def _llm_error(request: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(TradingAgentError)
    async def _domain_error(request: Request, exc: TradingAgentError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": "Trading-Agent-CN",
            "docs": "/docs",
            "api": "/api/v1",
            "disclaimer": "AI 生成内容，仅供研究参考，不构成投资建议",
        }

    return app


app = create_app()
