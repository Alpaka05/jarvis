"""Browser-Tool: URLs/YouTube öffnen und (optional) autonome Browser-Aufgaben via browser-use.

browser-use ist ein optionales Extra (`uv sync --extra browser` + `playwright install chromium`).
"""
from __future__ import annotations

import asyncio
import threading
import urllib.parse

from config import config
from core import platform_utils
from tools.base import BaseTool, ToolResult


class BrowserTool(BaseTool):
    name = "browser"
    description = (
        "Browser-Aktionen: eine Webseite öffnen, eine YouTube-Suche/ein Video öffnen oder "
        "eine mehrstufige Aufgabe autonom im Browser erledigen lassen (z.B. etwas bestellen, ein Formular ausfüllen). "
        "Autonome Aufgaben laufen im Hintergrund und brauchen das Extra 'browser-use'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open_url", "youtube", "agent"],
                "description": "open_url = Seite öffnen, youtube = YouTube-Suche öffnen, agent = autonome Browser-Aufgabe",
            },
            "url": {"type": "string", "description": "URL für open_url."},
            "query": {"type": "string", "description": "Suchbegriff für youtube."},
            "task": {"type": "string", "description": "Ausführliche Aufgabenbeschreibung für agent (Ziel, Website, Details)."},
        },
        "required": ["action"],
    }

    # ── einfache Aktionen ────────────────────────────────────────────────────

    def open_youtube(self, query: str) -> ToolResult:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        if platform_utils.open_url(url):
            return ToolResult.ok(f"YouTube-Suche für '{query}' geöffnet.")
        return ToolResult.fail("Browser konnte nicht geöffnet werden.")

    # ── browser-use Agent (optional) ─────────────────────────────────────────

    def _make_browser_llm(self):
        """Erzeugt das LLM-Objekt für browser-use passend zum konfigurierten Provider."""
        from browser_use import llm as bu_llm  # type: ignore

        provider = config.LLM_PROVIDER
        if provider == "anthropic" and config.ANTHROPIC_API_KEY:
            return bu_llm.ChatAnthropic(model=config.ANTHROPIC_MODEL, api_key=config.ANTHROPIC_API_KEY)
        if provider == "openai" and config.OPENAI_API_KEY:
            return bu_llm.ChatOpenAI(model=config.OPENAI_MODEL, api_key=config.OPENAI_API_KEY)
        if provider == "gemini" and config.GEMINI_API_KEY:
            return bu_llm.ChatGoogle(model=config.GEMINI_MODEL, api_key=config.GEMINI_API_KEY)
        if provider == "ollama":
            return bu_llm.ChatOllama(model=config.OLLAMA_MODEL, host=config.OLLAMA_HOST)
        raise RuntimeError("Kein passender LLM-Provider für browser-use konfiguriert.")

    def run_agent(self, task: str) -> ToolResult:
        try:
            from browser_use import Agent  # type: ignore
        except ImportError:
            return ToolResult.fail(
                "browser-use ist nicht installiert. Installation: `uv sync --extra browser` und `playwright install chromium`."
            )
        try:
            llm = self._make_browser_llm()
        except Exception as e:
            return ToolResult.fail(f"browser-use LLM konnte nicht erstellt werden: {e}")

        def _worker():
            try:
                agent = Agent(task=task, llm=llm)
                asyncio.run(agent.run())
            except Exception as e:  # pragma: no cover
                print(f"\n[browser-use Fehler]: {e}")

        threading.Thread(target=_worker, daemon=True).start()
        return ToolResult.ok(
            f"Browser-Agent gestartet (läuft im Hintergrund). Aufgabe: {task}"
        )

    def execute(self, action: str = "open_url", **kwargs) -> ToolResult:
        if action == "open_url":
            url = (kwargs.get("url") or "").strip()
            if not url:
                return ToolResult.fail("Bitte eine URL angeben ('url').")
            return ToolResult.ok(f"{url} geöffnet.") if platform_utils.open_url(url) else ToolResult.fail("Browser konnte nicht geöffnet werden.")
        if action in ("youtube", "video"):
            q = (kwargs.get("query") or "").strip()
            if not q:
                return ToolResult.fail("Bitte einen Suchbegriff angeben ('query').")
            return self.open_youtube(q)
        if action == "agent":
            task = (kwargs.get("task") or kwargs.get("query") or "").strip()
            if not task:
                return ToolResult.fail("Bitte die Aufgabe beschreiben ('task').")
            return self.run_agent(task)
        return ToolResult.fail(f"Unbekannte Browser-Aktion: {action}")
