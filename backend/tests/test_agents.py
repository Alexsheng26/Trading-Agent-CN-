"""智能体流水线。"""

from __future__ import annotations

import pytest

from app.agents.base import parse_verdict
from app.agents.context import AnalysisContext
from app.agents.orchestrator import AnalysisOrchestrator, _sanitize_decision
from app.core.config import Settings
from app.llm.echo_client import EchoLLM

# ---------------------------------------------------------------- 尾标解析


@pytest.mark.parametrize(
    ("raw", "signal", "confidence"),
    [
        ("正文\n\n---\n倾向：看多 | 置信度：72", "看多", 72),
        ("正文\n---\n倾向: 看空 | 置信度: 33", "看空", 33),
        ("正文\n\n---\n**倾向：中性** | **置信度：50**", "中性", 50),
        ("正文\n---\n倾向：偏多", "看多", None),  # 别名归一
        ("正文\n---\n倾向：看多 | 置信度：150", "看多", 100),  # 越界夹紧
        ("模型忘了写尾标", None, None),
    ],
)
def test_parse_verdict(raw: str, signal: str | None, confidence: int | None) -> None:
    got_signal, got_conf, _ = parse_verdict(raw)
    assert got_signal == signal
    assert got_conf == confidence


def test_parse_verdict_ignores_mention_in_body() -> None:
    """正文里引用别人的倾向时，只能取最后那个真正的尾标。"""
    raw = "空头的倾向：看空 值得注意。\n\n---\n倾向：看多 | 置信度：61"
    signal, conf, body = parse_verdict(raw)
    assert (signal, conf) == ("看多", 61)
    assert "空头的倾向：看空 值得注意。" in body  # 正文没被吞掉


def test_parse_verdict_strips_tail_from_body() -> None:
    signal, _, body = parse_verdict("## 结论\n\n有内容。\n\n---\n倾向：看多 | 置信度：80")
    assert signal == "看多"
    assert "倾向" not in body
    assert body.endswith("有内容。")


# ---------------------------------------------------------------- 上下文渲染


async def test_market_brief_contains_key_facts(snapshot) -> None:  # type: ignore[no-untyped-def]
    ctx = AnalysisContext.build(snapshot, horizon="波段")
    brief = ctx.market_brief()

    # 简报必须带上这些，否则分析师无从下手
    for marker in ("最新价", "MA20", "RSI14", "涨跌幅限制", "T+1", "财务数据", "资金流向"):
        assert marker in brief, f"简报缺少 {marker}"

    # 长度要够触发提示缓存（最小可缓存前缀约 1024+ token）
    assert len(brief) > 2000


async def test_market_brief_flags_mock_data(snapshot) -> None:  # type: ignore[no-untyped-def]
    """用了 mock 数据必须在简报里明确警告，否则模型会把假数据当真。"""
    brief = AnalysisContext.build(snapshot).market_brief()
    assert "mock" in brief
    assert "假数据" in brief


async def test_market_brief_marks_missing_data(snapshot) -> None:  # type: ignore[no-untyped-def]
    """某个维度抓取失败时要写"数据缺失"，不能让模型以为是 0。"""
    snapshot.financials = []
    snapshot.news = []
    brief = AnalysisContext.build(snapshot).market_brief()
    assert "财务数据缺失" in brief
    assert "新闻数据缺失" in brief


async def test_horizon_falls_back_to_default(snapshot) -> None:  # type: ignore[no-untyped-def]
    assert AnalysisContext.build(snapshot, horizon="乱填").horizon == "波段"


# ---------------------------------------------------------------- 流水线


async def test_pipeline_runs_all_stages(snapshot) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(llm_provider="echo", debate_rounds=2, data_provider_order=["mock"])
    ctx = AnalysisContext.build(snapshot, horizon="波段")

    events = [e async for e in AnalysisOrchestrator(EchoLLM(), settings).run(ctx)]
    types = [e.type for e in events]

    assert types[0] == "run_start"
    assert types[-1] == "run_done"
    assert "error" not in types
    assert types.count("stage_start") == 4  # 分析师 / 辩论 / 风控 / 决策
    # 4 位分析师 + 2 轮 × 2 位研究员 + 1 位风控 = 9
    assert types.count("agent_done") == 9
    assert types.count("decision") == 1
    assert types.count("agent_delta") > 0  # 流式增量真的产生了


async def test_pipeline_assigns_distinct_signals(snapshot) -> None:  # type: ignore[no-untyped-def]
    """多头和空头必须拿到不同的倾向 —— 角色识别不能串。"""
    settings = Settings(llm_provider="echo", debate_rounds=1, data_provider_order=["mock"])
    ctx = AnalysisContext.build(snapshot)

    outputs = {
        e.data["agent"]: e.data["output"]
        async for e in AnalysisOrchestrator(EchoLLM(), settings).run(ctx)
        if e.type == "agent_done"
    }
    assert outputs["bull_researcher_r1"]["signal"] == "看多"
    assert outputs["bear_researcher_r1"]["signal"] == "看空"


async def test_debate_can_be_disabled(snapshot) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(llm_provider="echo", debate_rounds=0, data_provider_order=["mock"])
    ctx = AnalysisContext.build(snapshot)

    events = [e async for e in AnalysisOrchestrator(EchoLLM(), settings).run(ctx)]
    stages = {e.data.get("stage") for e in events if e.type == "stage_start"}
    assert "debate" not in stages
    assert sum(1 for e in events if e.type == "agent_done") == 5  # 4 分析师 + 风控


async def test_failing_agent_does_not_kill_pipeline(snapshot) -> None:  # type: ignore[no-untyped-def]
    """单个智能体挂掉，其余照跑，最终仍要出决策。"""

    class _Flaky(EchoLLM):
        calls = 0

        async def stream(self, system_blocks, messages, **kwargs):  # type: ignore[no-untyped-def]
            _Flaky.calls += 1
            if _Flaky.calls == 1:
                raise RuntimeError("模拟模型调用失败")
            async for chunk in super().stream(system_blocks, messages, **kwargs):
                yield chunk

    settings = Settings(llm_provider="echo", debate_rounds=0, data_provider_order=["mock"])
    ctx = AnalysisContext.build(snapshot)

    events = [e async for e in AnalysisOrchestrator(_Flaky(), settings).run(ctx)]
    types = [e.type for e in events]
    outputs = [e.data["output"] for e in events if e.type == "agent_done"]

    assert "decision" in types
    assert types[-1] == "run_done"
    assert sum(1 for o in outputs if o["error"]) == 1  # 恰好一个失败
    assert sum(1 for o in outputs if not o["error"]) == 4  # 其余都成功


async def test_pipeline_plan_matches_actual_agents(snapshot) -> None:  # type: ignore[no-untyped-def]
    """前端靠 plan 先画骨架，它必须和真正跑出来的智能体对得上。"""
    settings = Settings(llm_provider="echo", debate_rounds=2, data_provider_order=["mock"])
    ctx = AnalysisContext.build(snapshot)

    planned: list[str] = []
    actual: list[str] = []
    async for event in AnalysisOrchestrator(EchoLLM(), settings).run(ctx):
        if event.type == "run_start":
            for stage in event.data["pipeline"]:
                planned += [a["key"] for a in stage["agents"]]
        elif event.type == "agent_done":
            actual.append(event.data["agent"])

    # plan 里多一个 "trader"（决策环节不是流式智能体）
    assert set(planned) - set(actual) == {"trader"}


# ---------------------------------------------------------------- 决策兜底


def test_sanitize_clamps_out_of_range(snapshot) -> None:  # type: ignore[no-untyped-def]
    ctx = AnalysisContext.build(snapshot)
    cleaned = _sanitize_decision(
        {
            "action": "梭哈",  # 非法枚举
            "conviction": 999,
            "position_pct": 300,  # 想压上全部身家
            "horizon": "永久",
            "key_reasons": ["a", "b", "c", "d", "e", "f"],
            "key_risks": "不是列表",
        },
        ctx,
    )
    assert cleaned["action"] == "观望"  # 非法值退回保守选项
    assert cleaned["conviction"] == 100
    assert cleaned["position_pct"] == 50.0  # 单票仓位硬上限
    assert cleaned["horizon"] == ctx.horizon
    assert len(cleaned["key_reasons"]) == 4
    assert cleaned["key_risks"] == []
    assert cleaned["disclaimer"]  # 免责声明必须在


def test_sanitize_handles_missing_fields(snapshot) -> None:  # type: ignore[no-untyped-def]
    ctx = AnalysisContext.build(snapshot)
    cleaned = _sanitize_decision({}, ctx)
    assert cleaned["action"] == "观望"
    assert cleaned["conviction"] == 0
    assert cleaned["position_pct"] == 0.0


def test_brief_flags_non_realtime_quote() -> None:
    """非实时价必须在简报里写明。

    不写的话模型会拿上一交易日的收盘价当现价去算入场区间和止损，
    而且不会在结论里声明这个前提 —— 用户看到的就是一套基于过期价格、
    却毫无提示的点位。
    """
    from dataclasses import replace
    from datetime import date

    from app.agents.context import AnalysisContext
    from app.data.base import Quote
    from app.data.symbols import normalize
    from app.services.indicators import compute_snapshot

    stale = Quote(
        symbol="600519.SH",
        name="贵州茅台",
        price=1430.0,
        prev_close=1410.0,
        is_realtime=False,
        quote_date=date(2026, 9, 29),
    )

    import asyncio

    from app.data.mock_provider import MockProvider
    from app.data.registry import ProviderRegistry
    from app.services.market_data import MarketDataService

    sym = normalize("600519")
    snap = asyncio.run(MarketDataService(ProviderRegistry([MockProvider()])).snapshot(sym))
    snap = replace(snap, quote=stale)

    ctx = AnalysisContext(
        symbol=sym, snapshot=snap, indicators=compute_snapshot(snap.bars), horizon="波段"
    )
    brief = ctx.market_brief()

    assert "不是实时价" in brief
    assert "2026-09-29" in brief
    assert "收盘价，非实时" in brief
    assert "降低置信度" in brief


def test_brief_does_not_warn_for_realtime_quote() -> None:
    """实时价就别加警告，否则每次分析都在喊狼来了。"""
    import asyncio

    from app.agents.context import AnalysisContext
    from app.data.mock_provider import MockProvider
    from app.data.registry import ProviderRegistry
    from app.data.symbols import normalize
    from app.services.indicators import compute_snapshot
    from app.services.market_data import MarketDataService

    sym = normalize("600519")
    snap = asyncio.run(MarketDataService(ProviderRegistry([MockProvider()])).snapshot(sym))
    ctx = AnalysisContext(
        symbol=sym, snapshot=snap, indicators=compute_snapshot(snap.bars), horizon="波段"
    )
    assert "不是实时价" not in ctx.market_brief()
