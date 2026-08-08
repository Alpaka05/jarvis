import requests
from typing import Dict, Any, List, Optional
from config import config
from tools.base import BaseTool, ToolResult

class HomeAssistantTool(BaseTool):
    name = "homeassistant"
    description = "Liest Statuswerte und steuert Smart-Home Geräte über die Home Assistant REST API."

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {config.HA_TOKEN}",
            "Content-Type": "application/json"
        }

    def _is_configured(self) -> bool:
        return bool(config.HA_URL and config.HA_TOKEN)

    def get_states(self, domain_filter: Optional[str] = None) -> ToolResult:
        if not self._is_configured():
            return ToolResult(
                success=False,
                output="Home Assistant ist nicht konfiguriert. Bitte trage HA_URL und HA_TOKEN in die .env-Datei ein."
            )
        try:
            url = f"{config.HA_URL}/api/states"
            resp = requests.get(url, headers=self._headers(), timeout=5)
            if resp.status_code != 200:
                return ToolResult(success=False, output=f"Home Assistant Antwort-Fehler (Status {resp.status_code}): {resp.text}")

            states = resp.json()
            if domain_filter:
                states = [s for s in states if s.get("entity_id", "").startswith(f"{domain_filter}.")]

            summary = []
            for s in states[:15]:
                entity_id = s.get("entity_id")
                state_val = s.get("state")
                friendly_name = s.get("attributes", {}).get("friendly_name", entity_id)
                summary.append(f"- {friendly_name} ({entity_id}): {state_val}")

            output_text = f"Home Assistant Status ({len(states)} Entitäten):\n" + "\n".join(summary)
            return ToolResult(success=True, output=output_text, data=states)
        except Exception as e:
            return ToolResult(success=False, output=f"Fehler bei Verbindung zu Home Assistant ({config.HA_URL}): {str(e)}")

    def call_service(self, domain: str, service: str, entity_id: str, **extra_data) -> ToolResult:
        if not self._is_configured():
            return ToolResult(
                success=False,
                output="Home Assistant ist noch nicht konfiguriert (HA_URL / HA_TOKEN fehlen in .env)."
            )
        try:
            url = f"{config.HA_URL}/api/services/{domain}/{service}"
            payload = {"entity_id": entity_id, **extra_data}
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=5)

            if resp.status_code == 200:
                return ToolResult(
                    success=True,
                    output=f"Dienst '{domain}.{service}' auf '{entity_id}' erfolgreich ausgeführt."
                )
            else:
                return ToolResult(
                    success=False,
                    output=f"Fehler bei Home Assistant Service ({resp.status_code}): {resp.text}"
                )
        except Exception as e:
            return ToolResult(success=False, output=f"Ausnahmefehler bei Home Assistant Call: {str(e)}")

    def execute(self, action: str = "get_states", **kwargs) -> ToolResult:
        if action in ("get_states", "list", "status"):
            domain = kwargs.get("domain")
            return self.get_states(domain_filter=domain)
        elif action in ("call_service", "control", "turn_on", "turn_off", "toggle"):
            entity_id = kwargs.get("entity_id")
            if not entity_id:
                return ToolResult(success=False, output="Bitte gib eine 'entity_id' an (z.B. light.wohnzimmer).")

            if action in ("turn_on", "turn_off", "toggle"):
                domain = entity_id.split(".")[0]
                service = action
            else:
                domain = kwargs.get("domain", entity_id.split(".")[0])
                service = kwargs.get("service", "turn_on")

            return self.call_service(domain, service, entity_id)
        else:
            return ToolResult(success=False, output=f"Unbekannte Home Assistant Aktion: {action}")
