"""主机可达性探测的判断逻辑。

这层的价值全在"结论对不对"：它要分清
「连不上这个域名」和「客户端被按指纹拒绝」——
两者的处理方式完全不同（前者换数据源/查代理，后者换 HTTP 客户端），
而光看 akshare 抛的异常是分不清的。

判断和总结都是纯函数，这里不发任何网络请求。
"""

from __future__ import annotations

import pytest

from app.services.host_probe import HOSTS, HostResult, _summarize, _verdict


@pytest.mark.parametrize(
    ("plain", "browser", "expected_prefix"),
    [
        ("正常", "正常", "可达"),
        ("正常", "未测", "可达"),
        ("失败", "正常", "指纹被拦"),
        ("失败", "失败", "不可达"),
        ("正常", "失败", "浏览器指纹反而不通"),
    ],
)
def test_verdict(plain: str, browser: str, expected_prefix: str) -> None:
    assert _verdict(plain, browser).startswith(expected_prefix)


def _r(host: str, verdict: str) -> HostResult:
    return HostResult(host=host, used_by="测试", verdict=verdict)


def test_summary_points_at_fingerprint_when_browser_works() -> None:
    results = [
        _r("push2.eastmoney.com", "指纹被拦（开浏览器指纹即可）"),
        _r("xueqiu.com", "可达"),
    ]
    out = _summarize(results)
    assert "指纹" in out
    assert "AKSHARE_BROWSER_TRANSPORT" in out


def test_summary_recommends_backup_sources_when_only_eastmoney_is_blocked() -> None:
    """这正是用户的情况：东财不可达、雪球/新浪可达 —— 要明确告诉他改配置。"""
    results = [
        _r("push2.eastmoney.com", "不可达（本机网络问题）"),
        _r("push2his.eastmoney.com", "不可达（本机网络问题）"),
        _r("xueqiu.com", "可达"),
        _r("finance.sina.com.cn", "可达"),
    ]
    out = _summarize(results)
    assert "push2.eastmoney.com" in out
    assert "xueqiu.com" in out
    assert "akshare,xueqiu,sina,tushare" in out, "要给出可以直接抄的配置"


def test_summary_says_network_when_everything_is_blocked() -> None:
    results = [_r(h, "不可达（本机网络问题）") for h, _ in HOSTS]
    out = _summarize(results)
    assert "本机网络" in out
    assert "代理" in out


def test_summary_when_all_reachable() -> None:
    results = [_r(h, "可达") for h, _ in HOSTS]
    out = _summarize(results)
    assert "都可达" in out
    assert "接口层面" in out


def test_host_list_covers_both_eastmoney_and_alternatives() -> None:
    """探测清单必须同时覆盖东财和备份源，否则没法做对比判断。"""
    hosts = [h for h, _ in HOSTS]
    assert any("push2.eastmoney.com" == h for h in hosts)
    assert any("eastmoney" in h and "search-api-web" in h for h in hosts)
    assert "xueqiu.com" in hosts
    assert any("sina" in h for h in hosts)
    assert "api.tushare.pro" in hosts


async def test_probe_returns_one_result_per_host() -> None:
    """真的发请求（沙箱里大概全失败），只断言结构完整。"""
    from app.services.host_probe import probe_hosts

    report = await probe_hosts("chrome")
    assert len(report.results) == len(HOSTS)
    assert report.summary
    for r in report.results:
        assert r.host and r.used_by and r.verdict
        assert r.plain in ("正常", "失败", "未测")
        assert r.browser in ("正常", "失败", "未测")
