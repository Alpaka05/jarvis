"""Provider-neutrale Datentypen für den LLM-Layer.

Der Agent arbeitet ausschließlich mit diesen Typen. Jeder Provider übersetzt
sie in sein eigenes Wire-Format (Anthropic, OpenAI/Ollama, Gemini).

Verlauf (history) ist eine Liste von dicts:
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall-dicts], "_raw": {...}}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "...", "is_error": bool}

Für Einzelaufrufe mit Bild (describe_image, z.B. Screenshot) darf `content` einer Nutzernachricht
auch eine Liste von Teilen sein – im Gesprächsverlauf stehen nur Texte:
    {"role": "user", "content": [{"type": "image", "mime": "image/jpeg", "data": b"..."},
                                 {"type": "text", "text": "..."}]}

`_raw` enthält providerspezifische Rohdaten (z.B. Anthropic-Content-Blöcke inkl.
Thinking), damit der gleiche Provider sie unverändert zurückspielen kann.
"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

TextFn = Callable[[str], None]  # nimmt Textstücke entgegen, sobald das Modell sie erzeugt


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
class Usage:
    """Token-Verbrauch eines Aufrufs (0, wenn der Provider nichts liefert)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.calls + other.calls,
        )

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None  # wird in history["_raw"] gespeichert
    stop_reason: str = ""
    usage: Usage = field(default_factory=Usage)

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

    def chat_stream(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], on_text: TextFn
    ) -> LLMResponse:
        """Wie chat(), meldet den Antworttext aber schon während der Erzeugung Stück für Stück an
        on_text – damit die Sprachausgabe nicht auf die ganze Antwort warten muss. Gibt am Ende
        dieselbe LLMResponse wie chat() zurück. Ohne eigenes Streaming kommt der Text auf einmal."""
        response = self.chat(system, messages, tools)
        if response.text:
            on_text(response.text)
        return response

    def describe(self) -> str:
        return f"{self.name} ({self.model})"

    def describe_image(self, image: bytes, mime: str, prompt: str, system: str = "") -> "LLMResponse":
        """Wertet ein Bild aus (z.B. einen Screenshot) und beantwortet `prompt` dazu – ohne Tools,
        ohne Gesprächsverlauf. Nutzt chat(), damit Rotation, Fehler und Kosten wie gewohnt laufen."""
        message = {
            "role": "user",
            "content": [{"type": "image", "mime": mime, "data": image}, {"type": "text", "text": prompt}],
        }
        return self.chat(system, [message], [])


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


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
