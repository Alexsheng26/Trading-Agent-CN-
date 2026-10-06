"""运行方式，以及跟它有关的提示。

改完 .env 之后怎么让它生效，三种跑法各不一样：Docker 要 `docker compose up -d`，
Windows 一键脚本是再双击一次 start-windows.cmd，开发模式重启后端就行。
提示里写死其中一种（以前全写的是 docker 命令），另外两种用户照着做只会更糊涂。

运行方式由启动方用 APP_RUNTIME 明说，不去猜（比如探测 /.dockerenv）：
  - docker-compose.yml 设 APP_RUNTIME=docker
  - scripts/windows/start.ps1 设 APP_RUNTIME=windows
  - 都没设就是开发模式
"""

from __future__ import annotations

from typing import Literal

from app.core.config import Settings, get_settings

Runtime = Literal["docker", "windows", "dev"]

_RESTART_HINTS: dict[Runtime, str] = {
    "docker": "执行 docker compose up -d",
    # start.ps1 发现代码或 .env 变了会自己停掉旧的、重新启动
    "windows": "再双击一次 start-windows.cmd（它会自动重启）",
    "dev": "重启后端",
}


def current_runtime(settings: Settings | None = None) -> Runtime:
    match (settings or get_settings()).app_runtime.strip().lower():
        case "docker":
            return "docker"
        case "windows":
            return "windows"
        case _:
            return "dev"


def restart_hint(settings: Settings | None = None) -> str:
    """让 .env 的改动生效的那一步。是个短句，直接接在"……，然后"后面。"""
    return _RESTART_HINTS[current_runtime(settings)]
