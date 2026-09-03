"""Verdrahtungstest des Sprachmodus mit Fake-Mikrofon, Fake-Detektor und Fake-Agent."""
import numpy as np
from rich.console import Console

import core.voice_loop as voice_loop_module
from core.voice_loop import VoiceLoop
from core.wakeword import FRAME_SAMPLES


class FakeStream:
    def __init__(self, blocks):
        self.blocks = list(blocks)

    def read(self, n):
        if self.blocks:
            return self.blocks.pop(0), False
        return np.zeros((n, 1), dtype=np.int16), False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDetector:
    model_name = "fake"
    threshold = 0.5

    def __init__(self, trigger_on_call: int):
        self.calls = 0
        self.trigger_on_call = trigger_on_call
        self.resets = 0

    def triggered(self, frame):
        self.calls += 1
        return self.calls == self.trigger_on_call

    def reset(self):
        self.resets += 1


class FakeListener:
    def __init__(self, recordings):
        self.recordings = list(recordings)

    def record_from_stream(self, stream, **kwargs):
        return self.recordings.pop(0) if self.recordings else None

    def recognize(self, recording):
        return "wie spät ist es"


class FakeVoice:
    def __init__(self):
        self.spoken = []

    def speak(self, text, **kwargs):
        self.spoken.append(text)

    def is_speaking(self):
        return False

    def stop(self):
        pass


class FakeAgent:
    def __init__(self):
        self.queries = []

    def process_query(self, q):
        self.queries.append(q)
        return "Es ist zwölf Uhr."


def test_voice_loop_wake_record_answer_and_stop(monkeypatch):
    frames = [np.zeros((FRAME_SAMPLES, 1), dtype=np.int16) for _ in range(5)]
    stream = FakeStream(frames)
    monkeypatch.setattr(voice_loop_module.sd, "InputStream", lambda **kw: stream)
    monkeypatch.setattr(voice_loop_module, "play_beep", lambda *a, **k: None)
    monkeypatch.setattr(voice_loop_module, "play_chime", lambda *a, **k: None)

    class SilentAck:  # keine Netzwerk-Synthese im Test
        def __init__(self, phrase):
            self.path = None

        def play(self, wait=True):
            pass

    monkeypatch.setattr(voice_loop_module, "AckVoice", SilentAck)

    agent, voice = FakeAgent(), FakeVoice()
    loop = VoiceLoop(
        agent, voice, console=Console(quiet=True),
        listener=FakeListener([np.ones(1600, dtype=np.int16)]),  # eine Aufnahme, dann nichts mehr
        detector=FakeDetector(trigger_on_call=2),
        follow_up_seconds=1.0,
    )

    # Nach der Interaktion Schleife beenden
    original = loop._interaction

    def interaction_then_stop(stream):
        original(stream)
        loop.stop()

    loop._interaction = interaction_then_stop
    loop.run()

    assert agent.queries == ["wie spät ist es"]
    assert voice.spoken == ["Es ist zwölf Uhr."]
    assert loop.detector.resets >= 2  # Start + nach Erkennung
    assert agent.__dict__.get("voice_mode") is False  # nach run() zurückgesetzt


def test_stop_phrases():
    assert VoiceLoop._is_stop_phrase("Stopp.")
    assert VoiceLoop._is_stop_phrase("Danke, das reicht!")
    assert VoiceLoop._is_stop_phrase("jarvis stop")
    assert not VoiceLoop._is_stop_phrase("Stopp die Musik und mach das Licht aus")
    assert not VoiceLoop._is_stop_phrase("Wie spät ist es")
