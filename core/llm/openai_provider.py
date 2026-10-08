"""OpenAI-kompatibler Provider – für OpenAI selbst und für Ollama (lokal, /v1-Endpunkt)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from core.llm.base import LLMError, LLMProvider, LLMResponse, TextFn, ToolCall, Usage, b64, group_tool_results


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
        # Ollama auf CPU braucht länger, sonst gilt ein knappes Limit
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0 if base_url else 60.0, max_retries=1)

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
                if isinstance(payload, list):  # Text + Bild (describe_image)
                    payload = [
                        {"type": "image_url", "image_url": {"url": f"data:{p['mime']};base64,{b64(p['data'])}"}}
                        if p["type"] == "image"
                        else {"type": "text", "text": p["text"]}
                        for p in payload
                    ]
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

    def _installed_models_hint(self) -> str:
        """Bei Ollama: welche Modelle tatsächlich installiert sind (sonst leer)."""
        if self.name != "ollama":
            return ""
        try:
            import requests

            base = str(self.client.base_url).rstrip("/").removesuffix("/v1")
            names = [m["name"] for m in requests.get(f"{base}/api/tags", timeout=3).json().get("models", [])]
        except Exception:
            return ""
        if not names:
            return f" Es ist noch kein Modell installiert: ollama pull {self.model}"
        return f" Installiert: {', '.join(names)} – OLLAMA_MODEL in der .env anpassen oder: ollama pull {self.model}"

    def _params(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": self.convert_messages(system, messages),
        }
        if tools:
            params["tools"] = self.convert_tools(tools)
            params["tool_choice"] = "auto"
        return params

    @contextmanager
    def _errors(self):
        o = self._openai
        try:
            yield
        except o.AuthenticationError as e:
            raise LLMError(f"{self.name}: API-Key ungültig ({e})") from e
        except o.RateLimitError as e:
            raise LLMError(f"{self.name}: Rate-Limit erreicht, bitte kurz warten.") from e
        except o.NotFoundError as e:
            raise LLMError(f"{self.name}: Modell '{self.model}' nicht gefunden.{self._installed_models_hint()}") from e
        except o.APIConnectionError as e:
            raise LLMError(f"{self.name}: Keine Verbindung zu {self.client.base_url} ({e})") from e
        except o.APIStatusError as e:
            raise LLMError(f"{self.name}: API-Fehler {e.status_code}: {e}") from e
        except o.APIError as e:  # z.B. Fehler mitten im Stream
            raise LLMError(f"{self.name}: {e}") from e

    @staticmethod
    def _parse_args(raw_args: Any) -> Dict[str, Any]:
        raw_args = raw_args or "{}"
        try:
            return json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            return {"_raw": raw_args}

    @staticmethod
    def _usage(raw_usage: Any) -> Usage:
        usage = Usage(calls=1)
        if raw_usage:
            usage.input_tokens = raw_usage.prompt_tokens or 0
            usage.output_tokens = raw_usage.completion_tokens or 0
            details = getattr(raw_usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
            usage.input_tokens -= cached
            usage.cache_read_tokens = cached
        return usage

    def chat(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse:
        with self._errors():
            response = self.client.chat.completions.create(**self._params(system, messages, tools))

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
            tool_calls.append(ToolCall(id=tc.id or f"call_{i}", name=fn.name, arguments=self._parse_args(fn.arguments)))

        usage = self._usage(getattr(response, "usage", None))
        return LLMResponse(text=text, tool_calls=tool_calls, raw=None, stop_reason=choice.finish_reason or "", usage=usage)

    def chat_stream(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], on_text: TextFn
    ) -> LLMResponse:
        params = self._params(system, messages, tools)
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}  # Verbrauch kommt im letzten Stück

        text_parts: List[str] = []
        calls: Dict[int, Dict[str, Any]] = {}  # Index → Teilstücke eines Tool-Aufrufs
        finish_reason = ""
        raw_usage = None
        got_choice = False
        with self._errors():
            for chunk in self.client.chat.completions.create(**params):
                if getattr(chunk, "usage", None):
                    raw_usage = chunk.usage
                if not chunk.choices:
                    continue
                got_choice = True
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                if delta.content:
                    text_parts.append(delta.content)
                    on_text(delta.content)
                for tc in delta.tool_calls or []:
                    entry = calls.setdefault(tc.index if tc.index is not None else len(calls), {"id": "", "name": "", "args": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        entry["name"] += fn.name or ""
                        entry["args"] += fn.arguments or ""
        if not got_choice:
            raise LLMError(f"{self.name}: Leere Antwort vom Modell.")

        tool_calls = [
            ToolCall(id=c["id"] or f"call_{i}", name=c["name"], arguments=self._parse_args(c["args"]))
            for i, c in sorted(calls.items())
            if c["name"]
        ]
        return LLMResponse(
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            raw=None,
            stop_reason=finish_reason,
            usage=self._usage(raw_usage),
        )


def make_ollama_provider(host: str, model: str) -> OpenAICompatProvider:
    return OpenAICompatProvider(api_key="ollama", model=model, base_url=f"{host.rstrip('/')}/v1", name="ollama")
