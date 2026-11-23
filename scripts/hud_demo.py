"""Dashboard ausprobieren – ohne API-Schlüssel, Mikrofon oder eingerichtete Dienste.

    uv run python scripts/hud_demo.py            # Server starten, Adresse für den Browser ausgeben
    uv run python scripts/hud_demo.py --window   # zusätzlich das Jarvis-Fenster öffnen (muss gebaut sein)

Ein gespieltes Jarvis beantwortet ein paar Fragen über das echte Protokoll (core/hud.py):
„Was gibt es Neues in der Raumfahrt?“ (Suche mit Quellen), „Zeig mir die Züge nach Hamburg“
(Tabelle über das display-Tool), „Schick Max eine Mail“ (Rückfrage). Die Anzeigen links zeigen
Beispieldaten. Für den Browser muss die Oberfläche laufen: `cd orb && npm run dev`.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import orb  # noqa: E402
from core.agent import JarvisAgent  # noqa: E402
from core.hud import Session, hud  # noqa: E402
from core.llm.base import LLMProvider, LLMResponse, ToolCall  # noqa: E402
from core.widgets import Widget, WidgetHub  # noqa: E402
from tools.base import BaseTool, Policy, Risk, ToolResult  # noqa: E402
from tools.display_tool import DisplayTool  # noqa: E402

RESULTS = [
    ("https://www.nasa.gov/artemis-iii", "Artemis III: Mission Overview", "NASA nennt Mitte 2027 als neuen Starttermin für die Mondlandung."),
    ("https://www.esa.int/Science_Exploration/ariane6", "Ariane 6 – ESA", "Der vierte Flug der Ariane 6 brachte zwei Galileo-Satelliten in die Umlaufbahn."),
    ("https://www.tagesschau.de/wissen/raumfahrt", "Raumfahrt aktuell – tagesschau.de", "Überblick über die Starts der kommenden Wochen."),
]


class DemoSearch(BaseTool):
    name = "web_search"
    description = "Gespielte Websuche."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

    def use_sources(self, registry):
        self.sources = registry

    def policy(self, **kwargs) -> Policy:
        return Policy(untrusted_output=True)

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        time.sleep(1.2)
        lines = [f"[{self.sources.add(url, title, text)}] {title}\n   {url}\n   {text}" for url, title, text in RESULTS]
        return ToolResult.ok(f"Suchergebnisse für '{query}':\n" + "\n".join(lines))


class DemoMail(BaseTool):
    name = "mail"
    description = "Gespielter Mailversand."
    parameters = {"type": "object", "properties": {}}

    def policy(self, **kwargs) -> Policy:
        return Policy(Risk.CONFIRM, "E-Mail an max@example.org senden\nBetreff: Treffen am Freitag\nText:\nHallo Max, passt dir 15 Uhr?")

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult.ok("E-Mail an max@example.org gesendet.")


class DemoProvider(LLMProvider):
    """Antwortet nach Stichworten statt mit einem Sprachmodell – und tippt dabei wie ein echtes."""

    name = "demo"
    model = "demo"

    def describe(self) -> str:
        return "Demo (kein KI-Dienst)"

    def chat(self, system, messages, tools) -> LLMResponse:
        last = messages[-1]
        query = next(m["content"] for m in reversed(messages) if m["role"] == "user").lower()
        after_tool = last["role"] == "tool"
        if any(w in query for w in ("raumfahrt", "neues", "nachricht")):
            if not after_tool:
                return LLMResponse(text="", tool_calls=[ToolCall(id="s1", name="web_search", arguments={"action": "news", "query": "Raumfahrt aktuell"})])
            n = [int(x) for x in re.findall(r"\[(\d+)\]", last["content"])]
            return LLMResponse(
                text=f"Die Mondlandung Artemis III ist auf Mitte 2027 verschoben [{n[0]}]. In Europa hat die Ariane 6 ihren "
                f"vierten Flug geschafft und zwei Galileo-Satelliten ausgesetzt [{n[1]}]. Soll ich etwas davon vertiefen?"
            )
        if any(w in query for w in ("zug", "züge", "tabelle", "zeig")):
            if not after_tool:
                args = {
                    "kind": "table", "title": "Kiel → Hamburg, heute Abend", "columns": ["Ab", "An", "Zug", "Gleis"],
                    "rows": [["18:21", "19:35", "RE 7", "3"], ["18:43", "19:55", "ICE 1178", "4"], ["19:21", "20:35", "RE 7", "3"]],
                    "text": "Alle Verbindungen ohne Umstieg.",
                }
                return LLMResponse(text="", tool_calls=[ToolCall(id="d1", name="display", arguments=args)])
            return LLMResponse(text="Die nächsten drei Verbindungen stehen rechts in der Anzeige. Am schnellsten ist der ICE um 18:43.")
        if "mail" in query:
            if not after_tool:
                return LLMResponse(text="", tool_calls=[ToolCall(id="m1", name="mail", arguments={})])
            sent = "abgelehnt" not in last["content"]
            return LLMResponse(text="Die Mail an Max ist raus." if sent else "In Ordnung, ich habe nichts gesendet.")
        return LLMResponse(text="Das ist die Demo – frag mich nach Neuem aus der Raumfahrt, nach Zügen nach Hamburg oder lass mich eine Mail schicken.")

    def chat_stream(self, system, messages, tools, on_text) -> LLMResponse:
        time.sleep(0.6)
        response = self.chat(system, messages, tools)
        words = response.text.split(" ")
        for i, word in enumerate(words):
            on_text(word + (" " if i < len(words) - 1 else ""))
            time.sleep(0.04)
        return response


class SilentVoice:
    """Keine Sprachausgabe in der Demo; der Orb zeigt trotzdem kurz „Spricht“."""

    enabled = False

    def stop(self): ...

    def is_speaking(self) -> bool:
        return False

    def open_stream(self, listen_for_interrupt: bool = True):
        class Stream:
            def feed(self, text): ...

            def close(self): ...

        return Stream()


class Static(Widget):
    interval = 5.0

    def __init__(self, name: str, data):
        self.name, self.data = name, data

    def fetch(self):
        return self.data() if callable(self.data) else self.data


class DemoHome(Widget):
    name = "homeassistant"
    interval = 5.0

    def __init__(self):
        self.lights = {"light.wohnzimmer": ("Wohnzimmer", True), "light.bad": ("Licht Bad", False), "switch.kaffee": ("Kaffeemaschine", False)}

    def fetch(self):
        entities = [
            {"id": i, "name": n, "domain": i.split(".")[0], "state": "on" if on else "off", "unit": "",
             "brightness": 70 if on and i.startswith("light.") else None, "temperature": None, "target": None, "toggle": True}
            for i, (n, on) in self.lights.items()
        ]
        entities.append({"id": "climate.wohnzimmer", "name": "Heizung Wohnzimmer", "domain": "climate", "state": "heat", "unit": "",
                         "brightness": None, "temperature": 21.5, "target": 22, "toggle": False})
        return {"entities": entities}

    def action(self, action, args) -> bool:
        entity = args.get("entity_id")
        if action != "toggle" or entity not in self.lights:
            return False
        name, on = self.lights[entity]
        self.lights[entity] = (name, not on)
        return True


def demo_widgets():
    from datetime import datetime, timedelta

    start = time.time()
    today = datetime.now().replace(minute=0, second=0, microsecond=0)
    days = [("Mo", 4, 0, "cloudy", "bedeckt", 10), ("Di", 3, -1, "fog", "Nebel", 5), ("Mi", 3, 0, "snow", "Schneeschauer", 40), ("Do", 6, 2, "rain", "Regen", 80)]
    weather = {
        "location": "Kiel", "region": "Schleswig-Holstein, Deutschland",
        "current": {"temp": 1.4, "feels": -3.0, "text": "bedeckt", "icon": "cloudy", "wind": 9, "humidity": 80, "is_day": True},
        "days": [{"date": "", "weekday": w, "max": hi, "min": lo, "icon": icon, "text": text, "rain": rain} for w, hi, lo, icon, text, rain in days],
    }
    calendar = {"events": [
        {"title": "Zahnarzt", "datetime": today.replace(hour=14).isoformat(), "description": "Kontrolle"},
        {"title": "Training", "datetime": today.replace(hour=18, minute=30).isoformat(), "description": ""},
        {"title": "Abgabe Projektbericht", "datetime": (today + timedelta(days=1)).replace(hour=9).isoformat(), "description": ""},
        {"title": "Essen mit Lea", "datetime": (today + timedelta(days=3)).replace(hour=19).isoformat(), "description": ""},
    ]}
    spotify = lambda: {  # noqa: E731
        "playing": True, "track": "Weightless", "artists": "Marconi Union", "album": "Weightless", "device": "MacBook",
        "progress_ms": int((time.time() - start) * 1000) % 480000, "duration_ms": 480000, "controls": True, "at": time.time(),
    }
    mail = {"unread": 3, "items": [
        {"from": "Lea Hansen", "subject": "Re: Wochenende", "date": ""},
        {"from": "Stadtwerke Kiel", "subject": "Ihre Abrechnung für Oktober", "date": ""},
    ]}
    return [Static("weather", weather), Static("calendar", calendar), DemoHome(), Static("spotify", spotify), Static("mail", mail)]


def main():
    port = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--port=")), 8765)
    agent = JarvisAgent(provider=DemoProvider(), tools=[DemoSearch(), DemoMail()])
    voice = SilentVoice()
    widgets = WidgetHub(orb.bus, demo_widgets())
    hud.attach(agent, voice, widgets)
    agent.add_tool(DisplayTool(hud))
    session = Session(hud, agent, voice)
    agent.confirm = session.confirm
    widgets.start()
    session.start()

    if "--window" in sys.argv[1:]:
        ok, message = orb.open_window(port=port, hud=True)
        print(message)
    else:
        error = orb.start(port=port)
        if error:
            raise SystemExit(error)
    actual = orb.bus.address.rsplit(":", 1)[-1]
    print(f"Demo läuft. Im Browser (nach `cd orb && npm run dev`):\n  http://localhost:5173/hud.html?port={actual}&token={orb.bus.token}")
    print("Strg+C beendet.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
