"""Wake-Word-Erkennung mit openWakeWord (lokal, CPU, ONNX).

Das vortrainierte Modell "hey_jarvis" reagiert auf „Hey Jarvis“. Audio wird als
16 kHz, mono, int16 in Blöcken von 1280 Samples (80 ms) verarbeitet.
"""
from __future__ import annotations

import os

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # 80 ms, von openWakeWord erwartet


class WakeWordDetector:
    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5):
        import openwakeword
        from openwakeword.model import Model

        self.model_name = model_name
        self.threshold = threshold
        self._ensure_models(openwakeword, model_name)
        self.model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self.last_score: float = 0.0

    @staticmethod
    def _ensure_models(openwakeword, model_name: str):
        models_dir = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
        needed = ("melspectrogram.onnx", "embedding_model.onnx")
        have_word = any(f.startswith(model_name) and f.endswith(".onnx") for f in os.listdir(models_dir)) if os.path.isdir(models_dir) else False
        if not have_word or not all(os.path.exists(os.path.join(models_dir, n)) for n in needed):
            from openwakeword import utils

            utils.download_models(model_names=[model_name])

    def process(self, frame: np.ndarray) -> float:
        """Verarbeitet einen 80-ms-Block (int16) und gibt den höchsten Score zurück."""
        if frame.ndim > 1:
            frame = frame[:, 0]
        scores = self.model.predict(frame.astype(np.int16))
        self.last_score = max(scores.values()) if scores else 0.0
        return self.last_score

    def triggered(self, frame: np.ndarray) -> bool:
        return self.process(frame) >= self.threshold

    def reset(self):
        """Verwirft den internen Audio-Puffer (nach einer Erkennung aufrufen)."""
        try:
            self.model.reset()
        except Exception:
            pass


def score_wav(path: str, model_name: str = "hey_jarvis") -> float:
    """Hilfsfunktion für Tests: höchster Score über eine WAV-Datei (16 kHz mono int16)."""
    import wave

    det = WakeWordDetector(model_name, threshold=0.5)
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE and wf.getnchannels() == 1
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    best = 0.0
    for i in range(0, len(audio) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        best = max(best, det.process(audio[i : i + FRAME_SAMPLES]))
    return best
