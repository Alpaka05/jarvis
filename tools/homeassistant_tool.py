from typing import Any, Dict, List, Optional

import requests

from config import config
from tools.base import BaseTool, ToolResult

MAX_LISTED = 60


class HomeAssistantTool(BaseTool):
    name = "homeassistant"
    description = (
        "Smart Home über Home Assistant: Geräte/Entitäten finden, Zustände lesen und Dienste aufrufen "
        "(z.B. Licht schalten, Steckdosen, Heizung, Szenen). "
        "Wenn die entity_id unbekannt ist, zuerst mit action=list_entities und search nach dem Gerät suchen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_entities", "get_state", "call_service"],
                "description": "list_entities = Geräte suchen/auflisten, get_state = Zustand einer Entität, call_service = Aktion ausführen",
            },
            "domain": {
                "type": "string",
                "description": "Domain-Filter für list_entities, z.B. light, switch, climate, sensor, media_player, scene.",
            },
            "search": {
                "type": "string",
                "description": "Suchbegriff für list_entities (Name oder Raum, z.B. 'bad', 'küche', 'tv').",
            },
            "entity_id": {"type": "string", "description": "Entität, z.B. light.licht_bad (get_state / call_service)."},
            "service": {
                "type": "string",
                "description": "Dienst für call_service, z.B. turn_on, turn_off, toggle, set_temperature, select_option.",
            },
            "data": {
                "type": "object",
                "description": "Zusätzliche Service-Daten, z.B. {\"brightness_pct\": 50, \"color_name\": \"red\"} oder {\"temperature\": 21}.",
            },
        },
        "required": ["action"],
    }

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {config.HA_TOKEN}", "Content-Type": "application/json"}

    def _is_configured(self) -> bool:
        return bool(config.HA_URL and config.HA_TOKEN)

    def _not_configured(self) -> ToolResult:
        return ToolResult.fail("Home Assistant ist nicht konfiguriert (HA_URL / HA_TOKEN in .env fehlen).")

    # ── Lesen ────────────────────────────────────────────────────────────────

    def _fetch_states(self) -> List[Dict[str, Any]]:
        resp = requests.get(f"{config.HA_URL}/api/states", headers=self._headers(), timeout=8)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _fmt(state: Dict[str, Any]) -> str:
        attrs = state.get("attributes", {})
        name = attrs.get("friendly_name", state.get("entity_id"))
        extra = ""
        if "brightness" in attrs and attrs["brightness"] is not None:
            extra = f", Helligkeit {round(attrs['brightness'] / 2.55)}%"
        elif "current_temperature" in attrs:
            extra = f", aktuell {attrs['current_temperature']}°"
        unit = attrs.get("unit_of_measurement", "")
        return f"- {name} [{state.get('entity_id')}]: {state.get('state')}{unit and ' ' + unit}{extra}"

    def list_entities(self, domain: Optional[str] = None, search: Optional[str] = None) -> ToolResult:
        if not self._is_configured():
            return self._not_configured()
        try:
            states = self._fetch_states()
        except Exception as e:
            return ToolResult.fail(f"Verbindung zu Home Assistant ({config.HA_URL}) fehlgeschlagen: {e}")

        if domain:
            states = [s for s in states if s.get("entity_id", "").startswith(f"{domain.lower()}.")]
        if search:
            q = search.lower()
            states = [
                s for s in states
                if q in s.get("entity_id", "").lower()
                or q in str(s.get("attributes", {}).get("friendly_name", "")).lower()
            ]
        if not states:
            return ToolResult.ok("Keine passenden Entitäten gefunden.", data=[])

        # Ohne Filter nur steuerbare Domains zeigen, sonst wird die Liste riesig.
        if not domain and not search:
            prefer = ("light.", "switch.", "climate.", "media_player.", "scene.", "cover.", "fan.", "vacuum.", "lock.")
            states = [s for s in states if s.get("entity_id", "").startswith(prefer)] or states

        total = len(states)
        lines = [self._fmt(s) for s in states[:MAX_LISTED]]
        more = f"\n… und {total - MAX_LISTED} weitere (bitte Filter verwenden)" if total > MAX_LISTED else ""
        return ToolResult.ok(f"{total} Entitäten:\n" + "\n".join(lines) + more, data=states[:MAX_LISTED])

    def get_state(self, entity_id: str) -> ToolResult:
        if not self._is_configured():
            return self._not_configured()
        try:
            resp = requests.get(f"{config.HA_URL}/api/states/{entity_id}", headers=self._headers(), timeout=8)
            if resp.status_code == 404:
                return ToolResult.fail(f"Entität '{entity_id}' existiert nicht.")
            resp.raise_for_status()
            state = resp.json()
            attrs = {k: v for k, v in state.get("attributes", {}).items() if not isinstance(v, (list, dict))}
            return ToolResult.ok(f"{self._fmt(state)}\nAttribute: {attrs}", data=state)
        except Exception as e:
            return ToolResult.fail(f"Fehler bei Home Assistant: {e}")

    # ── Steuern ──────────────────────────────────────────────────────────────

    def call_service(self, entity_id: str, service: str, data: Optional[Dict[str, Any]] = None) -> ToolResult:
        if not self._is_configured():
            return self._not_configured()
        if "." not in entity_id:
            return ToolResult.fail(f"Ungültige entity_id '{entity_id}' (erwartet z.B. light.wohnzimmer).")
        domain = entity_id.split(".")[0]
        # Szenen und Skripte werden immer über turn_on aktiviert
        if domain in ("scene", "script") and service in ("activate", "run", "start"):
            service = "turn_on"
        payload = {"entity_id": entity_id, **(data or {})}
        try:
            resp = requests.post(
                f"{config.HA_URL}/api/services/{domain}/{service}", headers=self._headers(), json=payload, timeout=8
            )
            if resp.status_code != 200:
                return ToolResult.fail(f"Home Assistant Fehler ({resp.status_code}): {resp.text[:300]}")
        except Exception as e:
            return ToolResult.fail(f"Fehler bei Home Assistant Aufruf: {e}")

        # Neuen Zustand mitliefern, damit das LLM bestätigen kann
        try:
            state = requests.get(f"{config.HA_URL}/api/states/{entity_id}", headers=self._headers(), timeout=5).json()
            return ToolResult.ok(f"'{domain}.{service}' ausgeführt. Neuer Zustand: {self._fmt(state)}")
        except Exception:
            return ToolResult.ok(f"'{domain}.{service}' auf {entity_id} ausgeführt.")

    def execute(self, action: str = "list_entities", **kwargs) -> ToolResult:
        if action in ("list_entities", "list", "get_states", "status"):
            return self.list_entities(kwargs.get("domain"), kwargs.get("search"))
        if action == "get_state":
            entity_id = kwargs.get("entity_id")
            if not entity_id:
                return ToolResult.fail("Bitte eine entity_id angeben.")
            return self.get_state(entity_id)
        if action in ("call_service", "turn_on", "turn_off", "toggle"):
            entity_id = kwargs.get("entity_id")
            if not entity_id:
                return ToolResult.fail("Bitte eine entity_id angeben (ggf. erst mit list_entities suchen).")
            service = kwargs.get("service") or (action if action != "call_service" else "turn_on")
            data = kwargs.get("data") if isinstance(kwargs.get("data"), dict) else None
            return self.call_service(entity_id, service, data)
        return ToolResult.fail(f"Unbekannte Home Assistant Aktion: {action}")
