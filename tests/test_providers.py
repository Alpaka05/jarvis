import json

from core.llm.anthropic_provider import AnthropicProvider
from core.llm.openai_provider import OpenAICompatProvider

HISTORY = [
    {"role": "user", "content": "Licht an"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "t1", "name": "homeassistant", "arguments": {"action": "call_service", "entity_id": "light.bad", "service": "turn_on"}}],
    },
    {"role": "tool", "tool_call_id": "t1", "name": "homeassistant", "content": "ok", "is_error": False},
    {"role": "assistant", "content": "Erledigt.", "tool_calls": []},
    {"role": "user", "content": "danke"},
]

TOOLS = [{"name": "homeassistant", "description": "HA", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}}}]


def test_anthropic_message_conversion():
    msgs = AnthropicProvider.convert_messages(HISTORY)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    tool_use = msgs[1]["content"][0]
    assert tool_use["type"] == "tool_use" and tool_use["id"] == "t1" and tool_use["input"]["entity_id"] == "light.bad"
    tool_result = msgs[2]["content"][0]
    assert tool_result == {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
    assert msgs[3]["content"] == [{"type": "text", "text": "Erledigt."}]


def test_anthropic_raw_blocks_are_replayed_unchanged():
    raw_blocks = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "Hi"}]
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hi", "tool_calls": [], "_raw": {"provider": "anthropic", "content": raw_blocks}},
    ]
    msgs = AnthropicProvider.convert_messages(history)
    assert msgs[1]["content"] is raw_blocks


def test_anthropic_error_flag_and_tools():
    history = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "t", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "a", "name": "t", "content": "kaputt", "is_error": True},
    ]
    msgs = AnthropicProvider.convert_messages(history)
    assert msgs[2]["content"][0]["is_error"] is True
    tools = AnthropicProvider.convert_tools(TOOLS)
    assert tools[0]["input_schema"]["type"] == "object"


def test_openai_message_conversion():
    msgs = OpenAICompatProvider.convert_messages("SYS", HISTORY)
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assistant = msgs[2]
    assert assistant["role"] == "assistant" and assistant["content"] is None
    fn = assistant["tool_calls"][0]["function"]
    assert fn["name"] == "homeassistant"
    assert json.loads(fn["arguments"])["service"] == "turn_on"
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "ok"}
    tools = OpenAICompatProvider.convert_tools(TOOLS)
    assert tools[0]["type"] == "function" and tools[0]["function"]["name"] == "homeassistant"
