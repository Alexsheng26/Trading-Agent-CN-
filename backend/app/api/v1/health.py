"""健康检查与系统状态。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.version import APP_VERSION
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

    # healthcheck() 只说明"依赖装好了"，不代表调用真能成功 ——
    # AKShare 模块在、但接口全挂的情况很常见。所以额外给出
    # **最近一次实际命中的数据源**，这才是地面真相。
    active = dict(registry.last_sources)
    using_mock = "mock" in active.values()

    return {
        "app_env": settings.app_env,
        # 版本与运输层状态：排查时第一件要确认的事就是"跑的是哪一版、
        # 浏览器指纹到底有没有生效"。不显示出来只能靠猜。
        "app_version": APP_VERSION,
        "transport": _transport_status(settings),
        "active_sources": active,
        # 前端据此给出醒目警示：模拟数据是程序生成的假数字，
        # 在金融工具里被误当成真实行情后果很严重。
        "using_mock_data": using_mock,
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


def _transport_status(settings) -> dict:  # type: ignore[no-untyped-def]
    """akshare 的 HTTP 运输层现在是什么状态。"""
    from app.data.browser_transport import is_enabled  # noqa: PLC0415

    return {
        "browser_impersonation": settings.akshare_browser_transport,
        "impersonate": settings.akshare_impersonate,
        # 配置开着不代表真的打上了补丁（akshare 没装就不会）
        "active": is_enabled(),
    }
