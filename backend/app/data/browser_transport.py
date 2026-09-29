"""让 AKShare 的 HTTP 请求走浏览器 TLS 指纹（curl_cffi）。

**为什么需要这个**

用户机器上的诊断结果非常干净地指向一个原因：

    个股新闻   正常
    搜索/实时行情/日线/大盘指数
              失败：('Connection aborted.', RemoteDisconnected(
                    'Remote end closed connection without response'))

而 akshare 里**唯一**用 `curl_cffi` 取数的接口就是个股新闻
（`akshare.news.news_stock` 里写的是 `from curl_cffi import requests`），
其余全部用普通 `requests`。一个能通、其余全挂，且失败不是超时而是
"对方直接掐断连接不给响应" —— 这是 TLS 指纹层面的拦截：
普通 Python requests 的握手特征和浏览器不一样，被识别出来就直接断连。
`curl_cffi` 会模拟真实 Chrome 的指纹，所以能过。

**做法**

只换运输层，不碰 akshare 的字段解析：把 akshare 各模块里的 `requests`
换成一个会自动带上 `impersonate` 参数的代理。这样字段映射、重试、
分页逻辑全部照旧，唯一的变化是底下用什么发请求。

代理要兼容 akshare 实际用到的那些 requests 用法，尤其是
`akshare.utils.request.request_with_retry` 里的
`with requests.Session() as s: s.mount(...); s.get(...)` ——
curl_cffi 的 Session 没有 `mount`，所以这里要垫一层。

关掉它：`AKSHARE_BROWSER_TRANSPORT=false`。
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)

#: 已替换过的模块 -> 原来的 requests，用于还原
_patched: dict[str, Any] = {}


class _BrowserSession:
    """垫在 curl_cffi Session 外面，补齐 akshare 用到的 requests Session 接口。"""

    def __init__(self, session: Any, impersonate: str) -> None:
        self._session = session
        self._impersonate = impersonate

    # akshare 里是 `with requests.Session() as s:` 这种写法
    def __enter__(self) -> _BrowserSession:
        return self

    def __exit__(self, *exc: object) -> None:
        close = getattr(self._session, "close", None)
        if close is not None:
            close()

    def mount(self, *_args: object, **_kwargs: object) -> None:
        """空实现。

        akshare 挂 HTTPAdapter 是为了禁用连接池复用，curl_cffi 没有这个概念，
        忽略掉即可 —— 比让它抛 AttributeError 好。
        """

    def get(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("impersonate", self._impersonate)
        return self._session.get(*args, **kwargs)

    def post(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("impersonate", self._impersonate)
        return self._session.post(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class BrowserRequests:
    """伪装成 `requests` 模块的代理，get/post 自动带浏览器指纹。"""

    def __init__(self, cffi: ModuleType, fallback: ModuleType, impersonate: str) -> None:
        self._cffi = cffi
        self._fallback = fallback
        self._impersonate = impersonate

    def get(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("impersonate", self._impersonate)
        return self._cffi.get(*args, **kwargs)

    def post(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("impersonate", self._impersonate)
        return self._cffi.post(*args, **kwargs)

    def request(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("impersonate", self._impersonate)
        return self._cffi.request(*args, **kwargs)

    def Session(self, *args: Any, **kwargs: Any) -> _BrowserSession:  # noqa: N802
        return _BrowserSession(self._cffi.Session(*args, **kwargs), self._impersonate)

    def __getattr__(self, name: str) -> Any:
        """其余属性先找 curl_cffi，没有就回落到真的 requests。

        回落是必要的：akshare 有些模块会用到 `requests.adapters`、
        `requests.exceptions` 里的东西，curl_cffi 不一定都有同名对象。
        """
        try:
            return getattr(self._cffi, name)
        except AttributeError:
            return getattr(self._fallback, name)


def enable(impersonate: str = "chrome") -> list[str]:
    """把已导入的 akshare 模块里的 `requests` 换成浏览器指纹版本。

    返回被替换的模块名。任何一步失败都只记日志、不抛 ——
    这是个可选的绕障手段，不该让整个数据源挂掉。
    """
    if _patched:
        return list(_patched)

    try:
        import requests as real_requests  # noqa: PLC0415
        from curl_cffi import requests as cffi_requests  # noqa: PLC0415
    except ImportError as exc:
        log.warning("curl_cffi 不可用，akshare 继续用普通 requests：%s", exc)
        return []

    proxy = BrowserRequests(cffi_requests, real_requests, impersonate)

    # 按名字在已导入的模块里找，而不是写死一张清单 ——
    # akshare 版本一变，写死的清单就会悄悄失效。
    for name, module in list(sys.modules.items()):
        if not name.startswith("akshare") or module is None:
            continue
        current = getattr(module, "requests", None)
        if current is real_requests:
            try:
                module.requests = proxy  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover —— 防御性
                log.debug("替换 %s 的 requests 失败：%s", name, exc)
                continue
            _patched[name] = current

    if _patched:
        log.info(
            "已让 %d 个 akshare 模块走浏览器 TLS 指纹（impersonate=%s）",
            len(_patched),
            impersonate,
        )
    else:
        log.warning("没有找到可替换的 akshare 模块，继续用普通 requests")
    return list(_patched)


def disable() -> None:
    """还原。测试用。"""
    for name, original in list(_patched.items()):
        module = sys.modules.get(name)
        if module is not None:
            module.requests = original  # type: ignore[attr-defined]
    _patched.clear()


def is_enabled() -> bool:
    return bool(_patched)
