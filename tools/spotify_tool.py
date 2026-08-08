import urllib.parse
import subprocess
from tools.base import BaseTool, ToolResult

class SpotifyTool(BaseTool):
    name = "spotify"
    description = "Steuert Spotify auf dem Mac (Play, Pause, nächster Song, Lautstärke, Song-Info, Suche)."

    def _run_applescript(self, script: str) -> str:
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
            return res.stdout.strip()
        except Exception:
            return ""

    def play_pause(self) -> ToolResult:
        script = 'tell application "Spotify" to playpause'
        self._run_applescript(script)
        status = self.get_current_track().output
        return ToolResult(success=True, output=f"Spotify Play/Pause umgeschaltet.\n{status}")

    def next_track(self) -> ToolResult:
        script = 'tell application "Spotify" to next track'
        self._run_applescript(script)
        status = self.get_current_track().output
        return ToolResult(success=True, output=f"Nächster Titel gestartet.\n{status}")

    def previous_track(self) -> ToolResult:
        script = 'tell application "Spotify" to previous track'
        self._run_applescript(script)
        status = self.get_current_track().output
        return ToolResult(success=True, output=f"Vorheriger Titel gestartet.\n{status}")

    def set_volume(self, volume: int) -> ToolResult:
        volume = max(0, min(100, volume))
        script = f'tell application "Spotify" to set sound volume to {volume}'
        self._run_applescript(script)
        return ToolResult(success=True, output=f"Spotify Lautstärke auf {volume}% gesetzt.")

    def get_current_track(self) -> ToolResult:
        script = '''
        if application "Spotify" is running then
            tell application "Spotify"
                if player state is playing or player state is paused then
                    set trackName to name of current track
                    set artistName to artist of current track
                    set stateStr to player state as string
                    return "🎵 " & trackName & " - " & artistName & " [" & stateStr & "]"
                else
                    return "Spotify ist geöffnet, spielt aber aktuell nichts ab."
                end if
            end tell
        else
            return "Spotify ist aktuell nicht geöffnet."
        end if
        '''
        res = self._run_applescript(script)
        return ToolResult(success=True, output=res or "Spotify Status konnte nicht abgerufen werden.")

    def play_search(self, query: str) -> ToolResult:
        encoded = urllib.parse.quote(query)
        script = f'''
        tell application "Spotify"
            activate
            open location "spotify:search:{encoded}"
        end tell
        delay 1
        tell application "System Events"
            keystroke return
        end tell
        '''
        self._run_applescript(script)
        return ToolResult(success=True, output=f"Suche gestartet und Wiedergabe für '{query}' auf Spotify gestartet.")

    def execute(self, action: str = "status", **kwargs) -> ToolResult:
        if action in ("status", "info", "current"):
            return self.get_current_track()
        elif action in ("play", "pause", "toggle", "playpause"):
            return self.play_pause()
        elif action in ("next", "weiter", "skip"):
            return self.next_track()
        elif action in ("prev", "previous", "zurück"):
            return self.previous_track()
        elif action in ("volume", "lautstärke"):
            vol = kwargs.get("volume", 50)
            return self.set_volume(int(vol))
        elif action in ("search", "search_play"):
            q = kwargs.get("query", kwargs.get("q", ""))
            if not q:
                return ToolResult(success=False, output="Bitte gib einen Suchbegriff/Songnamen an.")
            return self.play_search(q)
        else:
            return ToolResult(success=False, output=f"Unbekannte Spotify Aktion: {action}")
