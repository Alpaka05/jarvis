"""Websuche.

Backends in dieser Reihenfolge:
  1. Tavily (LLM-optimiert, liefert Kurzantwort) – wenn TAVILY_API_KEY gesetzt ist
  2. ddgs (DuckDuckGo/Bing/Brave-Metasuche ohne API-Key) – Standard
Dazu: Nachrichtensuche und das Auslesen einer Webseite als Text.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import requests

from config import config
from tools.base import BaseTool, Policy, Risk, ToolResult

# Wikipedia & Co. verlangen einen identifizierbaren User-Agent; manche Seiten sperren dagegen alles,
# was nicht wie ein Browser aussieht. Daher zuerst ehrlich, bei 403 als Browser erneut.
MAX_DOWNLOAD_BYTES = 2_000_000  # Seiten größer als 2 MB werden abgeschnitten
USER_AGENTS = (
    "Jarvis/0.2 (persoenlicher Assistent; +https://github.com/Alpaka05/jarvis) requests",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
)
MAX_PAGE_CHARS = 6000
MAX_REDIRECTS = 5


def blocked_reason(url: str) -> Optional[str]:
    """Grund, warum read_url diese Adresse nicht laden darf, sonst None.

    Gesperrt sind lokale und private Ziele (Router, Home Assistant, localhost …): Sonst könnte eine
    präparierte Webseite Jarvis dazu bringen, Geräte im Heimnetz abzufragen oder anzusprechen.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"Ungültige URL: {url}"
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError) as e:
        return f"Host '{parts.hostname}' nicht gefunden ({e})."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            return f"'{parts.hostname}' zeigt auf eine lokale/private Adresse ({ip}) – aus Sicherheitsgründen gesperrt."
    return None


class SearchTool(BaseTool):
    name = "web_search"
    description = (
        "Internet-Recherche. action=search für allgemeine Websuche (Fakten, Wetter, Produkte), "
        "action=news für aktuelle Nachrichten der letzten Tage, action=read_url um den Textinhalt einer "
        "bestimmten Webseite zu lesen (z.B. einen Treffer vertiefen). Liefert Titel, URL und Kurzbeschreibung."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "news", "read_url"],
                "description": "search = Websuche (Standard), news = Nachrichten, read_url = Seite auslesen",
            },
            "query": {"type": "string", "description": "Suchanfrage, möglichst präzise (search/news)."},
            "url": {"type": "string", "description": "Vollständige URL der zu lesenden Seite (read_url)."},
            "max_results": {"type": "integer", "description": "Anzahl Treffer (Standard 5, max 10)."},
        },
        "required": ["action"],
    }

    # ── Backends ─────────────────────────────────────────────────────────────

    def _tavily(self, query: str, max_results: int, news: bool) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "api_key": config.TAVILY_API_KEY,
            "query": query,
            "max_results": max_results,
            "include_answer": True,
            "search_depth": "basic",
        }
        if news:
            payload["topic"] = "news"
            payload["days"] = 7
        resp = requests.post("https://api.tavily.com/search", json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": (r.get("content") or "")[:400]}
            for r in data.get("results", [])
        ]
        if data.get("answer"):
            results.insert(0, {"title": "Kurzantwort (Tavily)", "url": "", "snippet": data["answer"]})
        return results

    def _ddgs(self, query: str, max_results: int, news: bool) -> List[Dict[str, Any]]:
        from ddgs import DDGS

        region = "de-de" if config.LANGUAGE.lower().startswith("de") else "wt-wt"
        with DDGS(timeout=15) as ddgs:
            if news:
                rows = ddgs.news(query, max_results=max_results, region=region, timelimit="w")
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": f"{(r.get('date') or '')[:10]} {r.get('source', '')}: {r.get('body', '')}".strip(),
                    }
                    for r in rows
                ]
            rows = ddgs.text(query, max_results=max_results, region=region)
            return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in rows]

    def search(self, query: str, max_results: int = 5, news: bool = False) -> ToolResult:
        errors = []
        backends = []
        if config.TAVILY_API_KEY:
            backends.append(("tavily", self._tavily))
        backends.append(("ddgs", self._ddgs))

        for name, fn in backends:
            try:
                results = fn(query, max_results, news)
            except Exception as e:
                errors.append(f"{name}: {str(e)[:120]}")
                continue
            if not results:
                errors.append(f"{name}: keine Treffer")
                continue
            label = "Nachrichten" if news else "Suchergebnisse"
            lines = [
                f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}".rstrip() for i, r in enumerate(results, 1)
            ]
            return ToolResult.ok(f"{label} für '{query}' ({name}):\n" + "\n".join(lines), data=results)

        return ToolResult.fail("Websuche fehlgeschlagen: " + "; ".join(errors))

    # ── Seite lesen ──────────────────────────────────────────────────────────

    def read_url(self, url: str) -> ToolResult:
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        page: Optional[tuple] = None
        last_error: Exception | None = None
        for ua in USER_AGENTS:
            try:
                page = self._download(url, ua)
                break
            except _Blocked as e:
                return ToolResult.fail(str(e))
            except Exception as e:
                last_error = e
        if page is None:
            return ToolResult.fail(f"Seite konnte nicht geladen werden: {last_error}")
        raw, charset, url = page

        from bs4 import BeautifulSoup

        # Bytes statt requests' Raten: ohne charset im Header nähme requests ISO-8859-1 an und aus
        # „ä“ würde „Ã¤“. BeautifulSoup liest dann das <meta charset> der Seite.
        soup = BeautifulSoup(raw, "html.parser", from_encoding=charset)
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "form"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = re.sub(r"\n\s*\n+", "\n\n", main.get_text("\n", strip=True))
        title = soup.title.get_text(strip=True) if soup.title else url
        if not text.strip():
            return ToolResult.fail("Seite enthält keinen auslesbaren Text (vermutlich per JavaScript gerendert).")
        truncated = " …[gekürzt]" if len(text) > MAX_PAGE_CHARS else ""
        return ToolResult.ok(f"{title}\n{url}\n\n{text[:MAX_PAGE_CHARS]}{truncated}", data={"title": title, "url": url})

    def _download(self, url: str, ua: str) -> tuple:
        """Lädt eine Seite begrenzt. Weiterleitungen werden einzeln geprüft (blocked_reason), damit
        eine öffentliche Seite nicht ins Heimnetz umleiten kann. Gibt (Bytes, charset, End-URL) zurück."""
        for _ in range(MAX_REDIRECTS + 1):
            reason = blocked_reason(url)
            if reason:
                raise _Blocked(reason)
            with requests.get(
                url,
                headers={"User-Agent": ua, "Accept-Language": config.LANGUAGE},
                timeout=(8, 12),
                stream=True,
                allow_redirects=False,
            ) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get("location", ""))
                    continue
                if resp.status_code in (401, 403, 429):
                    raise RuntimeError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if "html" not in ctype and "text" not in ctype:
                    raise _Blocked(f"Kein lesbarer Textinhalt (Content-Type {ctype}).")
                # Begrenzt lesen: schützt vor riesigen Seiten und tröpfelnden Servern
                chunks, size = [], 0
                for chunk in resp.iter_content(chunk_size=65536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_DOWNLOAD_BYTES:
                        break
                match = re.search(r"charset=([\w-]+)", ctype)
                return b"".join(chunks), (match.group(1) if match else None), url
        raise RuntimeError("zu viele Weiterleitungen")

    # ── Rückfragen ───────────────────────────────────────────────────────────

    def policy(self, action: str = "search", query: str = "", **kwargs) -> Policy:
        if action == "read_url":
            url = (kwargs.get("url") or query or "").strip()
            # Eine selbst zusammengebaute URL kann Daten nach außen tragen (…?q=<Inhalt einer Mail>)
            return Policy(Risk.GUARDED, f"Webseite laden: {url}", url=url, untrusted_output=True)
        return Policy(untrusted_output=True)  # Suchtreffer stammen von fremden Seiten

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def execute(self, action: str = "search", query: str = "", **kwargs) -> ToolResult:
        if action == "read_url":
            url = (kwargs.get("url") or query or "").strip()
            if not url:
                return ToolResult.fail("Bitte eine URL angeben ('url').")
            return self.read_url(url)
        q = (query or kwargs.get("q") or "").strip()
        if not q:
            return ToolResult.fail("Bitte eine Suchanfrage angeben ('query').")
        n = max(1, min(int(kwargs.get("max_results") or 5), 10))
        return self.search(q, n, news=(action == "news"))


class _Blocked(Exception):
    """read_url darf/kann die Seite nicht laden – kein erneuter Versuch mit anderem User-Agent."""
