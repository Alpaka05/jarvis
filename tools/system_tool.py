import subprocess
from datetime import datetime
from config import config
from tools.base import BaseTool, ToolResult

class SystemTool(BaseTool):
    name = "system"
    description = "Führt macOS Systemfunktionen aus (Uhrzeit, Benachrichtigungen, Sprachausgabe, Systeminfo)."

    def speak(self, text: str) -> ToolResult:
        if not config.TTS_ENABLED:
            return ToolResult(success=True, output=f"[TTS deaktiviert] {text}")
        try:
            voice = config.VOICE_NAME
            subprocess.run(["say", "-v", voice, text], check=False)
            return ToolResult(success=True, output=f"Sprachausgabe abgeschlossen: '{text}'")
        except Exception as e:
            return ToolResult(success=False, output=f"Fehler bei Sprachausgabe: {str(e)}")

    def notify(self, title: str, message: str) -> ToolResult:
        try:
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=False)
            return ToolResult(success=True, output=f"macOS Benachrichtigung gesendet: {title} - {message}")
        except Exception as e:
            return ToolResult(success=False, output=f"Fehler bei Benachrichtigung: {str(e)}")

    def get_info(self) -> ToolResult:
        now_str = datetime.now().strftime("%A, %d. %B %Y - %H:%M:%S Uhr")
        return ToolResult(
            success=True,
            output=f"Aktuelle Systemzeit: {now_str}"
        )

    def execute(self, action: str = "time", **kwargs) -> ToolResult:
        if action in ("time", "info"):
            return self.get_info()
        elif action == "speak":
            text = kwargs.get("text", "")
            if not text:
                return ToolResult(success=False, output="Bitte gib einen Text für die Sprachausgabe an ('text').")
            return self.speak(text)
        elif action == "notify":
            title = kwargs.get("title", "Jarvis")
            message = kwargs.get("message", kwargs.get("text", ""))
            return self.notify(title, message)
        else:
            return ToolResult(success=False, output=f"Unbekannte System-Aktion: {action}")
