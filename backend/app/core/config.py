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
    # 单个数据源单次调用的超时（秒）。超了就当该源失败、降级到下一个。
    # 没有这个保护的话，一个卡住的数据源会把整个 HTTP 请求拖死 ——
    # 前端就会永远显示"搜索中…"。AKShare 底层是爬第三方站点，
    # 慢或者挂是常态，必须时间盒住。
    provider_timeout: int = 12

    # ---------- LLM ----------
    llm_provider: LLMProvider = "echo"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    # 留空则分析师复用主模型
    anthropic_analyst_model: str = ""
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    # 模型因安全策略拒答时，让服务端自动换一个模型跑完同一请求
    anthropic_refusal_fallback: bool = True

    # 离线桩每段输出之间的间隔（秒）。设成 0.03 左右能看到打字机效果，
    # 首次体验和调前端动效时有用；跑测试时保持 0 以免拖慢。
    echo_delay: float = 0.0

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
