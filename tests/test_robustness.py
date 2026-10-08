"""Phase 3: Mikrofon fällt aus → neu öffnen, Audio-Zähler, TTS-Producer, Fallback, SQLite, Logging."""
import logging
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from rich.console import Console

import core.mic as mic_module
import core.platform_utils as pu
import core.voice_loop as voice_loop_module
from core.agent import JarvisAgent
from core.llm.base import LLMError, LLMResponse, ToolCall
from core.mic import MicError, MicStream
from core.voice_loop import VoiceLoop
from tests.test_agent import FakeProvider
from tests.test_voice_loop import FakeDetector, FakeListener, FakeVoice, OrbRecorder
from tools.base import BaseTool, ToolResult

OK = SimpleNamespace(input_overflow=False)


def _block(value, n=4):
    return np.full((n, 1), value, dtype=np.int16)


# ── MicStream ────────────────────────────────────────────────────────────────


def test_mic_read_assembles_blocks_across_boundaries():
    mic = MicStream(samplerate=16000, blocksize=4)
    for v in (1, 2, 3):
        mic._callback(_block(v), 4, None, OK)
    assert mic.read_available == 12
    data, overflowed = mic.read(6)
    assert data[:, 0].tolist() == [1, 1, 1, 1, 2, 2] and not overflowed
    data, _ = mic.read(6)
    assert data[:, 0].tolist() == [2, 2, 3, 3, 3, 3]


def test_mic_without_data_raises_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(mic_module, "STALL_SECONDS", 0.05)
    mic = MicStream(samplerate=16000, blocksize=4)
    start = time.monotonic()
    with pytest.raises(MicError):
        mic.read(4)
    assert time.monotonic() - start < 1


def test_mic_finished_by_system_raises():
    mic = MicStream(samplerate=16000, blocksize=4)
    mic._stream = object()  # läuft (aus Sicht des Callbacks)
    mic._finished()
    start = time.monotonic()
    with pytest.raises(MicError):
        mic.read(4)
    assert time.monotonic() - start < 0.5  # sofort, nicht erst nach STALL_SECONDS


def test_mic_keeps_only_recent_audio_when_nobody_reads(monkeypatch):
    monkeypatch.setattr(mic_module, "MAX_BUFFER_SECONDS", 3 * 4 / 16000)  # Platz für 3 Blöcke
    mic = MicStream(samplerate=16000, blocksize=4)
    for v in range(1, 7):
        mic._callback(_block(v), 4, None, OK)
    data, overflowed = mic.read(12)
    assert data[:, 0].tolist() == [4] * 4 + [5] * 4 + [6] * 4 and overflowed


def test_mic_flush_drops_buffered_audio():
    mic = MicStream(samplerate=16000, blocksize=4)
    mic._callback(_block(1), 4, None, OK)
    mic.read(2)
    mic._callback(_block(2), 4, None, OK)
    mic.flush()
    assert mic.read_available == 0
    mic._callback(_block(3), 4, None, OK)
    assert mic.read(4)[0][:, 0].tolist() == [3, 3, 3, 3]


# ── Sprachmodus öffnet das Mikrofon nach Ausfällen neu ───────────────────────


class DeadStream:
    def read(self, n):
        raise MicError("Mikrofon liefert keine Daten")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAgent:
    voice_mode = False


def _loop(monkeypatch, make_stream):
    opened = []

    def new_stream(**kw):
        opened.append(kw)
        return make_stream(len(opened))

    monkeypatch.setattr(voice_loop_module, "MicStream", new_stream)
    monkeypatch.setattr(voice_loop_module, "orb", OrbRecorder())
    monkeypatch.setattr(voice_loop_module.time, "sleep", lambda s: None)
    loop = VoiceLoop(
        FakeAgent(), FakeVoice(), console=Console(quiet=True),
        listener=FakeListener([]), detector=FakeDetector(trigger_on_call=-1),
    )
    return loop, opened


def test_voice_mode_reopens_mic_after_failure(monkeypatch):
    class StopAfterSomeFrames:
        def __init__(self):
            self.n = 0

        def read(self, n):
            self.n += 1
            if self.n > 3:
                loop.stop()
            return np.zeros((n, 1), dtype=np.int16), False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    loop, opened = _loop(monkeypatch, lambda i: DeadStream() if i == 1 else StopAfterSomeFrames())
    loop.run()
    assert len(opened) == 2  # erst ausgefallen, dann neu geöffnet und weitergelauscht


def test_voice_mode_gives_up_after_repeated_failures(monkeypatch):
    loop, opened = _loop(monkeypatch, lambda i: DeadStream())
    loop.run()  # endet von selbst statt endlos zu hängen
    assert len(opened) == voice_loop_module.MAX_MIC_FAILURES
    assert not loop.running


# ── Audio-Streams: Zähler und Abtastrate ─────────────────────────────────────


def test_stream_counter_drops_only_after_close_even_on_error(monkeypatch):
    monkeypatch.setattr(pu, "refresh_audio_devices", lambda: None)
    seen = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            seen.append(pu._open_streams)  # beim Schließen noch mitgezählt
            raise RuntimeError("PortAudio mag nicht")

    before = pu._open_streams
    with pu.open_audio_stream(Stream):
        assert pu._open_streams == before + 1
    assert seen == [before + 1] and pu._open_streams == before


def test_failed_rate_query_is_not_cached(monkeypatch):
    import sounddevice as sd

    monkeypatch.setattr(pu, "IS_MAC", True)
    pu._output_device_cache.clear()
    answers = [RuntimeError("Gerät weg"), {"default_samplerate": 48000.0}]

    def query(device, kind):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(sd, "query_devices", query)
    assert pu.output_sample_rate("x") is None
    assert pu.output_sample_rate("x") == 48000
    pu._output_device_cache.clear()


# ── Sprachausgabe: Producer hängt nicht nach Abbruch ─────────────────────────


def test_tts_producer_ends_when_playback_fails(monkeypatch):
    import core.voice as voice_module

    monkeypatch.setattr(voice_module, "synthesize_pcm", lambda sentence, rate: np.zeros(100, dtype=np.int16))
    monkeypatch.setattr(voice_module.platform_utils, "default_output_device", lambda: None)
    monkeypatch.setattr(voice_module.platform_utils, "output_sample_rate", lambda device: 24000)

    def broken_stream(factory):
        raise RuntimeError("Ausgabegerät weg")

    monkeypatch.setattr(voice_module.platform_utils, "open_audio_stream", broken_stream)
    engine = voice_module.VoiceEngine.__new__(voice_module.VoiceEngine)
    text = " ".join(f"Satz Nummer {i} ist hier." for i in range(10))  # mehr Sätze als die Warteschlange fasst
    utterance = voice_module._Utterance()
    for sentence in voice_module.split_sentences(text):
        utterance.add(sentence)
    utterance.finish()
    engine._run_edge(utterance, listen_for_interrupt=False)
    deadline = time.monotonic() + 2
    while any(t.name == "tts-producer" for t in threading.enumerate()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(t.name == "tts-producer" for t in threading.enumerate())


# ── Agent: beim Ersatz bleiben, ehrliche Timeouts ────────────────────────────


class CountingProvider(FakeProvider):
    def __init__(self, name, fail=False):
        super().__init__([])
        self.name, self.model, self.fail, self.n = name, name, fail, 0

    def chat(self, system, messages, tools):
        self.n += 1
        if self.fail:
            raise LLMError(f"{self.name} offline")
        return LLMResponse(text=f"von {self.name}")


def test_agent_sticks_to_fallback_after_primary_failure():
    primary, fallback = CountingProvider("haupt", fail=True), CountingProvider("ersatz")
    agent = JarvisAgent(provider=primary, fallback=fallback, tools=[], on_notice=lambda m: None)
    assert agent.process_query("eins") == "von ersatz"
    assert agent.process_query("zwei") == "von ersatz"
    assert primary.n == 1  # zweite Anfrage wartet nicht erneut auf den ausgefallenen Provider

    agent._fallback_until = 0  # Zeit abgelaufen
    primary.fail = False
    assert agent.process_query("drei") == "von haupt"


def test_failing_fallback_during_sticky_period_tries_primary_again():
    primary, fallback = CountingProvider("haupt"), CountingProvider("ersatz", fail=True)
    agent = JarvisAgent(provider=primary, fallback=fallback, tools=[])
    agent._fallback_until = time.monotonic() + 60
    assert agent.process_query("x") == "von haupt"


class HangingTool(BaseTool):
    name = "hang"

    def execute(self, **kwargs):
        time.sleep(1)
        return ToolResult.ok("zu spät")


def test_tool_timeout_says_it_may_still_run():
    provider = FakeProvider([
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="hang", arguments={})]),
        LLMResponse(text="ok"),
    ])
    agent = JarvisAgent(provider=provider, tools=[HangingTool()])
    agent.TOOL_TIMEOUT = 0.05
    agent.process_query("los")
    tool_msg = [m for m in agent.history if m["role"] == "tool"][0]
    assert "eventuell" in tool_msg["content"] and "NICHT wiederholen" in tool_msg["content"]


class BrokenTool(BaseTool):
    name = "broken"

    def execute(self, **kwargs):
        raise ValueError("kaputt")


def test_tool_crash_is_logged_with_traceback(caplog):
    provider = FakeProvider([
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="broken", arguments={})]),
        LLMResponse(text="ok"),
    ])
    with caplog.at_level(logging.ERROR, logger="core.agent"):
        JarvisAgent(provider=provider, tools=[BrokenTool()]).process_query("los")
    assert any(r.exc_info and "broken" in r.getMessage() for r in caplog.records)


# ── Gedächtnis aus mehreren Threads ──────────────────────────────────────────


def test_memory_store_survives_concurrent_threads(tmp_path):
    from core.memory import MemoryStore

    store = MemoryStore(tmp_path / "m.db")
    errors = []

    def work(i):
        try:
            for j in range(20):
                store.add_fact(f"Fakt {i}-{j}")
                store.log_message("s", "user", f"Nachricht {i}-{j}")
                store.facts_for_prompt(10)
        except Exception as e:  # pragma: no cover - soll nicht passieren
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and store.count_facts() == 160


# ── Logging ──────────────────────────────────────────────────────────────────


def test_setup_logging_writes_to_data_dir(tmp_path):
    from core.log import setup_logging

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = setup_logging(tmp_path)
        logging.getLogger("jarvis.test").warning("Hallo Log")
        for h in root.handlers:
            h.flush()
        assert "Hallo Log" in path.read_text(encoding="utf-8")
    finally:
        for h in root.handlers:
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_rotate_native_log_keeps_last_session(tmp_path):
    from core.log import rotate_native_log

    (tmp_path / "native.log").write_text("Absturz von gestern")
    path = rotate_native_log(tmp_path)
    assert not path.exists() and (tmp_path / "native.log.1").read_text() == "Absturz von gestern"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Deskriptor-Umleitung nur unter macOS genutzt")
def test_native_output_goes_to_log_file_python_output_stays(tmp_path):
    log_file = tmp_path / "native.log"
    script = (
        "import os, sys; sys.path.insert(0, sys.argv[1]);"
        "from core import platform_utils as pu;"
        "assert pu.silence_native_output(sys.argv[2]);"
        "os.write(1, b'C-Bibliothek stdout\\n'); os.write(2, b'C-Bibliothek stderr\\n');"
        "print('Python sichtbar', flush=True)"
    )
    root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-c", script, root, str(log_file)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "Python sichtbar" in out.stdout and "C-Bibliothek" not in out.stdout + out.stderr
    native = log_file.read_text()
    assert "C-Bibliothek stdout" in native and "C-Bibliothek stderr" in native
