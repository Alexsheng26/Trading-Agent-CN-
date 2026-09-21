"""自选股请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.market import QuoteOut


class WatchlistCreate(BaseModel):
    symbol: str
    group_name: str = Field(default="默认", max_length=32)
    note: str = Field(default="", max_length=200)


class WatchlistUpdate(BaseModel):
    group_name: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=200)
    sort_order: int | None = None


class WatchlistItemOut(BaseModel):
    id: int
    symbol: str
    name: str = ""
    group_name: str = "默认"
    note: str = ""
    sort_order: int = 0
    created_at: datetime | None = None
    # 列表页一次性带上行情，省一轮请求（手机上尤其明显）
    quote: QuoteOut | None = None
