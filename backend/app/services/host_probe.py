"""逐个主机探测可达性，并分别用两种 HTTP 客户端各试一次。

**为什么要这个**

排查"为什么取不到行情"时，最关键的一个岔路口是：

    到底是**这台机器连不上那个域名**，还是**客户端被对方按指纹拒绝**？

这两种情况的处理方式完全不同（前者要看代理/防火墙或换数据源，
后者换个 HTTP 客户端就能解决），而光看 akshare 抛出来的异常很难分清 ——
`RemoteDisconnected` 和超时都可能是两者之一造成的。

所以这里对每个主机**用普通 requests 和 curl_cffi（浏览器指纹）各打一次**：

    两个都失败        -> 主机不可达，本机网络层面的问题
    只有普通 requests 失败 -> 指纹拦截，开浏览器指纹即可
    两个都成功        -> 这个主机没问题，问题在别处

一次探测把 akshare 用到的所有站点都覆盖，用户看一眼就知道该动哪里。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: 每个主机每种客户端的超时（秒）。只是探连通性，不用给太多
_TIMEOUT = 6.0

#: 主机 -> 谁在用它。顺序即展示顺序。
HOSTS: tuple[tuple[str, str], ...] = (
    ("push2.eastmoney.com", "东财-实时行情"),
    ("push2his.eastmoney.com", "东财-日线"),
    ("82.push2.eastmoney.com", "东财-全市场列表"),
    ("search-api-web.eastmoney.com", "东财-个股新闻"),
    ("xueqiu.com", "雪球-实时行情"),
    ("finance.sina.com.cn", "新浪-日线"),
    ("vip.stock.finance.sina.com.cn", "新浪-大盘指数"),
    ("api.tushare.pro", "Tushare"),
)


@dataclass(slots=True)
class HostResult:
    host: str
    used_by: str
    #: 普通 requests 的结果："正常" / "失败"
    plain: str = "未测"
    plain_detail: str = ""
    #: curl_cffi（浏览器指纹）的结果
    browser: str = "未测"
    browser_detail: str = ""
    #: 结论：一句人话
    verdict: str = ""


@dataclass(slots=True)
class ProbeReport:
    results: list[HostResult] = field(default_factory=list)
    summary: str = ""


def _try_plain(url: str) -> tuple[str, str]:
    import requests  # noqa: PLC0415

    try:
        r = requests.get(url, timeout=_TIMEOUT)
        return "正常", f"HTTP {r.status_code}"
    except Exception as exc:
        return "失败", _brief(exc)


def _try_browser(url: str, impersonate: str) -> tuple[str, str]:
    try:
        from curl_cffi import requests as cffi  # noqa: PLC0415
    except ImportError:
        return "未测", "未安装 curl_cffi"
    try:
        r = cffi.get(url, timeout=_TIMEOUT, impersonate=impersonate)
        return "正常", f"HTTP {r.status_code}"
    except Exception as exc:
        return "失败", _brief(exc)


def _brief(exc: object) -> str:
    """异常压成一句短话。这里的报错只是给人看方向，不需要完整堆栈。"""
    from app.data.registry import short_error  # noqa: PLC0415

    return f"{type(exc).__name__}: {short_error(exc)}"[:140]


def _verdict(plain: str, browser: str) -> str:
    if plain == "正常" and browser in ("正常", "未测"):
        return "可达"
    if plain == "失败" and browser == "正常":
        return "指纹被拦（开浏览器指纹即可）"
    if plain == "失败" and browser == "失败":
        return "不可达（本机网络问题）"
    if plain == "正常" and browser == "失败":
        return "浏览器指纹反而不通（少见，可关掉该开关）"
    return "结果不明"


async def probe_hosts(impersonate: str = "chrome") -> ProbeReport:
    """并发探测所有主机。

    ⚠️ 会真的发网络请求。最慢约 `_TIMEOUT` 秒（并发，不是累加）。
    """

    async def one(host: str, used_by: str) -> HostResult:
        url = f"https://{host}/"
        plain, plain_detail = await asyncio.to_thread(_try_plain, url)
        browser, browser_detail = await asyncio.to_thread(_try_browser, url, impersonate)
        return HostResult(
            host=host,
            used_by=used_by,
            plain=plain,
            plain_detail=plain_detail,
            browser=browser,
            browser_detail=browser_detail,
            verdict=_verdict(plain, browser),
        )

    results = list(await asyncio.gather(*(one(h, u) for h, u in HOSTS)))
    return ProbeReport(results=results, summary=_summarize(results))


def _summarize(results: list[HostResult]) -> str:
    """把一堆结果收成一句"所以该干什么"。"""
    unreachable = [r.host for r in results if r.verdict.startswith("不可达")]
    fingerprint = [r.host for r in results if r.verdict.startswith("指纹")]
    reachable = [r for r in results if r.verdict == "可达"]

    if fingerprint and not unreachable:
        return (
            f"{len(fingerprint)} 个主机是被按客户端指纹拦掉的，"
            "开着 AKSHARE_BROWSER_TRANSPORT=true 就能用。"
        )
    if unreachable:
        em = [h for h in unreachable if "eastmoney" in h]
        alt = [r.host for r in reachable if "eastmoney" not in r.host]
        if em and alt:
            return (
                f"东方财富的 {len(em)} 个主机在这台机器上不可达"
                f"（{'、'.join(em)}），但 {'、'.join(alt)} 是通的 —— "
                "把 DATA_PROVIDER_ORDER 设成 akshare,xueqiu,sina,tushare，"
                "行情和 K 线会自动走这些通的源。"
            )
        if not alt:
            return (
                "所有行情主机都不可达，基本可以确定是本机网络层面的问题："
                "代理软件、安全软件、公司/校园网出口策略。先试着关掉代理再探测。"
            )
        return f"{len(unreachable)} 个主机不可达：{'、'.join(unreachable)}。"
    return "所有主机都可达。取不到数据的话，原因在接口层面而不是网络。"
