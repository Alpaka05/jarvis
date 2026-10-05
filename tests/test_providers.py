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


# ── Gemini-Rotation und Ollama-Hinweis ───────────────────────────────────────

import pytest  # noqa: E402

from core.llm.base import LLMError  # noqa: E402


def _gemini(monkeypatch, outcomes):
    """Gemini-Provider, dessen Modelle der Reihe nach die angegebenen Fehler/Antworten liefern."""
    pytest.importorskip("google.genai")
    from core.llm.gemini_provider import GeminiProvider

    provider = GeminiProvider("key", model="m1", fallback_models=["m2", "m3"])
    tried = []

    def generate_content(model, contents, config):
        tried.append(model)
        outcome = outcomes[model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(provider.client.models, "generate_content", generate_content)
    return provider, tried


def test_gemini_skips_retired_model_and_uses_the_next(monkeypatch):
    from google.genai import types

    ok = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="hallo")]))]
    )
    provider, tried = _gemini(
        monkeypatch,
        {
            "m1": RuntimeError("429 RESOURCE_EXHAUSTED retry in 30s"),
            "m2": RuntimeError("404 NOT_FOUND. This model is no longer available to new users."),
            "m3": ok,
        },
    )
    assert provider.chat("sys", [{"role": "user", "content": "hi"}], []).text == "hallo"
    assert tried == ["m1", "m2", "m3"]
    # m2 bleibt für die Sitzung draußen
    tried.clear()
    provider._cooldown_until.pop("m1")
    provider.chat("sys", [{"role": "user", "content": "hi"}], [])
    assert "m2" not in tried


def test_gemini_reports_every_model_when_none_works(monkeypatch):
    provider, _ = _gemini(
        monkeypatch,
        {
            "m1": RuntimeError("429 RESOURCE_EXHAUSTED"),
            "m2": RuntimeError("503 UNAVAILABLE high demand"),
            "m3": RuntimeError("404 NOT_FOUND"),
        },
    )
    with pytest.raises(LLMError) as exc:
        provider.chat("sys", [{"role": "user", "content": "hi"}], [])
    message = str(exc.value)
    assert "m1" in message and "m2" in message and "m3" in message and "gibt es nicht" in message


def test_ollama_missing_model_lists_installed_models(monkeypatch):
    import requests

    from core.llm.openai_provider import make_ollama_provider

    class Tags:
        def json(self):
            return {"models": [{"name": "gemma4:26b"}]}

    monkeypatch.setattr(requests, "get", lambda url, timeout: Tags() if url.endswith("/api/tags") else None)
    provider = make_ollama_provider("http://localhost:11434", "llama3.1:8b")
    hint = provider._installed_models_hint()
    assert "gemma4:26b" in hint and "OLLAMA_MODEL" in hint
