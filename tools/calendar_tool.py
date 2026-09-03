import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config
from tools.base import BaseTool, ToolResult


class CalendarTool(BaseTool):
    name = "calendar"
    description = (
        "Lokaler Kalender: Termine auflisten, anlegen oder löschen. "
        "Datumsangaben immer als YYYY-MM-DD, Uhrzeiten als HH:MM (24h). "
        "Relative Angaben wie 'morgen' oder 'nächsten Freitag' vorher anhand des heutigen Datums umrechnen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "delete"],
                "description": "list = Termine anzeigen, add = Termin anlegen, delete = Termin löschen",
            },
            "days": {
                "type": "integer",
                "description": "Für list: Zeitraum in Tagen ab heute (Standard 7).",
            },
            "title": {"type": "string", "description": "Titel des Termins (add/delete)."},
            "date": {"type": "string", "description": "Datum YYYY-MM-DD (add/delete)."},
            "time": {"type": "string", "description": "Uhrzeit HH:MM (add, Standard 10:00)."},
            "description": {"type": "string", "description": "Optionale Notiz zum Termin (add)."},
        },
        "required": ["action"],
    }

    def __init__(self, calendar_file: Optional[Path] = None):
        self.calendar_file = calendar_file or (config.DATA_DIR / "calendar.json")
        self.calendar_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.calendar_file.exists():
            self.calendar_file.write_text("[]", encoding="utf-8")

    # ── Persistenz ───────────────────────────────────────────────────────────

    def _load_events(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.calendar_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_events(self, events: List[Dict[str, Any]]):
        self.calendar_file.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Aktionen ─────────────────────────────────────────────────────────────

    def list_events(self, days: int = 7) -> ToolResult:
        events = self._load_events()
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)

        upcoming = []
        for ev in events:
            try:
                ev_time = datetime.fromisoformat(ev["datetime"])
            except (KeyError, ValueError, TypeError):
                continue
            if start <= ev_time < end:
                upcoming.append(ev)
        upcoming.sort(key=lambda x: x["datetime"])

        if not upcoming:
            return ToolResult.ok(f"Keine Termine in den nächsten {days} Tagen.", data=[])

        lines = []
        for ev in upcoming:
            dt = datetime.fromisoformat(ev["datetime"])
            note = f" – {ev['description']}" if ev.get("description") else ""
            lines.append(f"- {dt.strftime('%a %d.%m.%Y %H:%M')}: {ev['title']}{note}")
        return ToolResult.ok(f"Termine der nächsten {days} Tage:\n" + "\n".join(lines), data=upcoming)

    def add_event(self, title: str, date_str: str, time_str: str = "10:00", description: str = "") -> ToolResult:
        try:
            full_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return ToolResult.fail("Ungültiges Format. Datum als YYYY-MM-DD, Uhrzeit als HH:MM angeben.")

        events = self._load_events()
        new_event = {
            "title": title,
            "datetime": full_dt.isoformat(),
            "description": description,
            "created_at": datetime.now().isoformat(),
        }
        events.append(new_event)
        self._save_events(events)
        return ToolResult.ok(
            f"Termin '{title}' am {full_dt.strftime('%d.%m.%Y um %H:%M')} Uhr eingetragen.", data=new_event
        )

    def delete_event(self, title: str, date_str: str = "") -> ToolResult:
        events = self._load_events()
        title_l = title.lower().strip()
        remaining, removed = [], []
        for ev in events:
            matches_title = title_l in ev.get("title", "").lower()
            matches_date = (not date_str) or ev.get("datetime", "").startswith(date_str)
            (removed if matches_title and matches_date else remaining).append(ev)
        if not removed:
            return ToolResult.fail(f"Kein Termin mit Titel '{title}' gefunden.")
        self._save_events(remaining)
        names = ", ".join(f"'{ev['title']}' ({ev['datetime'][:16]})" for ev in removed)
        return ToolResult.ok(f"{len(removed)} Termin(e) gelöscht: {names}", data=removed)

    def execute(self, action: str = "list", **kwargs) -> ToolResult:
        if action == "list":
            days = int(kwargs.get("days") or 7)
            return self.list_events(days=max(1, days))
        if action == "add":
            title = (kwargs.get("title") or "").strip()
            if not title:
                return ToolResult.fail("Bitte einen Titel für den Termin angeben.")
            date_str = kwargs.get("date") or datetime.now().strftime("%Y-%m-%d")
            time_str = kwargs.get("time") or "10:00"
            return self.add_event(title, date_str, time_str, kwargs.get("description") or "")
        if action == "delete":
            title = (kwargs.get("title") or "").strip()
            if not title:
                return ToolResult.fail("Bitte den Titel des zu löschenden Termins angeben.")
            return self.delete_event(title, kwargs.get("date") or "")
        return ToolResult.fail(f"Unbekannte Kalender-Aktion: {action}")
