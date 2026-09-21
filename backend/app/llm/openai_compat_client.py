"""OpenAI 兼容接口客户端（DeepSeek / 通义千问 / Kimi 等）。

为什么留这个口子：本项目面向 A股用户，部分网络环境直连 Anthropic 不方便，
换个国产模型能立刻跑起来。**默认后端仍是 Claude**（效果更好），
这里只是备选，通过 `LLM_PROVIDER=openai_compat` 切换。

常用 base_url：
    DeepSeek     https://api.deepseek.com/v1                      deepseek-chat
    通义千问      https://dashscope.aliyuncs.com/compatible-mode/v1  qwen-plus
    Kimi         https://api.moonshot.cn/v1                        moonshot-v1-32k

注意：这类接口一般不支持 Claude 的提示缓存断点和 adaptive thinking，
所以 `system_blocks` 会被拼成一整段，`effort` 参数被忽略。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.core.errors import LLMError
from app.llm.base import Completion, Effort, Message, StreamChunk, Usage

log = logging.getLogger(__name__)


class OpenAICompatLLM:
    name = "openai_compat"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        timeout: float = 300.0,
    ) -> None:
        try:
            from openai import AsyncOpenAI  # noqa: PLC0415 —— 延迟导入
        except ImportError as exc:  # pragma: no cover
            raise LLMError(
                'openai 包未安装，执行 pip install -e ".[openai]"，'
                "或把 LLM_PROVIDER 改回 anthropic / echo"
            ) from exc

        self._client = AsyncOpenAI(
            api_key=api_key or "unset", base_url=base_url, timeout=timeout, max_retries=3
        )
        self._model = model

    def _payload(
        self,
        system_blocks: list[str],
        messages: list[Message],
        model: str | None,
        max_tokens: int,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        system = "\n\n".join(b for b in system_blocks if b.strip())
        payload: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
        }
        if json_schema is not None:
            # 兼容接口对 json_schema 支持参差不齐，统一退化成 json_object
            # 并在提示词里说明字段（见 agents/prompts.py），解析后再自行校验。
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,  # 兼容接口无此概念，忽略
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        payload = self._payload(system_blocks, messages, model, max_tokens, json_schema)
        try:
            resp = await self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise LLMError(f"{self._model} 调用失败：{exc}") from exc

        text = resp.choices[0].message.content or ""
        usage = Usage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        data: dict[str, Any] | None = None
        if json_schema is not None:
            data = _parse_json(text)
        return Completion(text=text, model=resp.model, usage=usage, data=data)

    async def stream(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._payload(system_blocks, messages, model, max_tokens, None)
        payload["stream"] = True
        try:
            stream = await self._client.chat.completions.create(**payload)
            async for event in stream:
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                # DeepSeek-R1 等推理模型会额外给 reasoning_content
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield StreamChunk(thinking=reasoning)
                if delta.content:
                    yield StreamChunk(text=delta.content)
        except Exception as exc:
            raise LLMError(f"{self._model} 流式调用失败：{exc}") from exc


def _parse_json(text: str) -> dict[str, Any] | None:
    """兼容接口常把 JSON 包在 ```json 围栏里，先剥掉再解析。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [ln for ln in cleaned.splitlines() if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.warning("结构化输出解析失败：%s；原文前 200 字：%s", exc, text[:200])
        return None
    return parsed if isinstance(parsed, dict) else None
