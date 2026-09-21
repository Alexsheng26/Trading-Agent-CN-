"""健康检查与系统状态。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.data.base import ALL_CAPABILITIES
from app.data.registry import get_registry
from app.schemas.market import ProviderStatusOut
from app.services.run_store import store

router = APIRouter(tags=["系统"])


@router.get("/health", summary="存活探针")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/status", summary="系统状态：数据源、模型、进行中的任务")
async def status() -> dict:
    settings = get_settings()
    registry = get_registry()
    health_map = await registry.health()

    providers = [
        ProviderStatusOut(
            name=name,
            healthy=health_map.get(name, False),
            capabilities=sorted(
                c for c in ALL_CAPABILITIES if c in _capabilities_of(registry, name)
            ),
        )
        for name in registry.provider_names
    ]

    return {
        "app_env": settings.app_env,
        "llm": {
            "provider": settings.llm_provider,
            "model": (
                settings.anthropic_model
                if settings.llm_provider == "anthropic"
                else settings.openai_compat_model
                if settings.llm_provider == "openai_compat"
                else "echo（离线桩）"
            ),
            "analyst_model": (
                settings.analyst_model if settings.llm_provider == "anthropic" else None
            ),
            "effort": settings.llm_effort,
            "is_stub": settings.llm_provider == "echo",
        },
        "data_providers": [p.model_dump() for p in providers],
        "pipeline": {
            "debate_rounds": settings.debate_rounds,
            "timeout_sec": settings.analysis_timeout,
        },
        "active_runs": store.active_count(),
    }


def _capabilities_of(registry, name: str) -> frozenset[str]:  # type: ignore[no-untyped-def]
    for provider in registry._providers:  # noqa: SLF001 —— 仅用于状态展示
        if provider.name == name:
            return provider.capabilities
    return frozenset()
