"""Claude 客户端（项目默认模型后端）。

用到的几个关键特性：
- **提示缓存**：`system_blocks[0]` 打上 `cache_control` 断点。一次分析里
  7 个智能体共享同一份行情上下文，缓存命中后省下大量输入 token。
- **自适应思考**：`thinking={"type": "adaptive"}`，并把思考摘要流给前端折叠展示。
- **拒答兜底**：`fallbacks="default"` —— 投研内容偶尔会被安全分类器拦下，
  开了这个参数后服务端会自动换一个模型把同一请求跑完，而不是直接中断。
  不想要可以设 `ANTHROPIC_REFUSAL_FALLBACK=false`。
- **结构化输出**：交易决策走 `output_config.format` 的 json_schema，
  保证拿到的是能直接入库的字段，而不是要正则去抠的自由文本。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from app.core.errors import LLMError
from app.llm.base import Completion, Effort, Message, StreamChunk, Usage

log = logging.getLogger(__name__)

# 拒答兜底（scalar 形式）要求的 beta 标识
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
        *,
        default_effort: Effort = "high",
        refusal_fallback: bool = True,
        timeout: float = 300.0,
    ) -> None:
        if not api_key:
            # 不抛异常：SDK 也会从 ANTHROPIC_API_KEY 环境变量或 `ant auth login`
            # 的本地凭证里找，没配才在真正调用时报错。
            log.info("未显式传入 ANTHROPIC_API_KEY，将由 SDK 自行解析凭证")
        self._client = anthropic.AsyncAnthropic(
            **({"api_key": api_key} if api_key else {}),
            timeout=timeout,
            max_retries=3,
        )
        self._model = model
        self._default_effort: Effort = default_effort
        self._refusal_fallback = refusal_fallback

    # ---------------- 请求装配 ----------------

    def _system(self, blocks: list[str]) -> list[dict[str, Any]]:
        """第一段（共享行情上下文）打缓存断点，后面的角色指令不打。

        顺序很重要：缓存是前缀匹配的，共享内容必须排在最前面，
        否则每个智能体各自的角色指令会让前缀各不相同，缓存全部失效。
        """
        out: list[dict[str, Any]] = []
        for i, text in enumerate(blocks):
            if not text.strip():
                continue
            block: dict[str, Any] = {"type": "text", "text": text}
            if i == 0 and len(text) > 2000:
                # 太短的前缀不会被缓存，加断点也是白加
                block["cache_control"] = {"type": "ephemeral"}
            out.append(block)
        return out

    def _kwargs(
        self,
        system_blocks: list[str],
        messages: list[Message],
        model: str | None,
        max_tokens: int,
        effort: Effort | None,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        output_config: dict[str, Any] = {"effort": effort or self._default_effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": max_tokens,
            "system": self._system(system_blocks),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": output_config,
        }
        if self._refusal_fallback:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    # ---------------- 非流式 ----------------

    async def complete(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        kwargs = self._kwargs(
            system_blocks, messages, model, max_tokens, effort, json_schema
        )
        try:
            # 大 max_tokens 走流式再取最终消息，避免 HTTP 超时
            async with self._client.beta.messages.stream(**kwargs) as stream:
                message = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise _wrap(exc) from exc

        _check_refusal(message)
        text = "".join(b.text for b in message.content if b.type == "text")
        data: dict[str, Any] | None = None
        if json_schema is not None:
            data = _parse_json(text)

        return Completion(
            text=text,
            model=getattr(message, "model", kwargs["model"]),
            usage=_usage(message),
            data=data,
        )

    # ---------------- 流式 ----------------

    async def stream(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,
    ) -> AsyncIterator[StreamChunk]:
        kwargs = self._kwargs(system_blocks, messages, model, max_tokens, effort, None)
        try:
            async with self._client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamChunk(text=delta.text)
                    elif delta.type == "thinking_delta":
                        yield StreamChunk(thinking=delta.thinking)
                message = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise _wrap(exc) from exc
        _check_refusal(message)


# ---------------------------------------------------------------- 工具


def _usage(message: Any) -> Usage:
    u = getattr(message, "usage", None)
    if u is None:
        return Usage()
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _check_refusal(message: Any) -> None:
    """整条兜底链都拒答时 stop_reason 才会是 refusal。"""
    if getattr(message, "stop_reason", None) != "refusal":
        return
    details = getattr(message, "stop_details", None)
    category = getattr(details, "category", None) or "未说明"
    raise LLMError(
        f"模型拒绝了本次请求（类别：{category}）。"
        "请检查提示词里是否出现了会被安全策略拦下的表述。"
    )


def _parse_json(text: str) -> dict[str, Any] | None:
    """output_config.format 保证是合法 JSON，但网络截断等情况仍要兜住。"""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("结构化输出解析失败：%s；原文前 200 字：%s", exc, text[:200])
        return None
    return parsed if isinstance(parsed, dict) else None


def _wrap(exc: anthropic.APIError) -> LLMError:
    """把 SDK 异常翻成带可操作建议的中文提示。"""
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("Claude 鉴权失败：请检查 ANTHROPIC_API_KEY")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError(f"模型不存在或无权访问：{exc}")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("触发 Claude 速率限制，请稍后重试或降低并发（DEBATE_ROUNDS）")
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("Claude 请求超时，可调大 ANALYSIS_TIMEOUT 或降低 LLM_EFFORT")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError(f"连不上 Claude API（检查网络/代理）：{exc}")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(f"Claude 返回错误 {exc.status_code}：{exc}")
    return LLMError(f"Claude 调用失败：{exc}")
