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
    # 刻意**不含 mock**：mock 是程序生成的假行情，在一个会输出买卖点位的
    # 工具里被当成真实数据，后果很严重（本项目真的发生过：全市场快照每次
    # 超时，于是每次静默降级到 mock，用户看到"华宝股份 299.42"还拿到了
    # 一整套止盈止损）。取不到真数据就该报错，不该编一个。
    # 要离线演示时显式写 DATA_PROVIDER_ORDER=mock。
    # 顺序是按"主机多样性"排的：东方财富（字段最全）-> 雪球 -> 新浪 -> Tushare。
    # 中间两个和东方财富**没有任何共用主机**，所以当 push2.eastmoney.com 这类
    # 主机在某些网络下不可达时（代理软件、企业网络、个别运营商线路），
    # 行情和 K 线还能自动降级过去，而不是整体没数据。
    data_provider_order: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["akshare", "xueqiu", "sina", "tushare"]
    )
    tushare_token: str = ""
    quote_cache_ttl: int = 60
    history_cache_ttl: int = 3600
    # 单个数据源单次调用的超时（秒）。超了就当该源失败、降级到下一个。
    # 没有这个保护的话，一个卡住的数据源会把整个 HTTP 请求拖死 ——
    # 前端就会永远显示"搜索中…"。AKShare 底层是爬第三方站点，
    # 慢或者挂是常态，必须时间盒住。
    provider_timeout: int = 12
    # 启动后在后台建立"全市场代码-名称目录"（按名称搜索靠它）。
    # 测试里关掉：后台任务会和断言抢时间。
    symbol_index_autorefresh: bool = True
    # 目录多久算过期（小时）。A股每天有新股上市，一天一次足够。
    symbol_index_ttl_hours: int = 24
    # 建目录时给数据源的超时（秒）。全市场列表本身就要 60-90 秒，
    # 不能用 PROVIDER_TIMEOUT 那个给交互接口准备的小数值。
    symbol_index_timeout: int = 240
    # 让 akshare 的请求走浏览器 TLS 指纹（curl_cffi）。
    # 东方财富会按 TLS 握手特征拦掉普通 Python requests：表现是
    # "Remote end closed connection without response"，而 akshare 里唯一用
    # curl_cffi 的个股新闻接口照样能通。开着它能绕过这类拦截。
    # curl_cffi 本来就是 akshare 的依赖，不需要额外安装。
    akshare_browser_transport: bool = True
    # 模拟哪个浏览器，取值见 curl_cffi 文档（chrome / safari / edge ...）
    akshare_impersonate: str = "chrome"
    # 雪球行情接口需要一个 xq_a_token（登录后的 cookie）。不填的话雪球源会
    # 直接报 400016 要求登录 —— 那时候行情会自动降级到后面的源。
    # 取法：浏览器打开 xueqiu.com，F12 -> 应用/Application -> Cookie，
    # 复制 xq_a_token 的值。它会过期，失效后重新取一次即可。
    xueqiu_token: str = ""

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
