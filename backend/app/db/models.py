"""数据库模型（SQLAlchemy 2.0）。

只存三类东西：分析记录、智能体产出、自选股。
行情数据不入库 —— 那是数据源的职责，缓存层已经够了。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
