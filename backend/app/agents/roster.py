"""智能体名册：四位分析师 + 多空研究员 + 风控 + 交易员。"""

from __future__ import annotations

from app.agents.base import AgentOutput, LLMAgent
from app.agents.context import AnalysisContext
from app.agents.prompts import (
    BEAR_RESEARCHER,
    BULL_RESEARCHER,
    CAPITAL_ANALYST,
    FUNDAMENTAL_ANALYST,
    NEWS_ANALYST,
    RISK_MANAGER,
    TECHNICAL_ANALYST,
)

# ---------------------------------------------------------------- 第一阶段：分析师


class FundamentalAnalyst(LLMAgent):
    key = "fundamental_analyst"
    label = "基本面分析师"
    stage = "analysts"
    role_prompt = FUNDAMENTAL_ANALYST


class TechnicalAnalyst(LLMAgent):
    key = "technical_analyst"
    label = "技术面分析师"
    stage = "analysts"
    role_prompt = TECHNICAL_ANALYST


class NewsAnalyst(LLMAgent):
    key = "news_analyst"
    label = "消息面分析师"
    stage = "analysts"
    role_prompt = NEWS_ANALYST


class CapitalAnalyst(LLMAgent):
    key = "capital_analyst"
    label = "资金面分析师"
    stage = "analysts"
    role_prompt = CAPITAL_ANALYST


ANALYST_CLASSES = (FundamentalAnalyst, TechnicalAnalyst, NewsAnalyst, CapitalAnalyst)


# ---------------------------------------------------------------- 第二阶段：多空辩论


class _DebateAgent(LLMAgent):
    stage = "debate"
    max_tokens = 3500

    def __init__(self, llm, model=None, round_no: int = 1) -> None:  # type: ignore[no-untyped-def]
        super().__init__(llm, model)
        self.round_no = round_no
        # 每轮各自独立的 key，前端才能把多轮发言分开渲染
        self.key = f"{self.base_key}_r{round_no}"
        self.label = f"{self.base_label}（第 {round_no} 轮）"

    base_key = "debater"
    base_label = "研究员"

    def user_message(self, ctx: AnalysisContext, board: dict[str, AgentOutput]) -> str:
        parts = [
            f"标的：{ctx.display_name}（{ctx.symbol.canonical}）",
            f"这是第 {self.round_no} 轮辩论。",
            "",
            "## 四位分析师的报告",
            _render_stage(board, "analysts"),
        ]
        history = _render_stage(board, "debate")
        if history:
            parts += ["", "## 此前的辩论记录", history]
        if ctx.user_note:
            parts += ["", f"## 用户特别关注\n{ctx.user_note}"]
        parts += ["", self.instruction()]
        return "\n".join(parts)

    def instruction(self) -> str:
        return "请给出你的论点。"


class BullResearcher(_DebateAgent):
    base_key = "bull_researcher"
    base_label = "多头研究员"
    role_prompt = BULL_RESEARCHER

    def instruction(self) -> str:
        if self.round_no == 1:
            return "请提出你的多头论点。"
        return "请针对空头上一轮的质疑逐条回应，并补强你的多头论点。"


class BearResearcher(_DebateAgent):
    base_key = "bear_researcher"
    base_label = "空头研究员"
    role_prompt = BEAR_RESEARCHER

    def instruction(self) -> str:
        if self.round_no == 1:
            return "请提出你的空头论点，并对多头本轮的说法提出质疑。"
        return "请针对多头上一轮的回应逐条反驳，并补强你的风险论证。"


# ---------------------------------------------------------------- 第三阶段：风控


class RiskManager(LLMAgent):
    key = "risk_manager"
    label = "风控经理"
    stage = "risk"
    role_prompt = RISK_MANAGER
    max_tokens = 3500

    def user_message(self, ctx: AnalysisContext, board: dict[str, AgentOutput]) -> str:
        return "\n".join(
            [
                f"标的：{ctx.display_name}（{ctx.symbol.canonical}）",
                f"用户的分析周期：{ctx.horizon}",
                "",
                "## 分析师报告",
                _render_stage(board, "analysts"),
                "",
                "## 多空辩论",
                _render_stage(board, "debate") or "（本次未进行辩论）",
                "",
                "请给出风险评估、仓位上限与止损方案。",
            ]
        )


# ---------------------------------------------------------------- 工具


def _render_stage(board: dict[str, AgentOutput], stage: str) -> str:
    """把某个阶段的所有产出拼成给下游看的文本。"""
    blocks: list[str] = []
    for output in board.values():
        if output.stage != stage:
            continue
        if output.error:
            blocks.append(f"### {output.label}\n\n（该智能体执行失败：{output.error}）")
            continue
        tail = ""
        if output.signal:
            conf = f"，置信度 {output.confidence}" if output.confidence is not None else ""
            tail = f"\n\n> 倾向：**{output.signal}**{conf}"
        blocks.append(f"### {output.label}\n\n{output.content}{tail}")
    return "\n\n".join(blocks)
