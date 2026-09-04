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
from core.llm import LLMError, LLMProvider, LLMResponse, ToolCall, Usage, create_providers, estimate_cost_usd
from core.memory import MemoryStore
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
    TOOL_TIMEOUT = 45.0    # Sekunden, die ein einzelner Tool-Aufruf höchstens dauern darf

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        fallback: Optional[LLMProvider] = None,
        tools: Optional[List[BaseTool]] = None,
        confirm: Optional[ConfirmFn] = None,
        on_tool_call: Optional[ToolCallHook] = None,
        on_tool_result: Optional[ToolResultHook] = None,
        on_notice: Optional[NoticeHook] = None,
        memory: Optional[MemoryStore] = None,
    ):
        self.notes: List[str] = []
        if provider is None and fallback is None:
            provider, fallback, self.notes = create_providers()
        self.provider = provider
        self.fallback = fallback
        # Ohne explizite Tool-Liste: Standard-Tools inkl. Langzeitgedächtnis
        if memory is None and tools is None:
            memory = MemoryStore(config.MEMORY_DB)
        self.memory = memory
        self.tools: Dict[str, BaseTool] = {
            t.name: t for t in (tools if tools is not None else default_tools(memory=memory))
        }
        self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.history: List[Dict[str, Any]] = []
        self.last_usage = Usage()
        self.session_usage = Usage()
        self.voice_mode = False  # wird vom Sprachmodus gesetzt: kürzere, vorlesbare Antworten
        self.confirm = confirm
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_notice = on_notice

    # ── Prompt ───────────────────────────────────────────────────────────────

    def system_prompt(self) -> str:
        now = datetime.now()
        owner = f" von {config.USER_NAME}" if config.USER_NAME else ""
        prompt = (
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
        if self.voice_mode:
            prompt += (
                "\n- Sprachmodus: Deine Antwort wird vorgelesen. Höchstens drei bis vier Sätze. Bei umfangreichen Themen "
                "nennst du nur das Wichtigste und bietest an, bei Bedarf mehr zu erzählen. Keine Aufzählungen, keine Listen."
            )
        if config.SALUTATION:
            s = config.SALUTATION.strip().rstrip(",")
            prompt += (
                f"\n- Anrede: Jede Antwort beginnt mit „{s},“ – ausnahmslos, auch bei Rückfragen und Fehlern. "
                f"Beispiel: „{s}, das Licht im Bad ist aus.“"
            )
        if self.memory is not None:
            facts = self.memory.facts_for_prompt(config.MEMORY_MAX_FACTS)
            prompt += (
                "\n\nLangzeitgedächtnis (memory-Tool):\n"
                "- Speichere mit remember, was dauerhaft relevant ist: Name, Vorlieben, wichtige Personen, "
                "Geräte- und Raumzuordnungen (z.B. entity_ids), Gewohnheiten, laufende Projekte. "
                "Nichts Flüchtiges wie Uhrzeiten oder einmalige Aufgaben.\n"
                "- Widerspricht der Nutzer einem gespeicherten Fakt, korrigiere ihn mit update oder lösche ihn mit forget.\n"
                "- Fragen nach früheren Gesprächen beantwortest du mit recall_conversations.\n"
            )
            if facts:
                prompt += f"\nDas weißt du bereits über den Nutzer und seine Umgebung:\n{facts}"
            else:
                prompt += "\nDas Gedächtnis ist noch leer."
        return prompt

    # ── Öffentliche API ──────────────────────────────────────────────────────

    def reset(self):
        self.history.clear()

    def tool_schemas(self) -> List[Dict[str, Any]]:
        return [t.to_schema() for t in self.tools.values()]

    def cost_of(self, usage: Usage) -> Optional[float]:
        return estimate_cost_usd(self.provider.model, usage) if self.provider else None

    def usage_summary(self, usage: Usage) -> str:
        """Kurze Zeile wie '2 Aufrufe · 6.1k Eingabe (5.2k aus Cache) · 210 Ausgabe · ≈ 0.012 $'."""
        cost = self.cost_of(usage)

        def k(n: int) -> str:
            return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

        cached = f" ({k(usage.cache_read_tokens)} aus Cache)" if usage.cache_read_tokens else ""
        cost_str = f" · ≈ {cost:.3f} $" if cost is not None else ""
        return f"{usage.calls} Aufruf(e) · {k(usage.total_input)} Eingabe{cached} · {k(usage.output_tokens)} Ausgabe{cost_str}"

    def process_query(self, query: str) -> str:
        if self.provider is None:
            return (
                "Es ist kein LLM-Provider verfügbar. Bitte in der .env einen API-Key setzen "
                "(z.B. ANTHROPIC_API_KEY) oder Ollama starten.\n" + "\n".join(self.notes)
            )

        self.history.append({"role": "user", "content": query})
        self._log("user", query)
        schemas = self.tool_schemas()
        # System-Prompt einmal pro Anfrage fixieren: stabil für Prompt-Caching und damit
        # Fakten, die mitten im Tool-Loop gespeichert werden, nicht als "längst bekannt" erscheinen.
        system = self.system_prompt()
        self.last_usage = Usage()

        try:
            for _ in range(self.MAX_STEPS):
                response = self._chat(system, schemas)
                self._append_assistant(response)

                if not response.tool_calls:
                    self._trim_history()
                    answer = self._apply_salutation(response.text or "(keine Antwort erhalten)")
                    self._log("assistant", answer)
                    return answer

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
            response = self._chat(system, [])
            self._append_assistant(response)
            self._trim_history()
            answer = self._apply_salutation(response.text or "Ich habe die maximale Anzahl an Schritten erreicht.")
            self._log("assistant", answer)
            return answer

        except LLMError as e:
            self._rollback_turn()
            return f"Der KI-Dienst ist gerade nicht erreichbar: {e}"
        except KeyboardInterrupt:
            self._rollback_turn()
            raise

    # ── Intern ───────────────────────────────────────────────────────────────

    _LOWERCASE_STARTERS = {
        "es", "der", "die", "das", "ich", "du", "sie", "wir", "ihr", "dein", "deine", "deinen", "in", "im", "am",
        "auf", "für", "heute", "morgen", "gestern", "aktuell", "leider", "gerne", "gern", "natürlich", "alles",
        "kein", "keine", "nichts", "laut", "ja", "nein", "okay", "ok", "hier", "dort", "zurzeit", "derzeit",
        "momentan", "bei", "mit", "nach", "um", "an", "zu", "seit", "wie", "was", "wenn", "soll", "möchtest",
        "willst", "kann", "könnte", "habe", "hast", "haben", "gibt", "erledigt", "verstanden", "gemerkt",
    }

    @classmethod
    def _apply_salutation(cls, answer: str) -> str:
        """Stellt sicher, dass die Antwort mit der konfigurierten Anrede beginnt (falls das Modell sie vergisst)."""
        s = config.SALUTATION.strip().rstrip(",") if config.SALUTATION else ""
        if not s or not answer.strip():
            return answer
        stripped = answer.lstrip()
        if stripped.lower().startswith(s.lower()):
            return answer
        first_word = stripped.split(" ", 1)[0].strip(",.!?:;")
        if first_word.lower() in cls._LOWERCASE_STARTERS:
            stripped = stripped[0].lower() + stripped[1:]
        return f"{s}, {stripped}"

    def _log(self, role: str, content: str):
        if self.memory is None:
            return
        try:
            self.memory.log_message(self.session_id, role, content)
        except Exception:
            pass

    def _chat(self, system: str, schemas: List[Dict[str, Any]]) -> LLMResponse:
        assert self.provider is not None
        try:
            return self.provider.chat(system, self.history, schemas)
        except LLMError as e:
            if self.fallback is None:
                raise
            if self.on_notice:
                self.on_notice(f"{self.provider.describe()} nicht erreichbar ({e}). Wechsle zu {self.fallback.describe()}.")
            return self.fallback.chat(system, self.history, schemas)

    def _append_assistant(self, response: LLMResponse):
        self.last_usage = self.last_usage.add(response.usage)
        self.session_usage = self.session_usage.add(response.usage)
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
                result = self._execute_with_timeout(tool, call, args)

        if self.on_tool_result:
            self.on_tool_result(call, result)
        return result

    def _execute_with_timeout(self, tool: BaseTool, call: ToolCall, args: Dict[str, Any]) -> ToolResult:
        """Führt ein Tool in einem Hilfs-Thread aus, damit ein hängendes Tool Jarvis nicht blockiert."""
        import threading

        box: Dict[str, Any] = {}

        def run():
            try:
                box["result"] = tool.execute(**args)
            except TypeError as e:
                box["result"] = ToolResult.fail(
                    f"Ungültige Argumente für '{call.name}': {e}. Erhalten: {json.dumps(args, ensure_ascii=False)}"
                )
            except Exception as e:
                box["result"] = ToolResult.fail(f"Fehler in Tool '{call.name}': {e}")

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(self.TOOL_TIMEOUT)
        if worker.is_alive():
            return ToolResult.fail(
                f"Tool '{call.name}' hat nach {int(self.TOOL_TIMEOUT)} Sekunden nicht geantwortet und wurde abgebrochen. "
                "Bitte dem Nutzer kurz sagen, dass die Quelle nicht erreichbar war, oder eine andere Quelle versuchen."
            )
        return box.get("result") or ToolResult.fail(f"Tool '{call.name}' lieferte kein Ergebnis.")

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
