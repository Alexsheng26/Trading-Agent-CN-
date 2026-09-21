"""进行中分析的事件缓冲。

**为什么需要它**：手机上跑一次分析要几十秒到几分钟，期间锁屏、切 App、
地铁进隧道都会断开 SSE 连接。如果事件是"来了就推、不留存"，断线就等于白跑。

所以这里把每次 run 的事件按序号全量留在内存里：
- 客户端带 `Last-Event-ID` 重连时，从那个序号之后接着发；
- 新客户端连上来，可以从 0 开始把已经发生的事件快速回放一遍，
  界面立刻恢复到当前进度，然后继续跟进实时增量。

单进程内存实现。多 worker 部署要换 Redis Streams：把 `append`/`read_from`
换成 XADD/XREAD 即可，接口不用动（见 docs/ARCHITECTURE.md）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.agents.orchestrator import Event

log = logging.getLogger(__name__)

# 完成的 run 在内存里保留多久（秒），之后只能从数据库读最终结果
_RETAIN_SECONDS = 1800
# 单次 run 最多缓冲多少条事件，防止异常情况把内存吃满
_MAX_EVENTS = 20000


@dataclass
class RunBuffer:
    run_id: str
    events: list[Event] = field(default_factory=list)
    done: bool = False
    finished_at: float | None = None
    # 有新事件时唤醒所有等待的订阅者
    _notify: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def append(self, event: Event) -> None:
        if len(self.events) >= _MAX_EVENTS:
            log.warning("run %s 事件数超过 %d，后续增量将被丢弃", self.run_id, _MAX_EVENTS)
            return
        self.events.append(event)
        self._notify.set()

    def finish(self) -> None:
        self.done = True
        self.finished_at = time.monotonic()
        self._notify.set()

    async def wait(self, timeout: float = 30.0) -> None:  # noqa: ASYNC109
        """等到有新事件（或超时，用于发 SSE 心跳）。

        这里刻意用参数化的 timeout 而不是外部 cancel scope：
        超时本身就是业务语义（该发心跳了），不是取消。
        """
        try:
            await asyncio.wait_for(self._notify.wait(), timeout=timeout)
        except TimeoutError:
            return
        finally:
            self._notify.clear()


class RunStore:
    def __init__(self) -> None:
        self._buffers: dict[str, RunBuffer] = {}

    def create(self, run_id: str) -> RunBuffer:
        self._gc()
        buffer = RunBuffer(run_id=run_id)
        self._buffers[run_id] = buffer
        return buffer

    def get(self, run_id: str) -> RunBuffer | None:
        return self._buffers.get(run_id)

    async def read_from(self, run_id: str, start: int = 0):
        """从第 `start` 条事件开始，边回放边跟进实时增量。

        产出 `(序号, 事件)`；序号就是客户端要回传的 `Last-Event-ID`。
        `None` 表示该等一个心跳了。
        """
        buffer = self._buffers.get(run_id)
        if buffer is None:
            return

        cursor = max(0, start)
        while True:
            # 先把已缓冲的补齐（断线重连走这条路）
            while cursor < len(buffer.events):
                yield cursor, buffer.events[cursor]
                cursor += 1

            if buffer.done:
                return

            before = len(buffer.events)
            await buffer.wait(timeout=15.0)
            if len(buffer.events) == before and not buffer.done:
                # 超时且无新事件：让调用方发个心跳，避免中间代理掐断连接
                yield -1, None  # type: ignore[misc]

    def cancel(self, run_id: str) -> bool:
        buffer = self._buffers.get(run_id)
        if buffer is None or buffer.task is None or buffer.task.done():
            return False
        buffer.task.cancel()
        return True

    def _gc(self) -> None:
        now = time.monotonic()
        stale = [
            rid
            for rid, b in self._buffers.items()
            if b.done and b.finished_at is not None and now - b.finished_at > _RETAIN_SECONDS
        ]
        for rid in stale:
            self._buffers.pop(rid, None)

    def active_count(self) -> int:
        return sum(1 for b in self._buffers.values() if not b.done)


store = RunStore()
