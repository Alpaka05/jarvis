import numpy as np

from core.voice_input import VoiceInputListener, rms


class FakeStream:
    """Liefert vorbereitete Blöcke wie sounddevice.InputStream.read()."""

    def __init__(self, blocks):
        self.blocks = list(blocks)

    def read(self, n):
        if self.blocks:
            return self.blocks.pop(0), False
        return np.zeros((n, 1), dtype=np.int16), False


def silence(n=960):
    return np.zeros((n, 1), dtype=np.int16)


def loud(n=960, amp=8000):
    t = np.arange(n)
    return (np.sin(t * 0.3) * amp).astype(np.int16).reshape(-1, 1)


def test_rms_scales_to_unit_range():
    assert rms(silence()) == 0.0
    assert 0.1 < rms(loud()) < 0.3


def test_record_from_stream_returns_none_without_speech():
    listener = VoiceInputListener(use_vad=False)
    stream = FakeStream([silence() for _ in range(40)])
    assert listener.record_from_stream(stream, start_timeout=0.05, threshold=0.05) is None


def test_record_from_stream_captures_speech_until_silence():
    listener = VoiceInputListener(use_vad=False)
    blocks = [silence()] * 3 + [loud()] * 10 + [silence()] * 60
    stream = FakeStream(blocks)
    rec = listener.record_from_stream(stream, silence_limit=0.3, start_timeout=5, threshold=0.05)
    assert rec is not None
    # 10 laute Blöcke + Vorlauf + 0.3 s Stille (5 Blöcke), aber nicht alles
    assert 960 * 10 <= len(rec) < 960 * 25
    assert rms(rec) > 0.05


def test_hysteresis_keeps_quiet_speech_tail():
    """Ein leiserer Satzausklang (über der Halte-, unter der Startschwelle) beendet die Aufnahme nicht."""
    listener = VoiceInputListener(use_vad=False)
    quiet_tail = [loud(amp=2500)] * 10  # RMS ≈ 0.054: unter Start 0.08, über Halte 0.044
    blocks = [loud()] * 5 + quiet_tail + [silence()] * 60
    rec = listener.record_from_stream(FakeStream(blocks), silence_limit=0.3, start_timeout=5, threshold=0.08)
    assert len(rec) >= 960 * 15


def test_recognize_survives_broken_flac_converter(monkeypatch):
    """Ein kaputter FLAC-Konverter (z.B. Intel-Binary auf Apple Silicon) beendet den Sprachmodus nicht."""
    listener = VoiceInputListener(use_vad=False)

    def bad_cpu(*args, **kwargs):
        raise OSError(86, "Bad CPU type in executable")

    monkeypatch.setattr(listener.recognizer, "recognize_google", bad_cpu)
    assert listener.recognize(loud().reshape(-1)) == ""
