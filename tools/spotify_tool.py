"""Spotify-Steuerung.

Bevorzugt die Spotify Web API (spotipy) – funktioniert auf Windows, macOS und
Linux, benötigt aber eine Spotify-App (Client-ID/Secret) und Spotify Premium.
Ohne Web-API-Konfiguration wird auf dem Mac AppleScript als Fallback genutzt.
"""
from __future__ import annotations

import subprocess
import urllib.parse
from typing import Any, Dict, Optional

from config import config, IS_MAC
from tools.base import BaseTool, ToolResult

SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


class SpotifyTool(BaseTool):
    name = "spotify"
    description = (
        "Spotify steuern: aktuellen Titel anzeigen, Wiedergabe starten/pausieren, vor/zurück, "
        "Lautstärke setzen oder nach einem Song/Künstler/Album/Playlist suchen und abspielen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "play", "pause", "next", "previous", "volume", "play_search"],
                "description": "Gewünschte Aktion.",
            },
            "query": {"type": "string", "description": "Suchbegriff für play_search, z.B. 'Bohemian Rhapsody Queen'."},
            "search_type": {
                "type": "string",
                "enum": ["track", "artist", "album", "playlist"],
                "description": "Art des Suchtreffers für play_search (Standard track).",
            },
            "volume": {"type": "integer", "description": "Lautstärke 0–100 für action=volume."},
        },
        "required": ["action"],
    }

    def __init__(self):
        self._sp = None  # spotipy client, lazy

    # ── Backend-Auswahl ──────────────────────────────────────────────────────

    @property
    def web_api_configured(self) -> bool:
        return bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET)

    def backend(self) -> str:
        if self.web_api_configured:
            return "web"
        if IS_MAC:
            return "applescript"
        return "none"

    def _client(self):
        if self._sp is not None:
            return self._sp
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
        from spotipy.cache_handler import CacheFileHandler

        auth = SpotifyOAuth(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
            redirect_uri=config.SPOTIFY_REDIRECT_URI,
            scope=SCOPES,
            cache_handler=CacheFileHandler(cache_path=str(config.DATA_DIR / ".spotify_cache")),
            open_browser=True,
        )
        self._sp = spotipy.Spotify(auth_manager=auth, requests_timeout=10)
        return self._sp

    # ── Web API ──────────────────────────────────────────────────────────────

    def _device_id(self, sp) -> Optional[str]:
        """Aktives Gerät oder erstes verfügbares Gerät."""
        try:
            devices = sp.devices().get("devices", [])
        except Exception:
            return None
        for d in devices:
            if d.get("is_active"):
                return d.get("id")
        return devices[0].get("id") if devices else None

    def _web_status(self, sp) -> ToolResult:
        pb = sp.current_playback()
        if not pb or not pb.get("item"):
            return ToolResult.ok("Spotify spielt gerade nichts (kein aktives Gerät oder Wiedergabe gestoppt).")
        item = pb["item"]
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        state = "spielt" if pb.get("is_playing") else "pausiert"
        device = pb.get("device", {}).get("name", "?")
        vol = pb.get("device", {}).get("volume_percent")
        return ToolResult.ok(
            f"🎵 {item['name']} – {artists} [{state}] auf '{device}'" + (f", Lautstärke {vol}%" if vol is not None else ""),
            data={"track": item["name"], "artists": artists, "is_playing": pb.get("is_playing"), "device": device},
        )

    def _web(self, action: str, kw: Dict[str, Any]) -> ToolResult:
        try:
            import spotipy

            sp = self._client()
            dev = self._device_id(sp)
            if action == "status":
                return self._web_status(sp)
            if action == "play":
                sp.start_playback(device_id=dev)
            elif action == "pause":
                sp.pause_playback(device_id=dev)
            elif action == "next":
                sp.next_track(device_id=dev)
            elif action == "previous":
                sp.previous_track(device_id=dev)
            elif action == "volume":
                vol = max(0, min(100, int(kw.get("volume", 50))))
                sp.volume(vol, device_id=dev)
                return ToolResult.ok(f"Spotify Lautstärke auf {vol}% gesetzt.")
            elif action == "play_search":
                query = (kw.get("query") or "").strip()
                if not query:
                    return ToolResult.fail("Bitte einen Suchbegriff angeben ('query').")
                stype = kw.get("search_type") or "track"
                res = sp.search(q=query, type=stype, limit=1)
                items = res.get(f"{stype}s", {}).get("items", [])
                if not items:
                    return ToolResult.fail(f"Nichts gefunden für '{query}'.")
                hit = items[0]
                if stype == "track":
                    sp.start_playback(device_id=dev, uris=[hit["uri"]])
                else:
                    sp.start_playback(device_id=dev, context_uri=hit["uri"])
                label = hit["name"] + (f" – {', '.join(a['name'] for a in hit.get('artists', []))}" if hit.get("artists") else "")
                return ToolResult.ok(f"Spiele jetzt: {label}")
            else:
                return ToolResult.fail(f"Unbekannte Spotify-Aktion: {action}")

            status = self._web_status(sp)
            return ToolResult.ok(f"Aktion '{action}' ausgeführt. {status.output}")
        except Exception as e:  # spotipy.SpotifyException u.a.
            msg = str(e)
            if "NO_ACTIVE_DEVICE" in msg or "No active device" in msg or "404" in msg:
                return ToolResult.fail("Kein aktives Spotify-Gerät. Bitte Spotify auf einem Gerät öffnen und kurz abspielen.")
            if "PREMIUM_REQUIRED" in msg or "Premium" in msg:
                return ToolResult.fail("Wiedergabesteuerung über die Web API erfordert Spotify Premium.")
            return ToolResult.fail(f"Spotify-Fehler: {msg}")

    # ── AppleScript (macOS-Fallback) ─────────────────────────────────────────

    @staticmethod
    def _osa(script: str) -> str:
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False, timeout=15)
            return res.stdout.strip()
        except Exception:
            return ""

    def _apple_status(self) -> ToolResult:
        script = '''
        if application "Spotify" is running then
            tell application "Spotify"
                if player state is playing or player state is paused then
                    return "🎵 " & (name of current track) & " – " & (artist of current track) & " [" & (player state as string) & "]"
                else
                    return "Spotify ist geöffnet, spielt aber nichts."
                end if
            end tell
        else
            return "Spotify ist nicht geöffnet."
        end if'''
        return ToolResult.ok(self._osa(script) or "Spotify-Status nicht abrufbar.")

    def _apple(self, action: str, kw: Dict[str, Any]) -> ToolResult:
        if action == "status":
            return self._apple_status()
        cmds = {
            "play": 'tell application "Spotify" to play',
            "pause": 'tell application "Spotify" to pause',
            "next": 'tell application "Spotify" to next track',
            "previous": 'tell application "Spotify" to previous track',
        }
        if action in cmds:
            self._osa(cmds[action])
            return ToolResult.ok(f"Aktion '{action}' ausgeführt. {self._apple_status().output}")
        if action == "volume":
            vol = max(0, min(100, int(kw.get("volume", 50))))
            self._osa(f'tell application "Spotify" to set sound volume to {vol}')
            return ToolResult.ok(f"Spotify Lautstärke auf {vol}% gesetzt.")
        if action == "play_search":
            query = (kw.get("query") or "").strip()
            if not query:
                return ToolResult.fail("Bitte einen Suchbegriff angeben ('query').")
            encoded = urllib.parse.quote(query)
            self._osa(f'''
            tell application "Spotify"
                activate
                open location "spotify:search:{encoded}"
            end tell
            delay 1
            tell application "System Events" to keystroke return''')
            return ToolResult.ok(f"Spotify-Suche für '{query}' geöffnet und Wiedergabe gestartet.")
        return ToolResult.fail(f"Unbekannte Spotify-Aktion: {action}")

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def execute(self, action: str = "status", **kwargs) -> ToolResult:
        be = self.backend()
        if be == "web":
            return self._web(action, kwargs)
        if be == "applescript":
            return self._apple(action, kwargs)
        return ToolResult.fail(
            "Spotify ist auf diesem System nicht eingerichtet. Bitte SPOTIFY_CLIENT_ID und "
            "SPOTIFY_CLIENT_SECRET in der .env eintragen (App unter developer.spotify.com anlegen)."
        )
