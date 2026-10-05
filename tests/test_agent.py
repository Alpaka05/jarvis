from typing import Any, Dict, List

import pytest

from core.agent import JarvisAgent
from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall
from tools.base import BaseTool, ToolResult


class EchoTool(BaseTool):
    name = "echo"
    description = "Gibt den Text zurück."
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def execute(self, text: str = "", **kwargs) -> ToolResult:
        self.calls.append({"text": text})
        return ToolResult.ok(f"echo:{text}")


class DangerousTool(BaseTool):
    name = "danger"
    description = "Braucht Bestätigung."
    parameters = {"type": "object", "properties": {}}

    def __init__(self):
        self.executed = False

    def confirmation_prompt(self, **kwargs):
        return "Wirklich?"

    def execute(self, **kwargs) -> ToolResult:
        self.executed = True
        return ToolResult.ok("done")


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self, responses: List[LLMResponse]):
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def chat(self, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        if not self.responses:
            raise LLMError("keine Antworten mehr")
        return self.responses.pop(0)


class FailingProvider(LLMProvider):
    name = "failing"
    model = "x"

    def chat(self, system, messages, tools):
        raise LLMError("offline")


def test_tool_loop_executes_tool_and_feeds_result_back():
    echo = EchoTool()
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hallo"})]),
            LLMResponse(text="Fertig: hallo"),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[echo])
    answer = agent.process_query("sag hallo")

    assert answer == "Fertig: hallo"
    assert echo.calls == [{"text": "hallo"}]
    # zweiter Aufruf enthält das Tool-Ergebnis
    second_call_msgs = provider.calls[1]["messages"]
    tool_msgs = [m for m in second_call_msgs if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"] == "echo:hallo"
    assert tool_msgs[0]["tool_call_id"] == "c1"
    # Schemas wurden übergeben
    assert provider.calls[0]["tools"][0]["name"] == "echo"
    # Verlauf: user, assistant, tool, assistant
    assert [m["role"] for m in agent.history] == ["user", "assistant", "tool", "assistant"]


def test_unknown_tool_returns_error_result():
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="nope", arguments={})]),
            LLMResponse(text="ok"),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[EchoTool()])
    agent.process_query("x")
    tool_msg = [m for m in agent.history if m["role"] == "tool"][0]
    assert tool_msg["is_error"] is True
    assert "Unbekanntes Tool" in tool_msg["content"]


def test_confirmation_decline_blocks_execution():
    danger = DangerousTool()
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="danger", arguments={})]),
            LLMResponse(text="abgebrochen"),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[danger], confirm=lambda prompt: False)
    agent.process_query("mach was gefährliches")
    assert danger.executed is False
    tool_msg = [m for m in agent.history if m["role"] == "tool"][0]
    assert "abgelehnt" in tool_msg["content"]


def test_confirmation_accept_runs_tool():
    danger = DangerousTool()
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="danger", arguments={})]),
            LLMResponse(text="ok"),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[danger], confirm=lambda prompt: True)
    agent.process_query("los")
    assert danger.executed is True


def test_fallback_provider_used_when_primary_fails():
    fallback = FakeProvider([LLMResponse(text="vom fallback")])
    notices = []
    agent = JarvisAgent(provider=FailingProvider(), fallback=fallback, tools=[], on_notice=notices.append)
    assert agent.process_query("hi") == "vom fallback"
    assert notices and "Wechsle" in notices[0]


def test_llm_error_without_fallback_rolls_back_history():
    agent = JarvisAgent(provider=FailingProvider(), tools=[])
    answer = agent.process_query("hi")
    assert "nicht erreichbar" in answer
    assert agent.history == []


def test_max_steps_forces_summary():
    # Provider ruft endlos Tools auf -> nach MAX_STEPS wird eine Zusammenfassung ohne Tools angefordert
    responses = [
        LLMResponse(text="", tool_calls=[ToolCall(id=f"c{i}", name="echo", arguments={"text": str(i)})])
        for i in range(JarvisAgent.MAX_STEPS)
    ] + [LLMResponse(text="Zusammenfassung")]
    provider = FakeProvider(responses)
    agent = JarvisAgent(provider=provider, tools=[EchoTool()])
    assert agent.process_query("loop") == "Zusammenfassung"
    assert provider.calls[-1]["tools"] == []


def test_history_trim_keeps_user_first():
    provider = FakeProvider([LLMResponse(text=f"a{i}") for i in range(40)])
    agent = JarvisAgent(provider=provider, tools=[])
    agent.MAX_HISTORY = 10
    for i in range(40):
        agent.process_query(f"q{i}")
    assert len(agent.history) <= 10
    assert agent.history[0]["role"] == "user"


def test_no_provider_gives_setup_hint():
    agent = JarvisAgent(provider=None, fallback=None, tools=[])
    agent.provider = None
    assert "API-Key" in agent.process_query("hallo")


class BrokenPromptTool(DangerousTool):
    name = "broken"

    def confirmation_prompt(self, **kwargs):
        raise ValueError("kaputt")


def test_confirmation_prompt_error_blocks_execution():
    broken = BrokenPromptTool()
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="broken", arguments={})]),
            LLMResponse(text="ok"),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[broken], confirm=lambda prompt: True)
    agent.process_query("los")
    assert broken.executed is False


def test_confirmation_needed_without_confirm_callback_blocks_execution():
    danger = DangerousTool()
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="danger", arguments={})]),
            LLMResponse(text="ok"),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[danger])
    agent.process_query("los")
    assert danger.executed is False


def test_unexpected_error_rolls_back_whole_turn():
    def boom(call, result):
        raise RuntimeError("Anzeige kaputt")

    provider = FakeProvider(
        [
            LLMResponse(text="erste Antwort"),
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})]),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[EchoTool()])
    agent.process_query("eins")
    before = list(agent.history)
    agent.on_tool_result = boom
    answer = agent.process_query("zwei")
    assert "interner Fehler" in answer
    assert agent.history == before


def test_failed_summary_call_rolls_back_to_user_question():
    responses = [
        LLMResponse(text="", tool_calls=[ToolCall(id=f"c{i}", name="echo", arguments={"text": str(i)})])
        for i in range(JarvisAgent.MAX_STEPS)
    ]  # danach keine Antwort mehr -> Zusammenfassung scheitert mit LLMError
    agent = JarvisAgent(provider=FakeProvider(responses), tools=[EchoTool()])
    assert "nicht erreichbar" in agent.process_query("loop")
    assert agent.history == []
