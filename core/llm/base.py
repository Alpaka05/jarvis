"""Provider-neutrale Datentypen für den LLM-Layer.

Der Agent arbeitet ausschließlich mit diesen Typen. Jeder Provider übersetzt
sie in sein eigenes Wire-Format (Anthropic, OpenAI/Ollama, Gemini).

Verlauf (history) ist eine Liste von dicts:
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall-dicts], "_raw": {...}}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "...", "is_error": bool}

`_raw` enthält providerspezifische Rohdaten (z.B. Anthropic-Content-Blöcke inkl.
Thinking), damit der gleiche Provider sie unverändert zurückspielen kann.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolCall":
        return cls(id=d["id"], name=d["name"], arguments=d.get("arguments") or {})


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None  # wird in history["_raw"] gespeichert
    stop_reason: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMError(RuntimeError):
    """Fehler beim Aufruf eines Providers (Netzwerk, Auth, Rate-Limit, ...)."""


class LLMProvider(ABC):
    """Gemeinsame Schnittstelle aller Provider."""

    name: str = "base"
    model: str = ""

    @abstractmethod
    def chat(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse:
        """Führt einen Chat-Turn aus.

        Args:
            system: System-Prompt.
            messages: Verlauf im neutralen Format (siehe Modul-Docstring).
            tools: Tool-Schemas im Format {"name", "description", "parameters": <JSON Schema>}.
        """

    def describe(self) -> str:
        return f"{self.name} ({self.model})"


def group_tool_results(messages: List[Dict[str, Any]]):
    """Iteriert über den Verlauf und fasst aufeinanderfolgende tool-Messages zusammen.

    Liefert Tupel (role, payload):
        ("user", str) | ("assistant", msg_dict) | ("tools", [tool_msg_dicts])
    """
    pending: List[Dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "tool":
            pending.append(msg)
            continue
        if pending:
            yield "tools", pending
            pending = []
        if msg["role"] == "user":
            yield "user", msg.get("content", "")
        elif msg["role"] == "assistant":
            yield "assistant", msg
    if pending:
        yield "tools", pending
