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
            # 兼容接口普遍不支持 json_schema，只有 json_object —— 它**只保证
            # 输出是合法 JSON，不保证字段正确**。而 Claude 那条路是服务端按
            # schema 强制约束的，提示词里因此没必要罗列字段。
            # 所以这里把 schema 自动渲染成字段说明追加到系统提示，
            # 否则模型会漏字段（实测 horizon/summary/data_quality 就没被提到过）。
            # 另外 DeepSeek 要求 json_object 模式下提示里必须出现 "json" 字样。
            payload["messages"][0]["content"] = (
                system + "\n\n" + _render_schema(json_schema)
            )
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


def _render_schema(schema: dict[str, Any]) -> str:
    """把 JSON Schema 渲染成模型看得懂的字段清单。

    兼容接口没有服务端 schema 约束，只能靠提示词讲清楚要输出什么。
    刻意用中文 + 逐字段列举，而不是直接丢原始 schema ——
    原始 schema 里的 JSON 结构容易被模型当成待填的模板抄回来。
    """
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))

    lines = [
        "## 输出格式（严格遵守）",
        "",
        "只输出一个 json 对象，不要 markdown 代码块，不要任何解释文字。",
        "必须包含下列全部字段，一个都不能少：",
        "",
    ]
    for name, spec in props.items():
        parts = [f"- `{name}`"]
        type_cn = {
            "string": "字符串",
            "integer": "整数",
            "number": "数字",
            "boolean": "布尔值",
            "array": "数组",
            "object": "对象",
        }.get(spec.get("type", ""), spec.get("type", ""))
        if spec.get("type") == "array":
            item_type = spec.get("items", {}).get("type", "string")
            type_cn = f"数组（元素为{'字符串' if item_type == 'string' else item_type}）"
        parts.append(f"（{type_cn}）")
        if "enum" in spec:
            parts.append("，只能取：" + " / ".join(str(v) for v in spec["enum"]))
        if name in required:
            parts.append("，**必填**")
        if spec.get("description"):
            parts.append("：" + spec["description"])
        lines.append("".join(parts))

    return "\n".join(lines)


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
