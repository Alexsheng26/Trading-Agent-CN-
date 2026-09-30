"""智能体基类。

每个智能体 = 一段角色提示词 + 一次模型调用 + 一份结构化产出。
共享的行情简报作为 `system_blocks[0]`，角色提示词作为 `system_blocks[1]`，
这样一次分析里所有智能体命中同一段提示缓存前缀。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from app.agents.context import AnalysisContext
from app.agents.prompts import with_discipline
from app.llm.base import Effort, LLMClient, Message, StreamChunk, Usage

log = logging.getLogger(__name__)

Signal = Literal["看多", "中性", "看空"]
Stage = Literal["analysts", "debate", "risk", "decision"]


@dataclass(slots=True)
class AgentOutput:
    """一个智能体的产出。"""

    key: str  # 程序标识，如 "technical_analyst"
    label: str  # 展示名，如 "技术面分析师"
    stage: Stage
    content: str = ""  # Markdown 正文
    thinking: str = ""  # 思考摘要（可折叠展示）
    signal: Signal | None = None
    confidence: int | None = None  # 0-100
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    error: str = ""  # 非空表示该智能体失败，但流水线继续

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "stage": self.stage,
            "content": self.content,
            "thinking": self.thinking,
            "signal": self.signal,
            "confidence": self.confidence,
            "usage": self.usage.as_dict(),
            "model": self.model,
            "error": self.error,
        }


class LLMAgent:
    """基于提示词的智能体。"""

    #: 程序标识
    key: str = "agent"
    #: 展示名
    label: str = "智能体"
    #: 所属阶段
    stage: Stage = "analysts"
    #: 角色提示词
    role_prompt: str = ""
    #: 输出上限
    max_tokens: int = 4000
    #: 推理投入，None 表示用全局默认
    effort: Effort | None = None

    def __init__(
        self,
        llm: LLMClient,
        model: str | None = None,
        *,
        effort: Effort | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._llm = llm
        self._model = model
        # 分析档位按次覆盖这两个类属性（见 app/agents/profile.py）；
        # 不传就沿用类上的默认值
        if effort is not None:
            self.effort = effort
        if max_tokens is not None:
            self.max_tokens = max_tokens

    # ------------------------------------------------------------ 可覆写

    def role_prompt_for(self, ctx: AnalysisContext) -> str:
        """按标的挑角色提示词。默认一套通用；需要区分股票 / ETF 的角色覆写它。"""
        return self.role_prompt

    def user_message(self, ctx: AnalysisContext, board: dict[str, AgentOutput]) -> str:
        """构造用户轮内容。`board` 是此前所有智能体的产出。"""
        parts = [f"请分析 {ctx.display_name}（{ctx.symbol.canonical}）。"]
        if ctx.user_note:
            parts.append(f"\n用户特别关注：{ctx.user_note}")
        return "\n".join(parts)

    # ------------------------------------------------------------ 执行

    async def run_stream(
        self, ctx: AnalysisContext, board: dict[str, AgentOutput]
    ) -> AsyncIterator[StreamChunk | AgentOutput]:
        """流式执行：先吐出若干 `StreamChunk`，最后吐出一个 `AgentOutput`。"""
        system_blocks = [ctx.market_brief(), with_discipline(self.role_prompt_for(ctx))]
        messages = [Message(role="user", content=self.user_message(ctx, board))]

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        try:
            async for chunk in self._llm.stream(
                system_blocks,
                messages,
                model=self._model,
                max_tokens=self.max_tokens,
                effort=self.effort,
            ):
                if chunk.text:
                    text_parts.append(chunk.text)
                if chunk.thinking:
                    thinking_parts.append(chunk.thinking)
                yield chunk
        except Exception as exc:
            log.warning("%s 执行失败：%s", self.key, exc)
            yield AgentOutput(
                key=self.key,
                label=self.label,
                stage=self.stage,
                error=str(exc),
            )
            return

        content = "".join(text_parts).strip()
        signal, confidence, content = parse_verdict(content)
        yield AgentOutput(
            key=self.key,
            label=self.label,
            stage=self.stage,
            content=content,
            thinking="".join(thinking_parts).strip(),
            signal=signal,
            confidence=confidence,
            model=self._model or "",
        )


# ---------------------------------------------------------------- 尾标解析

# 匹配 "倾向：看多 | 置信度：72"，容忍中英标点、空格与 markdown 加粗。
# 刻意不加 DOTALL：尾标就是一行，让 `.` 跨行会把正文一起吞掉。
_VERDICT_RE = re.compile(
    r"\**\s*倾向\s*[:：]\s*\**\s*(看多|中性|看空|偏多|偏空)\s*\**"
    r"(?:[^\n]*?置信度\s*[:：]\s*\**\s*(\d{1,3}))?",
)

_SIGNAL_ALIASES = {"偏多": "看多", "偏空": "看空"}


def parse_verdict(content: str) -> tuple[Signal | None, int | None, str]:
    """从正文尾部抠出倾向与置信度，并把那两行从正文里去掉。

    解析失败不报错 —— 前端只是少一个标签，正文照样能看。
    """
    # 取最后一个匹配：正文里可能引用过别人的倾向，真正的尾标在最后
    matches = list(_VERDICT_RE.finditer(content))
    if not matches:
        return None, None, content.strip()
    match = matches[-1]

    raw_signal = match.group(1)
    signal = _SIGNAL_ALIASES.get(raw_signal, raw_signal)

    confidence: int | None = None
    if match.group(2):
        try:
            confidence = max(0, min(100, int(match.group(2))))
        except ValueError:
            confidence = None

    # 去掉尾标本身，以及它前面残留的加粗星号和水平分隔线
    body = content[: match.start()].rstrip().rstrip("*").rstrip()
    body = re.sub(r"\n\s*-{3,}\s*$", "", body).rstrip()
    return signal, confidence, body  # type: ignore[return-value]
