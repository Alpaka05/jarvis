"""Anthropic (Claude) Provider – nutzt das offizielle `anthropic` SDK mit Tool Use."""
from __future__ import annotations

from typing import Any, Dict, List

from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, Usage, group_tool_results

MAX_TOKENS = 8192
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-5", effort: str = "medium"):
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY fehlt in der .env-Datei.")
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("Paket 'anthropic' nicht installiert (uv sync / pip install anthropic).") from e
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.effort = effort if effort in VALID_EFFORTS else "medium"

    # ── Konvertierung ────────────────────────────────────────────────────────

    @staticmethod
    def convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters") or {"type": "object", "properties": {}},
            }
            for t in tools
        ]

    @staticmethod
    def convert_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for kind, payload in group_tool_results(messages):
            if kind == "user":
                out.append({"role": "user", "content": payload})
            elif kind == "assistant":
                raw = payload.get("_raw") or {}
                if raw.get("provider") == "anthropic" and raw.get("content"):
                    # Rohblöcke (inkl. Thinking) unverändert zurückspielen
                    out.append({"role": "assistant", "content": raw["content"]})
                    continue
                blocks: List[Dict[str, Any]] = []
                if payload.get("content"):
                    blocks.append({"type": "text", "text": payload["content"]})
                for tc in payload.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("arguments") or {}})
                if not blocks:
                    blocks.append({"type": "text", "text": "(keine Antwort)"})
                out.append({"role": "assistant", "content": blocks})
            elif kind == "tools":
                results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": t["tool_call_id"],
                        "content": t.get("content") or "(leer)",
                        **({"is_error": True} if t.get("is_error") else {}),
                    }
                    for t in payload
                ]
                out.append({"role": "user", "content": results})
        return out

    # ── Aufruf ───────────────────────────────────────────────────────────────

    def chat(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse:
        a = self._anthropic
        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": self.convert_messages(messages),
            "output_config": {"effort": self.effort},
            # Prompt-Caching: Tools + System-Prompt + bisheriger Verlauf werden serverseitig
            # gecacht; Folgeaufrufe (Tool-Runden, nächste Fragen) zahlen dafür nur ~10 %.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = self.convert_tools(tools)

        try:
            response = self.client.messages.create(**params)
        except a.AuthenticationError as e:
            raise LLMError(f"Anthropic: API-Key ungültig ({e.message})") from e
        except a.RateLimitError as e:
            raise LLMError("Anthropic: Rate-Limit erreicht, bitte kurz warten.") from e
        except a.APIStatusError as e:
            raise LLMError(f"Anthropic: API-Fehler {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"Anthropic: Keine Verbindung ({e})") from e

        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=args))

        text = "\n".join(p for p in text_parts if p).strip()
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            reason = getattr(details, "explanation", None) or "Sicherheitsfilter"
            text = text or f"Diese Anfrage kann ich nicht ausführen ({reason})."
            tool_calls = []

        raw_content = [b.model_dump(exclude_none=True) for b in response.content]
        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            calls=1,
        )
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw={"provider": "anthropic", "content": raw_content},
            stop_reason=response.stop_reason or "",
            usage=usage,
        )
