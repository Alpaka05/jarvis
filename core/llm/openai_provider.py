"""OpenAI-kompatibler Provider – für OpenAI selbst und für Ollama (lokal, /v1-Endpunkt)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, Usage, group_tool_results


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None, name: str = "openai"):
        if not api_key:
            raise LLMError("OPENAI_API_KEY fehlt in der .env-Datei.")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise LLMError("Paket 'openai' nicht installiert (uv sync / pip install openai).") from e
        import openai

        self._openai = openai
        self.name = name
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    # ── Konvertierung ────────────────────────────────────────────────────────

    @staticmethod
    def convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    @staticmethod
    def convert_messages(system: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for kind, payload in group_tool_results(messages):
            if kind == "user":
                out.append({"role": "user", "content": payload})
            elif kind == "assistant":
                msg: Dict[str, Any] = {"role": "assistant", "content": payload.get("content") or None}
                calls = payload.get("tool_calls") or []
                if calls:
                    msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False)},
                        }
                        for tc in calls
                    ]
                out.append(msg)
            elif kind == "tools":
                for t in payload:
                    out.append({"role": "tool", "tool_call_id": t["tool_call_id"], "content": t.get("content") or "(leer)"})
        return out

    # ── Aufruf ───────────────────────────────────────────────────────────────

    def chat(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse:
        o = self._openai
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": self.convert_messages(system, messages),
        }
        if tools:
            params["tools"] = self.convert_tools(tools)
            params["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**params)
        except o.AuthenticationError as e:
            raise LLMError(f"{self.name}: API-Key ungültig ({e})") from e
        except o.RateLimitError:
            raise LLMError(f"{self.name}: Rate-Limit erreicht, bitte kurz warten.")
        except o.NotFoundError as e:
            raise LLMError(f"{self.name}: Modell '{self.model}' nicht gefunden ({e}).") from e
        except o.APIConnectionError as e:
            raise LLMError(f"{self.name}: Keine Verbindung zu {self.client.base_url} ({e})") from e
        except o.APIStatusError as e:
            raise LLMError(f"{self.name}: API-Fehler {e.status_code}: {e}") from e

        if not response.choices:
            raise LLMError(f"{self.name}: Leere Antwort vom Modell.")
        choice = response.choices[0]
        msg = choice.message
        text = (msg.content or "").strip()

        tool_calls: List[ToolCall] = []
        for i, tc in enumerate(msg.tool_calls or []):
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            raw_args = fn.arguments or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            tool_calls.append(ToolCall(id=tc.id or f"call_{i}", name=fn.name, arguments=args))

        usage = Usage(calls=1)
        if getattr(response, "usage", None):
            usage.input_tokens = response.usage.prompt_tokens or 0
            usage.output_tokens = response.usage.completion_tokens or 0
            details = getattr(response.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
            usage.input_tokens -= cached
            usage.cache_read_tokens = cached
        return LLMResponse(text=text, tool_calls=tool_calls, raw=None, stop_reason=choice.finish_reason or "", usage=usage)


def make_ollama_provider(host: str, model: str) -> OpenAICompatProvider:
    return OpenAICompatProvider(api_key="ollama", model=model, base_url=f"{host.rstrip('/')}/v1", name="ollama")
