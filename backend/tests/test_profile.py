"""分析深度档位与模型选择。

要钉死的几件事：
  1. **标准档 = 引入档位之前的行为**，老用户什么都不改结果不变；
  2. 档位在 DeepSeek 这类兼容接口下**不能是假开关** —— 那里没有 effort，
     必须靠轮数和模型区分，且对外如实说明；
  3. 手选模型只认白名单；
  4. 决策环节换过模型却没拿到可用决策时，用默认模型重做，并如实标注。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.context import AnalysisContext
from app.agents.orchestrator import AnalysisOrchestrator
from app.agents.profile import (
    ProfileError,
    allowed_models,
    describe_options,
    resolve_profile,
)
from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.base import Completion, Usage
from app.llm.echo_client import EchoLLM


def _claude(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_provider": "anthropic",
        "anthropic_model": "claude-main",
        "anthropic_analyst_model": "claude-analyst",
        "llm_effort": "high",
        "debate_rounds": 2,
        "data_provider_order": ["mock"],
    }
    return Settings(**{**base, **kw})


def _deepseek(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_provider": "openai_compat",
        "openai_compat_model": "deepseek-chat",
        "debate_rounds": 2,
        "data_provider_order": ["mock"],
    }
    return Settings(**{**base, **kw})


# ---------------------------------------------------------------- 标准档不变


def test_standard_matches_legacy_claude() -> None:
    p = resolve_profile(_claude())
    assert p.debate_rounds == 2
    assert (p.analyst_model, p.decision_model) == ("claude-analyst", "claude-main")
    assert (p.analyst_effort, p.decision_effort) == ("high", "high")
    assert p.token_scale == 1.0
    assert p.timeout_scale == 1.0


def test_standard_matches_legacy_deepseek() -> None:
    p = resolve_profile(_deepseek(openai_compat_reasoning_model="deepseek-reasoner"))
    assert p.debate_rounds == 2
    # 配了推理模型也不影响标准档
    assert (p.analyst_model, p.decision_model) == ("deepseek-chat", "deepseek-chat")


# ---------------------------------------------------------------- 档位差异


@pytest.mark.parametrize(("std", "quick", "deep"), [(2, 1, 3), (1, 1, 2), (0, 0, 1), (4, 1, 5)])
def test_rounds_are_monotonic(std: int, quick: int, deep: int) -> None:
    s = _deepseek(debate_rounds=std)
    got = [resolve_profile(s, d).debate_rounds for d in ("quick", "standard", "deep")]
    assert got == [quick, std, deep]


def test_llm_calls_formula() -> None:
    # 4 分析师 + 2 × 轮数 + 风控 + 决策
    s = _deepseek()
    assert [resolve_profile(s, d).llm_calls for d in ("quick", "standard", "deep")] == [8, 10, 12]


def test_claude_effort_shifts_relative_to_default() -> None:
    s = _claude(llm_effort="high")
    quick, deep = resolve_profile(s, "quick"), resolve_profile(s, "deep")
    assert (quick.analyst_effort, quick.decision_effort) == ("low", "medium")
    assert (deep.analyst_effort, deep.decision_effort) == ("xhigh", "max")


@pytest.mark.parametrize("default", ["low", "medium", "high", "xhigh", "max"])
def test_claude_effort_clamps_and_stays_ordered(default: str) -> None:
    order = ["low", "medium", "high", "xhigh", "max"]
    s = _claude(llm_effort=default)
    q, std, d = (resolve_profile(s, x) for x in ("quick", "standard", "deep"))
    for role in ("analyst_effort", "decision_effort"):
        ranks = [order.index(getattr(p, role)) for p in (q, std, d)]
        assert ranks == sorted(ranks), f"{role} 在默认 {default} 下高低关系乱了：{ranks}"


def test_deepseek_has_no_effort_at_all() -> None:
    """兼容接口没有 effort。档位绝不能在这里假装调了它。"""
    s = _deepseek()
    for depth in ("quick", "standard", "deep"):
        p = resolve_profile(s, depth)
        assert p.analyst_effort is None
        assert p.decision_effort is None


def test_deepseek_deep_uses_reasoning_model_for_decision_only() -> None:
    p = resolve_profile(_deepseek(openai_compat_reasoning_model="deepseek-reasoner"), "deep")
    assert p.decision_model == "deepseek-reasoner"
    # 分析师 + 辩论一共 4 + 2N 次调用，全换推理模型太慢太贵
    assert p.analyst_model == "deepseek-chat"


def test_deepseek_deep_without_reasoning_model() -> None:
    p = resolve_profile(_deepseek(), "deep")
    assert p.decision_model == "deepseek-chat"
    assert p.debate_rounds == 3


def test_deep_widens_budget_and_timeout() -> None:
    p = resolve_profile(_claude(), "deep")
    assert p.max_tokens(4000) == 8000
    assert p.timeout_scale > 1


# ---------------------------------------------------------------- 手选模型


def test_model_override_applies_to_every_role() -> None:
    s = _deepseek(llm_model_choices=["qwen-plus"])
    p = resolve_profile(s, "deep", "qwen-plus")
    assert (p.analyst_model, p.decision_model) == ("qwen-plus", "qwen-plus")
    assert p.model_override == "qwen-plus"


def test_configured_models_are_always_allowed() -> None:
    s = _claude()
    assert allowed_models(s) == ["claude-main", "claude-analyst"]
    resolve_profile(s, "standard", "claude-analyst")  # 不抛


def test_model_choices_env_is_csv() -> None:
    s = Settings(llm_provider="openai_compat", llm_model_choices="a, b,,a")  # type: ignore[arg-type]
    assert allowed_models(s) == ["deepseek-chat", "a", "b"], "去空、去重、保序"


def test_unknown_model_rejected() -> None:
    with pytest.raises(ProfileError, match="LLM_MODEL_CHOICES"):
        resolve_profile(_deepseek(), "standard", "gpt-whatever")


def test_echo_rejects_any_model() -> None:
    with pytest.raises(ProfileError):
        resolve_profile(Settings(llm_provider="echo"), "standard", "anything")


def test_blank_model_means_auto() -> None:
    assert resolve_profile(_deepseek(), "standard", "  ").model_override is None


def test_unknown_depth_rejected() -> None:
    with pytest.raises(ProfileError):
        resolve_profile(_deepseek(), "ultra")  # type: ignore[arg-type]


# ---------------------------------------------------------------- 对外说明


def test_options_are_honest_for_deepseek() -> None:
    opts = describe_options(_deepseek())
    assert [d["value"] for d in opts["depths"]] == ["quick", "standard", "deep"]
    text = " ".join(" ".join(d["changes"]) for d in opts["depths"])
    assert "推理投入" not in text, "DeepSeek 下不能宣称调了推理投入"
    assert any("OPENAI_COMPAT_REASONING_MODEL" in n for n in opts["notes"])


def test_options_describe_claude_effort() -> None:
    deep = describe_options(_claude())["depths"][2]
    assert any("max" in line for line in deep["changes"])
    assert deep["llm_calls"] == 12


def test_options_list_models_with_roles() -> None:
    models = describe_options(_deepseek(openai_compat_reasoning_model="deepseek-reasoner"))[
        "models"
    ]
    assert models == [
        {"value": "deepseek-chat", "note": "主模型"},
        {"value": "deepseek-reasoner", "note": "推理模型"},
    ]


# ---------------------------------------------------------------- 流水线接线


class _Recorder(EchoLLM):
    """记下每次调用拿到的模型 / 推理投入 / 输出上限。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    async def stream(self, system_blocks, messages, **kw):  # type: ignore[no-untyped-def]
        self.calls.append({"kind": "stream", **kw})
        async for chunk in super().stream(system_blocks, messages, **kw):
            yield chunk

    async def complete(self, system_blocks, messages, **kw):  # type: ignore[no-untyped-def]
        self.calls.append({"kind": "complete", **kw})
        return await super().complete(system_blocks, messages, **kw)


async def test_orchestrator_applies_profile(snapshot) -> None:  # type: ignore[no-untyped-def]
    settings = _claude()
    profile = resolve_profile(settings, "deep")
    llm = _Recorder()
    ctx = AnalysisContext.build(snapshot)

    events = [e async for e in AnalysisOrchestrator(llm, settings, profile).run(ctx)]

    assert events[0].data["config"]["depth"] == "deep"
    assert len(llm.calls) == profile.llm_calls == 12

    streams = [c for c in llm.calls if c["kind"] == "stream"]
    analysts_and_debate, risk = streams[:-1], streams[-1]
    for c in analysts_and_debate:
        assert (c["model"], c["effort"]) == ("claude-analyst", "xhigh")
    assert (risk["model"], risk["effort"]) == ("claude-main", "max")
    assert risk["max_tokens"] == 7000  # 风控 3500 × 2

    decision = llm.calls[-1]
    assert decision["kind"] == "complete"
    assert (decision["model"], decision["effort"], decision["max_tokens"]) == (
        "claude-main",
        "max",
        8000,
    )


async def test_pipeline_plan_follows_profile_rounds(snapshot) -> None:  # type: ignore[no-untyped-def]
    settings = _deepseek()
    ctx = AnalysisContext.build(snapshot)
    for depth, n in (("quick", 2), ("deep", 6)):
        orch = AnalysisOrchestrator(EchoLLM(), settings, resolve_profile(settings, depth))
        events = [e async for e in orch.run(ctx)]
        plan = {s["stage"]: s["agents"] for s in events[0].data["pipeline"]}
        assert len(plan["debate"]) == n
        assert sum(1 for e in events if e.type == "agent_done") == 4 + n + 1


# ---------------------------------------------------------------- 决策重做


class _BadDecision(EchoLLM):
    """指定模型下决策不可用（返回空 / 直接报错），默认模型下正常。"""

    def __init__(self, bad_model: str, mode: str) -> None:
        super().__init__()
        self.bad_model = bad_model
        self.mode = mode
        self.decision_models: list[str | None] = []

    async def complete(self, system_blocks, messages, **kw):  # type: ignore[no-untyped-def]
        self.decision_models.append(kw.get("model"))
        if kw.get("model") == self.bad_model:
            if self.mode == "raise":
                raise LLMError("该模型不支持 json 输出")
            return Completion(text="", usage=Usage(input_tokens=5, output_tokens=7))
        done = await super().complete(system_blocks, messages, **kw)
        done.usage = Usage(input_tokens=10, output_tokens=20)
        return done


def _deep_reasoner() -> tuple[Settings, Any]:
    s = _deepseek(debate_rounds=0, openai_compat_reasoning_model="deepseek-reasoner")
    return s, resolve_profile(s, "deep")


@pytest.mark.parametrize("mode", ["empty", "raise"])
async def test_decision_retries_on_default_model(snapshot, mode: str) -> None:  # type: ignore[no-untyped-def]
    settings, profile = _deep_reasoner()
    llm = _BadDecision("deepseek-reasoner", mode)
    orch = AnalysisOrchestrator(llm, settings, profile)

    decision, usage = await orch._decide(AnalysisContext.build(snapshot), {})

    assert llm.decision_models == ["deepseek-reasoner", "deepseek-chat"]
    assert decision["action"] in ("买入", "加仓", "持有", "减仓", "卖出", "观望")
    assert "deepseek-reasoner" in decision["fallback_note"]
    assert "deepseek-chat" in decision["fallback_note"]
    if mode == "empty":
        assert usage.output_tokens == 27, "重做前那次调用的 token 也要算上"


async def test_default_model_failure_is_not_masked(snapshot) -> None:  # type: ignore[no-untyped-def]
    """默认模型自己失败时不重做 —— 如实走"结论不可用"的兜底。"""
    settings = _deepseek(debate_rounds=0)
    llm = _BadDecision("deepseek-chat", "empty")
    orch = AnalysisOrchestrator(llm, settings, resolve_profile(settings, "standard"))

    decision, _ = await orch._decide(AnalysisContext.build(snapshot), {})

    assert llm.decision_models == ["deepseek-chat"]
    assert decision["action"] == "观望"
    assert "fallback_note" not in decision


async def test_default_model_error_propagates(snapshot) -> None:  # type: ignore[no-untyped-def]
    settings = _deepseek(debate_rounds=0)
    orch = AnalysisOrchestrator(
        _BadDecision("deepseek-chat", "raise"), settings, resolve_profile(settings, "standard")
    )
    with pytest.raises(LLMError):
        await orch._decide(AnalysisContext.build(snapshot), {})
