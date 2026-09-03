"""Jarvis-Agent: LLM mit echtem Tool-Use.

Ablauf pro Anfrage:
    1. Nutzeranfrage in den Verlauf
    2. LLM mit System-Prompt, Verlauf und Tool-Schemas aufrufen
    3. Falls das LLM Tools aufruft: ausführen, Ergebnisse anhängen, zurück zu 2
    4. Sonst: Textantwort zurückgeben
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from config import config
from core.llm import LLMError, LLMProvider, LLMResponse, ToolCall, create_providers
from tools import default_tools
from tools.base import BaseTool, ToolResult

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

ConfirmFn = Callable[[str], bool]
ToolCallHook = Callable[[ToolCall], None]
ToolResultHook = Callable[[ToolCall, ToolResult], None]
NoticeHook = Callable[[str], None]


class JarvisAgent:
    MAX_STEPS = 8          # max. Tool-Runden pro Anfrage
    MAX_HISTORY = 60       # Nachrichten im Verlauf, bevor alte Runden verworfen werden
    MAX_TOOL_OUTPUT = 6000 # Zeichen pro Tool-Ergebnis, die ans LLM gehen

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        fallback: Optional[LLMProvider] = None,
        tools: Optional[List[BaseTool]] = None,
        confirm: Optional[ConfirmFn] = None,
        on_tool_call: Optional[ToolCallHook] = None,
        on_tool_result: Optional[ToolResultHook] = None,
        on_notice: Optional[NoticeHook] = None,
    ):
        self.notes: List[str] = []
        if provider is None and fallback is None:
            provider, fallback, self.notes = create_providers()
        self.provider = provider
        self.fallback = fallback
        self.tools: Dict[str, BaseTool] = {t.name: t for t in (tools if tools is not None else default_tools())}
        self.history: List[Dict[str, Any]] = []
        self.confirm = confirm
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_notice = on_notice

    # ── Prompt ───────────────────────────────────────────────────────────────

    def system_prompt(self) -> str:
        now = datetime.now()
        owner = f" von {config.USER_NAME}" if config.USER_NAME else ""
        return (
            f"Du bist Jarvis, der persönliche KI-Assistent{owner}. Du läufst lokal auf einem {config.platform_name}-Rechner.\n"
            f"Heute ist {WEEKDAYS[now.weekday()]}, der {now.strftime('%d.%m.%Y')}. Die genaue Uhrzeit liefert das system-Tool.\n\n"
            "Verhalten:\n"
            "- Antworte auf Deutsch, knapp und natürlich. Deine Antworten werden oft vorgelesen: "
            "keine Markdown-Formatierung, keine Aufzählungszeichen, keine Überschriften, keine URLs außer auf Wunsch.\n"
            "- Nutze Tools, wann immer eine Aktion ausgeführt werden soll oder aktuelle Informationen nötig sind. "
            "Erfinde keine Ergebnisse. Mehrere unabhängige Tool-Aufrufe kannst du parallel absetzen.\n"
            "- Fehlen für eine Aktion wichtige Angaben (z.B. Empfänger einer E-Mail), frage kurz nach statt zu raten.\n"
            "- Smart Home: Wenn du die entity_id eines Geräts nicht kennst, suche sie zuerst mit homeassistant list_entities "
            "(search = Raum oder Gerätename). Merke dir gefundene IDs für den weiteren Gesprächsverlauf.\n"
            "- Relative Datumsangaben (morgen, nächsten Montag) rechnest du anhand des heutigen Datums in YYYY-MM-DD um.\n"
            "- Nach ausgeführten Aktionen bestätigst du in einem Satz, was passiert ist. Bei Fehlern erklärst du kurz die Ursache."
        )

    # ── Öffentliche API ──────────────────────────────────────────────────────

    def reset(self):
        self.history.clear()

    def tool_schemas(self) -> List[Dict[str, Any]]:
        return [t.to_schema() for t in self.tools.values()]

    def process_query(self, query: str) -> str:
        if self.provider is None:
            return (
                "Es ist kein LLM-Provider verfügbar. Bitte in der .env einen API-Key setzen "
                "(z.B. ANTHROPIC_API_KEY) oder Ollama starten.\n" + "\n".join(self.notes)
            )

        self.history.append({"role": "user", "content": query})
        schemas = self.tool_schemas()

        try:
            for _ in range(self.MAX_STEPS):
                response = self._chat(schemas)
                self._append_assistant(response)

                if not response.tool_calls:
                    self._trim_history()
                    return response.text or "(keine Antwort erhalten)"

                for call in response.tool_calls:
                    result = self._run_tool(call)
                    self.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": result.output[: self.MAX_TOOL_OUTPUT],
                            "is_error": not result.success,
                        }
                    )

            # Zu viele Runden: Abschlussantwort ohne Tools anfordern
            self.history.append(
                {"role": "user", "content": "Bitte fasse jetzt kurz zusammen, was du erledigt hast, ohne weitere Tools zu nutzen."}
            )
            response = self._chat([])
            self._append_assistant(response)
            self._trim_history()
            return response.text or "Ich habe die maximale Anzahl an Schritten erreicht."

        except LLMError as e:
            self._rollback_turn()
            return f"Der KI-Dienst ist gerade nicht erreichbar: {e}"

    # ── Intern ───────────────────────────────────────────────────────────────

    def _chat(self, schemas: List[Dict[str, Any]]) -> LLMResponse:
        assert self.provider is not None
        try:
            return self.provider.chat(self.system_prompt(), self.history, schemas)
        except LLMError as e:
            if self.fallback is None:
                raise
            if self.on_notice:
                self.on_notice(f"{self.provider.describe()} nicht erreichbar ({e}). Wechsle zu {self.fallback.describe()}.")
            return self.fallback.chat(self.system_prompt(), self.history, schemas)

    def _append_assistant(self, response: LLMResponse):
        msg: Dict[str, Any] = {
            "role": "assistant",
            "content": response.text,
            "tool_calls": [tc.to_dict() for tc in response.tool_calls],
        }
        if response.raw:
            msg["_raw"] = response.raw
        self.history.append(msg)

    def _run_tool(self, call: ToolCall) -> ToolResult:
        if self.on_tool_call:
            self.on_tool_call(call)

        tool = self.tools.get(call.name)
        if tool is None:
            result = ToolResult.fail(f"Unbekanntes Tool '{call.name}'. Verfügbar: {', '.join(self.tools)}")
        else:
            args = call.arguments if isinstance(call.arguments, dict) else {}
            prompt = None
            try:
                prompt = tool.confirmation_prompt(**args)
            except Exception:
                pass
            if prompt and self.confirm is not None and not self.confirm(prompt):
                result = ToolResult.ok("Der Nutzer hat diese Aktion abgelehnt. Nicht ausgeführt.")
            else:
                try:
                    result = tool.execute(**args)
                except TypeError as e:
                    result = ToolResult.fail(f"Ungültige Argumente für '{call.name}': {e}. Erhalten: {json.dumps(args, ensure_ascii=False)}")
                except Exception as e:
                    result = ToolResult.fail(f"Fehler in Tool '{call.name}': {e}")

        if self.on_tool_result:
            self.on_tool_result(call, result)
        return result

    def _rollback_turn(self):
        """Entfernt die unvollständige letzte Runde (bis inkl. der letzten Nutzernachricht)."""
        while self.history:
            msg = self.history.pop()
            if msg["role"] == "user" and not isinstance(msg.get("content"), list):
                break

    def _trim_history(self):
        if len(self.history) <= self.MAX_HISTORY:
            return
        # Vom Anfang löschen, aber nie mitten in einer Tool-Runde abschneiden
        while len(self.history) > self.MAX_HISTORY:
            self.history.pop(0)
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)
