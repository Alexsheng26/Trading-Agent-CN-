"""运行方式（APP_RUNTIME）与"改完 .env 怎么生效"的提示。

不用 Docker 之后，界面上再让用户"执行 docker compose up -d"就是在误导人：
照做只会报"docker 不是内部或外部命令"。提示必须跟着实际的运行方式走。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.profile import ProfileError, resolve_profile
from app.core.config import Settings, get_settings
from app.core.runtime import current_runtime, restart_hint


@pytest.mark.parametrize(
    ("value", "runtime", "needle"),
    [
        ("", "dev", "重启后端"),
        ("docker", "docker", "docker compose up -d"),
        ("windows", "windows", "start-windows.cmd"),
        ("Windows ", "windows", "start-windows.cmd"),
        ("k8s", "dev", "重启后端"),  # 不认识的值按开发模式，不瞎猜
    ],
)
def test_restart_hint_follows_runtime(monkeypatch, value: str, runtime: str, needle: str) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_RUNTIME", value)
    get_settings.cache_clear()
    assert current_runtime() == runtime
    assert needle in restart_hint()


def test_windows_hint_never_mentions_docker(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_RUNTIME", "windows")
    get_settings.cache_clear()
    assert "docker" not in restart_hint().lower()


def test_api_exposes_runtime(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_RUNTIME", "windows")
    get_settings.cache_clear()

    status = client.get("/api/v1/status").json()
    assert status["runtime"] == "windows"
    assert "start-windows.cmd" in status["restart_hint"]

    options = client.get("/api/v1/analysis/options").json()
    assert "start-windows.cmd" in options["restart_hint"]


def test_unlisted_model_error_uses_runtime_hint() -> None:
    settings = Settings(
        llm_provider="anthropic",
        anthropic_model="claude-main",
        data_provider_order=["mock"],
        app_runtime="windows",
    )
    with pytest.raises(ProfileError, match="LLM_MODEL_CHOICES") as info:
        resolve_profile(settings, "standard", "gpt-x")
    assert "start-windows.cmd" in str(info.value)
    assert "docker" not in str(info.value)


def test_env_file_with_bom_is_read(tmp_path: Path) -> None:
    """旧版记事本存 UTF-8 会加 BOM。按普通 utf-8 读，第一行的变量名前会多一个
    看不见的 \\ufeff，那一项就悄悄失效了 —— 偏偏用户最常改的就是第一行。"""
    env = tmp_path / ".env"
    env.write_bytes("\ufeffQUOTE_CACHE_TTL=7\n# 中文注释\nHISTORY_CACHE_TTL=9\n".encode())

    settings = Settings(_env_file=env)  # type: ignore[call-arg]

    assert settings.quote_cache_ttl == 7
    assert settings.history_cache_ttl == 9


def test_env_file_without_bom_still_works(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes(b"QUOTE_CACHE_TTL=7\n")
    assert Settings(_env_file=env).quote_cache_ttl == 7  # type: ignore[call-arg]
