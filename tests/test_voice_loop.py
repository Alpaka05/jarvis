"""Verdrahtungstest des Sprachmodus mit Fake-Mikrofon, Fake-Detektor und Fake-Agent."""
import io

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

    def open_stream(self, **kwargs):
        voice = self

        class Stream:
            def __init__(self):
                self.parts = []

            def feed(self, text):
                self.parts.append(text)

            def close(self):
                if self.parts:
                    voice.spoken.append("".join(self.parts))

        return Stream()

    def is_speaking(self):
        return False

    def stop(self):
        pass


class OrbRecorder:
    def __init__(self):
        self.events = []

    def state(self, name):
        self.events.append(name)

    def transcript(self, role, text):
        self.events.append(f"{role}: {text}")

    def error(self, message=""):
        self.events.append(f"error: {message}")


class FakeAgent:
    def __init__(self):
        self.queries = []

    def process_query(self, q, on_text=None):
        self.queries.append(q)
        if on_text:
            on_text("Es ist ")
            on_text("zwölf Uhr.")
        return "Es ist zwölf Uhr."


def test_voice_loop_wake_record_answer_and_stop(monkeypatch):
    frames = [np.zeros((FRAME_SAMPLES, 1), dtype=np.int16) for _ in range(5)]
    stream = FakeStream(frames)
    monkeypatch.setattr(voice_loop_module, "MicStream", lambda **kw: stream)
    monkeypatch.setattr(voice_loop_module, "play_beep", lambda *a, **k: None)
    monkeypatch.setattr(voice_loop_module, "play_chime", lambda *a, **k: None)

    class SilentAck:  # keine Netzwerk-Synthese im Test
        def __init__(self, phrase):
            self.path = None

        def play(self, wait=True):
            pass

    monkeypatch.setattr(voice_loop_module, "AckVoice", SilentAck)
    orb = OrbRecorder()
    monkeypatch.setattr(voice_loop_module, "orb", orb)

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
    assert orb.events == [
        "idle",
        "wake",
        "listening",
        "thinking",
        "user: wie spät ist es",
        "assistant: Es ist zwölf Uhr.",
        "listening",  # Nachfrage-Fenster
        "idle",  # nichts mehr gehört → zurück zum Lauschen auf das Wake-Word
        "idle",  # Sprachmodus beendet
    ]


def test_stop_phrases():
    assert VoiceLoop._is_stop_phrase("Stopp.")
    assert VoiceLoop._is_stop_phrase("Danke, das reicht!")
    assert VoiceLoop._is_stop_phrase("jarvis stop")
    assert not VoiceLoop._is_stop_phrase("Stopp die Musik und mach das Licht aus")
    assert not VoiceLoop._is_stop_phrase("Wie spät ist es")


def test_closing_phrases_end_follow_up():
    for text in [
        "Danke", "Danke schön", "Dankeschön!", "Vielen Dank", "Vielen Dank, Jarvis", "Das war's",
        "Das war's, danke", "Das wäre alles", "Super, danke dir", "Okay, passt", "Alles klar, danke",
        "Nein danke", "Nö", "Nichts mehr", "Tschüss", "Danke, bis später", "Perfekt, das war's erstmal",
        "Ja danke, das reicht", "Ich brauche nichts mehr", "Wir sind fertig",
    ]:
        assert VoiceLoop._is_stop_phrase(text), text


def test_follow_up_questions_are_not_closing():
    for text in [
        "Nein, mach das Licht aus", "Danke, und wie wird das Wetter morgen?", "Das reicht nicht",
        "Ja", "Okay", "Alles klar", "Super", "Spiel das nochmal", "Danke, kannst du das in Obsidian speichern",
        "Stopp die Musik", "Was war das letzte Lied",
    ]:
        assert not VoiceLoop._is_stop_phrase(text), text


def test_text_mode_requests():
    for text in [
        "Wechsel in den Chatmodus", "Wechsle bitte in den Chat-Modus", "Chat Modus", "Textmodus bitte",
        "Zurück zum Chat", "Geh in den Chat", "Sprachmodus beenden", "Beende den Sprachmodus",
        "Ich will lieber tippen", "Ich möchte jetzt schreiben", "Lass mich tippen", "Texteingabe",
    ]:
        assert VoiceLoop._is_text_mode_request(text), text
    for text in [
        "Schreib eine Mail an Max", "Ich möchte eine Notiz schreiben", "Was schreibt der Spiegel heute",
        "Wie spät ist es", "Danke", "Mach das Licht im Chatraum an",
    ]:
        assert not VoiceLoop._is_text_mode_request(text), text


def test_text_mode_request_leaves_voice_mode_without_agent(monkeypatch):
    stream = FakeStream([np.zeros((FRAME_SAMPLES, 1), dtype=np.int16) for _ in range(5)])
    monkeypatch.setattr(voice_loop_module, "MicStream", lambda **kw: stream)
    monkeypatch.setattr(voice_loop_module, "play_chime", lambda *a, **k: None)
    monkeypatch.setattr(voice_loop_module, "AckVoice", lambda phrase: type("A", (), {"path": None, "play": lambda self, wait=True: None})())
    monkeypatch.setattr(voice_loop_module, "orb", OrbRecorder())

    class ChatListener(FakeListener):
        def recognize(self, recording):
            return "Wechsel in den Chatmodus"

    agent = FakeAgent()
    loop = VoiceLoop(
        agent, FakeVoice(), console=Console(quiet=True),
        listener=ChatListener([np.ones(1600, dtype=np.int16)]),
        detector=FakeDetector(trigger_on_call=2),
    )
    loop.run()  # endet von selbst, ohne loop.stop()
    assert agent.queries == []
    assert loop.running is False


def test_mic_stream_is_reopened_when_audio_devices_change(monkeypatch):
    opened = []

    def new_stream(**kw):
        opened.append(kw)
        return FakeStream([np.zeros((FRAME_SAMPLES, 1), dtype=np.int16) for _ in range(5)])

    monkeypatch.setattr(voice_loop_module, "MicStream", new_stream)
    monkeypatch.setattr(voice_loop_module, "orb", OrbRecorder())
    checks = {"n": 0}

    def changed():
        checks["n"] += 1
        if checks["n"] == 1:
            return True  # erste Prüfung: Geräte haben sich geändert → neu öffnen
        loop.stop()
        return False

    monkeypatch.setattr(voice_loop_module.platform_utils, "audio_devices_changed", changed)
    loop = VoiceLoop(
        FakeAgent(), FakeVoice(), console=Console(quiet=True),
        listener=FakeListener([]), detector=FakeDetector(trigger_on_call=-1),
    )
    loop.run()
    assert len(opened) == 2


# ── Rückfrage per Sprache ────────────────────────────────────────────────────


def test_yes_no_detection():
    assert VoiceLoop._is_yes("Ja")
    assert VoiceLoop._is_yes("ja mach das")
    assert VoiceLoop._is_yes("Okay, los")
    assert not VoiceLoop._is_yes("Nein")
    assert not VoiceLoop._is_yes("ja aber nicht jetzt")
    assert not VoiceLoop._is_yes("lieber nicht")
    assert not VoiceLoop._is_yes("wie bitte")
    assert not VoiceLoop._is_yes("")


def _confirm_loop(monkeypatch, answer):
    from core.agent import UNTRUSTED_NOTE

    monkeypatch.setattr(voice_loop_module, "orb", OrbRecorder())
    listener = FakeListener([np.ones((1600, 1), dtype=np.int16)] if answer is not None else [])
    listener.recognize = lambda recording: answer
    voice = FakeVoice()
    loop = VoiceLoop.__new__(VoiceLoop)
    loop.console = Console(file=io.StringIO())
    loop.listener, loop.voice = listener, voice
    loop._stream = FakeStream([])
    return loop, voice, UNTRUSTED_NOTE


def test_voice_confirmation_reads_question_and_accepts_yes(monkeypatch):
    loop, voice, _ = _confirm_loop(monkeypatch, "Ja, bitte")
    assert loop._confirm_by_voice("E-Mail an max@x.de senden\nBetreff: Hallo\nText:\nlang …") is True
    assert voice.spoken == ["E-Mail an max@x.de senden. Soll ich das machen?"]


def test_voice_confirmation_warns_about_untrusted_content_and_no_answer_means_no(monkeypatch):
    loop, voice, note = _confirm_loop(monkeypatch, None)
    assert loop._confirm_by_voice(f"Im Browser öffnen: https://x.example\n\n{note}") is False
    assert "Webseite oder Mail" in voice.spoken[0]


def test_voice_confirmation_without_microphone_is_no(monkeypatch):
    loop, voice, _ = _confirm_loop(monkeypatch, "ja")
    loop._stream = None
    assert loop._confirm_by_voice("Senden") is False
