
from core.memory import CATEGORIES, MemoryStore
from tools.base import BaseTool, Policy, Risk, ToolResult


class MemoryTool(BaseTool):
    name = "memory"
    description = (
        "Langzeitgedächtnis über Sitzungen hinweg. remember speichert dauerhafte Fakten über den Nutzer "
        "und seine Umgebung (Name, Vorlieben, Personen, Geräte-IDs, Gewohnheiten). "
        "recall durchsucht gespeicherte Fakten, recall_conversations durchsucht frühere Gespräche "
        "(z.B. 'Worüber haben wir gestern gesprochen?'). update/forget pflegen veraltete Einträge."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["remember", "recall", "update", "forget", "list", "recall_conversations"],
                "description": "Gewünschte Aktion.",
            },
            "content": {
                "type": "string",
                "description": "Zu speichernder Fakt als kurzer, eigenständiger Satz (remember/update).",
            },
            "category": {
                "type": "string",
                "enum": list(CATEGORIES),
                "description": "Kategorie des Fakts (remember): person, preference, device, fact, routine, other.",
            },
            "id": {"type": "integer", "description": "ID des Fakts (update/forget), siehe [#id] im Gedächtnis."},
            "query": {"type": "string", "description": "Suchbegriffe (recall / recall_conversations)."},
            "days": {"type": "integer", "description": "Zeitraum in Tagen für recall_conversations (Standard 30)."},
        },
        "required": ["action"],
    }

    def __init__(self, store: MemoryStore):
        self.store = store

    def policy(self, action: str = "list", **kwargs) -> Policy:
        # Gespeicherte Fakten stehen in jedem künftigen System-Prompt: eine Webseite oder Mail, die
        # Jarvis etwas „merken“ lässt, wirkte dauerhaft weiter
        content = (kwargs.get("content") or "").strip()
        if action == "remember":
            return Policy(Risk.GUARDED, f"Im Gedächtnis speichern: {content}")
        if action == "update":
            return Policy(Risk.GUARDED, f"Gespeicherten Fakt #{kwargs.get('id')} ändern in: {content}")
        if action == "forget":
            return Policy(Risk.GUARDED, f"Gespeicherten Fakt #{kwargs.get('id')} löschen")
        return Policy()

    @staticmethod
    def _fmt_fact(f: dict) -> str:
        return f"[#{f['id']}|{f['category']}] {f['content']}"

    def execute(self, action: str = "list", **kwargs) -> ToolResult:
        if action == "remember":
            content = (kwargs.get("content") or "").strip()
            if not content:
                return ToolResult.fail("Bitte den zu merkenden Inhalt angeben ('content').")
            fact = self.store.add_fact(content, kwargs.get("category") or "fact")
            if fact.get("duplicate"):
                return ToolResult.ok(f"War bereits gespeichert: {self._fmt_fact(fact)}", data=fact)
            return ToolResult.ok(f"Gemerkt: {self._fmt_fact(fact)}", data=fact)

        if action == "recall":
            query = (kwargs.get("query") or "").strip()
            facts = self.store.search_facts(query) if query else self.store.list_facts(50)
            if not facts:
                return ToolResult.ok("Dazu ist nichts gespeichert.", data=[])
            return ToolResult.ok("Gespeicherte Fakten:\n" + "\n".join(self._fmt_fact(f) for f in facts), data=facts)

        if action == "list":
            facts = self.store.list_facts(200)
            if not facts:
                return ToolResult.ok("Das Gedächtnis ist noch leer.", data=[])
            return ToolResult.ok(f"{len(facts)} Fakten:\n" + "\n".join(self._fmt_fact(f) for f in facts), data=facts)

        if action == "update":
            fact_id = kwargs.get("id")
            content = (kwargs.get("content") or "").strip()
            if fact_id is None or not content:
                return ToolResult.fail("Bitte 'id' und neuen 'content' angeben.")
            if self.store.update_fact(int(fact_id), content):
                return ToolResult.ok(f"Fakt #{fact_id} aktualisiert: {content}")
            return ToolResult.fail(f"Kein Fakt mit ID {fact_id} gefunden.")

        if action == "forget":
            fact_id = kwargs.get("id")
            if fact_id is None:
                return ToolResult.fail("Bitte die 'id' des zu löschenden Fakts angeben.")
            if self.store.delete_fact(int(fact_id)):
                return ToolResult.ok(f"Fakt #{fact_id} vergessen.")
            return ToolResult.fail(f"Kein Fakt mit ID {fact_id} gefunden.")

        if action == "recall_conversations":
            query = (kwargs.get("query") or "").strip()
            days = max(1, int(kwargs.get("days") or 30))
            rows = self.store.search_conversations(query, days=days)
            if not rows:
                return ToolResult.ok(f"Keine passenden Gespräche in den letzten {days} Tagen.", data=[])
            lines = [
                f"{r['created_at'][:16].replace('T', ' ')} {'Nutzer' if r['role'] == 'user' else 'Jarvis'}: {r['content'][:300]}"
                for r in sorted(rows, key=lambda r: r["created_at"])
            ]
            return ToolResult.ok("Frühere Gespräche:\n" + "\n".join(lines), data=rows)

        return ToolResult.fail(f"Unbekannte Gedächtnis-Aktion: {action}")
