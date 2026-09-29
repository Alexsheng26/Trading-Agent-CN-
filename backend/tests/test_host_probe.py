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


# ---------------------------------------------------------------- 依赖缺失时


@pytest.mark.parametrize(
    ("plain", "browser", "expected"),
    [
        ("未测", "未测", "无法探测"),
        ("未测", "正常", "可达"),
        ("未测", "失败", "不可达"),
    ],
)
def test_verdict_handles_missing_clients(plain: str, browser: str, expected: str) -> None:
    """requests 不是本项目的运行时依赖（随 akshare 一起装）。

    只装了 tushare 的用户那里 `import requests` 会失败 ——
    产物环境的测试真的因此报过 ModuleNotFoundError，
    诊断接口当时会直接 500，而那是用户排查时唯一的工具。
    """
    assert _verdict(plain, browser).startswith(expected)


def test_plain_probe_reports_missing_requests(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """requests 没装时要报"未测"，不能抛异常。"""
    import builtins

    from app.services.host_probe import _try_plain

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "requests":
            raise ImportError("装作没装 requests")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    status, detail = _try_plain("https://example.invalid/")
    assert status == "未测"
    assert "requests" in detail


def test_diagnose_survives_probe_failure(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """主机探测自己挂了，诊断接口也必须照常返回逐接口结果。"""
    import app.services.host_probe as hp

    async def boom(_impersonate):  # type: ignore[no-untyped-def]
        raise RuntimeError("探测炸了")

    monkeypatch.setattr(hp, "probe_hosts", boom)
    body = client.get("/api/v1/market/diagnose").json()
    assert body["providers"], "逐接口结果不能因为主机探测失败而丢掉"
    assert body["hosts"] == []
    assert "探测本身失败" in body["host_summary"]
