"""Basisklasse für Jarvis-Tools.

Jedes Tool beschreibt sich selbst über `name`, `description` und `parameters`
(JSON Schema). Das LLM entscheidet anhand dieser Beschreibung, wann und mit
welchen Argumenten es das Tool aufruft.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class ToolResult(BaseModel):
    success: bool
    output: str
    data: Optional[Any] = None

    @classmethod
    def ok(cls, output: str, data: Any = None) -> "ToolResult":
        return cls(success=True, output=output, data=data)

    @classmethod
    def fail(cls, output: str) -> "ToolResult":
        return cls(success=False, output=output)


class BaseTool:
    name: str = "tool"
    description: str = ""
    # JSON Schema der Argumente (type: object). Wird 1:1 an den LLM-Provider gegeben.
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError("Tool execution must be implemented in subclass")

    def confirmation_prompt(self, **kwargs) -> Optional[str]:
        """Gibt einen Bestätigungstext zurück, wenn die Aktion vor Ausführung
        vom Nutzer freigegeben werden soll (z.B. E-Mail senden). Sonst None."""
        return None

    def to_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
