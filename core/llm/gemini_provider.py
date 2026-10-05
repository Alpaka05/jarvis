"""Google Gemini Provider – nutzt das `google-genai` SDK mit Function Calling."""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, Usage, group_tool_results


class GeminiProvider(LLMProvider):
    """Gemini mit Modell-Rotation.

    Das kostenlose Kontingent gilt pro Modell (z.B. 5 Anfragen/Minute). Bei 429/503 wird
    deshalb automatisch auf das nächste Modell der Liste gewechselt, statt zu warten.
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.6-flash",
        fallback_models: Optional[List[str]] = None,
        timeout_seconds: float = 45.0,
    ):
        if not api_key:
            raise LLMError("GEMINI_API_KEY fehlt in der .env-Datei.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover
            raise LLMError("Paket 'google-genai' nicht installiert (uv sync / pip install google-genai).") from e
        self._types = types
        # Hartes Zeitlimit pro Aufruf (ms) – ohne das kann eine hängende Anfrage minutenlang blockieren
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)))
        self.model = model
        self.models: List[str] = [model] + [m for m in (fallback_models or []) if m and m != model]
        self._cooldown_until: Dict[str, float] = {}  # Modell → Zeitpunkt, ab dem es wieder nutzbar ist

    # ── Rotation ─────────────────────────────────────────────────────────────

    @staticmethod
    def _retry_seconds(error_text: str, default: float = 60.0) -> float:
        m = re.search(r"retry in ([\d.]+)s", error_text, re.IGNORECASE) or re.search(r"retryDelay['\"]?: ?['\"](\d+)s", error_text)
        return float(m.group(1)) if m else default

    @staticmethod
    def _is_rate_limited(error_text: str) -> bool:
        markers = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED", "timed out", "Timeout")
        return any(m in error_text for m in markers)

    @staticmethod
    def _is_unknown_model(error_text: str) -> bool:
        """Modell gibt es (für diesen Key) nicht oder nicht mehr – Google stellt ältere Modelle ein."""
        return "404" in error_text or "NOT_FOUND" in error_text

    def _available_models(self) -> List[str]:
        now = time.time()
        ready = [m for m in self.models if self._cooldown_until.get(m, 0) <= now]
        return ready or sorted(self.models, key=lambda m: self._cooldown_until.get(m, 0))[:1]

    # ── Konvertierung ────────────────────────────────────────────────────────

    def convert_tools(self, tools: List[Dict[str, Any]]):
        t = self._types
        decls = [
            t.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters_json_schema=tool.get("parameters") or {"type": "object", "properties": {}},
            )
            for tool in tools
        ]
        return [t.Tool(function_declarations=decls)] if decls else None

    def convert_messages(self, messages: List[Dict[str, Any]]):
        t = self._types
        contents = []
        for kind, payload in group_tool_results(messages):
            if kind == "user":
                if isinstance(payload, list):  # Text + Bild (describe_image)
                    parts = [
                        t.Part.from_bytes(data=p["data"], mime_type=p["mime"]) if p["type"] == "image" else t.Part.from_text(text=p["text"])
                        for p in payload
                    ]
                else:
                    parts = [t.Part.from_text(text=payload or "(leer)")]
                contents.append(t.Content(role="user", parts=parts))
            elif kind == "assistant":
                raw = payload.get("_raw") or {}
                if raw.get("provider") == "gemini" and raw.get("content"):
                    try:
                        contents.append(t.Content.model_validate(raw["content"]))
                        continue
                    except Exception:
                        pass
                parts = []
                if payload.get("content"):
                    parts.append(t.Part.from_text(text=payload["content"]))
                for tc in payload.get("tool_calls") or []:
                    parts.append(t.Part.from_function_call(name=tc["name"], args=tc.get("arguments") or {}))
                if not parts:
                    parts.append(t.Part.from_text(text="(keine Antwort)"))
                contents.append(t.Content(role="model", parts=parts))
            elif kind == "tools":
                parts = [
                    t.Part.from_function_response(
                        name=tool_msg.get("name", "tool"),
                        response={"error" if tool_msg.get("is_error") else "result": tool_msg.get("content") or "(leer)"},
                    )
                    for tool_msg in payload
                ]
                contents.append(t.Content(role="user", parts=parts))
        return contents

    # ── Aufruf ───────────────────────────────────────────────────────────────

    def chat(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse:
        t = self._types
        cfg = t.GenerateContentConfig(
            system_instruction=system or None,
            tools=self.convert_tools(tools),
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = self.convert_messages(messages)
        response = None
        errors: List[str] = []
        for model in self._available_models():
            try:
                response = self.client.models.generate_content(model=model, contents=contents, config=cfg)
                self.model = model  # zuletzt erfolgreiches Modell merken (für Statusanzeige/Kosten)
                break
            except Exception as e:
                text = str(e)
                if self._is_rate_limited(text):
                    wait = self._retry_seconds(text)
                    self._cooldown_until[model] = time.time() + wait
                    errors.append(f"{model}: ausgelastet oder Kontingent erschöpft, wieder in {int(wait)} s")
                    continue
                if self._is_unknown_model(text):
                    # Für diese Sitzung aus der Rotation nehmen, statt die übrigen Modelle gar nicht zu versuchen
                    self._cooldown_until[model] = float("inf")
                    errors.append(f"{model}: gibt es nicht (mehr) – GEMINI_FALLBACK_MODELS anpassen")
                    continue
                raise LLMError(f"Gemini ({model}): {text[:300]}") from e
        if response is None:
            raise LLMError("Gemini: kein Modell verfügbar – " + "; ".join(errors))

        if not response.candidates:
            raise LLMError("Gemini: Leere Antwort (möglicherweise blockiert).")
        content = response.candidates[0].content
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for i, part in enumerate(content.parts or []):
            fc = getattr(part, "function_call", None)
            if fc is not None and fc.name:
                tool_calls.append(ToolCall(id=fc.id or f"call_{i}", name=fc.name, arguments=dict(fc.args or {})))
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                text_parts.append(part.text)

        raw = None
        try:
            raw = {"provider": "gemini", "content": content.model_dump(exclude_none=True)}
        except Exception:
            pass

        usage = Usage(calls=1)
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            cached = getattr(meta, "cached_content_token_count", 0) or 0
            usage.input_tokens = (getattr(meta, "prompt_token_count", 0) or 0) - cached
            usage.cache_read_tokens = cached
            usage.output_tokens = (getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0)
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            raw=raw,
            stop_reason=str(getattr(response.candidates[0], "finish_reason", "") or ""),
            usage=usage,
        )
