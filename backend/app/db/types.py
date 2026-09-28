"""自定义列类型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """始终以「带时区的 UTC」进出数据库。

    为什么需要它：**SQLite 没有时区类型**。给它一个带偏移的 datetime，
    落库时偏移会被静默丢掉，读回来就是个 naive 值。naive 值序列化给前端后，
    浏览器按自己的时区解释 —— "3 分钟前"会显示成"8 小时前"。
    这个项目真踩过：created_at 走 SQLite 的 func.now()（UTC）、
    finished_at 走 Python 的 datetime.now()（容器本地时区），两列时区不一致
    且都 naive，历史记录的时间全是错的。

    在类型层面一次兜住，比在每个接口里记得转换靠得住：
    写入时统一转 UTC，读出时补回 UTC 时区。
    PostgreSQL 的 timestamptz 本来就带时区，这层是无害的直通。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return value
        # naive 值一律按 UTC 解释：本项目所有写入都来自 clock.now_utc()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return value
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
