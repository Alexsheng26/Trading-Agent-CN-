"""离线桩模型。

`LLM_PROVIDER=echo` 时启用：不联网、不花钱、输出确定，
用来跑单测和"第一次 clone 下来就能看到完整链路跑通"。

它按智能体角色返回模板化的中文分析，并且**真的读了**传进来的行情数据
（价格、指标、形态标签都会出现在输出里），所以能验证数据是否正确流到了提示词层。

⚠️ 输出没有任何分析价值，只是占位。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.llm.base import Completion, Effort, Message, StreamChunk, Usage

_ROLE_TEMPLATES: dict[str, str] = {
    "基本面": (
        "## 基本面结论（离线桩）\n\n"
        "从提取到的财务数据看，营收与利润的同比变化已列在上下文中。"
        "估值方面 PE/PB 处于给定区间，需结合行业中枢判断。\n\n"
        "- **支撑项**：现金流与净利率具备可比性\n"
        "- **风险项**：单期数据无法判断趋势，需连续跟踪\n\n"
        "倾向：中性\n\n---\n倾向：中性 | 置信度：45"
    ),
    "技术面": (
        "## 技术面结论（离线桩）\n\n"
        "依据上下文中的均线、MACD、RSI、KDJ 读数与形态标签作出判断。\n\n"
        "- **支撑位**：参考 60 日区间下沿与布林下轨\n"
        "- **压力位**：参考 60 日高点\n\n"
        "倾向：中性\n\n---\n倾向：中性 | 置信度：50"
    ),
    "消息面": (
        "## 消息面与舆情结论（离线桩）\n\n"
        "已读取上下文中的新闻标题列表，未发现足以改变基本面判断的重大事项。\n\n"
        "---\n倾向：中性 | 置信度：40"
    ),
    "资金面": (
        "## 资金面结论（离线桩）\n\n"
        "根据主力净流入序列，近期资金进出方向已在上下文中给出。\n\n"
        "---\n倾向：中性 | 置信度：42"
    ),
    "多头": (
        "## 多头论点（离线桩）\n\n"
        "1. 技术形态未破坏关键均线支撑\n"
        "2. 资金面未出现持续大额流出\n"
        "3. 估值未进入历史极端高位\n\n"
        "结论：可小仓位参与，严格止损。\n\n---\n倾向：看多 | 置信度：55"
    ),
    "空头": (
        "## 空头论点（离线桩）\n\n"
        "1. 单只个股的波动率显著高于指数，回撤风险不可忽视\n"
        "2. 业绩增速的可持续性缺乏验证\n"
        "3. 大盘环境不明朗时个股难以独立走强\n\n"
        "结论：建议观望，等待右侧信号。\n\n---\n倾向：看空 | 置信度：58"
    ),
    "风控": (
        "## 风险评估（离线桩）\n\n"
        "- **波动风险**：依 ATR 占比设定止损距离\n"
        "- **流动性风险**：关注成交额与换手率\n"
        "- **规则风险**：注意所属板块的涨跌幅限制与 T+1 交易制度\n\n"
        "建议单票仓位上限：10%\n\n---\n倾向：中性 | 置信度：50"
    ),
}

_DEFAULT_TEMPLATE = (
    "## 分析结论（离线桩）\n\n已读取上下文数据，此处为占位输出。\n\n---\n倾向：中性 | 置信度：50"
)


class EchoLLM:
    """确定性离线桩。"""

    name = "echo"

    def __init__(self, delay: float = 0.0) -> None:
        # delay > 0 可以模拟真实模型的耗时，方便调前端的流式动效
        self._delay = delay

    def _render(self, system_blocks: list[str], messages: list[Message]) -> str:
        # 只看角色提示词的第一行（"你是空头研究员……"）。
        # 不能拿全文去匹配：空头的提示词里也写着"回应多头的论点"，
        # 全文匹配会把空头识别成多头。
        role_prompt = "\n".join(system_blocks[1:]) if len(system_blocks) > 1 else ""
        role_hint = next(
            (ln for ln in role_prompt.splitlines() if ln.strip()), ""
        )
        body = _DEFAULT_TEMPLATE
        for keyword, template in _ROLE_TEMPLATES.items():
            if keyword in role_hint:
                body = template
                break

        context = "\n".join(system_blocks)
        facts = _extract_facts(context)
        if facts:
            body += "\n\n**本次读到的关键数据**（用于验证数据链路）：\n" + "\n".join(
                f"- {f}" for f in facts
            )
        return body

    async def complete(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        if self._delay:
            await asyncio.sleep(self._delay)
        text = self._render(system_blocks, messages)
        data: dict[str, Any] | None = None
        if json_schema is not None:
            data = _stub_from_schema(json_schema, system_blocks)
            text = json.dumps(data, ensure_ascii=False, indent=2)
        return Completion(
            text=text,
            model="echo",
            usage=Usage(
                input_tokens=len(" ".join(system_blocks)) // 4,
                output_tokens=len(text) // 4,
            ),
            data=data,
        )

    async def stream(
        self,
        system_blocks: list[str],
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 8000,
        effort: Effort | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(thinking="（离线桩：正在核对上下文中的行情与指标……）")
        text = self._render(system_blocks, messages)
        # 按句子切片吐出，前端的打字机效果就能看出来
        for piece in re.findall(r"[^\n]*\n?", text):
            if not piece:
                continue
            if self._delay:
                await asyncio.sleep(self._delay)
            yield StreamChunk(text=piece)


def _extract_facts(context: str) -> list[str]:
    """从提示词上下文里抠几个关键数字，证明数据真的传进来了。"""
    facts: list[str] = []
    patterns = (
        (r"最新价[:：]\s*([\d.]+)", "最新价 {}"),
        (r"涨跌幅[:：]\s*(-?[\d.]+)%", "涨跌幅 {}%"),
        (r"MA20[:：]\s*([\d.]+)", "MA20 {}"),
        (r"RSI14[:：]\s*([\d.]+)", "RSI14 {}"),
        (r"形态[:：]\s*(.+)", "形态 {}"),
    )
    for pattern, label in patterns:
        m = re.search(pattern, context)
        if m:
            facts.append(label.format(m.group(1).strip()[:80]))
    return facts


def _stub_from_schema(schema: dict[str, Any], system_blocks: list[str]) -> dict[str, Any]:
    """按 json_schema 造一个字段齐全、类型正确的桩对象。"""
    props: dict[str, Any] = schema.get("properties", {})
    out: dict[str, Any] = {}
    for key, spec in props.items():
        out[key] = _stub_value(key, spec)
    return out


def _stub_value(key: str, spec: dict[str, Any]) -> Any:
    if "enum" in spec and spec["enum"]:
        # 决策类枚举优先挑"观望"这类中性选项，避免桩数据看起来像真建议
        for neutral in ("观望", "持有", "中性", "neutral", "hold"):
            if neutral in spec["enum"]:
                return neutral
        return spec["enum"][0]

    match spec.get("type"):
        case "string":
            return f"（离线桩占位：{key}）"
        case "integer":
            return 0
        case "number":
            return 0.0
        case "boolean":
            return False
        case "array":
            item = spec.get("items", {"type": "string"})
            return [_stub_value(key, item)]
        case "object":
            return {k: _stub_value(k, v) for k, v in spec.get("properties", {}).items()}
        case _:
            return None
