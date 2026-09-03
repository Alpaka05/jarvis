"""Google Gemini Provider – nutzt das `google-genai` SDK mit Function Calling."""
from __future__ import annotations

from typing import Any, Dict, List

from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, Usage, group_tool_results


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        if not api_key:
            raise LLMError("GEMINI_API_KEY fehlt in der .env-Datei.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover
            raise LLMError("Paket 'google-genai' nicht installiert (uv sync / pip install google-genai).") from e
        self._types = types
        self.client = genai.Client(api_key=api_key)
        self.model = model

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
                contents.append(t.Content(role="user", parts=[t.Part.from_text(text=payload or "(leer)")]))
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
            system_instruction=system,
            tools=self.convert_tools(tools),
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=self.convert_messages(messages),
                config=cfg,
            )
        except Exception as e:
            raise LLMError(f"Gemini: {e}") from e

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
