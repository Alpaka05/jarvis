"""display-Tool: Jarvis zeigt Karten auf der Oberfläche (Text, Liste, Tabelle, Bild).

Die Karten gehen als reine Daten an das Fenster und werden dort als Text eingesetzt – nie als HTML.
Inhalte können aus Webseiten oder Mails stammen; so bleibt ein dort versteckter <script>-Schnipsel
wirkungslos. Bilder lädt Jarvis selbst (mit denselben Sperren wie read_url) und reicht sie
verkleinert weiter, damit das Fenster keine fremden Adressen abruft.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

from tools.base import BaseTool, Policy, Risk, ToolResult
from tools.search_tool import MAX_REDIRECTS, USER_AGENTS, blocked_reason

log = logging.getLogger(__name__)

KINDS = ("text", "list", "table", "image")
MAX_TITLE = 120
MAX_TEXT = 4000
MAX_ITEMS = 40
MAX_ITEM_CHARS = 400
MAX_COLUMNS = 8
MAX_ROWS = 40
MAX_CELL_CHARS = 200
MAX_IMAGE_BYTES = 6_000_000
MAX_IMAGE_SIDE = 1400


def _text(value: Any, limit: int) -> str:
    return "" if value is None else str(value).strip()[:limit]


def fetch_image(url: str) -> str:
    """Lädt ein Bild und gibt es verkleinert als data:-URL zurück. ValueError mit Grund bei Fehlern."""
    for _ in range(MAX_REDIRECTS + 1):
        reason = blocked_reason(url)
        if reason:
            raise ValueError(reason)
        with requests.get(
            url, headers={"User-Agent": USER_AGENTS[-1]}, timeout=(8, 12), stream=True, allow_redirects=False
        ) as resp:
            if resp.is_redirect:
                url = urljoin(url, resp.headers.get("location", ""))
                continue
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if not ctype.startswith("image/"):
                raise ValueError(f"Kein Bild (Content-Type {ctype or 'unbekannt'}).")
            chunks, size = [], 0
            for chunk in resp.iter_content(chunk_size=65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise ValueError("Bild ist zu groß.")
            return encode_image(b"".join(chunks))
    raise ValueError("zu viele Weiterleitungen")


def encode_image(raw: bytes) -> str:
    """Dekodiert, verkleinert und kodiert neu – so kommt nur ein schlichtes JPEG/PNG im Fenster an."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise ValueError(f"Bild nicht lesbar ({e}).") from None
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    if img.mode in ("RGBA", "LA", "P"):
        img.convert("RGBA").save(out, "PNG", optimize=True)
        mime = "image/png"
    else:
        img.convert("RGB").save(out, "JPEG", quality=85)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(out.getvalue()).decode('ascii')}"


class DisplayTool(BaseTool):
    name = "display"
    description = (
        "Zeigt eine Karte auf dem Dashboard des Nutzers: kind=text (längerer Text), list (Punkte), table (Vergleich, "
        "Fahrplan, Übersicht) oder image (Bild von einer URL). Für alles, was man besser sieht als hört: Listen, "
        "Tabellen, Schritt-für-Schritt-Anleitungen, Rezepte, Zusammenfassungen. Mit 'sources' die Quellen-Nummern "
        "angeben, auf denen der Inhalt beruht. action=remove entfernt eine Karte (id), action=clear alle. "
        "Einfache Formatierung im Text: **fett**, *kursiv*, `code`, Zeilen mit '- ' als Aufzählung; kein HTML."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["show", "remove", "clear"], "description": "show (Standard), remove, clear"},
            "kind": {"type": "string", "enum": list(KINDS), "description": "Art der Karte (show)."},
            "title": {"type": "string", "description": "Kurze Überschrift der Karte."},
            "text": {"type": "string", "description": "Inhalt für kind=text; bei list/table/image eine optionale Anmerkung."},
            "items": {"type": "array", "items": {"type": "string"}, "description": "Punkte für kind=list."},
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Spaltenüberschriften für kind=table."},
            "rows": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
                "description": "Zeilen für kind=table, je Zeile ein Wert pro Spalte.",
            },
            "image_url": {"type": "string", "description": "Adresse des Bildes für kind=image (https://…)."},
            "sources": {"type": "array", "items": {"type": "integer"}, "description": "Quellen-Nummern, z.B. [3, 5]."},
            "id": {"type": "string", "description": "Karte ersetzen (show) oder entfernen (remove), z.B. 'k2'."},
        },
        "required": [],
    }

    def __init__(self, hud=None):
        self.hud = hud
        self.sources = None

    def use_sources(self, registry):
        self.sources = registry

    def available(self) -> bool:
        return self.hud is not None and self.hud.active

    def policy(self, action: str = "show", **kwargs) -> Policy:
        url = _text(kwargs.get("image_url"), 2000)
        if action == "show" and kwargs.get("kind") == "image" and url:
            # Eine selbst zusammengebaute Bild-URL kann Daten nach außen tragen (wie bei read_url)
            return Policy(Risk.GUARDED, f"Bild laden und anzeigen: {url}", url=url)
        return Policy()

    def execute(self, action: str = "show", **kwargs) -> ToolResult:
        if not self.available():
            return ToolResult.fail(
                "Es ist keine Oberfläche geöffnet – nichts angezeigt. Antworte stattdessen mündlich "
                "(der Nutzer kann das Dashboard mit 'dashboard' öffnen)."
            )
        if action == "clear":
            count = self.hud.clear_cards()
            return ToolResult.ok(f"{count} Karte(n) vom Dashboard entfernt.")
        card_id = _text(kwargs.get("id"), 20)
        if action == "remove":
            if not card_id:
                return ToolResult.fail("Bitte die id der Karte angeben (z.B. 'k2').")
            removed = self.hud.remove_card(card_id)
            return ToolResult.ok(f"Karte {card_id} entfernt.") if removed else ToolResult.fail(f"Keine Karte '{card_id}'.")
        if action != "show":
            return ToolResult.fail(f"Unbekannte Aktion '{action}'. Erlaubt: show, remove, clear.")

        kind = kwargs.get("kind") or "text"
        if kind not in KINDS:
            return ToolResult.fail(f"Unbekannte Kartenart '{kind}'. Erlaubt: {', '.join(KINDS)}.")
        card: Dict[str, Any] = {
            "kind": kind,
            "title": _text(kwargs.get("title"), MAX_TITLE),
            "text": _text(kwargs.get("text"), MAX_TEXT),
            "sources": self._sources(kwargs.get("sources")),
        }
        if kind == "text" and not card["text"]:
            return ToolResult.fail("Bitte den Inhalt angeben ('text').")
        if kind == "list":
            items = kwargs.get("items")
            card["items"] = [_text(i, MAX_ITEM_CHARS) for i in items[:MAX_ITEMS]] if isinstance(items, list) else []
            card["items"] = [i for i in card["items"] if i]
            if not card["items"]:
                return ToolResult.fail("Bitte die Punkte angeben ('items', eine Liste von Texten).")
        if kind == "table":
            error = self._table(card, kwargs.get("columns"), kwargs.get("rows"))
            if error:
                return ToolResult.fail(error)
        if kind == "image":
            url = _text(kwargs.get("image_url"), 2000)
            if not url.lower().startswith(("http://", "https://")):
                return ToolResult.fail("Bitte die Adresse des Bildes angeben ('image_url', https://…).")
            try:
                card["image"] = fetch_image(url)
            except (ValueError, requests.RequestException) as e:
                return ToolResult.fail(f"Bild konnte nicht geladen werden: {e}")
        shown = self.hud.show_card(card, card_id)
        name = f" „{card['title']}“" if card["title"] else ""
        return ToolResult.ok(f"Karte {shown}{name} wird auf dem Dashboard angezeigt.", data={"id": shown})

    @staticmethod
    def _table(card: Dict[str, Any], columns: Any, rows: Any) -> Optional[str]:
        if not isinstance(rows, list) or not rows:
            return "Bitte die Zeilen angeben ('rows', je Zeile eine Liste von Werten)."
        cols = [_text(c, MAX_CELL_CHARS) for c in columns[:MAX_COLUMNS]] if isinstance(columns, list) else []
        out: List[List[str]] = []
        for row in rows[:MAX_ROWS]:
            if isinstance(row, dict):  # manche Modelle schicken Zeilen als Objekt {Spalte: Wert}
                row = [row.get(c, "") for c in cols] if cols else list(row.values())
            if not isinstance(row, list):
                row = [row]
            out.append([_text(cell, MAX_CELL_CHARS) for cell in row[:MAX_COLUMNS]])
        width = max(len(cols), *(len(r) for r in out))
        card["columns"] = cols + [""] * (width - len(cols)) if cols else []
        card["rows"] = [r + [""] * (width - len(r)) for r in out]
        return None

    def _sources(self, numbers: Any) -> List[Dict[str, Any]]:
        if self.sources is None or not isinstance(numbers, list):
            return []
        found = []
        for n in numbers[:20]:
            try:
                source = self.sources.get(int(n))
            except (TypeError, ValueError):
                continue
            if source is not None and source not in found:
                found.append(source)
        return found
