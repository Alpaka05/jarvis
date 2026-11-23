"""Jarvis bei der Anmeldung starten – mit Oberfläche, ohne dass man ein Terminal öffnen muss.

    uv run python scripts/autostart.py an       # einrichten
    uv run python scripts/autostart.py aus      # wieder entfernen
    uv run python scripts/autostart.py status

Soll Jarvis dann gleich auf „Hey Jarvis“ lauschen: VOICE_MODE_ON_START=true in der .env.

macOS:   ein LaunchAgent (~/Library/LaunchAgents/dev.jarvis.plist), der launch/jarvis-ui.command
         im Terminal öffnet – im Hintergrund, aber im Terminal, weil macOS Mikrofon- und
         Bildschirmzugriff an das startende Programm bindet (das Terminal hat sie schon).
Windows: eine Datei im Autostart-Ordner, die launch\\jarvis-ui.bat minimiert startet.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
LABEL = "dev.jarvis"


def mac_plist_path(home: Optional[Path] = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def mac_plist(root: Path = ROOT) -> bytes:
    """LaunchAgent: öffnet das Startskript im Terminal, ohne es in den Vordergrund zu holen."""
    command = ["/usr/bin/open", "-g", "-a", "Terminal", str(root / "launch" / "jarvis-ui.command")]
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": command,
            "RunAtLoad": True,
            "ProcessType": "Interactive",
            "WorkingDirectory": str(root),
        }
    )


def windows_startup_path(appdata: Optional[str] = None) -> Path:
    base = Path(appdata or os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Jarvis.bat"


def windows_startup_script(root: Path = ROOT) -> str:
    """Startet das Startskript minimiert; die Konsole bleibt als Protokoll in der Taskleiste."""
    target = root / "launch" / "jarvis-ui.bat"
    return f'@echo off\r\nstart "Jarvis" /min "{target}"\r\n'


def install(platform: str = sys.platform) -> Tuple[bool, str]:
    if platform == "darwin":
        path = mac_plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(mac_plist())
        return True, f"Eingerichtet: {path}\nJarvis startet ab der nächsten Anmeldung mit Oberfläche."
    if platform.startswith("win"):
        path = windows_startup_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(windows_startup_script(), encoding="utf-8", newline="")
        return True, f"Eingerichtet: {path}\nJarvis startet ab der nächsten Anmeldung mit Oberfläche."
    return False, "Autostart wird nur unter macOS und Windows eingerichtet."


def remove(platform: str = sys.platform) -> Tuple[bool, str]:
    path = _path(platform)
    if path is None:
        return False, "Autostart wird nur unter macOS und Windows eingerichtet."
    if not path.exists():
        return True, "Autostart war nicht eingerichtet."
    if platform == "darwin":  # falls er in dieser Sitzung schon geladen wurde
        subprocess.run(["launchctl", "bootout", f"gui/{getattr(os, 'getuid', lambda: 0)()}/{LABEL}"], capture_output=True, check=False)
    path.unlink()
    return True, f"Entfernt: {path}"


def status(platform: str = sys.platform) -> Tuple[bool, str]:
    path = _path(platform)
    if path is None:
        return False, "Autostart wird nur unter macOS und Windows eingerichtet."
    return (True, f"Autostart ist eingerichtet ({path}).") if path.exists() else (True, "Autostart ist nicht eingerichtet.")


def _path(platform: str) -> Optional[Path]:
    if platform == "darwin":
        return mac_plist_path()
    if platform.startswith("win"):
        return windows_startup_path()
    return None


def main(argv: List[str]) -> int:
    command = (argv[0].lower() if argv else "status")
    if command in ("an", "on", "install"):
        ok, message = install()
    elif command in ("aus", "off", "remove"):
        ok, message = remove()
    elif command == "status":
        ok, message = status()
    else:
        ok, message = False, __doc__.strip()
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
