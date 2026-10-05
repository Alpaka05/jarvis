"""Screenshot des Bildschirms, auf dem gerade der Mauszeiger steht.

Für das screen-Tool: Jarvis schaut nur hin, wenn der Nutzer danach fragt. Das Bild wird verkleinert
(längste Seite MAX_SIDE) und als JPEG übergeben – genug für Text und Fehlermeldungen, aber deutlich
weniger Tokens als ein Retina-Screenshot in voller Größe.

macOS verlangt dafür die Berechtigung „Bildschirmaufnahme“ für die App, in der Jarvis läuft
(Terminal, iTerm …). Ohne sie liefert macOS nur Hintergrundbild und Menüleiste – deshalb wird
die Berechtigung vorher geprüft.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from config import IS_MAC, IS_WINDOWS

log = logging.getLogger(__name__)

MAX_SIDE = 1568  # längste Bildseite in Pixeln
JPEG_QUALITY = 80


class ScreenError(RuntimeError):
    """Screenshot nicht möglich (fehlende Berechtigung, kein Bildschirm, Paket fehlt)."""


@dataclass
class Screenshot:
    data: bytes
    mime: str
    width: int
    height: int
    monitor: int  # 1 = erster Bildschirm (Nummerierung wie bei mss)
    monitors: int  # Anzahl Bildschirme


# ── Mauszeiger ───────────────────────────────────────────────────────────────


def cursor_position() -> Optional[Tuple[float, float]]:
    """Position des Mauszeigers in globalen Bildschirmkoordinaten (wie mss sie verwendet), sonst None."""
    try:
        import ctypes

        if IS_MAC:
            cg = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")

            class CGPoint(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            cg.CGEventCreate.restype = ctypes.c_void_p
            cg.CGEventCreate.argtypes = [ctypes.c_void_p]
            cg.CGEventGetLocation.restype = CGPoint
            cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
            cg.CFRelease.argtypes = [ctypes.c_void_p]
            event = cg.CGEventCreate(None)
            if not event:
                return None
            point = cg.CGEventGetLocation(event)
            cg.CFRelease(event)
            return point.x, point.y
        if IS_WINDOWS:

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            point = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):  # type: ignore[attr-defined]
                return float(point.x), float(point.y)
    except Exception:
        log.debug("Mausposition nicht ermittelbar", exc_info=True)
    return None


def pick_monitor(monitors: List[dict], point: Optional[Tuple[float, float]]) -> int:
    """Index des Bildschirms (in mss.monitors, 0 = alle zusammen), der den Punkt enthält; sonst 1."""
    if point is not None:
        x, y = point
        for i, m in enumerate(monitors[1:], start=1):
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                return i
    return 1


# ── Berechtigung (macOS) ─────────────────────────────────────────────────────


def has_permission() -> bool:
    """macOS: darf diese App den Bildschirm aufnehmen? Andere Systeme: immer True."""
    if not IS_MAC:
        return True
    try:
        import ctypes

        cg = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        log.debug("Bildschirmaufnahme-Berechtigung nicht prüfbar", exc_info=True)
        return True  # nicht prüfbar (altes macOS) → einfach versuchen


def request_permission() -> None:
    """macOS: Systemdialog für die Berechtigung anstoßen (wirkt erst nach Neustart der App)."""
    if not IS_MAC:
        return
    try:
        import ctypes

        cg = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        cg.CGRequestScreenCaptureAccess()
    except Exception:
        log.debug("Berechtigungsdialog nicht anzeigbar", exc_info=True)


PERMISSION_HELP = (
    "Jarvis darf den Bildschirm noch nicht sehen. In den Systemeinstellungen unter „Datenschutz & "
    "Sicherheit → Bildschirm- und Systemaudioaufnahme“ die App freigeben, in der Jarvis läuft "
    "(z.B. Terminal), und diese App danach neu starten."
)


# ── Aufnahme ─────────────────────────────────────────────────────────────────


def encode(rgb: bytes, size: Tuple[int, int], max_side: int = MAX_SIDE) -> Tuple[bytes, int, int]:
    """RGB-Rohdaten → verkleinertes JPEG. Gibt (Bytes, Breite, Höhe) zurück."""
    from PIL import Image

    image = Image.frombytes("RGB", size, rgb)
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue(), image.width, image.height


def capture(max_side: int = MAX_SIDE) -> Screenshot:
    """Screenshot des Bildschirms unter dem Mauszeiger. ScreenError, wenn das nicht geht."""
    if not has_permission():
        request_permission()
        raise ScreenError(PERMISSION_HELP)
    try:
        import mss
    except ImportError as e:
        raise ScreenError("Paket 'mss' fehlt (uv sync).") from e
    try:
        with mss.mss() as sct:  # unter Windows setzt mss die DPI-Awareness – vor der Mausposition
            monitors = sct.monitors
            if len(monitors) < 2:
                raise ScreenError("Kein Bildschirm gefunden.")
            index = pick_monitor(monitors, cursor_position())
            shot = sct.grab(monitors[index])
            data, width, height = encode(shot.rgb, shot.size, max_side)
    except ScreenError:
        raise
    except Exception as e:
        log.warning("Screenshot fehlgeschlagen", exc_info=True)
        raise ScreenError(f"Screenshot fehlgeschlagen: {e}") from e
    return Screenshot(data, "image/jpeg", width, height, index, len(monitors) - 1)
