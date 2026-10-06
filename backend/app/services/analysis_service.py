"""分析任务的编排与落库。

`start()` 立刻返回 run_id，真正的分析在后台任务里跑：
事件一边进 `RunStore`（供 SSE 实时推送和断线重放），一边在结束时落库。
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context import AnalysisContext
from app.agents.orchestrator import AnalysisOrchestrator, Event
from app.agents.profile import Depth, RunProfile, resolve_profile
from app.core.clock import now_utc
from app.core.config import get_settings
from app.core.runtime import restart_hint
from app.db.models import AgentOutputRow, AnalysisRun
from app.db.session import get_sessionmaker
from app.llm.factory import get_llm
from app.services.market_data import MarketDataService
from app.services.run_store import store

log = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()

    # ------------------------------------------------------------ 启动

    async def start(
        self,
        symbol_raw: str,
        horizon: str = "波段",
        user_note: str = "",
        *,
        depth: Depth = "standard",
        model: str | None = None,
    ) -> str:
        """登记一次分析并在后台启动，立刻返回 run_id。

        档位和模型在这里就解析并校验 —— 不合法直接 400，而不是建了记录再失败。
        """
        from app.data.symbols import normalize  # noqa: PLC0415 —— 避免循环导入

        symbol = normalize(symbol_raw)
        profile = resolve_profile(self._settings, depth, model)
        run_id = uuid.uuid4().hex

        run = AnalysisRun(
            id=run_id,
            symbol=symbol.canonical,
            horizon=horizon,
            user_note=user_note,
            status="pending",
            llm_provider=self._settings.llm_provider,
            config=profile.as_record(),
        )
        self._session.add(run)
        await self._session.commit()

        buffer = store.create(run_id)
        buffer.task = asyncio.create_task(
            self._execute(run_id, symbol_raw, horizon, user_note, profile)
        )
        return run_id

    # ------------------------------------------------------------ 后台执行

    async def _execute(
        self,
        run_id: str,
        symbol_raw: str,
        horizon: str,
        user_note: str,
        profile: RunProfile | None = None,
    ) -> None:
        """后台任务。用自己的 session —— 请求的 session 早就关了。"""
        from app.data.symbols import normalize  # noqa: PLC0415

        buffer = store.get(run_id)
        assert buffer is not None
        profile = profile or resolve_profile(self._settings, "standard")
        timeout = round(self._settings.analysis_timeout * profile.timeout_scale)

        outputs: dict[str, dict] = {}
        decision: dict | None = None
        meta: dict = {}
        error = ""

        try:
            async with asyncio.timeout(timeout):
                symbol = normalize(symbol_raw)
                snapshot = await MarketDataService().snapshot(symbol)
                ctx = AnalysisContext.build(snapshot, horizon=horizon, user_note=user_note)

                await self._set_status(run_id, "running", name=ctx.display_name)

                orchestrator = AnalysisOrchestrator(get_llm(), self._settings, profile)
                async for event in orchestrator.run(ctx, run_id=run_id):
                    buffer.append(event)
                    match event.type:
                        case "run_start":
                            # 溯源信息只在开头这条事件里，落库时要用
                            meta["data_sources"] = event.data.get("data_sources")
                            meta["data_warnings"] = event.data.get("data_warnings")
                        case "agent_done":
                            outputs[event.data["agent"]] = event.data["output"]
                        case "decision":
                            decision = event.data["decision"]
                        case "run_done":
                            meta.update(event.data)
                        case "error":
                            error = event.data.get("message", "未知错误")
        except TimeoutError:
            error = (
                f"分析超时（超过 {timeout} 秒）。可以改用“快速”档，"
                f"或在 .env 里调大 ANALYSIS_TIMEOUT，然后{restart_hint()}"
            )
            log.warning("run %s %s", run_id, error)
            buffer.append(Event("error", {"run_id": run_id, "message": error}))
        except asyncio.CancelledError:
            log.info("run %s 被取消", run_id)
            buffer.append(Event("error", {"run_id": run_id, "message": "分析已取消"}))
            await self._persist(run_id, outputs, decision, meta, "分析已取消", status="cancelled")
            buffer.finish()
            raise
        except Exception as exc:
            error = str(exc)
            log.exception("run %s 执行失败", run_id)
            buffer.append(Event("error", {"run_id": run_id, "message": error}))

        try:
            await self._persist(run_id, outputs, decision, meta, error)
        finally:
            buffer.finish()

    # ------------------------------------------------------------ 落库

    async def _set_status(self, run_id: str, status: str, *, name: str = "") -> None:
        async with get_sessionmaker()() as session:
            run = await session.get(AnalysisRun, run_id)
            if run is None:
                return
            run.status = status
            if name:
                run.name = name
            await session.commit()

    async def _persist(
        self,
        run_id: str,
        outputs: dict[str, dict],
        decision: dict | None,
        meta: dict,
        error: str,
        status: str | None = None,
    ) -> None:
        async with get_sessionmaker()() as session:
            run = await session.get(AnalysisRun, run_id)
            if run is None:
                log.warning("run %s 不在数据库里，跳过落库", run_id)
                return

            run.status = status or ("error" if error else "done")
            run.error = error
            run.decision = decision
            run.usage = meta.get("usage")
            run.data_sources = meta.get("data_sources")
            run.data_warnings = meta.get("data_warnings")
            run.elapsed_sec = meta.get("elapsed_sec")
            run.finished_at = now_utc()

            # 重跑同一个 run_id 时先清掉旧产出（正常流程不会发生，防御性）
            await session.execute(delete(AgentOutputRow).where(AgentOutputRow.run_id == run_id))
            for seq, (key, output) in enumerate(outputs.items()):
                session.add(
                    AgentOutputRow(
                        run_id=run_id,
                        seq=seq,
                        key=key,
                        label=output.get("label", ""),
                        stage=output.get("stage", ""),
                        content=output.get("content", ""),
                        thinking=output.get("thinking", ""),
                        signal=output.get("signal"),
                        confidence=output.get("confidence"),
                        error=output.get("error", ""),
                        usage=output.get("usage"),
                    )
                )
            await session.commit()
            log.info("run %s 已落库，状态 %s，智能体 %d 个", run_id, run.status, len(outputs))

    # ------------------------------------------------------------ 读取

    async def get(self, run_id: str) -> AnalysisRun | None:
        result = await self._session.execute(
            select(AnalysisRun).where(AnalysisRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is not None:
            # 显式加载关联产出（lazy load 在 async 下会炸）
            await self._session.refresh(run, ["outputs"])
        return run

    async def list_runs(
        self, symbol: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[AnalysisRun], int]:
        query = select(AnalysisRun).order_by(AnalysisRun.created_at.desc())
        count_query = select(func.count()).select_from(AnalysisRun)
        if symbol:
            query = query.where(AnalysisRun.symbol == symbol)
            count_query = count_query.where(AnalysisRun.symbol == symbol)

        total = (await self._session.execute(count_query)).scalar() or 0
        rows = (await self._session.execute(query.limit(limit).offset(offset))).scalars().all()
        return list(rows), total

    async def delete(self, run_id: str) -> bool:
        run = await self._session.get(AnalysisRun, run_id)
        if run is None:
            return False
        await self._session.delete(run)
        await self._session.commit()
        return True
