"""Windows 一键脚本（根目录 *.cmd + scripts/windows/*.ps1）的"看不见"的约定。

这些脚本在开发环境（Linux）里跑不了真家伙，最容易坏在肉眼看不出来的地方：
  - .ps1 没有 UTF-8 BOM：Win10/11 自带的 Windows PowerShell 5.1 会按系统代码页
    （中文系统是 GBK）解码，中文变乱码、引号被吞，整个脚本语法错误；
  - .ps1 用了 PowerShell 7 才有的语法（?? 、&& 等）：作者机器上好好的，用户那边直接报错；
  - .cmd 里混进非 ASCII 字符：cmd.exe 按系统代码页读批处理，命令会被读坏；
  - .cmd 是 LF 换行：cmd.exe 的标签跳转和长行解析有已知问题。
真机上的完整流程（安装 → 启动 → 停止）由 CI 的 windows-native 任务跑。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent.parent
SCRIPTS = REPO / "scripts" / "windows"
CMD_FILES = sorted(REPO.glob("*.cmd"))
PS1_FILES = sorted(SCRIPTS.glob("*.ps1"))


def test_scripts_are_all_there() -> None:
    assert {p.name for p in CMD_FILES} >= {
        "setup-windows.cmd",
        "start-windows.cmd",
        "stop-windows.cmd",
    }
    assert {p.name for p in PS1_FILES} >= {"common.ps1", "setup.ps1", "start.ps1", "stop.ps1"}


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_ps1_is_utf8_with_bom(path: Path) -> None:
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), (
        f"{path.name} 缺 UTF-8 BOM：Windows PowerShell 5.1 会按 GBK 读它，中文提示全变乱码"
    )
    data[3:].decode("utf-8")  # 其余部分必须是合法 UTF-8


@pytest.mark.parametrize("path", CMD_FILES, ids=lambda p: p.name)
def test_cmd_is_pure_ascii(path: Path) -> None:
    try:
        path.read_bytes().decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError(
            f"{path.name} 里有非 ASCII 字符（cmd.exe 会按系统代码页读坏）"
        ) from exc


@pytest.mark.parametrize("path", CMD_FILES, ids=lambda p: p.name)
def test_cmd_runs_an_existing_script(path: Path) -> None:
    names = re.findall(r"scripts\\windows\\([\w-]+\.ps1)", path.read_text(encoding="ascii"))
    assert names, f"{path.name} 没有调用 scripts\\windows 下的脚本"
    for name in names:
        assert (SCRIPTS / name).is_file(), f"{path.name} 调用的 {name} 不存在"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def _in_git_repo() -> bool:
    try:
        _git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


@pytest.mark.skipif(not _in_git_repo(), reason="不在 git 仓库里（例如从发布包解压出来的目录）")
def test_windows_scripts_are_packed_with_crlf() -> None:
    """.gitattributes 要让 .cmd / .ps1 以 CRLF 检出 —— git archive 打包时也按这个转换。"""
    files = [str(p.relative_to(REPO)) for p in [*CMD_FILES, *PS1_FILES]]
    lines = _git("check-attr", "eol", "--", *files).splitlines()
    assert lines and all(line.endswith("eol: crlf") for line in lines), "\n".join(lines)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="没装 PowerShell 7（pwsh）")
def test_ps1_parses_and_avoids_ps7_only_syntax() -> None:
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(TESTS / "ps51_compat.ps1"),
            *map(str, PS1_FILES),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
