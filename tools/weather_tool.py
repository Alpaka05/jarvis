"""Wetter über Open-Meteo (kostenlos, ohne API-Key).

Ort → Koordinaten über die Open-Meteo-Geokodierung, dann aktuelle Werte und Tagesvorhersage.
`forecast()` liefert die Daten auch für das Wetter-Widget der Oberfläche (core/widgets.py).
"""
from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

import requests

from config import config
from tools.base import BaseTool, ToolResult

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 8
MAX_DAYS = 7
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

# WMO-Wettercodes → (Beschreibung, Symbolname für die Oberfläche)
WMO: Dict[int, Tuple[str, str]] = {
    0: ("klar", "clear"), 1: ("überwiegend klar", "clear"), 2: ("teils bewölkt", "partly"), 3: ("bedeckt", "cloudy"),
    45: ("Nebel", "fog"), 48: ("Reifnebel", "fog"),
    51: ("leichter Nieselregen", "drizzle"), 53: ("Nieselregen", "drizzle"), 55: ("starker Nieselregen", "drizzle"),
    56: ("gefrierender Nieselregen", "sleet"), 57: ("gefrierender Nieselregen", "sleet"),
    61: ("leichter Regen", "rain"), 63: ("Regen", "rain"), 65: ("starker Regen", "rain"),
    66: ("gefrierender Regen", "sleet"), 67: ("gefrierender Regen", "sleet"),
    71: ("leichter Schneefall", "snow"), 73: ("Schneefall", "snow"), 75: ("starker Schneefall", "snow"),
    77: ("Schneegriesel", "snow"),
    80: ("leichte Regenschauer", "showers"), 81: ("Regenschauer", "showers"), 82: ("heftige Regenschauer", "showers"),
    85: ("Schneeschauer", "snow"), 86: ("starke Schneeschauer", "snow"),
    95: ("Gewitter", "storm"), 96: ("Gewitter mit Hagel", "storm"), 99: ("Gewitter mit Hagel", "storm"),
}

_places: Dict[str, Dict[str, Any]] = {}  # Ortsname → Koordinaten (ändern sich nicht)
_places_lock = threading.Lock()


class WeatherError(RuntimeError):
    """Wetter nicht abrufbar (Ort unbekannt, Dienst nicht erreichbar)."""


def describe(code: Any) -> Tuple[str, str]:
    try:
        return WMO.get(int(code), ("unbekannt", "cloudy"))
    except (TypeError, ValueError):
        return "unbekannt", "cloudy"


def geocode(name: str) -> Dict[str, Any]:
    key = name.strip().lower()
    with _places_lock:
        if key in _places:
            return _places[key]
    lang = "de" if config.LANGUAGE.lower().startswith("de") else "en"
    try:
        resp = requests.get(
            GEOCODE_URL, params={"name": name.strip(), "count": 1, "language": lang, "format": "json"}, timeout=TIMEOUT
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except (requests.RequestException, ValueError) as e:
        raise WeatherError(f"Ortssuche nicht erreichbar: {e}") from None
    if not results:
        raise WeatherError(f"Ort '{name}' nicht gefunden.")
    hit = results[0]
    place = {
        "name": hit.get("name") or name.strip(),
        "region": ", ".join(x for x in (hit.get("admin1"), hit.get("country")) if x),
        "lat": hit["latitude"],
        "lon": hit["longitude"],
    }
    with _places_lock:
        _places[key] = place
    return place


def forecast(location: str, days: int = 4) -> Dict[str, Any]:
    """Aktuelles Wetter und Tagesvorhersage für einen Ort (Name wie „Kiel“)."""
    place = geocode(location)
    days = max(1, min(int(days), MAX_DAYS))
    params = {
        "latitude": place["lat"],
        "longitude": place["lon"],
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        current, daily = data["current"], data["daily"]
    except (requests.RequestException, ValueError, KeyError) as e:
        raise WeatherError(f"Wetterdienst nicht erreichbar: {e}") from None

    text, icon = describe(current.get("weather_code"))
    out_days = []
    for i, day in enumerate(daily.get("time", [])):
        day_text, day_icon = describe(daily["weather_code"][i])
        out_days.append(
            {
                "date": day,
                "weekday": WEEKDAYS[date.fromisoformat(day).weekday()],
                "min": daily["temperature_2m_min"][i],
                "max": daily["temperature_2m_max"][i],
                "text": day_text,
                "icon": day_icon,
                "rain": (daily.get("precipitation_probability_max") or [None] * (i + 1))[i],
                "sunrise": (daily.get("sunrise") or [""] * (i + 1))[i][-5:],
                "sunset": (daily.get("sunset") or [""] * (i + 1))[i][-5:],
            }
        )
    return {
        "location": place["name"],
        "region": place["region"],
        "current": {
            "temp": current.get("temperature_2m"),
            "feels": current.get("apparent_temperature"),
            "text": text,
            "icon": icon,
            "wind": current.get("wind_speed_10m"),
            "humidity": current.get("relative_humidity_2m"),
            "is_day": bool(current.get("is_day", 1)),
        },
        "days": out_days,
        "updated": datetime.now().isoformat(timespec="minutes"),
    }


def _num(value: Any) -> str:
    return "?" if value is None else f"{round(float(value))}"


class WeatherTool(BaseTool):
    name = "weather"
    description = (
        "Wetter: aktuelle Werte und Vorhersage für die nächsten Tage (Temperatur, Regen, Wind) für einen Ort. "
        "Ohne Ortsangabe gilt der Wohnort des Nutzers. Für Wetterfragen immer dieses Tool statt der Websuche."
    )
    parameters = {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "Ort, z.B. 'Berlin'. Leer = Standardort des Nutzers."},
            "days": {"type": "integer", "description": "Anzahl Tage ab heute (1–7, Standard 3)."},
        },
    }

    def execute(self, location: str = "", days: Optional[int] = None, **kwargs) -> ToolResult:
        place = (location or "").strip() or config.WEATHER_LOCATION
        if not place:
            return ToolResult.fail("Für welchen Ort? (Kein Standardort gesetzt: WEATHER_LOCATION in der .env.)")
        try:
            data = forecast(place, days or 3)
        except WeatherError as e:
            return ToolResult.fail(str(e))
        cur = data["current"]
        where = data["location"] + (f" ({data['region']})" if data["region"] else "")
        lines = [
            f"Wetter in {where}: aktuell {_num(cur['temp'])} °C (gefühlt {_num(cur['feels'])} °C), {cur['text']}, "
            f"Wind {_num(cur['wind'])} km/h, Luftfeuchte {_num(cur['humidity'])} %."
        ]
        today = date.today().isoformat()
        for day in data["days"]:
            label = "Heute" if day["date"] == today else f"{day['weekday']} {day['date'][8:]}.{day['date'][5:7]}."
            rain = f", Regenwahrscheinlichkeit {_num(day['rain'])} %" if day["rain"] is not None else ""
            lines.append(f"- {label}: {_num(day['min'])} bis {_num(day['max'])} °C, {day['text']}{rain}")
        return ToolResult.ok("\n".join(lines), data=data)
