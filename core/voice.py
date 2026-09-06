"""Sprachausgabe mit niedriger Latenz und Unterbrechung.

Edge-Stimmen werden satzweise erzeugt: Der erste Satz wird synthetisiert und sofort
abgespielt, während die folgenden Sätze im Hintergrund erzeugt werden. Die Wiedergabe
läuft direkt im Prozess über sounddevice (kein externer Player, kein Anlauf), und
stop() greift innerhalb von ~100 ms.

Ohne Internet oder mit TTS_ENGINE=system wird die Betriebssystem-Stimme genutzt.
"""
from __future__ import annotations

import queue
import re
import threading
from typing import List, Optional

import numpy as np

from config import config
from core import platform_utils

try:  # Mikrofon-Überwachung und Wiedergabe sind optional
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None

EDGE_SAMPLE_RATE = 24000
_FAIL = object()

# Satzgrenze: Satzzeichen, dann Leerraum, dann Großbuchstabe/Zahl/Anführungszeichen
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ0-9„\"(])")


def split_sentences(text: str, min_len: int = 30) -> List[str]:
    """Teilt in Sätze; sehr kurze Stücke werden mit dem nächsten zusammengefasst."""
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    merged: List[str] = []
    for part in parts:
        if merged and len(merged[-1]) < min_len:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged or ([text.strip()] if text.strip() else [])


def synthesize_pcm(text: str, sample_rate: int = EDGE_SAMPLE_RATE) -> Optional[np.ndarray]:
    """Edge-TTS → PCM (int16, mono) im Speicher. None bei Fehler (z.B. offline)."""
    try:
        import edge_tts
        import miniaudio

        comm = edge_tts.Communicate(text, config.EDGE_VOICE, rate=config.EDGE_RATE, pitch=config.EDGE_PITCH)
        data = b"".join(chunk["data"] for chunk in comm.stream_sync() if chunk["type"] == "audio")
        if not data:
            return None
        dec = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=sample_rate)
        pcm = np.frombuffer(bytes(dec.samples), dtype=np.int16)
        return _trim_trailing_silence(pcm, sample_rate)
    except Exception:
        return None


def _trim_trailing_silence(pcm: np.ndarray, sample_rate: int, threshold: int = 300, keep: float = 0.18) -> np.ndarray:
    """Edge hängt an jede Äußerung ~0,5 s Stille an – zwischen Sätzen kürzen wir das."""
    loud = np.where(np.abs(pcm.astype(np.int32)) > threshold)[0]
    if loud.size == 0:
        return pcm
    end = min(len(pcm), loud[-1] + int(sample_rate * keep))
    return pcm[:end]


class VoiceEngine:
    BARGE_IN_THRESHOLD = max(0.035, getattr(config, "BARGE_IN_THRESHOLD", 0.06))

    def __init__(self):
        self.enabled = config.TTS_ENABLED and (config.TTS_ENGINE == "edge" or platform_utils.tts_available())
        self.voice = platform_utils.default_voice()
        self.label = platform_utils.voice_label()
        self._speaking = False
        self._current: Optional[threading.Event] = None  # Stop-Event der laufenden Äußerung
        self._process = None  # System-TTS-Prozess (Fallback)
        self._lock = threading.Lock()
        if self.enabled and config.TTS_ENGINE == "edge":
            # Erste Edge-Anfrage eines Prozesses ist ~0,6 s langsamer (Verbindungsaufbau, Token).
            # Im Hintergrund vorwärmen, damit die erste echte Antwort schnell kommt.
            threading.Thread(target=synthesize_pcm, args=("Ok.",), daemon=True).start()

    # ── Öffentliche API ──────────────────────────────────────────────────────

    @staticmethod
    def clean_text_for_speech(text: str) -> str:
        clean = re.sub(r"https?://\S+", "", text)
        clean = re.sub(r"[*#_`\[\]>|]", "", clean)
        clean = re.sub(r":[a-z_]+:", "", clean)  # :emoji_codes:
        clean = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", clean)  # Emojis
        return re.sub(r"\s+", " ", clean).strip()

    def is_speaking(self) -> bool:
        proc = self._process
        return self._speaking or (proc is not None and proc.poll() is None)

    def stop(self):
        with self._lock:
            if self._current is not None:
                self._current.set()
            proc = self._process
            self._process = None
            self._speaking = False
        if proc is not None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
            platform_utils.cleanup_process(proc)

    def speak(self, text: str, listen_for_interrupt: bool = True, block: bool = False):
        if not self.enabled:
            return
        self.stop()
        cleaned = self.clean_text_for_speech(text)
        if not cleaned:
            return
        stop_event = threading.Event()
        with self._lock:
            self._current = stop_event
            self._speaking = True
        worker = threading.Thread(target=self._run, args=(cleaned, stop_event, listen_for_interrupt), daemon=True)
        worker.start()
        if block:
            worker.join()

    # ── Intern ───────────────────────────────────────────────────────────────

    def _run(self, text: str, stop_event: threading.Event, listen_for_interrupt: bool):
        try:
            if config.TTS_ENGINE == "edge" and sd is not None:
                if self._run_edge(text, stop_event, listen_for_interrupt) or stop_event.is_set():
                    return
            self._run_system(text, stop_event, listen_for_interrupt)
        finally:
            with self._lock:
                if self._current is stop_event:
                    self._speaking = False

    def _run_edge(self, text: str, stop_event: threading.Event, listen_for_interrupt: bool) -> bool:
        """Satzweise Pipeline: synthetisieren im Hintergrund, abspielen sobald der erste Satz da ist."""
        sentences = split_sentences(text)
        q: "queue.Queue" = queue.Queue(maxsize=3)

        def producer():
            for sentence in sentences:
                if stop_event.is_set():
                    break
                pcm = synthesize_pcm(sentence)
                q.put(pcm if pcm is not None else _FAIL)
            q.put(None)

        threading.Thread(target=producer, daemon=True).start()

        first = q.get()
        if first is None or first is _FAIL:
            return False  # Edge nicht erreichbar → Systemstimme

        try:
            with platform_utils.open_audio_stream(
                lambda: sd.OutputStream(
                    samplerate=EDGE_SAMPLE_RATE, channels=1, dtype="int16", device=platform_utils.default_output_device()
                )
            ) as out:
                if listen_for_interrupt:
                    threading.Thread(target=self._monitor_barge_in, args=(stop_event,), daemon=True).start()
                item = first
                while item is not None and not stop_event.is_set():
                    if item is not _FAIL:
                        self._write_pcm(out, item, stop_event)
                    item = q.get()
        except Exception:
            return stop_event.is_set()  # Ausgabegerät-Problem → Fallback nur, wenn nicht gestoppt
        return True

    @staticmethod
    def _write_pcm(out, pcm: np.ndarray, stop_event: threading.Event, chunk: int = 2400):
        data = pcm.reshape(-1, 1)
        for i in range(0, len(data), chunk):
            if stop_event.is_set():
                break
            out.write(data[i : i + chunk])

    def _run_system(self, text: str, stop_event: threading.Event, listen_for_interrupt: bool):
        proc = platform_utils.speak_system_process(text, self.voice)
        if proc is None:
            return
        with self._lock:
            self._process = proc
        if listen_for_interrupt and sd is not None:
            threading.Thread(target=self._monitor_barge_in, args=(stop_event,), daemon=True).start()
        while proc.poll() is None:
            if stop_event.wait(0.1):
                try:
                    proc.terminate()
                    proc.kill()
                except Exception:
                    pass
                break
        platform_utils.cleanup_process(proc)
        with self._lock:
            if self._process is proc:
                self._process = None

    def _monitor_barge_in(self, stop_event: threading.Event):
        """Text-Modus: lautes, anhaltendes Reinreden stoppt die Ausgabe."""
        if self.BARGE_IN_THRESHOLD <= 0:
            return
        sample_rate = 16000
        chunk = int(sample_rate * 0.1)
        loud_frames = 0
        try:
            with platform_utils.open_audio_stream(
                lambda: sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16")
            ) as stream:
                stream.read(int(sample_rate * 0.4))  # Anlauf der eigenen Ausgabe ignorieren
                while not stop_event.is_set() and self.is_speaking():
                    data, _ = stream.read(chunk)
                    volume = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)) / 32768.0)
                    loud_frames = loud_frames + 1 if volume > self.BARGE_IN_THRESHOLD else 0
                    if loud_frames >= 4:  # ~0,4 s anhaltend laut
                        print("\n[🔊 Unterbrochen durch Spracheingabe]")
                        self.stop()
                        break
        except Exception:
            pass
