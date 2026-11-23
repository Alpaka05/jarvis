"""Quellen-Register: nummeriert Webseiten, auf die sich Jarvis stützt.

Suchtreffer und gelesene Seiten bekommen eine feste Nummer. Das Tool schreibt sie als „[3]“ in sein
Ergebnis, das LLM setzt sie hinter die Aussage, und die Oberfläche zeigt dazu Titel und Link.
Dieselbe URL behält ihre Nummer für das ganze Gespräch (bis 'reset').
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

# „[3]“, „[3, 7]“ – nur reine Zahlen, damit normale eckige Klammern im Text unberührt bleiben
CITATION_RE = re.compile(r"\[(\d{1,4}(?:\s*[,;]\s*\d{1,4})*)\]")
MAX_SNIPPET = 300


def strip_citations(text: str) -> str:
    """Entfernt Quellen-Marker samt Leerraum davor (für Sprachausgabe und Untertitel)."""
    return re.sub(r"[ \t]*" + CITATION_RE.pattern, "", text or "")


def host_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


class SourceRegistry:
    def __init__(self):
        self._items: List[Dict[str, Any]] = []
        self._by_url: Dict[str, int] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._items)

    @staticmethod
    def _key(url: str) -> str:
        return url.strip().rstrip("/").lower()

    def add(self, url: str, title: str = "", snippet: str = "") -> Optional[int]:
        """Nummer der Quelle (neu vergeben oder die bisherige). None ohne brauchbare URL."""
        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return None
        key = self._key(url)
        with self._lock:
            n = self._by_url.get(key)
            if n is not None:
                item = self._items[n - 1]
                item["title"] = item["title"] or (title or "").strip()
                item["snippet"] = item["snippet"] or (snippet or "").strip()[:MAX_SNIPPET]
                return n
            n = len(self._items) + 1
            self._items.append(
                {
                    "n": n,
                    "url": url,
                    "title": (title or "").strip() or host_of(url),
                    "host": host_of(url),
                    "snippet": (snippet or "").strip()[:MAX_SNIPPET],
                }
            )
            self._by_url[key] = n
            return n

    def get(self, n: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._items[n - 1]) if 1 <= n <= len(self._items) else None

    def since(self, count: int) -> List[Dict[str, Any]]:
        """Quellen, die nach dem Stand `count` (früheres len()) dazugekommen sind."""
        with self._lock:
            return [dict(item) for item in self._items[count:]]

    def cited(self, text: str) -> List[Dict[str, Any]]:
        """Quellen, die im Text als [n] vorkommen – in der Reihenfolge der ersten Nennung."""
        seen: List[int] = []
        for match in CITATION_RE.finditer(text or ""):
            for part in re.split(r"[,;]", match.group(1)):
                n = int(part)
                if n not in seen:
                    seen.append(n)
        return [item for item in (self.get(n) for n in seen) if item is not None]

    def knows(self, url: str) -> bool:
        with self._lock:
            return self._key(url or "") in self._by_url

    def reset(self):
        with self._lock:
            self._items.clear()
            self._by_url.clear()
