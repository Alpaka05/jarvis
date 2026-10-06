"""Mikrofon-Stream, der nicht hängen bleibt.

Ein blockierendes `sd.InputStream.read()` wartet ewig, wenn CoreAudio den Stream abbricht
(z.B. „PaMacCore (AUHAL) … err='-50'“, wenn eine andere App die Rate des Geräts ändert) oder
das USB-Mikrofon abgezogen wird – der Sprachmodus stand dann bei „Ich höre …“ still.

MicStream liest deshalb per Callback in eine Warteschlange. `read()` hat dieselbe Form wie bei
sounddevice (`(frames, overflowed)`), wartet aber höchstens STALL_SECONDS auf Daten und wirft
sonst MicError – der Sprachmodus öffnet das Mikrofon dann neu.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

STALL_SECONDS = 2.0  # so lange ohne Audio-Daten gilt der Stream als tot (er liefert sonst ~12×/s)
MAX_BUFFER_SECONDS = 1.5  # mehr wird nicht gepuffert; ältere Daten fallen weg (wie beim alten Stream)


class MicError(RuntimeError):
    """Das Mikrofon liefert keine Daten mehr (abgezogen, von CoreAudio abgebrochen …)."""


class MicStream:
    def __init__(self, samplerate: int, blocksize: int, channels: int = 1, dtype: str = "int16", device=None):
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.channels = channels
        self.dtype = dtype
        self.device = device
        max_blocks = max(2, int(MAX_BUFFER_SECONDS * samplerate / blocksize))
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=max_blocks)
        self._pending = np.zeros((0, channels), dtype=dtype)
        self._overflowed = False
        self._error: Optional[str] = None
        self._lock = threading.Lock()
        self._stream = None

    # ── Kontextmanager (wie sd.InputStream) ──────────────────────────────────

    def __enter__(self) -> "MicStream":
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=self.channels,
            dtype=self.dtype,
            device=self.device,
            callback=self._callback,
            finished_callback=self._finished,
        )
        self._stream.start()
        return self

    def __exit__(self, *exc) -> bool:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()
        return False

    # ── PortAudio-Thread ─────────────────────────────────────────────────────

    def _callback(self, indata, frames, time_info, status):
        if status.input_overflow:
            self._overflowed = True
        block = indata.copy()
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            # Niemand liest (Jarvis denkt nach oder spricht): ältesten Block verwerfen, damit später
            # keine veraltete Aufnahme verarbeitet wird
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._overflowed = True
            try:
                self._queue.put_nowait(block)
            except queue.Full:
                pass

    def _finished(self):
        if self._stream is not None:  # nicht von uns beendet
            self._error = "Mikrofon-Stream wurde vom System beendet"

    # ── Lesen ────────────────────────────────────────────────────────────────

    @property
    def read_available(self) -> int:
        """Gepufferte Samples (wie bei sounddevice)."""
        return len(self._pending) + self._queue.qsize() * self.blocksize

    def read(self, frames: int) -> Tuple[np.ndarray, bool]:
        """Genau `frames` Samples; MicError, wenn STALL_SECONDS lang nichts kommt."""
        with self._lock:
            parts = [self._pending]
            have = len(self._pending)
            while have < frames:
                if self._error:
                    raise MicError(self._error)
                try:
                    block = self._queue.get(timeout=STALL_SECONDS)
                except queue.Empty:
                    raise MicError(f"Mikrofon liefert seit {STALL_SECONDS:.0f} s keine Daten") from None
                parts.append(block)
                have += len(block)
            data = np.concatenate(parts) if len(parts) > 1 else parts[0]
            self._pending = data[frames:]
            overflowed, self._overflowed = self._overflowed, False
            return data[:frames], overflowed

    def flush(self) -> None:
        """Alles Gepufferte verwerfen (z.B. das Echo der eigenen Sprachausgabe)."""
        with self._lock:
            self._pending = self._pending[:0]
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
