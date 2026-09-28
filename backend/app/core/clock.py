"""时间处理。

两条规则：
1. **存储和传输一律用带时区的 UTC**。数据库里混着 UTC 和本地时间的话，
   "N 分钟前"这类计算必错；naive 时间戳传到前端，浏览器会按自己的时区
   解释，同样错。
2. **展示和交易时段判断用 UTC+8 固定偏移，不依赖 TZ 环境变量**。
   中国没有夏令时，UTC+8 永远成立；而 `TZ=Asia/Shanghai` 需要系统装了
   tzdata，python:3.11-slim 这类精简镜像未必有 —— 缺了会静默回落到 UTC，
   差 8 小时且毫无报错。固定偏移绕开这个坑。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

#: 中国标准时间。无夏令时，固定 UTC+8。
CST = timezone(timedelta(hours=8), name="CST")


def now_utc() -> datetime:
    """带时区的当前 UTC 时间。用于存库与接口返回。"""
    return datetime.now(UTC)


def now_cst() -> datetime:
    """带时区的当前北京时间。用于展示与交易时段判断。"""
    return datetime.now(CST)


def to_cst(dt: datetime | None) -> datetime | None:
    """转成北京时间。naive 值按 UTC 解释 —— 库里的历史数据就是这么存的。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(CST)
