"""按配置创建 LLM 客户端。"""

from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.core.errors import LLMError
from app.llm.base import LLMClient

log = logging.getLogger(__name__)


def build_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()

    match settings.llm_provider:
        case "anthropic":
            from app.llm.anthropic_client import AnthropicLLM  # noqa: PLC0415

            return AnthropicLLM(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
                default_effort=settings.llm_effort,
                refusal_fallback=settings.anthropic_refusal_fallback,
            )
        case "openai_compat":
            from app.llm.openai_compat_client import OpenAICompatLLM  # noqa: PLC0415

            return OpenAICompatLLM(
                api_key=settings.openai_compat_api_key,
                base_url=settings.openai_compat_base_url,
                model=settings.openai_compat_model,
            )
        case "echo":
            from app.llm.echo_client import EchoLLM  # noqa: PLC0415

            log.warning(
                "LLM_PROVIDER=echo —— 使用离线桩模型，输出无分析价值。"
                "要跑真实分析请配置 ANTHROPIC_API_KEY 并设 LLM_PROVIDER=anthropic"
            )
            return EchoLLM()
        case other:  # pragma: no cover - pydantic 已经校验过
            raise LLMError(f"未知的 LLM_PROVIDER：{other}")


_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = build_llm()
    return _llm


def reset_llm() -> None:
    """测试用。"""
    global _llm
    _llm = None
