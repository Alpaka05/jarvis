"""Widgets der Oberfläche, Wetter-Tool und display-Tool."""
import io
import json
import threading
import time

import pytest

from config import config
from core.sources import SourceRegistry
from core.widgets import (
    CalendarWidget,
    HomeAssistantWidget,
    SpotifyWidget,
    Widget,
    WidgetHub,
    default_widgets,
)
from tools import weather_tool
from tools.base import Risk, ToolResult
from tools.calendar_tool import CalendarTool
from tools.display_tool import DisplayTool, encode_image
from tools.weather_tool import WeatherTool

# ── Verwaltung ───────────────────────────────────────────────────────────────


class FakeBus:
    """Steht für den Ereigniskanal: merkt sich, was an Dashboards ginge."""

    def __init__(self, hud_active=True):
        self.hud_active = hud_active
        self.sent = []

    def hud(self, msg):
        self.sent.append(msg)


class Counter(Widget):
    name = "counter"
    interval = 30.0

    def __init__(self):
        self.value = 0
        self.fail = False
        self.fetched = threading.Event()

    def fetch(self):
        self.fetched.set()
        if self.fail:
            raise RuntimeError("Dienst weg")
        return {"value": self.value}

    def action(self, action, args):
        if action != "inc":
            return False
        self.value += 1
        return True


def _wait(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_hub_publishes_only_changes_and_reacts_to_actions():
    bus, widget = FakeBus(), Counter()
    hub = WidgetHub(bus, [widget])
    hub.start()
    try:
        assert _wait(lambda: bus.sent == [{"type": "widget", "name": "counter", "data": {"value": 0}}])
        hub.wake()  # gleicher Stand → keine zweite Nachricht
        widget.fetched.clear()
        assert widget.fetched.wait(2) and len(bus.sent) == 1
        hub.action("counter", "inc", {})  # Klick → Aktion → gleich neu laden
        assert _wait(lambda: len(bus.sent) == 2 and bus.sent[1]["data"] == {"value": 1})
        hub.action("counter", "unbekannt", {})
        hub.action("gibtsnicht", "inc", {})
        assert hub.snapshot() == {"counter": {"value": 1}}
    finally:
        hub.stop()


def test_hub_sleeps_without_dashboard_and_shows_first_error():
    bus, widget = FakeBus(hud_active=False), Counter()
    hub = WidgetHub(bus, [widget])
    hub.start()
    try:
        assert not widget.fetched.wait(0.3)  # niemand schaut zu → keine Abfrage
        widget.fail = True
        bus.hud_active = True
        hub.wake()
        assert _wait(lambda: bus.sent and bus.sent[0]["data"] == {"error": "Dienst weg"})
        widget.fail, widget.value = False, 5
        hub.wake()
        assert _wait(lambda: bus.sent[-1]["data"] == {"value": 5})
        widget.fail = True  # späterer Ausfall: alter Stand bleibt stehen
        hub.wake()
        widget.fetched.clear()
        assert widget.fetched.wait(2) and hub.snapshot() == {"counter": {"value": 5}}
    finally:
        hub.stop()


def test_default_widgets_follow_config_and_skip_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "WEATHER_LOCATION", "")
    monkeypatch.setattr(config, "HA_TOKEN", "")
    monkeypatch.setattr(config, "EMAIL_ACCOUNT", "")
    assert [w.name for w in default_widgets(names="weather,calendar,homeassistant,mail,quatsch")] == ["calendar"]
    monkeypatch.setattr(config, "WEATHER_LOCATION", "Kiel")
    assert [w.name for w in default_widgets(names="calendar, weather,calendar")] == ["calendar", "weather"]
    assert default_widgets(names="") == []


def test_calendar_widget_lists_upcoming_events(tmp_path):
    tool = CalendarTool(tmp_path / "cal.json")
    tool.add_event("Zahnarzt", time.strftime("%Y-%m-%d"), "23:59", "Kontrolle")
    data = CalendarWidget(tool).fetch()
    assert [(e["title"], e["description"]) for e in data["events"]] == [("Zahnarzt", "Kontrolle")]


# ── Home Assistant: REST-Stand, dann live über WebSocket ─────────────────────


def _state(entity_id, state, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


class FakeHaTool:
    def __init__(self, states):
        self.states = states
        self.called = []

    def _is_configured(self):
        return True

    def _headers(self):
        return {}

    def _fetch_states(self):
        return self.states

    def call_service(self, entity_id, service, data=None):
        self.called.append((entity_id, service))
        return ToolResult.ok("ok")


def test_ha_widget_auto_selects_lights_and_climate_and_toggles_only_shown_safe_entities():
    tool = FakeHaTool(
        [
            _state("light.bad", "off", friendly_name="Licht Bad"),
            _state("light.wohnzimmer", "on", friendly_name="Wohnzimmer", brightness=128),
            _state("climate.bad", "heat", friendly_name="Heizung Bad", current_temperature=21.5, temperature=22),
            _state("lock.haustuer", "locked"),
            _state("sensor.strom", "230", unit_of_measurement="W"),
        ]
    )
    widget = HomeAssistantWidget(tool, entity_ids=[])
    entities = widget.fetch()["entities"]
    assert [e["id"] for e in entities] == ["climate.bad", "light.bad", "light.wohnzimmer"]
    assert entities[2]["brightness"] == 50 and entities[2]["toggle"] is True
    assert entities[0]["temperature"] == 21.5 and entities[0]["target"] == 22 and entities[0]["toggle"] is False

    assert widget.action("toggle", {"entity_id": "light.bad"}) is True
    assert widget.action("toggle", {"entity_id": "lock.haustuer"}) is False  # nicht angezeigt, nicht harmlos
    assert widget.action("toggle", {"entity_id": "climate.bad"}) is False
    assert widget.action("turn_on", {"entity_id": "light.bad"}) is False
    assert tool.called == [("light.bad", "toggle")]


@pytest.mark.parametrize("admin", [True, False])
def test_ha_widget_live_updates_over_websocket(monkeypatch, admin):
    """Echter WebSocket gegen einen nachgebauten Home Assistant. Ohne Admin-Token lehnt HA das
    gezielte Abo ab – dann abonniert das Widget alle Änderungen und filtert selbst."""
    pytest.importorskip("websockets")
    from websockets.sync.server import serve

    seen = []

    def home_assistant(ws):
        ws.send(json.dumps({"type": "auth_required"}))
        seen.append(json.loads(ws.recv()))
        ws.send(json.dumps({"type": "auth_ok"}))
        sub = json.loads(ws.recv())
        seen.append(sub)
        new_state = _state("light.bad", "on", friendly_name="Licht Bad", brightness=255)
        other = _state("light.keller", "on")
        if admin:
            ws.send(json.dumps({"id": sub["id"], "type": "result", "success": True}))
            ws.send(json.dumps({"id": sub["id"], "type": "event", "event": {"variables": {"trigger": {"to_state": new_state}}}}))
        else:
            ws.send(json.dumps({"id": sub["id"], "type": "result", "success": False, "error": {"message": "Unauthorized"}}))
            sub2 = json.loads(ws.recv())
            seen.append(sub2)
            ws.send(json.dumps({"id": sub2["id"], "type": "event", "event": {"data": {"new_state": other}}}))
            ws.send(json.dumps({"id": sub2["id"], "type": "event", "event": {"data": {"new_state": new_state}}}))
        try:
            ws.recv(timeout=3)
        except Exception:
            pass

    server = serve(home_assistant, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(config, "HA_URL", f"http://127.0.0.1:{server.socket.getsockname()[1]}")
        monkeypatch.setattr(config, "HA_TOKEN", "geheim")
        widget = HomeAssistantWidget(FakeHaTool([_state("light.bad", "off", friendly_name="Licht Bad")]), entity_ids=[])
        widget.fetch()
        published = []
        widget.listen(published.append, lambda: bool(published))
        assert seen[0] == {"type": "auth", "access_token": "geheim"}
        assert seen[1]["type"] == "subscribe_trigger" and seen[1]["trigger"]["entity_id"] == ["light.bad"]
        if not admin:
            assert seen[2]["type"] == "subscribe_events" and seen[2]["event_type"] == "state_changed"
        assert len(published) == 1  # light.keller wird nicht angezeigt und löst nichts aus
        assert published[0]["entities"][0]["state"] == "on" and published[0]["entities"][0]["brightness"] == 100
    finally:
        server.shutdown()


# ── Spotify ──────────────────────────────────────────────────────────────────


class FakeSpotify:
    def __init__(self):
        self.playing = {"playing": True, "track": "Titel", "artists": "Band", "art_url": "https://i.example/a.jpg", "controls": True}
        self.done = []

    def backend(self):
        return "web"

    def now_playing(self):
        return dict(self.playing) if self.playing else None

    def execute(self, action="status", **kwargs):
        self.done.append(action)
        return ToolResult.ok("ok")


def test_spotify_widget_sends_cover_separately_and_only_when_it_changes(monkeypatch):
    tool, bus = FakeSpotify(), FakeBus()
    widget = SpotifyWidget(tool)
    hub = WidgetHub(bus, [widget])
    loads = []
    monkeypatch.setattr(SpotifyWidget, "_load_art", staticmethod(lambda url: loads.append(url) or "data:image/jpeg;base64,AAAA"))

    hub.publish("spotify", widget.fetch())
    hub.publish("spotify", widget.fetch())  # gleicher Titel: Bild nicht erneut laden oder senden
    assert loads == ["https://i.example/a.jpg"]
    art = [m for m in bus.sent if m["name"] == "spotify.art"]
    assert art == [{"type": "widget", "name": "spotify.art", "data": {"image": "data:image/jpeg;base64,AAAA"}}]
    assert "art_url" not in hub.snapshot()["spotify"] and hub.snapshot()["spotify"]["track"] == "Titel"

    tool.playing = None  # nicht verknüpft
    assert widget.fetch() == {"linked": False}
    assert widget.action("next", {}) is True and widget.action("volume", {}) is False
    assert tool.done == ["next"]


def test_spotify_now_playing_has_no_side_effects_when_not_linked(monkeypatch):
    from tools.spotify_tool import SpotifyTool

    tool = SpotifyTool()
    monkeypatch.setattr(tool, "backend", lambda: "web")
    monkeypatch.setattr(tool, "is_linked", lambda: False)
    monkeypatch.setattr(tool, "_client", lambda: pytest.fail("kein Zugriff ohne Verknüpfung"))
    assert tool.now_playing() is None

    class Client:
        def current_playback(self):
            return {
                "is_playing": True,
                "progress_ms": 1000,
                "device": {"name": "Mac"},
                "item": {
                    "name": "Song",
                    "duration_ms": 200000,
                    "artists": [{"name": "A"}, {"name": "B"}],
                    "album": {"name": "Album", "images": [{"url": "big", "width": 640}, {"url": "mid", "width": 300}, {"url": "small", "width": 64}]},
                },
            }

    monkeypatch.setattr(tool, "is_linked", lambda: True)
    monkeypatch.setattr(tool, "_client", lambda: Client())
    data = tool.now_playing()
    assert data["track"] == "Song" and data["artists"] == "A, B" and data["art_url"] == "mid" and data["playing"] is True


# ── Mail ─────────────────────────────────────────────────────────────────────


def test_mail_unread_summary_reads_headers_without_marking_seen(monkeypatch):
    from tools import mail_tool

    log = []

    class FakeImap:
        def __init__(self, server, port, timeout=None):
            log.append(("connect", server))

        def login(self, user, password):
            pass

        def select(self, box, readonly=False):
            log.append(("select", box, readonly))

        def search(self, charset, criterion):
            return "OK", [b"3 7 9"]

        def fetch(self, msg_id, what):
            log.append(("fetch", msg_id, what))
            raw = f"From: =?utf-8?q?J=C3=BCrgen?= <j@x.de>\r\nSubject: Nr {msg_id}\r\nDate: Mon, 23 Nov 2026 10:00:00 +0100\r\n\r\n"
            return "OK", [(b"1 (BODY[HEADER.FIELDS])", raw.encode()), b")"]

        def logout(self):
            log.append(("logout",))

    monkeypatch.setattr(mail_tool.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(config, "EMAIL_ACCOUNT", "ich@x.de")
    monkeypatch.setattr(config, "EMAIL_PASSWORD", "pw")
    monkeypatch.setattr(config, "IMAP_SERVER", "")
    summary = mail_tool.MailTool().unread_summary(max_count=2)
    assert summary["unread"] == 3
    assert [(m["from"], m["subject"]) for m in summary["items"]] == [("Jürgen", "Nr 9"), ("Jürgen", "Nr 7")]
    assert ("select", "inbox", True) in log and log[-1] == ("logout",)
    assert all("PEEK" in entry[2] for entry in log if entry[0] == "fetch")

    monkeypatch.setattr(config, "EMAIL_ACCOUNT", "")
    assert mail_tool.MailTool().unread_summary() is None


# ── Wetter ───────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


FORECAST = {
    "current": {
        "temperature_2m": 8.4, "apparent_temperature": 5.1, "weather_code": 61,
        "wind_speed_10m": 22.3, "relative_humidity_2m": 87, "is_day": 1,
    },
    "daily": {
        "time": ["2026-11-23", "2026-11-24"],
        "weather_code": [61, 3],
        "temperature_2m_max": [9.2, 7.0],
        "temperature_2m_min": [5.6, 2.1],
        "precipitation_probability_max": [80, 10],
        "sunrise": ["2026-11-23T07:58", "2026-11-24T08:00"],
        "sunset": ["2026-11-23T16:10", "2026-11-24T16:09"],
    },
}


@pytest.fixture
def open_meteo(monkeypatch):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((url, params))
        if url == weather_tool.GEOCODE_URL:
            hits = [] if params["name"] == "Nirgendwo" else [
                {"name": "Kiel", "admin1": "Schleswig-Holstein", "country": "Deutschland", "latitude": 54.32, "longitude": 10.13}
            ]
            return FakeResponse({"results": hits})
        return FakeResponse(FORECAST)

    monkeypatch.setattr(weather_tool.requests, "get", get)
    weather_tool._places.clear()
    return calls


def test_weather_forecast_and_tool_text(open_meteo, monkeypatch):
    data = weather_tool.forecast("Kiel", days=2)
    assert data["location"] == "Kiel" and data["current"]["text"] == "leichter Regen" and data["current"]["icon"] == "rain"
    assert [(d["weekday"], d["min"], d["max"], d["rain"], d["sunset"]) for d in data["days"]] == [
        ("Mo", 5.6, 9.2, 80, "16:10"),
        ("Di", 2.1, 7.0, 10, "16:09"),
    ]
    weather_tool.forecast("kiel ")  # Koordinaten sind gemerkt: nur noch die Vorhersage wird geladen
    assert [url for url, _ in open_meteo].count(weather_tool.GEOCODE_URL) == 1

    monkeypatch.setattr(config, "WEATHER_LOCATION", "Kiel")
    result = WeatherTool().execute()
    assert result.success and "Wetter in Kiel (Schleswig-Holstein, Deutschland): aktuell 8 °C (gefühlt 5 °C)" in result.output
    assert "2 bis 7 °C, bedeckt, Regenwahrscheinlichkeit 10 %" in result.output


def test_weather_tool_needs_a_place_and_reports_unknown_ones(open_meteo, monkeypatch):
    monkeypatch.setattr(config, "WEATHER_LOCATION", "")
    assert "WEATHER_LOCATION" in WeatherTool().execute().output
    result = WeatherTool().execute(location="Nirgendwo")
    assert not result.success and "nicht gefunden" in result.output


# ── display-Tool ─────────────────────────────────────────────────────────────


class FakeHud:
    def __init__(self, active=True):
        self.active = active
        self.cards = {}

    def show_card(self, card, card_id=""):
        card_id = card_id or f"k{len(self.cards) + 1}"
        self.cards[card_id] = card
        return card_id

    def remove_card(self, card_id):
        return self.cards.pop(card_id, None) is not None

    def clear_cards(self):
        count = len(self.cards)
        self.cards.clear()
        return count


def test_display_is_only_offered_with_a_dashboard():
    assert DisplayTool(None).available() is False
    assert DisplayTool(FakeHud(active=False)).available() is False
    result = DisplayTool(FakeHud(active=False)).execute(kind="text", text="x")
    assert not result.success and "keine Oberfläche" in result.output


def test_display_builds_clean_cards():
    hud = FakeHud()
    tool = DisplayTool(hud)
    reg = SourceRegistry()
    reg.add("https://bahn.example/plan", "Fahrplan")
    tool.use_sources(reg)

    shown = tool.execute(
        kind="table",
        title="Nach Hamburg",
        columns=["Ab", "An"],
        rows=[["08:12", "09:25", "ICE"], {"Ab": "09:12", "An": "10:25"}, "nur ein Wert"],
        sources=[1, 99, "x"],
    )
    card = hud.cards["k1"]
    assert shown.success and "k1" in shown.output
    assert card["columns"] == ["Ab", "An", ""]
    assert card["rows"] == [["08:12", "09:25", "ICE"], ["09:12", "10:25", ""], ["nur ein Wert", "", ""]]
    assert [s["url"] for s in card["sources"]] == ["https://bahn.example/plan"]

    tool.execute(kind="list", title="Einkauf", items=["Milch", "", 3, "x" * 900], id="k1")  # ersetzt k1
    assert hud.cards["k1"]["items"][:2] == ["Milch", "3"] and len(hud.cards["k1"]["items"][2]) == 400

    assert not tool.execute(kind="list").success and not tool.execute(kind="text").success
    assert not tool.execute(kind="table", rows=[]).success and not tool.execute(kind="quatsch", text="x").success
    assert tool.execute(action="remove", id="k1").success and not tool.execute(action="remove", id="k1").success
    tool.execute(kind="text", text="bleibt nicht")
    assert "1 Karte" in tool.execute(action="clear").output and hud.cards == {}


def test_display_images_are_fetched_by_jarvis_reencoded_and_guarded(monkeypatch):
    from PIL import Image

    from tools import display_tool

    raw = io.BytesIO()
    Image.new("RGB", (3000, 1500), "red").save(raw, "PNG")
    data_url = encode_image(raw.getvalue())
    assert data_url.startswith("data:image/jpeg;base64,") and len(data_url) < 200_000
    with pytest.raises(ValueError):
        encode_image(b"<script>kein Bild</script>")

    tool = DisplayTool(FakeHud())
    url = "https://bilder.example/a.png?x=geheim"
    policy = tool.policy(kind="image", image_url=url)
    assert policy.risk is Risk.GUARDED and policy.url == url  # neu gebaute URL → Rückfrage wie bei read_url
    assert tool.policy(kind="text", text="x").risk is Risk.SAFE

    monkeypatch.setattr(display_tool, "blocked_reason", lambda u: "lokale Adresse – gesperrt")
    result = tool.execute(kind="image", image_url="http://192.168.1.1/cam.jpg")
    assert not result.success and "gesperrt" in result.output
    assert not tool.execute(kind="image", image_url="file:///etc/passwd").success

