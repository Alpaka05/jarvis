"""Live-Anzeigen der Oberfläche: Wetter, Kalender, Smart Home, Spotify, Mail.

Jedes Widget holt seine Daten direkt aus dem passenden Tool – ohne LLM, also ohne Kosten – und
schickt sie als {"type": "widget", "name": …, "data": …} an verbundene Dashboards. Abgefragt wird
nur, solange ein Dashboard offen ist; Home Assistant meldet Änderungen über seinen WebSocket sofort.

Auswahl und Reihenfolge: HUD_WIDGETS. Was nicht eingerichtet ist (kein HA_TOKEN, kein Postfach …),
fehlt von selbst.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from config import config
from core import orb

log = logging.getLogger(__name__)

Publish = Callable[[Dict[str, Any]], None]


class Widget:
    name = ""
    interval = 60.0  # Sekunden zwischen zwei Abfragen
    hub: Optional["WidgetHub"] = None

    def available(self) -> bool:
        return True

    def fetch(self) -> Optional[Dict[str, Any]]:
        """Aktuelle Daten. None = gerade nichts anzuzeigen (z.B. Dienst nicht verknüpft)."""
        raise NotImplementedError

    def action(self, action: str, args: Dict[str, Any]) -> bool:
        """Klick in der Anzeige (z.B. Licht umschalten). True, wenn etwas ausgeführt wurde."""
        return False


class WidgetHub:
    def __init__(self, bus: orb.OrbEvents, widgets: List[Widget]):
        self.bus = bus
        self.widgets: Dict[str, Widget] = {}
        for widget in widgets:
            widget.hub = self
            self.widgets[widget.name] = widget
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = {name: threading.Event() for name in self.widgets}
        self._threads: List[threading.Thread] = []

    def start(self):
        if self._threads:
            return
        self._stop.clear()
        for widget in self.widgets.values():
            thread = threading.Thread(target=self._run, args=(widget,), name=f"widget-{widget.name}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self):
        self._stop.set()
        self.wake()
        self._threads.clear()

    def wake(self):
        """Alle Widgets sofort neu laden (z.B. wenn sich ein Dashboard verbindet)."""
        for event in self._wake.values():
            event.set()

    def refresh(self, name: str, delay: float = 0.8):
        """Ein Widget gleich neu laden – nach einem Tool-Aufruf, der seine Daten geändert haben kann."""
        event = self._wake.get(name)
        if event is None:
            return
        timer = threading.Timer(delay, event.set)  # kurz warten, bis der Dienst den neuen Stand kennt
        timer.daemon = True
        timer.start()

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._data)

    def publish(self, name: str, data: Optional[Dict[str, Any]]):
        """Speichert den Stand und schickt ihn an die Dashboards – nur, wenn sich etwas geändert hat."""
        with self._lock:
            if data is None:
                changed = self._data.pop(name, None) is not None
            else:
                changed = self._data.get(name) != data
                self._data[name] = data
        if changed:
            self.bus.hud({"type": "widget", "name": name, "data": data})

    def action(self, name: str, action: str, args: Dict[str, Any]):
        """Klick in einem Widget; läuft im Hintergrund, damit die Verbindung frei bleibt."""
        widget = self.widgets.get(name)
        if widget is None or not action:
            return

        def run():
            try:
                if widget.action(action, args):
                    self.refresh(name, delay=0.5)
            except Exception:
                log.exception("Widget %s: Aktion %s fehlgeschlagen", name, action)

        threading.Thread(target=run, name=f"widget-action-{name}", daemon=True).start()

    def _run(self, widget: Widget):
        wake = self._wake[widget.name]
        while not self._stop.is_set():
            wait: Optional[float] = None  # ohne Dashboard: schlafen, bis sich eines verbindet
            if self.bus.hud_active:
                wait = widget.interval
                ok = self._fetch(widget)
                listen = getattr(widget, "listen", None)
                if ok and listen is not None:
                    try:
                        # Blockiert, solange die Live-Verbindung steht; Änderungen kommen über publish
                        listen(
                            lambda data: self.publish(widget.name, data),
                            lambda: self._stop.is_set() or wake.is_set() or not self.bus.hud_active,
                        )
                    except Exception as e:
                        log.info("Widget %s: Live-Verbindung beendet (%s) – frage weiter regelmäßig ab", widget.name, e)
            wake.wait(wait)
            wake.clear()

    def _fetch(self, widget: Widget) -> bool:
        try:
            self.publish(widget.name, widget.fetch())
            return True
        except Exception as e:
            log.info("Widget %s nicht aktualisiert: %s", widget.name, e)
            with self._lock:
                previous = self._data.get(widget.name)
            if previous is None:  # noch nie Daten: Grund anzeigen statt einer leeren Kachel
                self.publish(widget.name, {"error": str(e)[:160]})
            return False


# ── Widgets ──────────────────────────────────────────────────────────────────


class WeatherWidget(Widget):
    name = "weather"
    interval = 900.0

    def available(self) -> bool:
        return bool(config.WEATHER_LOCATION)

    def fetch(self) -> Optional[Dict[str, Any]]:
        from tools.weather_tool import forecast

        return forecast(config.WEATHER_LOCATION, days=4)


class CalendarWidget(Widget):
    name = "calendar"
    interval = 60.0
    MAX_EVENTS = 6

    def __init__(self, tool=None):
        from tools.calendar_tool import CalendarTool

        self.tool = tool or CalendarTool()

    def fetch(self) -> Optional[Dict[str, Any]]:
        events = self.tool.list_events(days=14).data or []
        return {
            "events": [
                {"title": e.get("title", ""), "datetime": e.get("datetime", ""), "description": e.get("description", "")}
                for e in events[: self.MAX_EVENTS]
            ]
        }


class HomeAssistantWidget(Widget):
    """Zustände ausgewählter Entitäten. Erst per REST, dann live über den Home-Assistant-WebSocket."""

    name = "homeassistant"
    interval = 30.0  # nur relevant, wenn die Live-Verbindung nicht zustande kommt
    MAX_ENTITIES = 12
    AUTO_DOMAINS = ("light.", "climate.")  # ohne HUD_HA_ENTITIES: Lichter und Heizungen
    TOGGLE_DOMAINS = {"light", "switch", "fan", "input_boolean"}

    def __init__(self, tool=None, entity_ids: Optional[List[str]] = None):
        from tools.homeassistant_tool import HomeAssistantTool

        self.tool = tool or HomeAssistantTool()
        if entity_ids is None:
            entity_ids = [e.strip().lower() for e in config.HUD_HA_ENTITIES.split(",") if e.strip()]
        self.entity_ids = entity_ids[: self.MAX_ENTITIES]
        self._states: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []

    def available(self) -> bool:
        return self.tool._is_configured()

    def fetch(self) -> Optional[Dict[str, Any]]:
        if self.entity_ids:
            states = []
            for entity_id in self.entity_ids:
                resp = requests.get(f"{config.HA_URL}/api/states/{entity_id}", headers=self.tool._headers(), timeout=8)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                states.append(resp.json())
        else:
            everything = self.tool._fetch_states()
            states = [s for s in everything if s.get("entity_id", "").startswith(self.AUTO_DOMAINS)]
            states.sort(key=lambda s: (s.get("state") in ("off", "unavailable", "unknown"), s.get("entity_id", "")))
            states = states[: self.MAX_ENTITIES]
            states.sort(key=lambda s: s.get("entity_id", ""))
        self._states = {s["entity_id"]: s for s in states}
        self._order = [s["entity_id"] for s in states]
        return self._data()

    def _data(self) -> Dict[str, Any]:
        return {"entities": [self._entity(self._states[e]) for e in self._order if e in self._states]}

    @classmethod
    def _entity(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        attrs = state.get("attributes") or {}
        entity_id = state.get("entity_id", "")
        domain = entity_id.split(".")[0]
        brightness = attrs.get("brightness")
        return {
            "id": entity_id,
            "name": attrs.get("friendly_name") or entity_id,
            "domain": domain,
            "state": state.get("state", ""),
            "unit": attrs.get("unit_of_measurement") or "",
            "brightness": round(brightness / 2.55) if isinstance(brightness, (int, float)) else None,
            "temperature": attrs.get("current_temperature"),
            "target": attrs.get("temperature") if domain == "climate" else None,
            "toggle": domain in cls.TOGGLE_DOMAINS,
        }

    def listen(self, publish: Publish, should_stop: Callable[[], bool]):
        """Abonniert Zustandsänderungen der angezeigten Entitäten, bis should_stop() oder ein Fehler."""
        from websockets.sync.client import connect

        entity_ids = list(self._order)
        if not entity_ids:
            return
        url = config.HA_URL.replace("http", "ws", 1) + "/api/websocket"
        with connect(url, open_timeout=8, max_size=8_000_000) as ws:
            if json.loads(ws.recv(timeout=8)).get("type") != "auth_required":
                raise RuntimeError("unerwartete Begrüßung")
            ws.send(json.dumps({"type": "auth", "access_token": config.HA_TOKEN}))
            if json.loads(ws.recv(timeout=8)).get("type") != "auth_ok":
                raise RuntimeError("Anmeldung abgelehnt")
            # Gezielt nur diese Entitäten (braucht ein Admin-Token); sonst alle Änderungen und hier filtern
            ws.send(json.dumps({"id": 1, "type": "subscribe_trigger", "trigger": {"platform": "state", "entity_id": entity_ids}}))
            fallback_sent = False
            while not should_stop():
                try:
                    msg = json.loads(ws.recv(timeout=1.0))
                except TimeoutError:
                    continue
                if msg.get("type") == "result" and not msg.get("success", True):
                    if fallback_sent:
                        raise RuntimeError(f"Abo abgelehnt: {(msg.get('error') or {}).get('message', '?')}")
                    fallback_sent = True
                    ws.send(json.dumps({"id": 2, "type": "subscribe_events", "event_type": "state_changed"}))
                    continue
                if msg.get("type") != "event":
                    continue
                event = msg.get("event") or {}
                new = ((event.get("variables") or {}).get("trigger") or {}).get("to_state") or (event.get("data") or {}).get("new_state")
                if isinstance(new, dict) and new.get("entity_id") in self._states:
                    self._states[new["entity_id"]] = new
                    publish(self._data())

    def action(self, action: str, args: Dict[str, Any]) -> bool:
        entity_id = str(args.get("entity_id") or "")
        # Nur angezeigte, harmlose Geräte – alles andere geht über Jarvis und dessen Rückfragen
        if action != "toggle" or entity_id not in self._states or entity_id.split(".")[0] not in self.TOGGLE_DOMAINS:
            return False
        return self.tool.call_service(entity_id, "toggle").success


class SpotifyWidget(Widget):
    name = "spotify"
    interval = 5.0
    ART_NAME = "spotify.art"  # das Titelbild als eigene Nachricht, damit es nicht alle 5 s mitgeschickt wird
    ACTIONS = ("play", "pause", "next", "previous")

    def __init__(self, tool=None):
        from tools.spotify_tool import SpotifyTool

        self.tool = tool or SpotifyTool()
        self._art_url = ""
        if self.tool.backend() == "applescript":
            self.interval = 10.0  # jeder Abruf startet osascript

    def available(self) -> bool:
        return self.tool.backend() != "none"

    def fetch(self) -> Optional[Dict[str, Any]]:
        data = self.tool.now_playing()
        if data is None:
            return {"linked": False}
        art_url = data.pop("art_url", "")
        if art_url != self._art_url:
            self._art_url = art_url
            if self.hub is not None:
                self.hub.publish(self.ART_NAME, {"image": self._load_art(art_url)} if art_url else None)
        data["at"] = time.time()  # Zeitpunkt von progress_ms: die Oberfläche zählt von hier weiter
        return data

    @staticmethod
    def _load_art(url: str) -> str:
        try:
            if not url.startswith("https://"):
                return ""
            resp = requests.get(url, timeout=6)
            resp.raise_for_status()
            if len(resp.content) > 400_000 or not resp.headers.get("content-type", "").startswith("image/"):
                return ""
            mime = resp.headers["content-type"].split(";")[0]
            return f"data:{mime};base64,{base64.b64encode(resp.content).decode('ascii')}"
        except requests.RequestException:
            return ""

    def action(self, action: str, args: Dict[str, Any]) -> bool:
        if action not in self.ACTIONS:
            return False
        return self.tool.execute(action=action).success


class MailWidget(Widget):
    name = "mail"
    interval = 180.0

    def __init__(self, tool=None):
        from tools.mail_tool import MailTool

        self.tool = tool or MailTool()

    def available(self) -> bool:
        return self.tool._check_config()

    def fetch(self) -> Optional[Dict[str, Any]]:
        return self.tool.unread_summary(max_count=4)


WIDGET_TYPES = {
    "weather": (WeatherWidget, None),
    "calendar": (CalendarWidget, "calendar"),
    "homeassistant": (HomeAssistantWidget, "homeassistant"),
    "spotify": (SpotifyWidget, "spotify"),
    "mail": (MailWidget, "mail"),
}


def default_widgets(tools: Optional[Dict[str, Any]] = None, names: Optional[str] = None) -> List[Widget]:
    """Die in HUD_WIDGETS genannten, eingerichteten Widgets. `tools` sind die Tools des Agenten –
    die Widgets nutzen dieselben Instanzen (z.B. die Spotify-Anmeldung)."""
    tools = tools or {}
    widgets: List[Widget] = []
    for name in dict.fromkeys(n.strip() for n in (config.HUD_WIDGETS if names is None else names).split(",")):
        if name not in WIDGET_TYPES:
            if name:
                log.warning("HUD_WIDGETS: unbekanntes Widget %r (bekannt: %s)", name, ", ".join(WIDGET_TYPES))
            continue
        cls, tool_name = WIDGET_TYPES[name]
        try:
            widget = cls(tools.get(tool_name)) if tool_name else cls()
            if widget.available():
                widgets.append(widget)
        except Exception:
            log.exception("Widget %s nicht eingerichtet", name)
    return widgets
