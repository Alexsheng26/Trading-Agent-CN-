"""发布完整性检查。

存在的理由：曾经因为根目录 .gitignore 写了 `data/`（不带前导斜杠，
git 会匹配任意层级），整个 backend/app/data/ 数据层从未被提交。
本地工作区有文件，所以测试、服务、浏览器验证全部通过；
但 git archive 出来的发布包缺了这一层，容器启动即 ModuleNotFoundError。

教训是：验证必须针对**发布出去的东西**，而不是本地工作区。
下面这些检查直接查 git 的追踪状态，本地有文件也骗不过去。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def _is_git_repo() -> bool:
    try:
        _git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


requires_git = pytest.mark.skipif(
    not _is_git_repo(), reason="不在 git 仓库里（例如从发布包解压出来的目录）"
)


@requires_git
def test_every_source_file_is_tracked() -> None:
    """backend/app 下的每个 .py 都必须被 git 追踪。"""
    tracked = {
        (REPO / line).resolve()
        for line in _git("ls-files", "backend/app").splitlines()
        if line.strip()
    }

    on_disk = {
        p.resolve()
        for p in (BACKEND / "app").rglob("*.py")
        if "__pycache__" not in p.parts
    }

    missing = sorted(str(p.relative_to(REPO)) for p in on_disk - tracked)
    assert not missing, (
        "以下源文件存在于工作区但未被 git 追踪，发布包里会缺失：\n  "
        + "\n  ".join(missing)
        + "\n\n多半是 .gitignore 里某条不带前导斜杠的规则误伤了。"
        "\n用 `git check-ignore -v <文件>` 查是哪一条。"
    )


@requires_git
def test_every_package_dir_has_tracked_init() -> None:
    """每个包目录的 __init__.py 都要被追踪 —— 空文件最容易被漏掉。"""
    tracked = set(_git("ls-files", "backend/app").splitlines())

    missing: list[str] = []
    for init in (BACKEND / "app").rglob("__init__.py"):
        if "__pycache__" in init.parts:
            continue
        rel = str(init.resolve().relative_to(REPO)).replace("\\", "/")
        if rel not in tracked:
            missing.append(rel)

    assert not missing, "以下 __init__.py 未被追踪，包在发布包里会不完整：\n  " + "\n  ".join(
        sorted(missing)
    )


def test_all_app_subpackages_importable() -> None:
    """逐个导入 app 下的所有子包。

    不依赖 git，从发布包解压出来的目录里也能跑 —— 只要有文件缺失就会炸。
    """
    import importlib

    expected = [
        "app.main",
        "app.core.config",
        "app.data.base",
        "app.data.registry",
        "app.data.symbols",
        "app.data.mock_provider",
        "app.data.akshare_provider",
        "app.data.tushare_provider",
        "app.data.cache",
        "app.llm.base",
        "app.llm.factory",
        "app.llm.echo_client",
        "app.agents.base",
        "app.agents.context",
        "app.agents.orchestrator",
        "app.agents.roster",
        "app.agents.prompts",
        "app.services.indicators",
        "app.services.market_data",
        "app.services.analysis_service",
        "app.services.run_store",
        "app.api.v1.router",
        "app.db.models",
        "app.db.session",
    ]
    for name in expected:
        importlib.import_module(name)
