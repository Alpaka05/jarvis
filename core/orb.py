"""Ereignisse für den Orb (schwebendes Overlay-Fenster).

Jarvis startet einen kleinen WebSocket-Server auf 127.0.0.1 (ORB_PORT, Standard 8765). Das
Orb-Fenster verbindet sich als Client und bekommt JSON-Nachrichten:

    {"type": "state", "state": "idle" | "wake" | "listening" | "thinking" | "speaking"}
    {"type": "level", "source": "mic" | "tts", "value": 0.0–1.0}      höchstens ~30 pro Sekunde
    {"type": "tool", "name": "spotify"}                                 kurz einblenden, Zustand bleibt
    {"type": "error", "message": "Nicht verstanden"}                    kurz aufblitzen, Zustand bleibt
    {"type": "transcript", "role": "user" | "assistant", "text": "…"}  für Untertitel

Verbindungen brauchen das Token aus ORB_TOKEN bzw. data/orb.token (?token=…) – sonst könnte
jede Webseite im Browser per ws://127.0.0.1 mitlesen, was gesagt und geantwortet wird.

Beim Verbinden bekommt der Client sofort den aktuellen Zustand. „idle“ wird erst gesendet,
wenn nicht innerhalb von IDLE_DELAY ein anderer Zustand folgt – so flackert der Orb nicht kurz
in den Ruhezustand, wenn z.B. auf die Sprachausgabe direkt das Nachfrage-Fenster folgt.

Ohne gestarteten Server oder ohne verbundenes Fenster kosten die Aufrufe praktisch nichts.
Aufrufer blockieren nie, Fehler werden nie nach außen gereicht.
"""
from __future__ import annotations

import atexit
import hmac
import json
import logging
import math
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import numpy as np

log = logging.getLogger(__name__)

IDLE_DELAY = 0.3
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOKEN_FILE = DATA_DIR / "orb.token"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def load_or_create_token(path: Optional[Path]) -> str:
    """Token für den Orb-Server. Mit Datei bleibt es über Neustarts gleich, damit ein selbst
    gestarteter Orb sich wieder verbinden kann; die Datei ist nur für den Nutzer lesbar."""
    if path is None:
        return secrets.token_urlsafe(24)
    try:
        token = path.read_text(encoding="ascii").strip()
        if _TOKEN_RE.match(token):
            return token
    except (OSError, UnicodeDecodeError):
        pass
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(token)
    return token


def normalize_rms(value: float, floor_db: float = -50.0, ceil_db: float = -10.0) -> float:
    """RMS (0–1, bezogen auf Vollaussteuerung) → Pegel 0–1 auf einer dB-Skala."""
    if value <= 0:
        return 0.0
    db = 20.0 * math.log10(value)
    return min(1.0, max(0.0, (db - floor_db) / (ceil_db - floor_db)))


class OrbEvents:
    def __init__(self, level_hz: float = 30.0, idle_delay: float = IDLE_DELAY, token_file: Optional[Path] = None):
        self.level_interval = 1.0 / level_hz
        self.token_file = token_file
        self.token = ""
        self.idle_delay = idle_delay
        self.enabled = False
        self.address = ""
        self._server = None
        self._clients: set = set()
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue(maxsize=256)
        self._state = "idle"
        self._idle_at: Optional[float] = None
        self._last_level = 0.0

    # ── Server ───────────────────────────────────────────────────────────────

    def start(self, host: str = "127.0.0.1", port: int = 8765) -> Optional[str]:
        """Startet den Server im Hintergrund. None bei Erfolg, sonst eine Fehlermeldung."""
        if self.enabled:
            return None
        try:
            from websockets.sync.server import serve

            self.token = self.token or load_or_create_token(self.token_file)
            self._server = serve(self._handle, host, port, process_request=self._check_token)
        except Exception as e:  # Paket fehlt, Port belegt, …
            log.warning("Orb-Server auf Port %s nicht gestartet: %s", port, e)
            return f"Orb-Server nicht gestartet: {e}"
        self.address = f"ws://{host}:{self._server.socket.getsockname()[1]}"
        self.enabled = True
        threading.Thread(target=self._server.serve_forever, name="orb-server", daemon=True).start()
        threading.Thread(target=self._send_loop, name="orb-sender", daemon=True).start()
        return None

    def stop(self):
        self.enabled = False
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass

    @property
    def active(self) -> bool:
        """True, wenn mindestens ein Orb-Fenster verbunden ist."""
        return self.enabled and bool(self._clients)

    def _check_token(self, connection, request):
        """Verbindungen ohne passendes Token abweisen (None = annehmen)."""
        given = parse_qs(urlsplit(request.path).query).get("token", [""])[0]
        if hmac.compare_digest(given.encode(), self.token.encode()):
            return None
        return connection.respond(403, "Falsches oder fehlendes Token.\n")

    def _handle(self, ws):
        with self._lock:
            self._clients.add(ws)
            hello = {"type": "state", "state": self._state}
        self._put(hello, target=ws)
        try:
            for _ in ws:  # Nachrichten des Orbs werden (noch) nicht ausgewertet
                pass
        except Exception:
            pass
        finally:
            with self._lock:
                self._clients.discard(ws)

    # ── Ereignisse ───────────────────────────────────────────────────────────

    def state(self, name: str):
        with self._lock:
            if not self.enabled:  # kein Versand-Thread, der „idle“ verzögert nachreichen könnte
                self._state, self._idle_at = name, None
                return
            if name == "idle":
                if self._state != "idle" and self._idle_at is None:
                    self._idle_at = time.monotonic() + self.idle_delay
                return
            self._idle_at = None
            self._state = name
        self._put({"type": "state", "state": name})

    def level(self, rms: float, source: str = "mic"):
        if not self.active:
            return
        now = time.monotonic()
        if now - self._last_level < self.level_interval:
            return
        self._last_level = now
        self._put({"type": "level", "source": source, "value": round(normalize_rms(rms), 3)})

    def pcm_level(self, pcm: np.ndarray, source: str = "mic"):
        """Wie level(), aber direkt aus int16-Samples; rechnet nur, wenn ein Orb zuhört."""
        if not self.active or pcm.size == 0:
            return
        data = pcm.astype(np.float32)
        self.level(float(np.sqrt(np.mean(data**2)) / 32768.0), source)

    def tool(self, name: str):
        self._put({"type": "tool", "name": name})

    def error(self, message: str = ""):
        self._put({"type": "error", "message": message})

    def transcript(self, role: str, text: str):
        self._put({"type": "transcript", "role": role, "text": text})

    # ── Versand (eigener Thread, damit Aufrufer nie blockieren) ──────────────

    def _put(self, msg: Dict[str, Any], target=None):
        if not self.enabled or (target is None and not self._clients):
            return
        try:
            self._queue.put_nowait((msg, target))
        except queue.Full:
            pass  # Orb hängt – lieber Ereignisse verwerfen als Jarvis aufhalten

    def _send_loop(self):
        while self.enabled:
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                item = None
            self._flush_idle()
            if item is not None:
                self._deliver(*item)

    def _flush_idle(self):
        with self._lock:
            due = self._idle_at is not None and time.monotonic() >= self._idle_at
            if due:
                self._idle_at = None
                self._state = "idle"
        if due:
            self._deliver({"type": "state", "state": "idle"}, None)

    def _deliver(self, msg: Dict[str, Any], target):
        data = json.dumps(msg, ensure_ascii=False)
        with self._lock:
            clients = [target] if target is not None else list(self._clients)
        for ws in clients:
            try:
                ws.send(data)
            except Exception:
                pass  # Verbindung weg – der Handler räumt auf


# ── Orb-Fenster (orb/, Tauri) als Kindprozess ────────────────────────────────

ORB_TARGET_DIR = Path(__file__).resolve().parent.parent / "orb" / "src-tauri" / "target"
ORB_LOG = DATA_DIR / "orb.log"
STARTUP_CHECK_SECONDS = 0.8  # beendet sich das Fenster so schnell, ist der Start gescheitert


def find_window_binary(target_dir: Optional[Path] = None) -> Optional[Path]:
    """Gebautes Orb-Fenster: Release-Build bevorzugt, sonst Debug-Build (cargo run)."""
    target_dir = target_dir or ORB_TARGET_DIR
    name = "jarvis-orb.exe" if sys.platform.startswith("win") else "jarvis-orb"
    for profile in ("release", "debug"):
        path = target_dir / profile / name
        if path.is_file():
            return path
    return None


class OrbWindow:
    """Startet das Orb-Fenster auf Wunsch (Befehl 'orb') und schließt es mit Jarvis."""

    def __init__(self, events: OrbEvents, log_path: Path = ORB_LOG):
        self.events = events
        self.log_path = log_path
        self._proc: Optional[subprocess.Popen] = None
        atexit.register(self.close)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def open(self, port: int = 8765, binary: Optional[Path] = None) -> Tuple[bool, str]:
        """Startet Server (falls nötig) und Fenster. Gibt (Erfolg, Meldung) zurück."""
        if self.running or self.events.active:
            return True, "Der Orb läuft schon."
        binary = binary or find_window_binary()
        if binary is None:
            return False, "Orb-Fenster noch nicht gebaut: cd orb/src-tauri && cargo build --release"
        if not self.events.enabled:
            error = self.events.start(port=port)
            if error and port:  # Port belegt → freien nehmen, das Fenster bekommt ihn mitgeteilt
                error = self.events.start(port=0)
            if error:
                return False, error
        actual_port = self.events.address.rsplit(":", 1)[-1]
        env = {**os.environ, "ORB_PORT": actual_port, "ORB_TOKEN": self.events.token, "JARVIS_ORB_CHILD": "1"}
        # Eigene Prozessgruppe: Strg+C im Terminal (Frage/Sprachmodus abbrechen) trifft sonst auch den Orb
        if sys.platform.startswith("win"):
            detach: Dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        else:
            detach = {"start_new_session": True}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "wb") as log_file:
                # stdin bleibt offen und ist die Lebensader: endet Jarvis – auch per Absturz oder
                # kill -9 –, sieht der Orb EOF und beendet sich selbst.
                self._proc = subprocess.Popen(
                    [str(binary)], env=env, stdin=subprocess.PIPE, stdout=log_file, stderr=subprocess.STDOUT, **detach
                )
        except OSError as e:
            return False, f"Orb-Fenster konnte nicht gestartet werden: {e}"
        try:
            code = self._proc.wait(timeout=STARTUP_CHECK_SECONDS)
        except subprocess.TimeoutExpired:
            log.info("Orb-Fenster gestartet (pid %s, Port %s)", self._proc.pid, actual_port)
            return True, "Orb gestartet."
        self._proc = None
        log.warning("Orb-Fenster beendete sich sofort (Code %s), siehe %s", code, self.log_path)
        return False, f"Orb-Fenster hat sich sofort beendet (Code {code}). {self._log_tail()}".strip()

    def _log_tail(self, lines: int = 3) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not text:
            return ""
        tail = " | ".join(text.splitlines()[-lines:])
        return f"Ausgabe: {tail[-300:]} (vollständig in {self.log_path})"

    def shut(self) -> Tuple[bool, str]:
        """Schließt das Fenster auf Wunsch. Gibt (Erfolg, Meldung) zurück."""
        if self.running:
            self.close()
            return True, "Orb ausgeschaltet."
        if self.events.active:
            return False, "Der Orb wurde nicht von Jarvis gestartet – bitte über das Tray-Symbol beenden."
        return True, "Der Orb ist schon aus."

    def close(self):
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            # Erst die Lebensader kappen: der Orb beendet sich selbst sauber (unter Windows
            # bliebe nach terminate() sonst ein Geister-Symbol im Tray).
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=2)
            return
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


bus = OrbEvents(token_file=TOKEN_FILE)
window = OrbWindow(bus)

start = bus.start
stop = bus.stop
state = bus.state
level = bus.level
pcm_level = bus.pcm_level
tool = bus.tool
error = bus.error
transcript = bus.transcript
open_window = window.open
close_window = window.shut
