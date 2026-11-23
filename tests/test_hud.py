"""Oberfläche: Agent-Ereignisse, Quellen, Rückfragen und Befehle – über einen echten WebSocket."""
import json
import threading
import time

import pytest

from core.agent import CANCELLED_TEXT, AgentListener, JarvisAgent
from core.hud import Hud, Session
from core.llm.base import LLMResponse, ToolCall
from core.orb import OrbEvents
from core.sources import SourceRegistry, strip_citations
from tests.test_agent import DangerousTool, EchoTool, FakeProvider
from tools.base import BaseTool, ToolResult
from tools.search_tool import SearchTool

# ── Quellen-Register ─────────────────────────────────────────────────────────


def test_registry_numbers_urls_once_and_finds_citations():
    reg = SourceRegistry()
    assert reg.add("https://www.nasa.gov/artemis/", "Artemis") == 1
    assert reg.add("https://tagesschau.de/raumfahrt", "Raumfahrt", "Kurztext") == 2
    assert reg.add("https://www.nasa.gov/artemis", "anderer Titel") == 1  # gleiche Seite, gleiche Nummer
    assert reg.add("", "ohne URL") is None and reg.add("javascript:alert(1)") is None
    assert reg.get(1)["host"] == "nasa.gov" and reg.get(3) is None

    cited = reg.cited("Verschoben [2]. Laut NASA [1, 2] und [9] sowie [kein Beleg].")
    assert [s["n"] for s in cited] == [2, 1]
    assert reg.knows("https://tagesschau.de/raumfahrt/") and not reg.knows("https://boese.example")
    reg.reset()
    assert len(reg) == 0 and reg.add("https://x.example") == 1


def test_citations_are_not_spoken():
    from core.voice import VoiceEngine

    assert strip_citations("Der Start wurde verschoben [3]. Mehr dazu [1, 2].") == "Der Start wurde verschoben. Mehr dazu."
    assert VoiceEngine.clean_text_for_speech("Es regnet morgen [12].") == "Es regnet morgen."
    assert strip_citations("Liste [a] und Feld[0] bleiben") == "Liste [a] und Feld bleiben"


def test_search_results_carry_registry_numbers(monkeypatch):
    tool = SearchTool()
    reg = SourceRegistry()
    reg.add("https://schon.da/")
    tool.use_sources(reg)
    hits = [
        {"title": "Kurzantwort (Tavily)", "url": "", "snippet": "42"},
        {"title": "A", "url": "https://a.example/x", "snippet": "erstes"},
        {"title": "B", "url": "https://schon.da", "snippet": "zweites"},
    ]
    monkeypatch.setattr(tool, "_ddgs", lambda q, n, news: hits)
    out = tool.search("frage").output
    assert "– Kurzantwort" in out and "[2] A\n   https://a.example/x" in out and "[1] B" in out

    plain = SearchTool()  # ohne Register (direkter Aufruf) bleibt es bei 1., 2., …
    monkeypatch.setattr(plain, "_ddgs", lambda q, n, news: hits[1:])
    assert plain.search("frage").output.splitlines()[1].startswith("1. A")


# ── Agent: Beobachter, Quellen der Runde, Abbruch ────────────────────────────


class Recorder(AgentListener):
    def __init__(self):
        self.events = []

    def turn_started(self, turn):
        self.events.append(("start", turn.id, turn.query))

    def text(self, turn, chunk):
        self.events.append(("text", chunk))

    def tool_started(self, turn, call):
        self.events.append(("tool", call.name))

    def tool_finished(self, turn, call, result):
        self.events.append(("result", result.success))

    def turn_finished(self, turn):
        self.events.append(("end", turn.text, [(s["n"], s["cited"]) for s in turn.sources], turn.cancelled))


class FindTool(BaseTool):
    name = "find"
    description = "Findet zwei Seiten."
    parameters = {"type": "object", "properties": {}}

    def use_sources(self, registry):
        self.registry = registry

    def execute(self, **kwargs) -> ToolResult:
        a = self.registry.add("https://a.example", "A")
        b = self.registry.add("https://b.example", "B")
        return ToolResult.ok(f"[{a}] A\n[{b}] B")


def _agent(responses, tools):
    return JarvisAgent(provider=FakeProvider(responses), tools=tools)


def test_listener_sees_turn_text_tools_and_sources():
    agent = _agent(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="find", arguments={})]),
            LLMResponse(text="Laut B ist es so [2]."),
        ],
        [FindTool()],
    )
    rec = Recorder()
    agent.listeners.append(rec)
    answer = agent.process_query("was stimmt?")

    assert answer == "Laut B ist es so [2]."
    kinds = [e[0] for e in rec.events]
    assert kinds == ["start", "tool", "result", "text", "end"]
    # zitierte Quelle zuerst, die nur gefundene danach
    assert rec.events[-1] == ("end", "Laut B ist es so [2].", [(2, True), (1, False)], False)
    assert "Quellen" in agent.system_prompt()


def test_broken_listener_does_not_break_the_agent():
    class Broken(AgentListener):
        def text(self, turn, chunk):
            raise RuntimeError("kaputt")

    agent = _agent([LLMResponse(text="Hallo.")], [])
    agent.listeners.append(Broken())
    assert agent.process_query("hi") == "Hallo."


def test_cancel_skips_remaining_tools_and_keeps_history_valid():
    echo = EchoTool()

    class CancelOnFirst(EchoTool):
        name = "first"

        def execute(self, **kwargs):
            agent.cancel()
            return ToolResult.ok("erledigt")

    agent = _agent(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="c1", name="first", arguments={}),
                    ToolCall(id="c2", name="echo", arguments={"text": "nicht mehr"}),
                ],
            ),
            LLMResponse(text="darf nicht mehr kommen"),
        ],
        [CancelOnFirst(), echo],
    )
    rec = Recorder()
    agent.listeners.append(rec)
    answer = agent.process_query("mach zwei Dinge")

    assert answer == CANCELLED_TEXT and echo.calls == []
    assert rec.events[-1][3] is True  # cancelled
    roles = [m["role"] for m in agent.history]
    assert roles == ["user", "assistant", "tool", "tool", "assistant"]  # jeder Aufruf hat ein Ergebnis
    assert "abgebrochen" in agent.history[3]["content"].lower()
    assert not agent.busy
    agent.cancel()  # ohne laufende Anfrage wirkungslos
    agent.provider.responses[:] = [LLMResponse(text="Wieder da.")]
    assert agent.process_query("und jetzt?") == "Wieder da."


def test_unavailable_tools_are_not_offered():
    class Hidden(EchoTool):
        name = "hidden"

        def available(self):
            return False

    agent = _agent([LLMResponse(text="ok")], [EchoTool(), Hidden()])
    assert [t["name"] for t in agent.tool_schemas()] == ["echo"]


# ── Oberfläche über WebSocket ────────────────────────────────────────────────


class FakeVoice:
    enabled = True

    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1

    def is_speaking(self):
        return False

    def open_stream(self, listen_for_interrupt=True):
        class Stream:
            def feed(self, text): ...

            def close(self): ...

        return Stream()


@pytest.fixture
def rig():
    pytest.importorskip("websockets")
    bus = OrbEvents(idle_delay=0.05)
    assert bus.start(port=0) is None
    hud = Hud(bus)
    yield bus, hud
    bus.stop()


def _connect(bus, client="hud"):
    from websockets.sync.client import connect

    return connect(f"{bus.address}/?token={bus.token}&client={client}", open_timeout=2)


def _recv(ws, timeout=2.0):
    return json.loads(ws.recv(timeout=timeout))


def _until(ws, kind, timeout=3.0):
    """Liest, bis eine Nachricht dieses Typs kommt; gibt (sie, alle bis dahin) zurück."""
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = _recv(ws, timeout=max(0.05, deadline - time.monotonic()))
        seen.append(msg)
        if msg["type"] == kind:
            return msg, seen
    raise AssertionError(f"kein {kind}: {[m['type'] for m in seen]}")


def _setup(hud, responses, tools, terminal_confirm=None, voice_loop_factory=None):
    agent = _agent(responses, tools)
    voice = FakeVoice()
    hud.attach(agent, voice)
    session = Session(hud, agent, voice, terminal_confirm=terminal_confirm, voice_loop_factory=voice_loop_factory)
    agent.confirm = session.confirm
    session.start()
    return agent, voice, session


def test_hud_gets_hello_and_overlay_gets_only_basics(rig):
    bus, hud = rig
    agent, _, session = _setup(hud, [LLMResponse(text="Hallo zurück.")], [])
    with _connect(bus, client="") as overlay, _connect(bus) as ws:
        assert _recv(overlay) == {"type": "state", "state": "idle"}
        assert _recv(ws)["type"] == "state"
        hello = _recv(ws)
        assert hello["type"] == "hello" and hello["protocol"] == 2 and hello["turns"] == []
        assert hello["status"]["provider"] and hello["status"]["busy"] is False

        ws.send(json.dumps({"type": "input", "text": "  hallo  "}))
        end, seen = _until(ws, "turn.end")
        kinds = [m["type"] for m in seen]
        assert kinds.index("turn.start") < kinds.index("delta") < kinds.index("turn.end")
        assert end["text"] == "Hallo zurück." and end["error"] is False
        assert next(m for m in seen if m["type"] == "turn.start")["query"] == "hallo"

        # der Orb bekommt Zustand und Untertitel, aber nichts von den HUD-Ereignissen
        got = []
        try:
            while True:
                got.append(_recv(overlay, timeout=0.3)["type"])
        except TimeoutError:
            pass
        assert set(got) <= {"state", "transcript"} and "transcript" in got

    # ein später geöffnetes Fenster bekommt den Verlauf nachgeliefert
    with _connect(bus) as ws:
        _recv(ws)
        turns = _recv(ws)["turns"]
        assert [(t["query"], t["text"], t["done"]) for t in turns] == [("hallo", "Hallo zurück.", True)]
    session.close()


def test_overlay_messages_are_ignored_and_garbage_is_harmless(rig):
    bus, hud = rig
    _, _, session = _setup(hud, [LLMResponse(text="nie")], [])
    with _connect(bus, client="") as overlay, _connect(bus) as ws:
        overlay.send(json.dumps({"type": "input", "text": "vom Orb"}))
        ws.send("kein json")
        ws.send(json.dumps(["liste"]))
        ws.send(json.dumps({"type": 7}))
        ws.send(json.dumps({"type": "unbekannt"}))
        time.sleep(0.3)
        assert hud.commands.empty()
    session.close()


def test_confirmation_via_hud_buttons(rig):
    bus, hud = rig
    danger = DangerousTool()
    responses = [
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="danger", arguments={})]),
        LLMResponse(text="Erledigt."),
        LLMResponse(text="", tool_calls=[ToolCall(id="c2", name="danger", arguments={})]),
        LLMResponse(text="Dann nicht."),
    ]
    agent, _, session = _setup(hud, responses, [danger])
    with _connect(bus) as ws:
        _until(ws, "hello")
        ws.send(json.dumps({"type": "input", "text": "mach es"}))
        ask, _ = _until(ws, "confirm.request")
        assert ask["prompt"] == "Wirklich?" and ask["voice"] is False
        ws.send(json.dumps({"type": "confirm.reply", "id": "falsch", "result": True}))  # passt nicht → ignoriert
        time.sleep(0.2)
        assert not danger.executed
        ws.send(json.dumps({"type": "confirm.reply", "id": ask["id"], "result": True}))
        resolved, _ = _until(ws, "confirm.resolved")
        assert resolved == {"type": "confirm.resolved", "id": ask["id"], "result": True, "via": "ui"}
        end, seen = _until(ws, "turn.end")
        assert danger.executed and end["text"] == "Erledigt."
        tools = [m for m in seen if m["type"].startswith("tool.")]
        assert [m["type"] for m in tools] == ["tool.end"] and tools[0]["ok"] is True

        # „Nein“ – nur ein echtes true zählt als Ja
        danger.executed = False
        ws.send(json.dumps({"type": "input", "text": "nochmal"}))
        ask, _ = _until(ws, "confirm.request")
        ws.send(json.dumps({"type": "confirm.reply", "id": ask["id"], "result": "true"}))
        end, _ = _until(ws, "turn.end")
        assert not danger.executed and end["text"] == "Dann nicht."
    session.close()


def test_confirmation_without_window_or_after_disconnect_means_no(rig):
    bus, hud = rig
    _, _, session = _setup(hud, [], [])
    assert hud.confirm("Senden?") is False  # kein Fenster verbunden

    result = {}
    with _connect(bus) as ws:
        _recv(ws)
        worker = threading.Thread(target=lambda: result.update(answer=hud.confirm("Senden?")))
        worker.start()
        _until(ws, "confirm.request")
    worker.join(3)
    assert result == {"answer": False}  # Fenster zu, bevor geantwortet wurde
    session.close()


def test_terminal_requests_keep_asking_in_the_terminal(rig):
    bus, hud = rig
    asked = []
    danger = DangerousTool()
    agent, _, session = _setup(
        hud,
        [LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="danger", arguments={})]), LLMResponse(text="ok")],
        [danger],
        terminal_confirm=lambda prompt: asked.append(prompt) or True,
    )
    with _connect(bus) as ws:
        _until(ws, "hello")
        assert agent.process_query("aus dem Terminal") == "ok"  # dieser Thread ist nicht die Session
        assert asked == ["Wirklich?"] and danger.executed
        _, seen = _until(ws, "turn.end")
        assert "confirm.request" not in [m["type"] for m in seen]
    session.close()


def test_stop_cancels_speech_request_and_pending_confirmation(rig):
    bus, hud = rig
    danger = DangerousTool()
    agent, voice, session = _setup(
        hud,
        [LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="danger", arguments={})]), LLMResponse(text="nie")],
        [danger],
    )
    with _connect(bus) as ws:
        _until(ws, "hello")
        ws.send(json.dumps({"type": "input", "text": "los"}))
        _until(ws, "confirm.request")
        ws.send(json.dumps({"type": "stop"}))
        end, _ = _until(ws, "turn.end")
        assert end["cancelled"] is True and end["text"] == CANCELLED_TEXT
        assert not danger.executed and voice.stopped >= 2
    session.close()


def test_reset_clears_conversation_cards_and_sources(rig):
    bus, hud = rig
    agent, _, session = _setup(hud, [LLMResponse(text="Hallo.")], [])
    agent.sources.add("https://a.example")
    with _connect(bus) as ws:
        _until(ws, "hello")
        ws.send(json.dumps({"type": "input", "text": "hi"}))
        _until(ws, "turn.end")
        hud.show_card({"kind": "text", "title": "T", "text": "x"})
        ws.send(json.dumps({"type": "input", "text": "reset"}))
        _until(ws, "reset")
    assert agent.history == [] and len(agent.sources) == 0 and hud.snapshot()[0]["turns"] == []
    assert hud.snapshot()[0]["cards"] == []
    session.close()


def test_only_displayed_links_can_be_opened(rig, monkeypatch):
    bus, hud = rig
    opened = []
    monkeypatch.setattr("core.hud.platform_utils.open_url", lambda url: opened.append(url) or True)
    agent, _, session = _setup(hud, [LLMResponse(text="Siehe https://gezeigt.example/seite.")], [])
    agent.sources.add("https://quelle.example/artikel", "Quelle")
    assert agent.process_query("link?")

    assert hud.open_url("https://gezeigt.example/seite") is True  # stand in der Antwort
    assert hud.open_url("https://quelle.example/artikel") is True  # nummerierte Quelle
    assert hud.open_url("https://boese.example/?daten=geheim") is False
    assert hud.open_url("file:///etc/passwd") is False
    assert opened == ["https://gezeigt.example/seite", "https://quelle.example/artikel"]
    session.close()


def test_cards_are_replaced_capped_and_dismissable(rig):
    bus, hud = rig
    _setup(hud, [], [])[2].close()
    with _connect(bus) as ws:
        _until(ws, "hello")
        first = hud.show_card({"kind": "text", "title": "A", "text": "eins"})
        assert _until(ws, "card")[0]["card"]["id"] == first
        assert hud.show_card({"kind": "text", "title": "A2", "text": "zwei"}, first) == first  # ersetzt
        for i in range(14):
            hud.show_card({"kind": "text", "title": str(i), "text": "x"})
        cards = hud.snapshot()[0]["cards"]
        assert len(cards) == 12 and first not in [c["id"] for c in cards]
        ws.send(json.dumps({"type": "card.dismiss", "id": cards[-1]["id"]}))
        time.sleep(0.3)
        assert len(hud.snapshot()[0]["cards"]) == 11
        ws.send(json.dumps({"type": "cards.clear"}))
        time.sleep(0.3)
        assert hud.snapshot()[0]["cards"] == []


def test_voice_toggle_starts_and_stops_the_loop(rig):
    bus, hud = rig
    started = threading.Event()

    class FakeLoop:
        def __init__(self):
            self._stop = threading.Event()

        def run(self):
            hud.set_voice_loop(self)
            started.set()
            self._stop.wait(5)
            hud.set_voice_loop(None)

        def stop(self):
            self._stop.set()

    _, _, session = _setup(hud, [], [], voice_loop_factory=FakeLoop)
    with _connect(bus) as ws:
        _until(ws, "hello")
        ws.send(json.dumps({"type": "voice.toggle"}))
        assert started.wait(3)
        deadline = time.monotonic() + 3
        while not _until(ws, "status")[0]["voice_mode"]:
            assert time.monotonic() < deadline
        ws.send(json.dumps({"type": "voice.toggle"}))
        while _until(ws, "status")[0]["voice_mode"]:
            assert time.monotonic() < deadline + 3
    session.close()


def test_tts_toggle_and_window_commands(rig):
    bus, hud = rig
    agent, voice, session = _setup(hud, [], [])
    hud.handle({"type": "tts", "enabled": False})
    assert voice.enabled is False and voice.stopped == 1
    hud.handle({"type": "tts", "enabled": True})
    assert voice.enabled is True
    session.close()
