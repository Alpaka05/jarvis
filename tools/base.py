"""Basisklasse für Jarvis-Tools.

Jedes Tool beschreibt sich selbst über `name`, `description` und `parameters`
(JSON Schema). Das LLM entscheidet anhand dieser Beschreibung, wann und mit
welchen Argumenten es das Tool aufruft.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


class Risk(str, Enum):
    """Wie vorsichtig der Agent mit einer Aktion umgeht."""

    SAFE = "safe"  # ohne Rückfrage (lesen, Musik, Licht …)
    GUARDED = "guarded"  # Rückfrage, sobald fremde Inhalte (Webseiten, Mails) im Gespräch stehen
    CONFIRM = "confirm"  # immer Rückfrage (Mail senden, Türschloss, Browser-Agent …)


@dataclass
class Policy:
    """Einstufung eines konkreten Tool-Aufrufs, bevor er ausgeführt wird.

    Hintergrund: Webseiten und Mails können Anweisungen enthalten, die das LLM für echte hält
    (Prompt-Injection). Deshalb fragt der Agent bei GUARDED-Aktionen nach, sobald solche Inhalte
    im Gespräch stehen, und bei CONFIRM-Aktionen immer.
    """

    risk: Risk = Risk.SAFE
    prompt: str = ""  # Bestätigungstext; die erste Zeile ist die Kurzfassung (wird im Sprachmodus vorgelesen)
    url: Optional[str] = None  # Ziel-URL: steht sie wörtlich schon im Gespräch, entfällt die GUARDED-Rückfrage
    untrusted_output: bool = False  # das Ergebnis enthält fremde Inhalte (Webseiten, Mails)


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

    def policy(self, **kwargs) -> Policy:
        """Einstufung des Aufrufs. Standard: CONFIRM, wenn confirmation_prompt einen Text liefert."""
        prompt = self.confirmation_prompt(**kwargs)
        return Policy(Risk.CONFIRM, prompt) if prompt else Policy()

    def to_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
