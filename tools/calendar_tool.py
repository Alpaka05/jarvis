import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List
from config import config
from tools.base import BaseTool, ToolResult

class CalendarTool(BaseTool):
    name = "calendar"
    description = "Erstellt, listet und verwaltet Kalendereinträge und Termine."

    def __init__(self):
        self.calendar_file = config.DATA_DIR / "calendar.json"
        if not self.calendar_file.exists():
            self.calendar_file.write_text(json.dumps([], indent=2), encoding="utf-8")

    def _load_events(self) -> List[Dict[str, Any]]:
        try:
            content = self.calendar_file.read_text(encoding="utf-8")
            return json.loads(content)
        except Exception:
            return []

    def _save_events(self, events: List[Dict[str, Any]]):
        self.calendar_file.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_events(self, days: int = 7) -> ToolResult:
        events = self._load_events()
        now = datetime.now()
        end_time = now + timedelta(days=days)

        upcoming = []
        for ev in events:
            try:
                ev_time = datetime.fromisoformat(ev.get("datetime"))
                if now <= ev_time <= end_time:
                    upcoming.append(ev)
            except ValueError:
                continue

        upcoming.sort(key=lambda x: x.get("datetime"))
        if not upcoming:
            return ToolResult(
                success=True,
                output=f"Keine Termine für die nächsten {days} Tage gefunden.",
                data=[]
            )

        formatted = "\n".join(
            [f"- [{ev['datetime']}] {ev['title']} ({ev.get('description', 'Keine Beschreibung')})" for ev in upcoming]
        )
        return ToolResult(
            success=True,
            output=f"Anstehende Termine:\n{formatted}",
            data=upcoming
        )

    def add_event(self, title: str, date_str: str, time_str: str = "10:00", description: str = "") -> ToolResult:
        events = self._load_events()
        try:
            full_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").isoformat()
        except ValueError:
            return ToolResult(
                success=False,
                output="Ungültiges Datumsformat! Bitte nutze YYYY-MM-DD für Datum und HH:MM für Uhrzeit."
            )

        new_event = {
            "title": title,
            "datetime": full_dt,
            "description": description,
            "created_at": datetime.now().isoformat()
        }
        events.append(new_event)
        self._save_events(events)

        return ToolResult(
            success=True,
            output=f"Termin '{title}' am {date_str} um {time_str} Uhr erfolgreich eingetragen.",
            data=new_event
        )

    def execute(self, action: str = "list", **kwargs) -> ToolResult:
        if action == "list":
            days = kwargs.get("days", 7)
            return self.list_events(days=days)
        elif action == "add":
            title = kwargs.get("title", "")
            date_str = kwargs.get("date", datetime.now().strftime("%Y-%m-%d"))
            time_str = kwargs.get("time", "10:00")
            description = kwargs.get("description", "")
            if not title:
                return ToolResult(success=False, output="Bitte gib einen Titel für den Termin an.")
            return self.add_event(title, date_str, time_str, description)
        else:
            return ToolResult(success=False, output=f"Unbekannte Aktion für Kalender: {action}")
