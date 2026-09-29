"""浏览器 TLS 指纹运输层。

由来：用户机器上 akshare 只有"个股新闻"能通，其余全部报
`RemoteDisconnected('Remote end closed connection without response')`。
而新闻恰好是 akshare 里**唯一**用 curl_cffi 的接口，其余都用普通 requests ——
对方在 TLS 握手层面就把普通客户端掐掉了。

所以我们把 akshare 各模块里的 `requests` 换成带浏览器指纹的代理。
这个文件用**本地 HTTP 服务**验证那个代理和 akshare 真实调用路径兼容 ——
尤其是 `request_with_retry` 里 `with requests.Session() as s: s.mount(...)`
这种 curl_cffi 原本不支持的写法。

不联网、不碰东方财富。
"""

from __future__ import annotations

import importlib.util
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

requires_akshare = pytest.mark.skipif(
    importlib.util.find_spec("akshare") is None, reason="未安装可选依赖 akshare"
)
requires_curl_cffi = pytest.mark.skipif(
    importlib.util.find_spec("curl_cffi") is None, reason="未安装 curl_cffi"
)

# f3（涨跌幅）是必须的：fetch_paginated_data 会按它排序，缺了就 KeyError
PAYLOAD = {
    "data": {"total": 1, "diff": [{"f12": "600519", "f14": "贵州茅台", "f3": 1.23}]}
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(PAYLOAD).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """别把请求日志打到测试输出里。"""


@pytest.fixture
def local_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    from app.data.browser_transport import disable

    disable()
    yield
    disable()


# ---------------------------------------------------------------- 替换本身


@requires_akshare
@requires_curl_cffi
def test_enable_patches_akshare_modules() -> None:
    import akshare  # noqa: F401 —— 需要先导入，模块才在 sys.modules 里
    import requests

    from app.data.browser_transport import BrowserRequests, disable, enable, is_enabled

    patched = enable()
    assert patched, "应当至少替换一个 akshare 模块"
    assert is_enabled()

    import akshare.stock.stock_ask_bid_em as ask_bid

    assert isinstance(ask_bid.requests, BrowserRequests), "单只行情模块必须被替换"

    # 新闻模块本来就用 curl_cffi，不该被动到
    import akshare.news.news_stock as news

    assert not isinstance(news.requests, BrowserRequests)

    disable()
    assert not is_enabled()
    assert ask_bid.requests is requests, "还原后必须回到真的 requests"


@requires_curl_cffi
def test_proxy_injects_impersonate() -> None:
    """代理必须把 impersonate 传下去 —— 不传就等于没换指纹。"""
    from app.data.browser_transport import BrowserRequests

    calls: list[dict] = []

    class _FakeCffi:
        def get(self, url, **kw):  # type: ignore[no-untyped-def]
            calls.append({"url": url, **kw})
            return "ok"

        def post(self, url, **kw):  # type: ignore[no-untyped-def]
            calls.append({"url": url, **kw})
            return "ok"

    proxy = BrowserRequests(_FakeCffi(), object(), "safari")  # type: ignore[arg-type]
    proxy.get("http://x", params={"a": 1})
    proxy.post("http://x")

    assert calls[0]["impersonate"] == "safari"
    assert calls[0]["params"] == {"a": 1}
    assert calls[1]["impersonate"] == "safari"


@requires_curl_cffi
def test_proxy_respects_explicit_impersonate() -> None:
    """调用方自己指定了就不要覆盖。"""
    from app.data.browser_transport import BrowserRequests

    seen: list[str] = []

    class _FakeCffi:
        def get(self, url, **kw):  # type: ignore[no-untyped-def]
            seen.append(kw["impersonate"])

    proxy = BrowserRequests(_FakeCffi(), object(), "chrome")  # type: ignore[arg-type]
    proxy.get("http://x", impersonate="edge")
    assert seen == ["edge"]


def test_proxy_falls_back_for_unknown_attributes() -> None:
    """curl_cffi 没有的属性要回落到真 requests，否则 akshare 里
    `requests.adapters` 这类写法会直接 AttributeError。"""
    from app.data.browser_transport import BrowserRequests

    class _FakeCffi:
        only_here = "cffi"

    class _FakeReal:
        adapters = "real-adapters"

    proxy = BrowserRequests(_FakeCffi(), _FakeReal(), "chrome")  # type: ignore[arg-type]
    assert proxy.only_here == "cffi"
    assert proxy.adapters == "real-adapters"


def test_enable_is_idempotent() -> None:
    from app.data.browser_transport import enable

    first = enable()
    assert enable() == first, "重复调用不该重复替换"


# ---------------------------------------------------------------- 与 akshare 真实调用路径兼容


@requires_akshare
@requires_curl_cffi
def test_request_with_retry_works_through_proxy(local_server: str) -> None:
    """**本文件最重要的一条。**

    `akshare.utils.request.request_with_retry` 用的是
    `with requests.Session() as s: s.mount(...); s.get(...)` ——
    curl_cffi 的 Session 没有 `mount`，垫片垫得不对这里就会 AttributeError。
    用本地 HTTP 服务打通一遍，确认 akshare 的真实调用路径能跑。
    """
    import akshare  # noqa: F401
    from akshare.utils.request import request_with_retry

    from app.data.browser_transport import enable

    enable()
    response = request_with_retry(local_server, params={"secid": "1.600519"}, timeout=5)
    assert response.status_code == 200
    assert response.json()["data"]["diff"][0]["f14"] == "贵州茅台"


@requires_akshare
@requires_curl_cffi
def test_paginated_fetch_works_through_proxy(local_server: str) -> None:
    """全市场快照走的是 fetch_paginated_data，也要能跑通。"""
    import akshare  # noqa: F401
    from akshare.utils.func import fetch_paginated_data

    from app.data.browser_transport import enable

    enable()
    df = fetch_paginated_data(local_server, {"pn": "1", "pz": "100"})
    assert not df.empty
    assert df.iloc[0]["f14"] == "贵州茅台"


@requires_akshare
@requires_curl_cffi
def test_plain_get_works_through_proxy(local_server: str) -> None:
    """多数接口是直接 requests.get，最常见的一条路径。"""
    import akshare  # noqa: F401
    import akshare.stock.stock_ask_bid_em as ask_bid

    from app.data.browser_transport import enable

    enable()
    r = ask_bid.requests.get(local_server, params={"secid": "0.300741"}, timeout=5)
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 1


# ---------------------------------------------------------------- 开关


@requires_akshare
def test_setting_off_skips_patching(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import requests

    from app.core.config import get_settings
    from app.data.akshare_provider import AkshareProvider
    from app.data.browser_transport import is_enabled

    monkeypatch.setenv("AKSHARE_BROWSER_TRANSPORT", "false")
    get_settings.cache_clear()

    provider = AkshareProvider()
    provider._module()  # 触发导入与（不）打补丁
    assert not is_enabled()

    import akshare.stock.stock_ask_bid_em as ask_bid

    assert ask_bid.requests is requests
    get_settings.cache_clear()


@requires_akshare
@requires_curl_cffi
def test_provider_enables_transport_on_first_use(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings
    from app.data.akshare_provider import AkshareProvider
    from app.data.browser_transport import is_enabled

    monkeypatch.setenv("AKSHARE_BROWSER_TRANSPORT", "true")
    get_settings.cache_clear()

    provider = AkshareProvider()
    provider._module()
    assert is_enabled(), "第一次用到 akshare 时就该把指纹换上"
    get_settings.cache_clear()
