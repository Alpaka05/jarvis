"""Oberfläche (HUD): verbindet Agent, Sprachmodus und Widgets mit dem Dashboard-Fenster.

Das Fenster (orb/, Seite hud.html) hängt am selben WebSocket wie der Orb (core/orb.py) und meldet
sich mit ?client=hud an. Zusätzlich zu Zustand und Pegel bekommt es:

    {"type": "hello", "protocol": 2, "turns": […], "cards": […], "widgets": {…}, "confirm": …, "status": {…}}
    {"type": "turn.start", "id": "t3", "query": "…"}
    {"type": "delta", "id": "t3", "text": "…"}                      Antworttext, während er entsteht
    {"type": "tool.start", "id": "t3", "call": "c1", "name": "web_search", "args": "{…}"}
    {"type": "tool.end", "id": "t3", "call": "c1", "ok": true, "summary": "…"}
    {"type": "turn.end", "id": "t3", "text": "…", "sources": […], "usage": "…", "error": false, "cancelled": false}
    {"type": "card", "card": {"id": "k1", "kind": "table", …}}      vom display-Tool
    {"type": "card.remove", "id": "k1"} / {"type": "cards.clear"}
    {"type": "widget", "name": "weather", "data": {…}}              Live-Daten (core/widgets.py)
    {"type": "confirm.request", "id": "q1", "prompt": "…", "voice": false, "warning": false}
    {"type": "confirm.resolved", "id": "q1", "result": true, "via": "ui"}
    {"type": "status", …} / {"type": "notice", "level": "info", "text": "…"} / {"type": "reset"}

Und es darf senden:

    {"type": "input", "text": "…"}            Anfrage wie im Terminal getippt
    {"type": "confirm.reply", "id": "q1", "result": true}
    {"type": "stop"}                          Sprachausgabe und laufende Anfrage abbrechen
    {"type": "voice.toggle"} / {"type": "voice.once"} / {"type": "tts", "enabled": false} / {"type": "reset"}
    {"type": "card.dismiss", "id": "k1"} / {"type": "cards.clear"}
    {"type": "open_url", "url": "…"}          nur Links, die Jarvis selbst angezeigt hat
    {"type": "action", "widget": "spotify", "action": "next"}

Alles hier ist so gebaut, dass es ohne verbundenes Fenster nichts kostet und nie blockiert.
"""
from __future__ import annotations

import itertools
import json
import logging
import queue
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Dict, List, Optional

from config import config
from core import orb, platform_utils
from core.agent import UNTRUSTED_NOTE, AgentListener, JarvisAgent, Turn
from core.llm import ToolCall
from core.llm.base import b64
from tools.base import ToolResult

log = logging.getLogger(__name__)

PROTOCOL = 2
MAX_TURNS = 40  # so viele Runden bekommt ein neu geöffnetes Fenster nachgeliefert
MAX_CARDS = 12
MAX_INPUT_CHARS = 4000
CONFIRM_TIMEOUT = 180.0  # Sekunden ohne Antwort auf eine Rückfrage = Nein
URL_RE = re.compile(r"https?://[^\s<>\"'\])}]+")

# Tools, nach deren Aufruf das passende Widget sofort neu geladen wird
WIDGET_OF_TOOL = {"calendar": "calendar", "homeassistant": "homeassistant", "spotify": "spotify", "mail": "mail"}


class PendingConfirm:
    def __init__(self, confirm_id: str, prompt: str, voice: bool):
        self.id = confirm_id
        self.prompt = prompt
        self.voice = voice
        self.event = threading.Event()
        self.result: Optional[bool] = None

    @property
    def answered(self) -> bool:
        return self.event.is_set()

    def message(self) -> Dict[str, Any]:
        return {
            "type": "confirm.request",
            "id": self.id,
            "prompt": self.prompt.replace(UNTRUSTED_NOTE, "").strip(),
            "voice": self.voice,
            "warning": UNTRUSTED_NOTE in self.prompt,
        }


class Hud(AgentListener):
    def __init__(self, bus: orb.OrbEvents):
        self.bus = bus
        self.agent: Optional[JarvisAgent] = None
        self.voice = None  # VoiceEngine
        self.widgets = None  # WidgetHub (core/widgets.py)
        self.voice_loop = None  # laufender Sprachmodus (VoiceLoop), sonst None
        self.voice_loading = False
        self.commands: "queue.Queue[tuple]" = queue.Queue()  # arbeitet die Session ab
        self._lock = threading.RLock()
        self._turns: "deque[Dict[str, Any]]" = deque(maxlen=MAX_TURNS)
        self._cards: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._confirm: Optional[PendingConfirm] = None
        self._urls: set = set()  # Links, die an die Oberfläche gingen – nur die darf sie öffnen lassen
        self._ids = itertools.count(1)
        self._tts_wanted = True
        self._busy = False  # läuft gerade eine Anfrage? (agent.busy ist in turn_finished noch True)

    # ── Aufbau ───────────────────────────────────────────────────────────────

    def attach(self, agent: JarvisAgent, voice=None, widgets=None):
        """Hängt die Oberfläche an Agent, Sprachausgabe und Widgets. Mehrfach aufrufbar."""
        self.agent, self.voice, self.widgets = agent, voice, widgets
        if self not in agent.listeners:
            agent.listeners.append(self)
        self._tts_wanted = bool(voice.enabled) if voice is not None else False
        self.bus.on_message = self.handle
        self.bus.hello = self.snapshot
        self.bus.on_hud_change = self._hud_changed

    @property
    def active(self) -> bool:
        """True, wenn ein Dashboard-Fenster verbunden ist."""
        return self.bus.hud_active

    def emit(self, msg: Dict[str, Any]):
        self.bus.hud(msg)

    def _hud_changed(self, connected: bool):
        if not connected:
            self._resolve_confirm(False)  # niemand mehr da, der antworten könnte
        if self.widgets is not None:
            self.widgets.wake()

    # ── Stand für neu verbundene Fenster ─────────────────────────────────────

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            hello = {
                "type": "hello",
                "protocol": PROTOCOL,
                "turns": [dict(t, tools=[dict(c) for c in t["tools"]]) for t in self._turns],
                "cards": list(self._cards.values()),
                "widgets": self.widgets.snapshot() if self.widgets is not None else {},
                "widget_names": list(self.widgets.widgets) if self.widgets is not None else [],
                "confirm": self._confirm.message() if self._confirm is not None else None,
                "status": self.status(),
            }
        return [hello]

    def status(self) -> Dict[str, Any]:
        agent, voice = self.agent, self.voice
        status: Dict[str, Any] = {
            "type": "status",
            "voice_mode": self.voice_loop is not None,
            "voice_loading": self.voice_loading,
            "tts": bool(voice is not None and voice.enabled),
            "tts_available": self._tts_wanted,
            "user": config.USER_NAME,
        }
        if agent is not None:
            usage = agent.session_usage
            status.update(
                provider=agent.provider.describe() if agent.provider else "",
                fallback=agent.fallback.describe() if agent.fallback else "",
                busy=self._busy,
                cost=agent.cost_of(usage) if usage.calls else None,
                usage=agent.usage_summary(usage) if usage.calls else "",
                untrusted=agent.has_untrusted_content(),
                notes=list(agent.notes),
            )
        return status

    def push_status(self):
        if self.active:
            self.emit(self.status())

    def notice(self, text: str, level: str = "info"):
        self.emit({"type": "notice", "level": level, "text": str(text)[:400]})

    def set_voice_loop(self, loop, loading: bool = False):
        """Meldet Beginn (loop) und Ende (None) des Sprachmodus."""
        self.voice_loop, self.voice_loading = loop, loading
        self.push_status()

    # ── Beobachter des Agenten ───────────────────────────────────────────────

    def _entry(self, turn: Turn) -> Optional[Dict[str, Any]]:
        for entry in reversed(self._turns):
            if entry["id"] == turn.id:
                return entry
        return None

    def turn_started(self, turn: Turn):
        entry = {
            "id": turn.id, "query": turn.query, "text": "", "tools": [], "sources": [],
            "done": False, "error": False, "cancelled": False, "usage": "", "time": time.time(),
        }
        with self._lock:
            self._turns.append(entry)
            self._busy = True
        self._remember_urls(turn.query)
        self.emit({"type": "turn.start", "id": turn.id, "query": turn.query})
        self.push_status()

    def text(self, turn: Turn, chunk: str):
        with self._lock:
            entry = self._entry(turn)
            if entry is not None:
                entry["text"] += chunk
        self.emit({"type": "delta", "id": turn.id, "text": chunk})

    def tool_started(self, turn: Turn, call: ToolCall):
        args = json.dumps(call.arguments, ensure_ascii=False) if call.arguments else ""
        item = {"call": call.id, "name": call.name, "args": args[:400], "ok": None, "summary": ""}
        with self._lock:
            entry = self._entry(turn)
            if entry is not None:
                entry["tools"].append(item)
        self.emit({"type": "tool.start", "id": turn.id, **{k: item[k] for k in ("call", "name", "args")}})

    def tool_finished(self, turn: Turn, call: ToolCall, result: ToolResult):
        lines = result.output.strip().splitlines()
        summary = (lines[0] if lines else "")[:200]
        with self._lock:
            entry = self._entry(turn)
            for item in entry["tools"] if entry is not None else []:
                if item["call"] == call.id and item["ok"] is None:
                    item["ok"], item["summary"] = result.success, summary
                    break
        self.emit({"type": "tool.end", "id": turn.id, "call": call.id, "ok": result.success, "summary": summary})
        if call.name == "screen" and result.success and isinstance(result.data, dict) and result.data.get("image"):
            self._show_screenshot(result.data)
        widget = WIDGET_OF_TOOL.get(call.name)
        if widget and self.widgets is not None:
            self.widgets.refresh(widget)

    def turn_finished(self, turn: Turn):
        with self._lock:
            entry = self._entry(turn)
            if entry is not None:
                entry.update(
                    text=turn.text, sources=turn.sources, done=True, error=turn.error,
                    cancelled=turn.cancelled, usage=turn.usage,
                )
            self._busy = False
        self._remember_urls(turn.text)
        for source in turn.sources:
            self._urls.add(source["url"])
        self.emit(
            {
                "type": "turn.end", "id": turn.id, "text": turn.text, "sources": turn.sources,
                "usage": turn.usage, "error": turn.error, "cancelled": turn.cancelled,
            }
        )
        self.push_status()

    def reset(self):
        """Gesprächsverlauf und Karten leeren (Befehl 'reset')."""
        with self._lock:
            self._turns.clear()
            self._cards.clear()
        self.emit({"type": "reset"})
        self.push_status()

    # ── Karten (display-Tool) ────────────────────────────────────────────────

    def show_card(self, card: Dict[str, Any], card_id: str = "") -> str:
        """Zeigt eine Karte oder ersetzt die mit derselben id. Gibt die id zurück."""
        with self._lock:
            if not card_id or card_id not in self._cards:
                card_id = f"k{next(self._ids)}"
            card = {**card, "id": card_id, "created": time.time()}
            self._cards[card_id] = card
            self._cards.move_to_end(card_id)
            dropped = []
            while len(self._cards) > MAX_CARDS:
                dropped.append(self._cards.popitem(last=False)[0])
        for value in (card.get("text"), *(card.get("items") or []), *(c for row in card.get("rows") or [] for c in row)):
            self._remember_urls(value)
        for source in card.get("sources") or []:
            self._urls.add(source.get("url", ""))
        for old in dropped:
            self.emit({"type": "card.remove", "id": old})
        self.emit({"type": "card", "card": card})
        return card_id

    def remove_card(self, card_id: str) -> bool:
        with self._lock:
            found = self._cards.pop(card_id, None) is not None
        if found:
            self.emit({"type": "card.remove", "id": card_id})
        return found

    def clear_cards(self) -> int:
        with self._lock:
            count = len(self._cards)
            self._cards.clear()
        self.emit({"type": "cards.clear"})
        return count

    def _show_screenshot(self, data: Dict[str, Any]):
        try:
            image = f"data:{data.get('mime', 'image/jpeg')};base64,{b64(data['image'])}"
            self.show_card({"kind": "image", "title": "Bildschirm", "image": image, "text": "Das hat Jarvis gerade gesehen."})
        except Exception:
            log.exception("Screenshot-Karte nicht angezeigt")

    # ── Rückfragen ───────────────────────────────────────────────────────────

    def confirm_begin(self, prompt: str, voice: bool = False) -> Optional[PendingConfirm]:
        """Zeigt eine Rückfrage an. None, wenn kein Fenster verbunden ist."""
        if not self.active:
            return None
        pending = PendingConfirm(f"q{next(self._ids)}", prompt, voice)
        with self._lock:
            self._confirm = pending
        self.emit(pending.message())
        return pending

    def confirm_end(self, pending: Optional[PendingConfirm], result: bool, via: str):
        if pending is None:
            return
        with self._lock:
            if self._confirm is pending:
                self._confirm = None
        self.emit({"type": "confirm.resolved", "id": pending.id, "result": bool(result), "via": via})

    def confirm(self, prompt: str) -> bool:
        """Rückfrage über die Oberfläche: wartet auf Ja/Nein. Ohne Fenster oder Antwort: Nein."""
        pending = self.confirm_begin(prompt)
        if pending is None:
            return False
        answered = pending.event.wait(CONFIRM_TIMEOUT)
        result = bool(pending.result) if answered else False
        self.confirm_end(pending, result, "ui" if answered else "timeout")
        return result

    def _resolve_confirm(self, result: bool, confirm_id: Optional[str] = None):
        with self._lock:
            pending = self._confirm
        if pending is None or pending.answered or (confirm_id is not None and pending.id != confirm_id):
            return
        pending.result = result
        pending.event.set()

    # ── Nachrichten der Oberfläche ───────────────────────────────────────────

    def handle(self, msg: Dict[str, Any]):
        """Läuft im Thread der Verbindung: Schnelles sofort, alles andere an die Session."""
        kind = msg["type"]
        if kind == "confirm.reply":
            self._resolve_confirm(msg.get("result") is True, str(msg.get("id") or ""))
        elif kind == "stop":
            self.stop()
        elif kind == "input":
            text = str(msg.get("text") or "").strip()[:MAX_INPUT_CHARS]
            if text:
                self.commands.put(("input", text))
        elif kind in ("voice.toggle", "voice.once", "reset"):
            self.commands.put((kind, None))
        elif kind == "tts":
            self.set_tts(msg.get("enabled") is True)
        elif kind == "card.dismiss":
            self.remove_card(str(msg.get("id") or ""))
        elif kind == "cards.clear":
            self.clear_cards()
        elif kind == "open_url":
            self.open_url(str(msg.get("url") or ""))
        elif kind == "action" and self.widgets is not None:
            self.widgets.action(str(msg.get("widget") or ""), str(msg.get("action") or ""), msg)

    def stop(self):
        """Stopp-Knopf: Sprachausgabe aus, laufende Anfrage abbrechen, offene Rückfrage = Nein."""
        if self.voice is not None:
            self.voice.stop()
        if self.agent is not None:
            self.agent.cancel()
        self._resolve_confirm(False)

    def set_tts(self, enabled: bool):
        voice = self.voice
        if voice is None or not self._tts_wanted:
            return
        if not enabled:
            voice.stop()
        voice.enabled = enabled
        self.push_status()

    def open_url(self, url: str) -> bool:
        """Öffnet einen Link im Browser – nur, wenn Jarvis ihn selbst angezeigt hat (Quelle, Karte,
        Antwort). So kann eine manipulierte Seite im Fenster keine beliebigen Adressen aufrufen lassen."""
        url = url.strip()
        known = url in self._urls or (self.agent is not None and self.agent.sources.knows(url))
        if not url.lower().startswith(("http://", "https://")) or not known:
            log.warning("Oberfläche wollte unbekannten Link öffnen: %r", url[:200])
            return False
        return platform_utils.open_url(url)

    def _remember_urls(self, text: Any):
        if not isinstance(text, str) or "http" not in text:
            return
        if len(self._urls) > 5000:
            self._urls.clear()
        for url in URL_RE.findall(text):
            self._urls.add(url.rstrip(".,;:!?"))


class Session:
    """Führt aus, was über die Oberfläche hereinkommt – in einem eigenen Thread, damit die Verbindung
    frei bleibt (Stopp und Rückfrage-Antworten müssen während einer laufenden Anfrage ankommen).

    Läuft neben der Terminal-Eingabe (dann kommen Rückfragen für Terminal-Anfragen weiter im
    Terminal) oder allein (`main.py --ui`, alle Rückfragen über die Oberfläche).
    """

    RESET_WORDS = ("reset", "neu", "clear")

    def __init__(
        self,
        hud: Hud,
        agent: JarvisAgent,
        voice,
        terminal_confirm: Optional[Callable[[str], bool]] = None,
        voice_loop_factory: Optional[Callable[[], Any]] = None,
    ):
        self.hud, self.agent, self.voice = hud, agent, voice
        self.terminal_confirm = terminal_confirm
        self.voice_loop_factory = voice_loop_factory
        self._worker: Optional[threading.Thread] = None
        self._voice_thread: Optional[threading.Thread] = None
        self._listener = None  # VoiceInputListener für einmalige Spracheingabe
        self._stop = threading.Event()

    def start(self):
        if self._worker is None or not self._worker.is_alive():
            self._stop.clear()
            self._worker = threading.Thread(target=self._work, name="hud-session", daemon=True)
            self._worker.start()

    def close(self):
        self._stop.set()
        self.stop_voice()

    # ── Rückfragen: dorthin, wo die Anfrage herkam ───────────────────────────

    def confirm(self, prompt: str) -> bool:
        if self.terminal_confirm is None or threading.current_thread() is self._worker:
            return self.hud.confirm(prompt)
        return self.terminal_confirm(prompt)

    # ── Befehle ──────────────────────────────────────────────────────────────

    def _work(self):
        while not self._stop.is_set():
            try:
                kind, arg = self.hud.commands.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.dispatch(kind, arg)
            except Exception as e:
                log.exception("Befehl der Oberfläche (%s) fehlgeschlagen", kind)
                self.hud.bus.error(str(e)[:120])
                self.hud.bus.state("idle")
                self.hud.notice(f"Fehler: {e}", "error")

    def dispatch(self, kind: str, arg: Any = None):
        if kind == "input":
            if arg.lower() in self.RESET_WORDS:
                self.reset()
            else:
                self.run_turn(arg)
        elif kind == "reset":
            self.reset()
        elif kind == "voice.toggle":
            self.stop_voice() if self.hud.voice_loop is not None else self.start_voice()
        elif kind == "voice.once":
            self.voice_once()

    def reset(self):
        self.voice.stop()
        self.agent.reset()
        self.hud.reset()

    def run_turn(self, text: str) -> str:
        self.voice.stop()  # laufende Ausgabe abbrechen, wenn eine neue Anfrage kommt
        self.hud.bus.transcript("user", text)
        self.hud.bus.state("thinking")
        speech = self.voice.open_stream()  # spricht schon, während die Antwort noch entsteht
        try:
            response = self.agent.process_query(text, on_text=speech.feed)
        finally:
            speech.close()
        self.hud.bus.transcript("assistant", response)
        if not self.voice.is_speaking():
            self.hud.bus.state("idle")
        return response

    # ── Sprache ──────────────────────────────────────────────────────────────

    def start_voice(self):
        """Startet den Wake-Word-Sprachmodus im Hintergrund (das Modell lädt einige Sekunden)."""
        if self.hud.voice_loop is not None or (self._voice_thread is not None and self._voice_thread.is_alive()):
            return
        if self.voice_loop_factory is None:
            self.hud.notice("Sprachmodus ist hier nicht verfügbar.", "error")
            return

        def run():
            self.hud.set_voice_loop(None, loading=True)
            try:
                loop = self.voice_loop_factory()
            except Exception as e:
                log.exception("Sprachmodus nicht verfügbar")
                self.hud.set_voice_loop(None)
                self.hud.notice(f"Sprachmodus nicht verfügbar: {e}", "error")
                return
            try:
                loop.run()  # meldet sich selbst bei der Oberfläche an und ab
            except Exception as e:
                log.exception("Sprachmodus abgebrochen")
                self.hud.notice(f"Sprachmodus abgebrochen: {e}", "error")
            finally:
                self.hud.set_voice_loop(None)

        self._voice_thread = threading.Thread(target=run, name="voice-loop", daemon=True)
        self._voice_thread.start()

    def stop_voice(self):
        loop = self.hud.voice_loop
        if loop is not None:
            loop.stop()

    def voice_once(self):
        """Mikrofon-Knopf: eine Frage aufnehmen und beantworten (wie 'v' im Terminal)."""
        if self.hud.voice_loop is not None:
            return  # der Sprachmodus hat das Mikrofon schon
        self.voice.stop()
        try:
            if self._listener is None:
                from core.voice_input import VoiceInputListener

                self._listener = VoiceInputListener(
                    language=config.LANGUAGE,
                    vad_threshold=config.VAD_THRESHOLD,
                    silence_limit=config.SILENCE_LIMIT_SECONDS,
                )
            self.hud.bus.state("listening")
            text = self._listener.record_and_recognize()
        except Exception as e:
            self.hud.bus.state("idle")
            self.hud.notice(f"Mikrofon nicht verfügbar: {e}", "error")
            return
        if not text:
            self.hud.bus.state("idle")
            self.hud.notice("Ich habe nichts verstanden.")
            return
        self.run_turn(text)


hud = Hud(orb.bus)
