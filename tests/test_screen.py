"""Screen-Tool: Bildschirm unter der Maus, Berechtigung, Verkleinerung, Bild an die Provider."""
import io
import sys
from types import SimpleNamespace

import pytest

import core.screen as screen
from core.agent import JarvisAgent
from core.llm.anthropic_provider import AnthropicProvider
from core.llm.base import LLMResponse, ToolCall, Usage
from core.llm.openai_provider import OpenAICompatProvider
from tools.base import Risk
from tools.screen_tool import ScreenTool, VISION_SYSTEM

from tests.test_agent import FakeProvider

MONITORS = [
    {"left": 0, "top": 0, "width": 4480, "height": 1440},  # 0 = alle zusammen
    {"left": 0, "top": 0, "width": 1920, "height": 1080},
    {"left": 1920, "top": -200, "width": 2560, "height": 1440},
]


def test_pick_monitor_follows_the_mouse():
    assert screen.pick_monitor(MONITORS, (100, 100)) == 1
    assert screen.pick_monitor(MONITORS, (2500, -100)) == 2
    assert screen.pick_monitor(MONITORS, (99999, 0)) == 1  # außerhalb → Hauptbildschirm
    assert screen.pick_monitor(MONITORS, None) == 1


def test_encode_shrinks_to_max_side_as_jpeg():
    from PIL import Image

    data, width, height = screen.encode(b"\x80" * (3000 * 2000 * 3), (3000, 2000), max_side=1568)
    assert (width, height) == (1568, 1045)
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG" and image.size == (1568, 1045)


def test_missing_permission_explains_and_asks(monkeypatch):
    asked = []
    monkeypatch.setattr(screen, "has_permission", lambda: False)
    monkeypatch.setattr(screen, "request_permission", lambda: asked.append(True))
    with pytest.raises(screen.ScreenError) as exc:
        screen.capture()
    assert "Systemaudioaufnahme" in str(exc.value) and asked


def _fake_mss(monkeypatch, grabbed):
    class Sct:
        monitors = MONITORS

        def grab(self, monitor):
            grabbed.append(monitor)
            return SimpleNamespace(rgb=b"\x00" * (40 * 20 * 3), size=(40, 20))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=Sct))
    monkeypatch.setattr(screen, "has_permission", lambda: True)


def test_capture_grabs_the_monitor_under_the_mouse(monkeypatch):
    grabbed = []
    _fake_mss(monkeypatch, grabbed)
    monkeypatch.setattr(screen, "cursor_position", lambda: (3000.0, 100.0))
    shot = screen.capture()
    assert grabbed == [MONITORS[2]]
    assert (shot.monitor, shot.monitors, shot.mime) == (2, 2, "image/jpeg") and shot.data[:2] == b"\xff\xd8"


# ── Tool ─────────────────────────────────────────────────────────────────────


def test_screen_tool_describes_and_marks_content_untrusted(monkeypatch):
    grabbed, asked = [], []
    _fake_mss(monkeypatch, grabbed)
    monkeypatch.setattr(screen, "cursor_position", lambda: None)
    tool = ScreenTool(lambda image, mime, prompt: asked.append((mime, prompt)) or "Ein Terminal mit ModuleNotFoundError.")
    policy = tool.policy(question="x")
    assert policy.risk is Risk.SAFE and policy.untrusted_output
    result = tool.execute(question="Was bedeutet der Fehler?")
    assert result.success and "ModuleNotFoundError" in result.output and "Bildschirm 1 von 2" in result.output
    assert asked == [("image/jpeg", "Frage des Nutzers: Was bedeutet der Fehler?")]


def test_screen_tool_without_vision_or_permission_fails_cleanly(monkeypatch):
    assert not ScreenTool().execute(question="x").success
    monkeypatch.setattr(screen, "has_permission", lambda: False)
    monkeypatch.setattr(screen, "request_permission", lambda: None)
    result = ScreenTool(lambda *a: "egal").execute(question="x")
    assert not result.success and "Systemaudioaufnahme" in result.output


def test_agent_sends_screenshot_in_separate_call_and_keeps_only_text(monkeypatch):
    monkeypatch.setattr(
        screen, "capture", lambda: screen.Screenshot(b"\xff\xd8JPEG", "image/jpeg", 10, 10, 1, 1)
    )
    provider = FakeProvider([
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="screen", arguments={"question": "was siehst du"})]),
        LLMResponse(text="Ein Browser mit der Tagesschau.", usage=Usage(calls=1, input_tokens=1500)),  # Bild-Aufruf
        LLMResponse(text="Du schaust gerade Nachrichten."),
    ])
    agent = JarvisAgent(provider=provider, tools=[ScreenTool()])
    assert agent.process_query("Was siehst du?") == "Du schaust gerade Nachrichten."

    vision_call = provider.calls[1]
    assert vision_call["system"] == VISION_SYSTEM and vision_call["tools"] == []
    parts = vision_call["messages"][0]["content"]
    assert parts[0] == {"type": "image", "mime": "image/jpeg", "data": b"\xff\xd8JPEG"}
    tool_msg = [m for m in agent.history if m["role"] == "tool"][0]
    assert "Tagesschau" in tool_msg["content"] and tool_msg["untrusted"]
    assert not any(isinstance(m.get("content"), list) for m in agent.history)  # kein Bild im Verlauf
    assert agent.last_usage.input_tokens == 1500  # Bild-Aufruf zählt bei den Kosten mit


# ── Bild-Teile in den Provider-Formaten ──────────────────────────────────────

IMAGE_MSG = {"role": "user", "content": [{"type": "image", "mime": "image/jpeg", "data": b"abc"}, {"type": "text", "text": "Frage"}]}


def test_anthropic_converts_image_parts():
    out = AnthropicProvider.convert_messages([IMAGE_MSG])
    assert out[0]["content"][0] == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "YWJj"}}
    assert out[0]["content"][1] == {"type": "text", "text": "Frage"}


def test_openai_converts_image_parts():
    out = OpenAICompatProvider.convert_messages("sys", [IMAGE_MSG])
    user = [m for m in out if m["role"] == "user"][0]
    assert user["content"][0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,YWJj"}}


def test_gemini_converts_image_parts():
    pytest.importorskip("google.genai")
    from core.llm.gemini_provider import GeminiProvider

    contents = GeminiProvider("key").convert_messages([IMAGE_MSG])
    image, text = contents[0].parts
    assert image.inline_data.mime_type == "image/jpeg" and image.inline_data.data == b"abc"
    assert text.text == "Frage"


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreGraphics nur unter macOS")
def test_cursor_position_works_on_this_mac():
    point = screen.cursor_position()
    assert point is not None and all(isinstance(v, float) for v in point)
