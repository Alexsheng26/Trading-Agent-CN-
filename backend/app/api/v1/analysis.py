"""分析接口。

两步式设计（而不是一个请求直接流式返回），是为了手机场景：

    POST /analysis/runs          -> 立刻拿到 run_id，后台开始跑
    GET  /analysis/runs/{id}/stream  -> SSE 跟进；断线后带 Last-Event-ID 重连续传
    GET  /analysis/runs/{id}     -> 任何时候读最终结果（已落库）

锁屏、切 App、隧道断网都不会让这次分析白跑。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.profile import ProfileError, describe_options
from app.core.config import get_settings
from app.core.errors import SymbolError
from app.db.models import AnalysisRun
from app.db.session import get_session
from app.schemas.analysis import (
    AnalysisRequest,
    AnalysisRunListOut,
    AnalysisRunOut,
    AnalysisRunSummaryOut,
    AnalysisStartedOut,
)
from app.services.analysis_service import AnalysisService
from app.services.run_store import store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["分析"])


@router.get("/options", summary="可选的分析深度与模型")
async def options() -> dict:
    """每一档在**当前模型厂商**下实际会改变什么。

    同一个"深度"在 Claude 下是调高推理投入，在 DeepSeek 下是换推理模型 ——
    所以说明文字由后端按配置生成，前端不写死。
    """
    return describe_options(get_settings())


@router.post("/runs", response_model=AnalysisStartedOut, status_code=202, summary="发起一次分析")
async def create_run(
    payload: AnalysisRequest,
    session: AsyncSession = Depends(get_session),
) -> AnalysisStartedOut:
    service = AnalysisService(session)
    try:
        run_id = await service.start(
            payload.symbol,
            payload.horizon,
            payload.user_note,
            depth=payload.depth,
            model=payload.model,
        )
    except (SymbolError, ProfileError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AnalysisStartedOut(
        run_id=run_id,
        symbol=payload.symbol,
        status="pending",
        stream_url=f"/api/v1/analysis/runs/{run_id}/stream",
    )


@router.get("/runs/{run_id}/stream", summary="SSE 实时进度（支持断线续传）")
async def stream_run(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    from_seq: int | None = Query(
        default=None, description="从第几条事件开始，优先级低于 Last-Event-ID"
    ),
) -> StreamingResponse:
    if store.get(run_id) is None:
        # 缓冲已过期或服务重启过 —— 让前端改去读落库的最终结果
        raise HTTPException(
            status_code=409,
            detail="该分析的实时事件已不在内存中，请直接读取 GET /analysis/runs/{run_id}",
        )

    start = 0
    if last_event_id is not None and last_event_id.lstrip("-").isdigit():
        start = int(last_event_id) + 1
    elif from_seq is not None:
        start = max(0, from_seq)

    async def generate() -> AsyncIterator[bytes]:
        try:
            async for seq, event in store.read_from(run_id, start):
                if await request.is_disconnected():
                    log.debug("客户端断开 run=%s", run_id)
                    break
                if event is None:
                    # 心跳：SSE 注释行，前端不会当成数据
                    yield b": keepalive\n\n"
                    continue
                payload = json.dumps(event.as_dict(), ensure_ascii=False)
                yield f"id: {seq}\nevent: {event.type}\ndata: {payload}\n\n".encode()
        except Exception as exc:  # pragma: no cover - 兜底，别让连接静默挂死
            log.exception("SSE 推送异常 run=%s", run_id)
            err = json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n".encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # 关掉 nginx 缓冲，否则事件会被攒着一起发，流式效果全无
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}", response_model=AnalysisRunOut, summary="读取分析结果")
async def get_run(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> AnalysisRunOut:
    run = await AnalysisService(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    return _to_out(run)


@router.get("/runs", response_model=AnalysisRunListOut, summary="历史分析列表")
async def list_runs(
    symbol: str | None = Query(default=None, description="按标的过滤，如 600519.SH"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AnalysisRunListOut:
    runs, total = await AnalysisService(session).list_runs(symbol, limit, offset)
    return AnalysisRunListOut(
        items=[
            AnalysisRunSummaryOut(
                id=r.id,
                symbol=r.symbol,
                name=r.name,
                horizon=r.horizon,
                status=r.status,
                action=(r.decision or {}).get("action"),
                conviction=(r.decision or {}).get("conviction"),
                depth=(r.config or {}).get("depth"),
                created_at=r.created_at,
                elapsed_sec=r.elapsed_sec,
            )
            for r in runs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/runs/{run_id}/cancel", summary="取消进行中的分析")
async def cancel_run(run_id: str) -> dict:
    cancelled = store.cancel(run_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="没有正在进行的该分析任务")
    return {"run_id": run_id, "status": "cancelling"}


@router.delete("/runs/{run_id}", status_code=204, summary="删除分析记录")
async def delete_run(run_id: str, session: AsyncSession = Depends(get_session)) -> None:
    if not await AnalysisService(session).delete(run_id):
        raise HTTPException(status_code=404, detail="分析记录不存在")


def _to_out(run: AnalysisRun) -> AnalysisRunOut:
    return AnalysisRunOut(
        id=run.id,
        symbol=run.symbol,
        name=run.name,
        horizon=run.horizon,
        user_note=run.user_note,
        status=run.status,
        error=run.error,
        decision=run.decision,  # type: ignore[arg-type]
        outputs=[
            {
                "key": o.key,
                "label": o.label,
                "stage": o.stage,
                "content": o.content,
                "thinking": o.thinking,
                "signal": o.signal,
                "confidence": o.confidence,
                "error": o.error,
                "usage": o.usage,
            }
            for o in run.outputs
        ],  # type: ignore[arg-type]
        data_sources=run.data_sources,
        data_warnings=run.data_warnings,
        usage=run.usage,
        llm_provider=run.llm_provider,
        config=run.config,
        elapsed_sec=run.elapsed_sec,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )
