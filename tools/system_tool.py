from datetime import datetime

from config import config
from core import platform_utils
from tools.base import BaseTool, Policy, Risk, ToolResult

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


class SystemTool(BaseTool):
    name = "system"
    description = (
        "Systemfunktionen auf diesem Rechner: aktuelle Uhrzeit/Datum abfragen, "
        "Desktop-Benachrichtigung anzeigen, URL im Browser öffnen, Text laut vorlesen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["time", "notify", "open_url", "speak"],
                "description": "time = Datum/Uhrzeit, notify = Benachrichtigung, open_url = Browser öffnen, speak = vorlesen",
            },
            "title": {"type": "string", "description": "Titel der Benachrichtigung (notify)."},
            "message": {"type": "string", "description": "Text der Benachrichtigung (notify)."},
            "url": {"type": "string", "description": "Zu öffnende URL (open_url)."},
            "text": {"type": "string", "description": "Vorzulesender Text (speak)."},
        },
        "required": ["action"],
    }

    def policy(self, action: str = "time", **kwargs) -> Policy:
        if action == "open_url":
            url = (kwargs.get("url") or "").strip()
            return Policy(Risk.GUARDED, f"Im Browser öffnen: {url}", url=url)
        return Policy()

    def get_time(self) -> ToolResult:
        now = datetime.now()
        text = f"{WEEKDAYS[now.weekday()]}, {now.strftime('%d.%m.%Y, %H:%M')} Uhr"
        return ToolResult.ok(f"Aktuelle Zeit: {text} ({config.platform_name})", data=now.isoformat())

    def notify(self, title: str, message: str) -> ToolResult:
        if platform_utils.notify(title, message):
            return ToolResult.ok(f"Benachrichtigung angezeigt: {title} – {message}")
        return ToolResult.fail("Benachrichtigung konnte auf diesem System nicht angezeigt werden.")

    def open_url(self, url: str) -> ToolResult:
        if platform_utils.open_url(url):
            return ToolResult.ok(f"{url} im Browser geöffnet.")
        return ToolResult.fail(f"Konnte {url} nicht öffnen.")

    def speak(self, text: str) -> ToolResult:
        if not config.TTS_ENABLED:
            return ToolResult.ok(f"[Sprachausgabe deaktiviert] {text}")
        if platform_utils.speak_blocking(text):
            return ToolResult.ok(f"Vorgelesen: '{text}'")
        return ToolResult.fail("Sprachausgabe ist auf diesem System nicht verfügbar.")

    def execute(self, action: str = "time", **kwargs) -> ToolResult:
        if action in ("time", "info", "date"):
            return self.get_time()
        if action == "notify":
            message = kwargs.get("message") or kwargs.get("text") or ""
            if not message:
                return ToolResult.fail("Bitte einen Text für die Benachrichtigung angeben ('message').")
            return self.notify(kwargs.get("title") or "Jarvis", message)
        if action == "open_url":
            url = (kwargs.get("url") or "").strip()
            if not url:
                return ToolResult.fail("Bitte eine URL angeben ('url').")
            return self.open_url(url)
        if action == "speak":
            text = (kwargs.get("text") or "").strip()
            if not text:
                return ToolResult.fail("Bitte einen Text angeben ('text').")
            return self.speak(text)
        return ToolResult.fail(f"Unbekannte System-Aktion: {action}")
