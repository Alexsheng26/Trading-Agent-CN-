"""OpenAI 兼容接口适配器（DeepSeek / 通义千问 / Kimi）。

这条路和 Claude 那条的关键差异：兼容接口只有 json_object 模式，
**只保证输出是合法 JSON，不保证字段正确**；Claude 那边是服务端按 schema
强制约束的。所以适配器必须把 schema 渲染进提示词，否则模型会漏字段。
"""

from __future__ import annotations

import json

import pytest

from app.agents.prompts import DECISION_SCHEMA
from app.llm.openai_compat_client import _parse_json, _render_schema

# ---------------------------------------------------------------- schema 渲染


def test_rendered_schema_covers_every_field() -> None:
    """一个字段都不能漏 —— 漏了模型就不会输出它。"""
    rendered = _render_schema(DECISION_SCHEMA)
    missing = [f for f in DECISION_SCHEMA["properties"] if f"`{f}`" not in rendered]
    assert not missing, f"以下字段没出现在渲染结果里：{missing}"


def test_rendered_schema_lists_enum_options() -> None:
    """枚举值要写出来，否则模型会自由发挥（比如输出"梭哈"）。"""
    rendered = _render_schema(DECISION_SCHEMA)
    for option in ("买入", "加仓", "持有", "减仓", "卖出", "观望"):
        assert option in rendered
    for horizon in ("短线", "波段", "中线"):
        assert horizon in rendered


def test_rendered_schema_contains_lowercase_json() -> None:
    """DeepSeek 要求 json_object 模式下提示里必须出现 "json" 字样，
    否则接口直接报错。这一条锁住它。"""
    assert "json" in _render_schema(DECISION_SCHEMA)


def test_rendered_schema_marks_required_fields() -> None:
    rendered = _render_schema(DECISION_SCHEMA)
    assert rendered.count("**必填**") == len(DECISION_SCHEMA["required"])


def test_render_handles_schema_without_properties() -> None:
    """空 schema 不该炸。"""
    assert "json" in _render_schema({"type": "object"})


# ---------------------------------------------------------------- 请求构造


def _client():  # type: ignore[no-untyped-def]
    """构造一个不发请求的客户端实例，只用来测 payload 组装。"""
    from app.llm.openai_compat_client import OpenAICompatLLM

    return OpenAICompatLLM(api_key="not-a-real-key", base_url="https://example.invalid/v1",
                           model="deepseek-chat")


def test_payload_injects_schema_only_in_json_mode() -> None:
    from app.llm.base import Message

    client = _client()
    blocks = ["行情简报正文", "你是交易决策员"]
    msgs = [Message(role="user", content="请决策")]

    plain = client._payload(blocks, msgs, None, 4000, None)  # noqa: SLF001
    assert "输出格式" not in plain["messages"][0]["content"]
    assert "response_format" not in plain

    structured = client._payload(blocks, msgs, None, 4000, DECISION_SCHEMA)  # noqa: SLF001
    assert "输出格式" in structured["messages"][0]["content"]
    assert structured["response_format"] == {"type": "json_object"}
    # 原有的系统提示不能被覆盖掉
    assert "行情简报正文" in structured["messages"][0]["content"]
    assert "你是交易决策员" in structured["messages"][0]["content"]


def test_payload_merges_system_blocks_in_order() -> None:
    """兼容接口没有分段 system，多段要按顺序拼成一段。"""
    from app.llm.base import Message

    payload = _client()._payload(  # noqa: SLF001
        ["第一段", "第二段"], [Message(role="user", content="hi")], None, 100, None
    )
    content = payload["messages"][0]["content"]
    assert content.index("第一段") < content.index("第二段")
    assert payload["messages"][1] == {"role": "user", "content": "hi"}


def test_payload_respects_model_override() -> None:
    from app.llm.base import Message

    payload = _client()._payload(  # noqa: SLF001
        ["x"], [Message(role="user", content="hi")], "qwen-plus", 100, None
    )
    assert payload["model"] == "qwen-plus"


# ---------------------------------------------------------------- JSON 解析


def test_parse_json_strips_markdown_fence() -> None:
    """兼容接口常把 JSON 包在 ```json 围栏里，即使要求了纯 JSON。"""
    fenced = '```json\n{"action": "观望", "conviction": 30}\n```'
    assert _parse_json(fenced) == {"action": "观望", "conviction": 30}


def test_parse_json_handles_plain_object() -> None:
    assert _parse_json('{"a": 1}') == {"a": 1}


@pytest.mark.parametrize("bad", ["不是 JSON", "", "[1,2,3]", '{"broken": '])
def test_parse_json_returns_none_on_garbage(bad: str) -> None:
    """解析失败要返回 None，让上层回落到保守决策，而不是抛异常。"""
    assert _parse_json(bad) is None


def test_parse_json_roundtrip_with_real_schema_shape() -> None:
    payload = {k: "x" for k in DECISION_SCHEMA["properties"]}
    payload["conviction"] = 50
    payload["position_pct"] = 5.0
    payload["key_reasons"] = ["a"]
    payload["key_risks"] = ["b"]
    text = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    assert _parse_json(text) == payload
