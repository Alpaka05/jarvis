from config import config
from core import orb
from tools.base import BaseTool, ToolResult


class OrbTool(BaseTool):
    name = "orb"
    description = (
        "Schaltet den Orb ein oder aus – das schwebende, leuchtende Overlay in der Bildschirmecke, "
        "das zeigt, ob Jarvis zuhört, nachdenkt oder spricht. Auch für Fragen, ob der Orb läuft."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["on", "off", "status"],
                "description": "on = einschalten, off = ausschalten, status = läuft er?",
            },
        },
        "required": ["action"],
    }

    def execute(self, action: str = "status", **kwargs) -> ToolResult:
        if action == "on":
            ok, message = orb.open_window(port=config.ORB_PORT)
        elif action == "off":
            ok, message = orb.close_window()
        elif action == "status":
            ok, message = True, "Der Orb läuft." if orb.window.running or orb.bus.active else "Der Orb ist aus."
        else:
            return ToolResult.fail(f"Unbekannte Aktion '{action}'. Erlaubt: on, off, status.")
        return ToolResult.ok(message) if ok else ToolResult.fail(message)
