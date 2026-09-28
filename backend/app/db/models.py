"""数据库模型（SQLAlchemy 2.0）。

只存三类东西：分析记录、智能体产出、自选股。
行情数据不入库 —— 那是数据源的职责，缓存层已经够了。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.clock import now_utc
from app.db.types import UtcDateTime


class Base(DeclarativeBase):
    pass


class AnalysisRun(Base):
    """一次完整的分析。"""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    horizon: Mapped[str] = mapped_column(String(8), default="波段")
    user_note: Mapped[str] = mapped_column(Text, default="")

    # pending | running | done | error | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str] = mapped_column(Text, default="")

    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    data_sources: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    data_warnings: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    llm_provider: Mapped[str] = mapped_column(String(32), default="")
    elapsed_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    # UtcDateTime + Python 侧默认值：
    # 原来 created_at 走 SQLite 的 func.now()（UTC），finished_at 走
    # Python 的 datetime.now()（容器本地时区），两列时区不一致；
    # 且都是 naive，传到前端会被按浏览器时区解释，"N 分钟前"直接错 8 小时。
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    outputs: Mapped[list[AgentOutputRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentOutputRow.seq"
    )

    __table_args__ = (Index("ix_runs_symbol_created", "symbol", "created_at"),)


class AgentOutputRow(Base):
    """单个智能体的产出。"""

    __tablename__ = "agent_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)

    key: Mapped[str] = mapped_column(String(48))
    label: Mapped[str] = mapped_column(String(48))
    stage: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    thinking: Mapped[str] = mapped_column(Text, default="")
    signal: Mapped[str | None] = mapped_column(String(8), nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[AnalysisRun] = relationship(back_populates="outputs")


class SymbolDirectory(Base):
    """全市场代码-名称目录。

    为什么要落库：按名称搜索必须有全市场列表，而拉这个列表要么慢
    （AKShare 那个接口 5000+ 行），要么在数据源抖动时整体失效 ——
    用户输入"茅台"一直卡在"搜索中"就是这么来的。
    成功拉到一次就存下来，之后搜索是纯本地查询（毫秒级），
    数据源事后挂掉也不影响搜索。

    只增量更新、不整表替换：一次拉漏了不该把好数据删掉。
    代价是退市股票会残留，对个人工具可以接受。
    """

    __tablename__ = "symbol_directory"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)  # 600519.SH
    code: Mapped[str] = mapped_column(String(8), index=True)  # 600519
    name: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str] = mapped_column(String(8), default="")
    board: Mapped[str] = mapped_column(String(16), default="")
    #: 这条记录来自哪个数据源，排查时有用
    source: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc)


class WatchlistItem(Base):
    """自选股。

    目前是单用户本地工具，所以没有 user_id。
    要做多用户时在这里加一列并配上唯一约束即可。
    """

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    group_name: Mapped[str] = mapped_column(String(32), default="默认")
    note: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc)
