"""Logdateien unter data/: jarvis.log für Jarvis selbst, native.log für C-Bibliotheken.

Im Terminal bleibt es ruhig; was dort nur als kurze Meldung erscheint (oder früher still
verschluckt wurde), steht hier mit Details und Traceback.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "jarvis.log"
NATIVE_LOG_NAME = "native.log"
# Bibliotheken, die auf INFO jede HTTP-Anfrage protokollieren
NOISY = ("httpx", "httpcore", "urllib3", "websockets", "google_genai", "anthropic", "openai", "asyncio", "edge_tts")


def setup_logging(data_dir: Path, level: int = logging.INFO) -> Path:
    """Richtet das Logging in data/jarvis.log ein (rotierend, 3 × 1 MB). Gibt den Pfad zurück."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOG_NAME
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    for old in [h for h in root.handlers if isinstance(h, RotatingFileHandler)]:
        root.removeHandler(old)
    root.addHandler(handler)
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    return path


def rotate_native_log(data_dir: Path) -> Path:
    """native.log der letzten Sitzung als native.log.1 aufheben, damit ein Absturz nachlesbar bleibt."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / NATIVE_LOG_NAME
    if path.exists():
        os.replace(path, data_dir / (NATIVE_LOG_NAME + ".1"))
    return path
