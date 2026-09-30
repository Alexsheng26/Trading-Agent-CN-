"""多智能体流水线编排。

流程：

    ┌──────────────── 第一阶段：四位分析师（并发） ────────────────┐
    │  基本面    技术面    消息面    资金面                        │
    └───────────────────────────┬────────────────────────────────┘
                                ▼
    ┌──────── 第二阶段：多空辩论（串行，N 轮，能看到对手发言）────────┐
    │  多头 → 空头 → 多头 → 空头 …                                │
    └───────────────────────────┬────────────────────────────────┘
                                ▼
                 第三阶段：风控经理（仓位上限 + 止损）
                                ▼
                 第四阶段：交易决策员（结构化 JSON 决策）

对外是一个异步事件流（`AsyncIterator[Event]`），API 层直接把它转成 SSE 推给前端，
所以手机上能看到每个智能体逐字produce的过程，而不是干等几分钟。

并发阶段的流式增量通过一个 `asyncio.Queue` 汇聚，每个片段都带 `agent` 标识，
前端按 agent 分卡片渲染即可。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agents.base import AgentOutput, LLMAgent
from app.agents.context import AnalysisContext
from app.agents.profile import RunProfile, resolve_profile
from app.agents.prompts import DECISION_SCHEMA, DISCLAIMER, TRADER, with_discipline
from app.agents.roster import (
    ANALYST_CLASSES,
    BearResearcher,
    BullResearcher,
    RiskManager,
    _render_stage,
)
from app.core.clock import now_utc
from app.core.config import Settings, get_settings
from app.core.errors import LLMError
from app.llm.base import Completion, LLMClient, Message, Usage

log = logging.getLogger(__name__)

EventType = Literal[
    "run_start",
    "stage_start",
    "agent_start",
    "agent_delta",
    "agent_thinking",
    "agent_done",
    "decision",
    "run_done",
    "error",
]


@dataclass(slots=True)
class Event:
    """推给前端的一条事件。"""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "ts": self.ts, **self.data}


STAGE_LABELS = {
    "analysts": "多维度分析",
    "debate": "多空辩论",
    "risk": "风险评估",
    "decision": "交易决策",
}


class AnalysisOrchestrator:
    def __init__(
        self,
        llm: LLMClient,
        settings: Settings | None = None,
        profile: RunProfile | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings or get_settings()
        # 不指定档位就是标准档 —— 和引入档位之前的行为完全一致
        self._profile = profile or resolve_profile(self._settings, "standard")

    # ------------------------------------------------------------ 主流程

    async def run(self, ctx: AnalysisContext, run_id: str | None = None) -> AsyncIterator[Event]:
        run_id = run_id or uuid.uuid4().hex
        board: dict[str, AgentOutput] = {}
        started = time.time()

        profile = self._profile

        yield Event(
            "run_start",
            {
                "run_id": run_id,
                "symbol": ctx.symbol.canonical,
                "name": ctx.display_name,
                "horizon": ctx.horizon,
                "as_of": ctx.as_of.isoformat(),
                "data_sources": ctx.snapshot.sources,
                "data_warnings": ctx.snapshot.warnings,
                "pipeline": self._pipeline_plan(),
                "config": profile.as_record(),
            },
        )

        try:
            # ---------- 第一阶段：分析师并发 ----------
            yield Event("stage_start", {"stage": "analysts", "label": STAGE_LABELS["analysts"]})
            analysts = [self._agent(cls, "analyst") for cls in ANALYST_CLASSES]
            async for event in self._run_concurrent(analysts, ctx, board):
                yield event

            # ---------- 第二阶段：多空辩论串行 ----------
            rounds = profile.debate_rounds
            if rounds:
                yield Event("stage_start", {"stage": "debate", "label": STAGE_LABELS["debate"]})
                for round_no in range(1, rounds + 1):
                    for cls in (BullResearcher, BearResearcher):
                        agent = self._agent(cls, "analyst", round_no=round_no)
                        async for event in self._run_one(agent, ctx, board):
                            yield event

            # ---------- 第三阶段：风控 ----------
            yield Event("stage_start", {"stage": "risk", "label": STAGE_LABELS["risk"]})
            async for event in self._run_one(self._agent(RiskManager, "decision"), ctx, board):
                yield event

            # ---------- 第四阶段：交易决策 ----------
            yield Event("stage_start", {"stage": "decision", "label": STAGE_LABELS["decision"]})
            decision, decision_usage = await self._decide(ctx, board)
            yield Event("decision", {"decision": decision})

            total = sum((o.usage for o in board.values()), Usage()) + decision_usage
            yield Event(
                "run_done",
                {
                    "run_id": run_id,
                    "elapsed_sec": round(time.time() - started, 1),
                    "usage": total.as_dict(),
                    "agent_count": len(board),
                    "disclaimer": DISCLAIMER,
                    "finished_at": now_utc().isoformat(),
                },
            )
        except asyncio.CancelledError:
            # 用户关掉页面时正常取消，不当成错误
            raise
        except Exception as exc:
            log.exception("分析流水线异常 run_id=%s", run_id)
            yield Event("error", {"run_id": run_id, "message": str(exc)})

    # ------------------------------------------------------------ 单个智能体

    async def _run_one(
        self, agent: LLMAgent, ctx: AnalysisContext, board: dict[str, AgentOutput]
    ) -> AsyncIterator[Event]:
        yield Event(
            "agent_start",
            {"agent": agent.key, "label": agent.label, "stage": agent.stage},
        )
        async for item in agent.run_stream(ctx, board):
            if isinstance(item, AgentOutput):
                board[item.key] = item
                yield Event("agent_done", {"agent": item.key, "output": item.as_dict()})
            elif item.text:
                yield Event("agent_delta", {"agent": agent.key, "text": item.text})
            elif item.thinking:
                yield Event("agent_thinking", {"agent": agent.key, "text": item.thinking})

    # ------------------------------------------------------------ 并发阶段

    async def _run_concurrent(
        self, agents: list[LLMAgent], ctx: AnalysisContext, board: dict[str, AgentOutput]
    ) -> AsyncIterator[Event]:
        """并发跑多个智能体，增量事件通过队列交错吐出。"""
        for agent in agents:
            yield Event(
                "agent_start",
                {"agent": agent.key, "label": agent.label, "stage": agent.stage},
            )

        queue: asyncio.Queue[Event | None] = asyncio.Queue()

        async def pump(agent: LLMAgent) -> None:
            try:
                async for item in agent.run_stream(ctx, board):
                    if isinstance(item, AgentOutput):
                        board[item.key] = item
                        await queue.put(
                            Event("agent_done", {"agent": item.key, "output": item.as_dict()})
                        )
                    elif item.text:
                        await queue.put(
                            Event("agent_delta", {"agent": agent.key, "text": item.text})
                        )
                    elif item.thinking:
                        await queue.put(
                            Event("agent_thinking", {"agent": agent.key, "text": item.thinking})
                        )
            except Exception as exc:  # 单个智能体挂掉不能拖垮整个阶段
                log.warning("%s 并发执行失败：%s", agent.key, exc)
                failed = AgentOutput(
                    key=agent.key, label=agent.label, stage=agent.stage, error=str(exc)
                )
                board[agent.key] = failed
                await queue.put(
                    Event("agent_done", {"agent": agent.key, "output": failed.as_dict()})
                )

        tasks = [asyncio.create_task(pump(a)) for a in agents]

        async def closer() -> None:
            await asyncio.gather(*tasks, return_exceptions=True)
            await queue.put(None)

        closer_task = asyncio.create_task(closer())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            # 调用方提前中断（关页面）时，把子任务一并取消，别留孤儿
            for task in tasks:
                task.cancel()
            closer_task.cancel()
            await asyncio.gather(*tasks, closer_task, return_exceptions=True)

    # ------------------------------------------------------------ 决策

    async def _decide(
        self, ctx: AnalysisContext, board: dict[str, AgentOutput]
    ) -> tuple[dict[str, Any], Usage]:
        user_content = "\n".join(
            [
                f"标的：{ctx.display_name}（{ctx.symbol.canonical}）",
                f"分析周期：{ctx.horizon}",
                f"当前价：{_or_missing(ctx.indicators.close)}",
                "",
                "## 分析师报告",
                _render_stage(board, "analysts"),
                "",
                "## 多空辩论",
                _render_stage(board, "debate") or "（本次未进行辩论）",
                "",
                "## 风控意见",
                _render_stage(board, "risk"),
                "",
                "请输出最终决策 JSON。",
            ]
        )

        completion, fallback_note = await self._complete_decision(
            [ctx.market_brief(), with_discipline(TRADER)],
            [Message(role="user", content=user_content)],
        )

        decision = completion.data or {}
        if not decision:
            # 结构化输出失败时给一个安全的兜底，而不是抛异常让整次分析白跑
            log.warning("交易决策未能解析为 JSON，回落到保守默认值")
            decision = {
                "action": "观望",
                "conviction": 0,
                "position_pct": 0,
                "horizon": ctx.horizon,
                "entry_zone": "不建议",
                "stop_loss": "不适用",
                "take_profit": "不适用",
                "summary": "模型未能返回结构化决策，请查看各智能体的原始分析自行判断。",
                "key_reasons": [],
                "key_risks": ["决策环节输出解析失败，本次结论不可用"],
                "dissent": "",
                "data_quality": "严重缺失",
                "raw_text": completion.text[:2000],
            }

        if fallback_note:
            decision["fallback_note"] = fallback_note
        decision = _sanitize_decision(decision, ctx)
        return decision, completion.usage

    async def _complete_decision(
        self, system_blocks: list[str], messages: list[Message]
    ) -> tuple[Completion, str]:
        """跑决策这一步。换过模型、却没拿到可用决策时，用厂商默认模型重做一次。

        为什么需要：决策是唯一要求结构化 JSON 的一步。深度档可能把它交给
        推理模型，用户也可能手选一个模型 —— 这些模型不一定支持 JSON 输出，
        推理过长时还可能把输出额度耗尽、正文为空。那样整次分析会落到
        "观望 / 结论不可用"的兜底上，前面十来次调用全白跑。
        重做只发生在**模型被换过**时；默认模型自己失败就如实失败，不掩盖。

        返回 (结果, 说明)。说明非空表示发生过重做，会显示在决策面板上。
        """
        profile = self._profile
        model = profile.decision_model

        async def attempt(use_model: str | None) -> Completion:
            return await self._llm.complete(
                system_blocks,
                messages,
                model=use_model,
                max_tokens=profile.max_tokens(4000),
                effort=profile.decision_effort,
                json_schema=DECISION_SCHEMA,
            )

        can_retry = bool(profile.default_model and model and model != profile.default_model)
        try:
            completion = await attempt(model)
        except LLMError as exc:
            if not can_retry:
                raise
            log.warning("决策模型 %s 调用失败，改用 %s 重做：%s", model, profile.default_model, exc)
            retried = await attempt(profile.default_model)
            return retried, f"{model} 调用失败，这一步已改用 {profile.default_model} 重做"

        if completion.data or not can_retry:
            return completion, ""

        log.warning("决策模型 %s 未返回可解析的 JSON，改用 %s 重做", model, profile.default_model)
        retried = await attempt(profile.default_model)
        retried.usage = completion.usage + retried.usage
        return retried, f"{model} 没有返回可解析的决策，这一步已改用 {profile.default_model} 重做"

    # ------------------------------------------------------------ 辅助

    def _agent(
        self, cls: type[LLMAgent], role: Literal["analyst", "decision"], **kwargs: Any
    ) -> LLMAgent:
        """按档位装配一个智能体。

        分析师和辩论双方归 "analyst"，风控归 "decision" —— 和决策员同一档模型与推理投入。
        """
        p = self._profile
        analyst = role == "analyst"
        return cls(
            self._llm,
            p.analyst_model if analyst else p.decision_model,
            effort=p.analyst_effort if analyst else p.decision_effort,
            max_tokens=p.max_tokens(cls.max_tokens),
            **kwargs,
        )

    def _pipeline_plan(self) -> list[dict[str, Any]]:
        """告诉前端总共会有哪些智能体，好先把骨架画出来。"""
        plan: list[dict[str, Any]] = [
            {
                "stage": "analysts",
                "label": STAGE_LABELS["analysts"],
                "agents": [{"key": c.key, "label": c.label} for c in ANALYST_CLASSES],
            }
        ]
        rounds = self._profile.debate_rounds
        if rounds:
            debate_agents = []
            for round_no in range(1, rounds + 1):
                for base_key, base_label in (
                    ("bull_researcher", "多头研究员"),
                    ("bear_researcher", "空头研究员"),
                ):
                    debate_agents.append(
                        {
                            "key": f"{base_key}_r{round_no}",
                            "label": f"{base_label}（第 {round_no} 轮）",
                        }
                    )
            plan.append(
                {"stage": "debate", "label": STAGE_LABELS["debate"], "agents": debate_agents}
            )
        plan.append(
            {
                "stage": "risk",
                "label": STAGE_LABELS["risk"],
                "agents": [{"key": RiskManager.key, "label": RiskManager.label}],
            }
        )
        plan.append(
            {
                "stage": "decision",
                "label": STAGE_LABELS["decision"],
                "agents": [{"key": "trader", "label": "交易决策员"}],
            }
        )
        return plan


def _sanitize_decision(decision: dict[str, Any], ctx: AnalysisContext) -> dict[str, Any]:
    """夹紧模型给的数值，别让越界数字流到前端。"""
    out = dict(decision)

    conviction = _as_int(out.get("conviction"))
    out["conviction"] = max(0, min(100, conviction)) if conviction is not None else 0

    position = _as_float(out.get("position_pct"))
    # 单票仓位硬上限 50%：再看好也不该把身家压在一只 A股上
    out["position_pct"] = max(0.0, min(50.0, position)) if position is not None else 0.0

    if out.get("action") not in ("买入", "加仓", "持有", "减仓", "卖出", "观望"):
        out["action"] = "观望"
    if out.get("horizon") not in ("短线", "波段", "中线"):
        out["horizon"] = ctx.horizon

    for key in ("key_reasons", "key_risks"):
        value = out.get(key)
        out[key] = [str(v) for v in value][:4] if isinstance(value, list) else []

    out["disclaimer"] = DISCLAIMER
    return out


def _or_missing(value: Any) -> str:
    return "数据缺失" if value is None else str(value)


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
