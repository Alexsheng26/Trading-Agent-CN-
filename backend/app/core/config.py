"""全局配置。所有可调项都来自环境变量，见根目录 .env.example。"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProvider = Literal["anthropic", "openai_compat", "echo"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 运行环境 ----------
    app_env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ---------- 数据库 ----------
    database_url: str = "sqlite+aiosqlite:///./data/trading_agent.db"

    # ---------- 数据源 ----------
    data_provider_order: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["akshare", "tushare", "mock"]
    )
    tushare_token: str = ""
    quote_cache_ttl: int = 60
    history_cache_ttl: int = 3600

    # ---------- LLM ----------
    llm_provider: LLMProvider = "echo"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    # 留空则分析师复用主模型
    anthropic_analyst_model: str = ""
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    # 模型因安全策略拒答时，让服务端自动换一个模型跑完同一请求
    anthropic_refusal_fallback: bool = True

    openai_compat_base_url: str = "https://api.deepseek.com/v1"
    openai_compat_api_key: str = ""
    openai_compat_model: str = "deepseek-chat"

    # ---------- 智能体流水线 ----------
    debate_rounds: int = 2
    analysis_timeout: int = 600

    @field_validator("cors_origins", "data_provider_order", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """支持 `A,B,C` 形式的环境变量写法（配合字段上的 NoDecode 生效）。"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def analyst_model(self) -> str:
        return self.anthropic_analyst_model or self.anthropic_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
