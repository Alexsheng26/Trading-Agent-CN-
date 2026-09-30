"""一次分析用什么档位跑：分析深度 + 可选的模型指定。

**为什么是"档位"而不是直接暴露 effort / 模型名**

各家模型能调的东西不一样：

    Claude       有 effort（推理投入），可以按角色分配不同模型
    DeepSeek 等  没有 effort 这个参数（兼容接口会直接忽略），
                 能换的只有模型本身（chat / 推理模型）

直接给用户一个 effort 滑块，在 DeepSeek 下怎么拖结果都一样 —— 那是个假开关。
所以对外只暴露"快速 / 标准 / 深度"三档，由这里按**当前厂商**翻译成
实际生效的参数。换厂商时用户不需要重新理解一套旋钮。

两家都生效、也是最主要的成本/耗时杠杆的是**辩论轮数**：
一次分析的调用次数 = 4（分析师，并发）+ 2 × 轮数（串行）+ 1（风控）+ 1（决策）。

**标准档必须和引入档位之前的行为完全一致** —— 它直接取 .env 里的
DEBATE_ROUNDS / LLM_EFFORT / 各模型配置。老用户什么都不改，结果不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.agents.roster import ANALYST_CLASSES
from app.core.config import Settings
from app.llm.base import Effort

Depth = Literal["quick", "standard", "deep"]

DEPTHS: tuple[Depth, ...] = ("quick", "standard", "deep")

DEPTH_LABELS: dict[Depth, str] = {"quick": "快速", "standard": "标准", "deep": "深度"}

DEPTH_BLURBS: dict[Depth, str] = {
    "quick": "盘中快速看一眼",
    "standard": "日常使用",
    "deep": "重要决策前，耗时和费用明显更高",
}

#: 推理投入从低到高。档位按"相对 .env 里的 LLM_EFFORT 挪几档"来算，
#: 这样用户改了默认值，三档之间的高低关系依然成立。
_EFFORT_ORDER: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")


class ProfileError(ValueError):
    """档位或模型不合法。API 层翻成 400。"""


@dataclass(frozen=True, slots=True)
class RunProfile:
    """一次分析实际生效的参数。"""

    depth: Depth
    provider: str
    debate_rounds: int
    #: 分析师与辩论双方用的模型
    analyst_model: str | None
    #: 风控与交易决策用的模型
    decision_model: str | None
    #: 只有 Claude 有这个概念，其余厂商恒为 None
    analyst_effort: Effort | None
    decision_effort: Effort | None
    #: 各智能体输出上限的倍数。推理投入调高后思考会变长，
    #: 上限不跟着放宽的话，Claude 的思考和正文共用额度，会被截断
    token_scale: float = 1.0
    #: 整体超时的倍数：深度档轮数多、思考长，沿用标准档的超时会被误杀
    timeout_scale: float = 1.0
    #: 用户手动指定的模型（None 表示按档位自动选）
    model_override: str | None = None
    #: 该厂商的默认模型。决策环节换了模型却没拿到可用结果时，用它重做
    default_model: str | None = None

    @property
    def llm_calls(self) -> int:
        return len(ANALYST_CLASSES) + 2 * self.debate_rounds + 2

    def max_tokens(self, base: int) -> int:
        return int(base * self.token_scale)

    def as_record(self) -> dict[str, Any]:
        """落库 + 推给前端的版本。以后回看报告时，靠它分清"结论不同"是行情变了还是换了档位。"""
        return {
            "depth": self.depth,
            "depth_label": DEPTH_LABELS[self.depth],
            "provider": self.provider,
            "debate_rounds": self.debate_rounds,
            "llm_calls": self.llm_calls,
            "analyst_model": self.analyst_model,
            "decision_model": self.decision_model,
            "analyst_effort": self.analyst_effort,
            "decision_effort": self.decision_effort,
            "model_override": self.model_override,
        }


# ---------------------------------------------------------------- 解析


def resolve_profile(
    settings: Settings, depth: Depth = "standard", model: str | None = None
) -> RunProfile:
    """把"档位 + 可选模型"翻译成这次分析实际生效的参数。"""
    if depth not in DEPTHS:
        raise ProfileError(f"未知的分析深度：{depth}，可选 {' / '.join(DEPTHS)}")

    model = (model or "").strip() or None
    if model is not None:
        allowed = allowed_models(settings)
        if not allowed:
            raise ProfileError("当前模型后端不支持指定模型")
        if model not in allowed:
            raise ProfileError(
                f"模型 {model} 不在可选列表里（可选：{'、'.join(allowed)}）。"
                "要加新模型，在 .env 的 LLM_MODEL_CHOICES 里追加后执行 docker compose up -d"
            )

    std_rounds = max(0, settings.debate_rounds)
    rounds = {"quick": min(1, std_rounds), "standard": std_rounds, "deep": std_rounds + 1}[depth]
    token_scale = 2.0 if depth == "deep" else 1.0
    timeout_scale = 1.5 if depth == "deep" else 1.0

    match settings.llm_provider:
        case "anthropic":
            base: Effort = settings.llm_effort
            shift = {"quick": (-2, -1), "standard": (0, 0), "deep": (1, 2)}[depth]
            return RunProfile(
                depth=depth,
                provider="anthropic",
                debate_rounds=rounds,
                analyst_model=model or settings.analyst_model,
                decision_model=model or settings.anthropic_model,
                analyst_effort=_shift_effort(base, shift[0]),
                decision_effort=_shift_effort(base, shift[1]),
                token_scale=token_scale,
                timeout_scale=timeout_scale,
                model_override=model,
                default_model=settings.anthropic_model,
            )
        case "openai_compat":
            default = settings.openai_compat_model
            reasoning = settings.openai_compat_reasoning_model.strip()
            # 深度档把风控和决策交给推理模型（配置了的话）。
            # 分析师和辩论不换：它们一共 4 + 2N 次调用，全换成推理模型太慢也太贵
            decision = reasoning if depth == "deep" and reasoning else default
            return RunProfile(
                depth=depth,
                provider="openai_compat",
                debate_rounds=rounds,
                analyst_model=model or default,
                decision_model=model or decision,
                analyst_effort=None,
                decision_effort=None,
                token_scale=token_scale,
                timeout_scale=timeout_scale,
                model_override=model,
                default_model=default,
            )
        case _:
            return RunProfile(
                depth=depth,
                provider=settings.llm_provider,
                debate_rounds=rounds,
                analyst_model=None,
                decision_model=None,
                analyst_effort=None,
                decision_effort=None,
                token_scale=token_scale,
                timeout_scale=timeout_scale,
            )


def allowed_models(settings: Settings) -> list[str]:
    """本次可以手选的模型：已配置的几个 + LLM_MODEL_CHOICES。

    只认这个白名单。不然前端随手传个字符串就会被原样发给模型 API ——
    轻则报错，重则落到一个你没打算付费的模型上。
    """
    match settings.llm_provider:
        case "anthropic":
            configured = [settings.anthropic_model, settings.analyst_model]
        case "openai_compat":
            configured = [settings.openai_compat_model, settings.openai_compat_reasoning_model]
        case _:
            return []
    out: list[str] = []
    for name in (*configured, *settings.llm_model_choices):
        name = name.strip()
        if name and name not in out:
            out.append(name)
    return out


def describe_options(settings: Settings) -> dict[str, Any]:
    """给前端画档位选择器用：每档在**当前厂商**下实际会改变什么。"""
    standard = resolve_profile(settings, "standard")
    depths = []
    for depth in DEPTHS:
        profile = resolve_profile(settings, depth)
        depths.append(
            {
                "value": depth,
                "label": DEPTH_LABELS[depth],
                "blurb": DEPTH_BLURBS[depth],
                "debate_rounds": profile.debate_rounds,
                "llm_calls": profile.llm_calls,
                "changes": _describe_changes(profile, standard),
                "config": profile.as_record(),
            }
        )

    notes: list[str] = []
    if settings.llm_provider == "openai_compat":
        notes.append(
            "当前是 OpenAI 兼容接口（如 DeepSeek），没有“推理投入”这个参数，"
            "档位靠辩论轮数和模型区分。"
        )
        if not settings.openai_compat_reasoning_model.strip():
            notes.append(
                "在 .env 里配置 OPENAI_COMPAT_REASONING_MODEL（如 DeepSeek 的推理模型）后，"
                "深度档的风控和决策会改用它。"
            )
    elif settings.llm_provider == "echo":
        notes.append("当前是离线桩，档位只影响流程长短，输出没有分析价值。")

    return {
        "provider": settings.llm_provider,
        "default_depth": "standard",
        "depths": depths,
        "models": _model_options(settings),
        "notes": notes,
    }


# ---------------------------------------------------------------- 内部


def _shift_effort(base: Effort, steps: int) -> Effort:
    idx = _EFFORT_ORDER.index(base) + steps
    return _EFFORT_ORDER[max(0, min(len(_EFFORT_ORDER) - 1, idx))]


def _describe_changes(profile: RunProfile, standard: RunProfile) -> list[str]:
    """和标准档相比，这一档改了什么。标准档自己返回它的基准参数。"""
    if profile.depth == "standard":
        lines = [f"多空辩论 {profile.debate_rounds} 轮"]
        if profile.analyst_effort:
            lines.append(f"推理投入 {profile.analyst_effort}")
        return lines

    lines = []
    if profile.debate_rounds != standard.debate_rounds:
        lines.append(f"多空辩论 {profile.debate_rounds} 轮（标准 {standard.debate_rounds} 轮）")
    if profile.analyst_effort and profile.analyst_effort != standard.analyst_effort:
        lines.append(f"分析师与辩论推理投入 {profile.analyst_effort}")
    if profile.decision_effort and profile.decision_effort != standard.decision_effort:
        lines.append(f"风控与决策推理投入 {profile.decision_effort}")
    if profile.decision_model and profile.decision_model != standard.decision_model:
        lines.append(f"风控与决策改用 {profile.decision_model}")
    if profile.token_scale > standard.token_scale:
        lines.append("输出长度上限加倍")
    return lines or ["与标准档相同"]


def _model_options(settings: Settings) -> list[dict[str, str]]:
    roles: dict[str, str] = {}
    match settings.llm_provider:
        case "anthropic":
            roles[settings.anthropic_model] = "主模型"
            roles.setdefault(settings.analyst_model, "分析师模型")
        case "openai_compat":
            roles[settings.openai_compat_model] = "主模型"
            reasoning = settings.openai_compat_reasoning_model.strip()
            if reasoning:
                roles.setdefault(reasoning, "推理模型")
    return [{"value": m, "note": roles.get(m, "")} for m in allowed_models(settings)]
