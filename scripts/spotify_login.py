"""Spotify einmalig mit Jarvis verknüpfen (OAuth).

    uv run python scripts/spotify_login.py

Öffnet den Browser zur Zustimmung und speichert das Token in data/.spotify_cache.
Danach kann Jarvis Wiedergabe, Suche und Lautstärke steuern (Spotify Premium nötig).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.spotify_tool import SpotifyTool  # noqa: E402

if __name__ == "__main__":
    tool = SpotifyTool()
    ok, message = tool.login()
    print(message)
    sys.exit(0 if ok else 1)
