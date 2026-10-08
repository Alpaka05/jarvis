"""Google Gemini Provider – nutzt das `google-genai` SDK mit Function Calling."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from core.llm.base import LLMError, LLMProvider, LLMResponse, TextFn, ToolCall, Usage, group_tool_results

log = logging.getLogger(__name__)


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
        return self._generate(system, messages, tools, None)

    def chat_stream(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], on_text: TextFn
    ) -> LLMResponse:
        return self._generate(system, messages, tools, on_text)

    def _generate(self, system, messages, tools, on_text: Optional[TextFn]) -> LLMResponse:
        t = self._types
        cfg = t.GenerateContentConfig(
            system_instruction=system or None,
            tools=self.convert_tools(tools),
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = self.convert_messages(messages)
        result = None
        errors: List[str] = []
        emitted = [False]  # schon Text weitergegeben? Dann nicht mehr auf ein anderes Modell wechseln

        def emit(text: str):
            emitted[0] = True
            on_text(text)

        for model in self._available_models():
            try:
                if on_text is None:
                    result = self._collect(self.client.models.generate_content(model=model, contents=contents, config=cfg))
                else:
                    result = self._collect_stream(
                        self.client.models.generate_content_stream(model=model, contents=contents, config=cfg), emit
                    )
                self.model = model  # zuletzt erfolgreiches Modell merken (für Statusanzeige/Kosten)
                break
            except LLMError:
                raise
            except Exception as e:
                text = str(e)
                if emitted[0]:  # Antwort bricht mitten im Satz ab – ein zweites Modell finge von vorn an
                    raise LLMError(f"Gemini ({model}): Antwort abgebrochen: {text[:300]}") from e
                if self._is_rate_limited(text):
                    wait = self._retry_seconds(text)
                    self._cooldown_until[model] = time.time() + wait
                    log.info("Gemini %s ausgelastet, Pause %.0f s: %s", model, wait, text[:200])
                    errors.append(f"{model}: ausgelastet oder Kontingent erschöpft, wieder in {int(wait)} s")
                    continue
                if self._is_unknown_model(text):
                    # Für diese Sitzung aus der Rotation nehmen, statt die übrigen Modelle gar nicht zu versuchen
                    self._cooldown_until[model] = float("inf")
                    log.warning("Gemini-Modell %s nicht verfügbar, für diese Sitzung übersprungen: %s", model, text[:200])
                    errors.append(f"{model}: gibt es nicht (mehr) – GEMINI_FALLBACK_MODELS anpassen")
                    continue
                raise LLMError(f"Gemini ({model}): {text[:300]}") from e
        if result is None:
            raise LLMError("Gemini: kein Modell verfügbar – " + "; ".join(errors))

        parts, finish_reason, meta = result
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for i, part in enumerate(parts):
            fc = getattr(part, "function_call", None)
            if fc is not None and fc.name:
                tool_calls.append(ToolCall(id=fc.id or f"call_{i}", name=fc.name, arguments=dict(fc.args or {})))
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                text_parts.append(part.text)

        raw = None
        try:
            content = self._types.Content(role="model", parts=parts)
            raw = {"provider": "gemini", "content": content.model_dump(exclude_none=True)}
        except Exception:
            pass

        usage = Usage(calls=1)
        if meta is not None:
            cached = getattr(meta, "cached_content_token_count", 0) or 0
            usage.input_tokens = (getattr(meta, "prompt_token_count", 0) or 0) - cached
            usage.cache_read_tokens = cached
            usage.output_tokens = (getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0)
        return LLMResponse(
            text=("" if on_text is not None else "\n").join(text_parts).strip(),
            tool_calls=tool_calls,
            raw=raw,
            stop_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _collect(response):
        """Einzelantwort → (Teile, finish_reason, usage_metadata)."""
        if not response.candidates:
            raise LLMError("Gemini: Leere Antwort (möglicherweise blockiert).")
        candidate = response.candidates[0]
        parts = list((candidate.content.parts if candidate.content else None) or [])
        return parts, str(getattr(candidate, "finish_reason", "") or ""), getattr(response, "usage_metadata", None)

    def _collect_stream(self, chunks, emit: TextFn):
        """Gestreamte Antwort einsammeln, Text sofort weitergeben → (Teile, finish_reason, usage_metadata)."""
        parts: List[Any] = []
        finish_reason = ""
        meta = None
        got_candidate = False
        for chunk in chunks:
            if getattr(chunk, "usage_metadata", None) is not None:
                meta = chunk.usage_metadata
            if not chunk.candidates:
                continue
            got_candidate = True
            candidate = chunk.candidates[0]
            if getattr(candidate, "finish_reason", None):
                finish_reason = str(candidate.finish_reason)
            for part in (candidate.content.parts if candidate.content else None) or []:
                if self._is_plain_text(part) and parts and self._is_plain_text(parts[-1]):
                    # Textstücke zu einem Teil zusammenfassen, damit der Verlauf nicht aus Hunderten Teilen besteht
                    parts[-1] = self._types.Part(text=parts[-1].text + part.text)
                else:
                    parts.append(part)
                if getattr(part, "text", None) and not getattr(part, "thought", False):
                    emit(part.text)
        if not got_candidate:
            raise LLMError("Gemini: Leere Antwort (möglicherweise blockiert).")
        return parts, finish_reason, meta

    @staticmethod
    def _is_plain_text(part) -> bool:
        """Reiner Antworttext – ohne Gedanken, Signatur oder Funktionsaufruf, die unverändert bleiben müssen."""
        return (
            bool(getattr(part, "text", None))
            and not getattr(part, "thought", False)
            and not getattr(part, "thought_signature", None)
            and getattr(part, "function_call", None) is None
        )
