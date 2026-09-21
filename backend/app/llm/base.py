"""LLM 抽象层契约。

智能体只依赖 `LLMClient`，不关心背后是 Claude 还是别的模型。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["user", "assistant"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


@dataclass(slots=True)
class Completion:
    text: str
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    # 结构化输出时解析好的 JSON
    data: dict[str, Any] | None = None


@dataclass(slots=True)
class StreamChunk:
    """流式输出的一个增量片段。"""

    text: str = ""
    # 思考摘要片段（Claude 的 thinking summary），前端可折叠展示
    thinking: str = ""


@runtime_checkable
class LLMClient(Protocol):
    """模型客户端。

    `system_blocks` 是有序的系统提示分段：
    第 0 段放"本次分析共享的行情数据"（长、所有智能体一样 —— 会被打上缓存断点），
    后续段放各智能体自己的角色指令。这样一次分析里 N 个智能体能复用同一段
    缓存前缀，省 token 也更快。
    """

    name: str

    async def complete(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion: ...

    def stream(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,
    ) -> AsyncIterator[StreamChunk]: ...
