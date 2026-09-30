"""分析相关的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Horizon = Literal["短线", "波段", "中线"]
Depth = Literal["quick", "standard", "deep"]


class AnalysisRequest(BaseModel):
    symbol: str = Field(description="股票代码，支持 600519 / sh600519 / 600519.SH 等写法")
    horizon: Horizon = "波段"
    user_note: str = Field(default="", max_length=500, description="你想让智能体特别关注的点")
    depth: Depth = Field(
        default="standard",
        description="分析深度：quick 快速 / standard 标准 / deep 深度。"
        "按当前模型厂商翻译成辩论轮数、推理投入、模型，见 GET /analysis/options",
    )
    model: str | None = Field(
        default=None,
        max_length=100,
        description="手动指定本次所有智能体用的模型。必须在 GET /analysis/options 的 models 里",
    )

    @field_validator("symbol")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("股票代码不能为空")
        return v.strip()


class AnalysisStartedOut(BaseModel):
    run_id: str
    symbol: str
    status: str
    stream_url: str = Field(description="SSE 地址，连上即可看实时进度")


class AgentOutputOut(BaseModel):
    key: str
    label: str
    stage: str
    content: str = ""
    thinking: str = ""
    signal: str | None = None
    confidence: int | None = None
    error: str = ""
    usage: dict[str, int] | None = None


class DecisionOut(BaseModel):
    action: str
    conviction: int = 0
    position_pct: float = 0
    horizon: str = "波段"
    entry_zone: str = ""
    stop_loss: str = ""
    take_profit: str = ""
    summary: str = ""
    key_reasons: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    dissent: str = ""
    data_quality: str = ""
    disclaimer: str = ""
    #: 非空表示决策这一步换过模型重做（例如推理模型没返回可解析的 JSON）
    fallback_note: str = ""


class AnalysisRunOut(BaseModel):
    id: str
    symbol: str
    name: str = ""
    horizon: str = "波段"
    user_note: str = ""
    status: str
    error: str = ""
    decision: DecisionOut | None = None
    outputs: list[AgentOutputOut] = Field(default_factory=list)
    data_sources: dict[str, Any] | None = None
    data_warnings: list[Any] | None = None
    usage: dict[str, Any] | None = None
    llm_provider: str = ""
    #: 档位、模型、推理投入。引入档位之前的老记录为 None
    config: dict[str, Any] | None = None
    elapsed_sec: float | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


class AnalysisRunSummaryOut(BaseModel):
    """列表页用的精简版，不带正文。"""

    id: str
    symbol: str
    name: str = ""
    horizon: str = "波段"
    status: str
    action: str | None = None
    conviction: int | None = None
    #: 分析深度。老记录为 None
    depth: str | None = None
    created_at: datetime | None = None
    elapsed_sec: float | None = None


class AnalysisRunListOut(BaseModel):
    items: list[AnalysisRunSummaryOut]
    total: int
    limit: int
    offset: int
