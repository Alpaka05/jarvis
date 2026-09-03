"""Sprachausgabe mit Unterbrechung durch Reinreden (Barge-in).

Die eigentliche Stimme kommt aus core.platform_utils (Windows SAPI, macOS say, Linux espeak).
"""
from __future__ import annotations

import re
import threading
from typing import Optional

from config import config
from core import platform_utils

try:  # Mikrofon-Überwachung ist optional
    import numpy as np
    import sounddevice as sd
except Exception:  # pragma: no cover
    np = None
    sd = None


class VoiceEngine:
    BARGE_IN_THRESHOLD = 0.035

    def __init__(self):
        self.enabled = config.TTS_ENABLED and (config.TTS_ENGINE == "edge" or platform_utils.tts_available())
        self.voice = platform_utils.default_voice()
        self.label = platform_utils.voice_label()
        self.current_process = None
        self._monitoring = False
        self._lock = threading.Lock()

    @staticmethod
    def clean_text_for_speech(text: str) -> str:
        clean = re.sub(r"https?://\S+", "", text)
        clean = re.sub(r"[*#_`\[\]>|]", "", clean)
        clean = re.sub(r":[a-z_]+:", "", clean)  # :emoji_codes:
        clean = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", clean)  # Emojis
        return re.sub(r"\s+", " ", clean).strip()

    def stop(self):
        self._monitoring = False
        with self._lock:
            proc = self.current_process
            self.current_process = None
        if proc is not None:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
            platform_utils.cleanup_process(proc)

    def is_speaking(self) -> bool:
        proc = self.current_process
        return proc is not None and proc.poll() is None

    def speak(self, text: str, listen_for_interrupt: bool = True, block: bool = False):
        if not self.enabled:
            return
        self.stop()
        cleaned = self.clean_text_for_speech(text)
        if not cleaned:
            return

        def _play():
            proc = platform_utils.speak_process(cleaned, self.voice)
            if proc is None:
                return
            with self._lock:
                self.current_process = proc
            self._monitoring = True
            if listen_for_interrupt and sd is not None:
                self._monitor_barge_in()
            try:
                proc.wait()
            except Exception:
                pass
            platform_utils.cleanup_process(proc)
            with self._lock:
                if self.current_process is proc:
                    self.current_process = None
            self._monitoring = False

        if block:
            _play()
        else:
            threading.Thread(target=_play, daemon=True).start()

    def _monitor_barge_in(self):
        sample_rate = 16000
        chunk = int(sample_rate * 0.1)
        try:
            with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16") as stream:
                while self.is_speaking() and self._monitoring:
                    data, _ = stream.read(chunk)
                    volume = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)) / 32768.0)
                    if volume > self.BARGE_IN_THRESHOLD:
                        print("\n[🔊 Unterbrochen durch Spracheingabe]")
                        self.stop()
                        break
        except Exception:
            pass
