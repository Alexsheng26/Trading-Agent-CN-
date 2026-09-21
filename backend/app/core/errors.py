"""领域异常。"""

from __future__ import annotations


class TradingAgentError(Exception):
    """本项目所有业务异常的基类。"""


class SymbolError(TradingAgentError):
    """股票代码无法识别。"""


class ProviderUnavailable(TradingAgentError):
    """数据源整体不可用（缺依赖、缺 token、网络不通）。"""


class CapabilityNotSupported(TradingAgentError):
    """数据源不支持该接口，registry 会据此降级到下一个数据源。"""


class DataNotFound(TradingAgentError):
    """所有数据源都拿不到该数据。"""


class LLMError(TradingAgentError):
    """模型调用失败。"""
