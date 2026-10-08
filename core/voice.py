"""Sprachausgabe mit niedriger Latenz und Unterbrechung.

Edge-Stimmen werden satzweise erzeugt: Der erste Satz wird synthetisiert und sofort
abgespielt, während die folgenden Sätze im Hintergrund erzeugt werden. Mit open_stream()
beginnt das schon, während das LLM den Rest der Antwort noch schreibt. Die Wiedergabe
läuft direkt im Prozess über sounddevice (kein externer Player, kein Anlauf), und
stop() greift innerhalb von ~100 ms.

Ohne Internet oder mit TTS_ENGINE=system wird die Betriebssystem-Stimme genutzt.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
from typing import Iterator, List, Optional, Tuple

import numpy as np

from config import config
from core import orb, platform_utils

log = logging.getLogger(__name__)

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


def take_sentences(buffer: str, min_len: int = 30, first: bool = False) -> Tuple[List[str], str]:
    """Für wachsenden Text: fertige Sätze abtrennen, den unfertigen Rest zurückgeben.

    Ein Satz gilt erst als fertig, wenn der nächste schon begonnen hat (sonst ist „3.“ in „3.5“
    nicht von einem Satzende zu unterscheiden). Kurze Sätze warten wie bei split_sentences auf den
    nächsten – außer dem ersten (`first`), der für eine schnelle Reaktion sofort raus darf.
    """
    parts = _SENTENCE_SPLIT.split(buffer)
    rest = parts.pop()
    done: List[str] = []
    pending = ""
    for part in parts:
        pending = f"{pending} {part.strip()}".strip()
        if pending and (len(pending) >= min_len or (first and not done)):
            done.append(pending)
            pending = ""
    if pending:
        rest = f"{pending} {rest.lstrip()}"
    return done, rest


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


class _Utterance:
    """Eine Äußerung, deren Sätze nach und nach dazukommen können (add, dann finish)."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self._sentences: List[str] = []
        self._finished = False
        self._cond = threading.Condition()

    @property
    def stopped(self) -> bool:
        return self.stop_event.is_set()

    def add(self, sentence: str):
        with self._cond:
            self._sentences.append(sentence)
            self._cond.notify_all()

    def finish(self):
        with self._cond:
            self._finished = True
            self._cond.notify_all()

    def stop(self):
        self.stop_event.set()
        with self._cond:
            self._cond.notify_all()

    def sentences(self) -> Iterator[str]:
        """Liefert alle Sätze der Reihe nach und wartet auf neue, bis finish() oder stop()."""
        i = 0
        while True:
            with self._cond:
                while i >= len(self._sentences) and not self._finished and not self.stopped:
                    self._cond.wait(0.2)
                if self.stopped or i >= len(self._sentences):
                    return
                sentence = self._sentences[i]
            i += 1
            yield sentence

    def full_text(self) -> str:
        """Wartet auf finish() (oder stop()) und gibt den ganzen Text zurück."""
        return " ".join(self.sentences())


class SpeechStream:
    """Liest Text vor, der noch entsteht (LLM-Streaming): feed() mit Textstücken, am Ende close().

    Unterbricht eine andere Ausgabe (z.B. eine Rückfrage per speak()) diese Äußerung, beginnt
    der nächste Satz eine neue. Nach VoiceEngine.stop() wird nichts mehr vorgelesen.
    """

    def __init__(self, engine: "VoiceEngine", listen_for_interrupt: bool = True):
        self.engine = engine
        self.listen_for_interrupt = listen_for_interrupt
        self.cancelled = False
        self._buffer = ""
        self._first = True
        self._closed = False
        self._utterance: Optional[_Utterance] = None
        self._lock = threading.Lock()

    def feed(self, text: str):
        with self._lock:
            if self._closed or self.cancelled or not text:
                return
            self._buffer += text
            sentences, self._buffer = take_sentences(self._buffer, first=self._first)
            for sentence in sentences:
                self._say(sentence)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            rest, self._buffer = self._buffer.strip(), ""
            if rest and not self.cancelled:
                self._say(rest)
            if self._utterance is not None:
                self._utterance.finish()

    def _say(self, sentence: str):
        cleaned = self.engine.clean_text_for_speech(sentence)
        if not cleaned:
            return
        self._first = False
        if self._utterance is None or self._utterance.stopped:
            self._utterance = self.engine._start_utterance(self.listen_for_interrupt)
            if self._utterance is None:  # Sprachausgabe aus
                return
        self._utterance.add(cleaned)


class VoiceEngine:
    BARGE_IN_THRESHOLD = max(0.035, getattr(config, "BARGE_IN_THRESHOLD", 0.06))

    def __init__(self):
        self.enabled = config.TTS_ENABLED and (config.TTS_ENGINE == "edge" or platform_utils.tts_available())
        self.voice = platform_utils.default_voice()
        self.label = platform_utils.voice_label()
        self._speaking = False
        self._current: Optional[_Utterance] = None  # laufende Äußerung
        self._stream: Optional[SpeechStream] = None  # zuletzt geöffneter SpeechStream
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
        """Bricht die Ausgabe ab – auch Sätze, die ein offener SpeechStream noch liefern würde."""
        with self._lock:
            if self._stream is not None:
                self._stream.cancelled = True
        self._halt()

    def _halt(self):
        """Beendet die laufende Äußerung (ein offener SpeechStream darf danach neu ansetzen)."""
        with self._lock:
            if self._current is not None:
                self._current.stop()
            proc = self._process
            self._process = None
            self._end_speaking()
        if proc is not None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
            platform_utils.cleanup_process(proc)

    def speak(self, text: str, listen_for_interrupt: bool = True, block: bool = False):
        """Liest einen fertigen Text vor. Eine laufende Ausgabe wird dabei unterbrochen."""
        if not self.enabled:
            return
        cleaned = self.clean_text_for_speech(text)
        if not cleaned:
            self._halt()
            return
        utterance = self._start_utterance(listen_for_interrupt)
        if utterance is None:
            return
        for sentence in split_sentences(cleaned):
            utterance.add(sentence)
        utterance.finish()
        if block and utterance.thread is not None:
            utterance.thread.join()

    def open_stream(self, listen_for_interrupt: bool = True) -> SpeechStream:
        """Für Antworten, die noch entstehen: Sätze werden vorgelesen, sobald sie fertig sind.
        Ein vorher geöffneter Stream wird beendet."""
        self.stop()
        stream = SpeechStream(self, listen_for_interrupt)
        with self._lock:
            self._stream = stream
        return stream

    # ── Intern ───────────────────────────────────────────────────────────────

    def _start_utterance(self, listen_for_interrupt: bool) -> Optional[_Utterance]:
        if not self.enabled:
            return None
        self._halt()
        utterance = _Utterance()
        with self._lock:
            self._current = utterance
            self._speaking = True
            orb.state("speaking")
        utterance.thread = threading.Thread(target=self._run, args=(utterance, listen_for_interrupt), daemon=True)
        utterance.thread.start()
        return utterance

    def _run(self, utterance: _Utterance, listen_for_interrupt: bool):
        try:
            if config.TTS_ENGINE == "edge" and sd is not None:
                if self._run_edge(utterance, listen_for_interrupt) or utterance.stopped:
                    return
            self._run_system(utterance, listen_for_interrupt)
        finally:
            with self._lock:
                if self._current is utterance:  # nicht schon von einer neuen Äußerung abgelöst
                    self._end_speaking()

    def _end_speaking(self):
        """Unter self._lock aufrufen. Meldet „idle“ an den Orb, bevor is_speaking() False liefert –
        so überholt der Zustand, den der Aufrufer danach setzt (z.B. Nachfrage-Fenster), es sicher."""
        if self._speaking:
            orb.state("idle")
        self._speaking = False

    def _run_edge(self, utterance: _Utterance, listen_for_interrupt: bool) -> bool:
        """Satzweise Pipeline: synthetisieren im Hintergrund, abspielen sobald der erste Satz da ist."""
        stop_event = utterance.stop_event
        q: "queue.Queue" = queue.Queue(maxsize=3)
        device = platform_utils.default_output_device()
        rate = platform_utils.output_sample_rate(device) or EDGE_SAMPLE_RATE

        abandoned = threading.Event()  # Wiedergabe beendet (Stopp, Fehler) – niemand liest mehr

        def put(item) -> bool:
            # Mit Zeitlimit: Bei voller Warteschlange und beendeter Wiedergabe hinge der Thread sonst
            # für immer (samt bereits synthetisiertem Audio im Speicher)
            while not (stop_event.is_set() or abandoned.is_set()):
                try:
                    q.put(item, timeout=0.2)
                    return True
                except queue.Full:
                    continue
            return False

        def producer():
            for sentence in utterance.sentences():  # wartet auf Sätze, die noch geschrieben werden
                pcm = synthesize_pcm(sentence, rate)
                if pcm is None:
                    log.warning("Edge-TTS: Satz nicht synthetisiert: %r", sentence[:60])
                if not put(pcm if pcm is not None else _FAIL):
                    return
            put(None)

        threading.Thread(target=producer, name="tts-producer", daemon=True).start()

        try:
            return self._play_queue(q, rate, device, stop_event, listen_for_interrupt)
        finally:
            abandoned.set()

    def _play_queue(self, q, rate, device, stop_event, listen_for_interrupt) -> bool:
        def next_item():
            # Beim Streaming kann der nächste Satz auf sich warten lassen; nach stop() kommt keiner mehr
            while True:
                try:
                    return q.get(timeout=0.2)
                except queue.Empty:
                    if stop_event.is_set():
                        return None

        first = next_item()
        if first is None or first is _FAIL:
            return False  # Edge nicht erreichbar → Systemstimme

        try:
            with platform_utils.open_audio_stream(
                lambda: sd.OutputStream(samplerate=rate, channels=1, dtype="int16", device=device)
            ) as out:
                if listen_for_interrupt:
                    threading.Thread(target=self._monitor_barge_in, args=(stop_event,), daemon=True).start()
                item = first
                while item is not None and not stop_event.is_set():
                    if item is not _FAIL:
                        self._write_pcm(out, item, stop_event, chunk=rate // 30)
                    item = next_item()
        except Exception:
            log.warning("Wiedergabe über Ausgabegerät %r fehlgeschlagen", device, exc_info=True)
            return stop_event.is_set()  # Ausgabegerät-Problem → Fallback nur, wenn nicht gestoppt
        return True

    @staticmethod
    def _write_pcm(out, pcm: np.ndarray, stop_event: threading.Event, chunk: int = 800):
        # 33 ms pro Block (800 Samples bei 24 kHz): kurze Stop-Latenz und ~30 Pegelwerte pro Sekunde für den Orb
        data = pcm.reshape(-1, 1)
        for i in range(0, len(data), chunk):
            if stop_event.is_set():
                break
            piece = data[i : i + chunk]
            orb.pcm_level(piece, "tts")
            out.write(piece)

    def _run_system(self, utterance: _Utterance, listen_for_interrupt: bool):
        # Systemstimme (Ersatz) am Stück: Ein Prozess pro Satz hätte spürbare Pausen (v.a. SAPI)
        stop_event = utterance.stop_event
        text = utterance.full_text()
        if not text or stop_event.is_set():
            return
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
