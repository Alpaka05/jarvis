import urllib.parse

import requests

from tools.base import BaseTool, ToolResult


class SearchTool(BaseTool):
    name = "web_search"
    description = (
        "Websuche (DuckDuckGo) für aktuelle Informationen, Nachrichten, Wetter, Fakten. "
        "Liefert Titel, Link und Kurzbeschreibung der Top-Treffer."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Suchanfrage, möglichst präzise (z.B. 'Wetter Berlin morgen')."},
            "max_results": {"type": "integer", "description": "Anzahl Treffer (Standard 5, max 10)."},
        },
        "required": ["query"],
    }

    def search_web(self, query: str, max_results: int = 5) -> ToolResult:
        try:
            from bs4 import BeautifulSoup

            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis/0.2"}
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code != 200:
                return ToolResult.fail(f"Suche fehlgeschlagen (HTTP {resp.status_code}).")

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for res in soup.select("div.result")[:max_results]:
                title_el = res.select_one("a.result__a")
                snippet_el = res.select_one(".result__snippet")
                if not title_el:
                    continue
                href = title_el.get("href", "")
                # DuckDuckGo verpackt Links als Redirect (uddg=...)
                if "uddg=" in href:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = qs.get("uddg", [href])[0]
                results.append(
                    {
                        "title": title_el.get_text(" ", strip=True),
                        "url": href,
                        "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
                    }
                )

            if not results:
                return ToolResult.ok(f"Keine Treffer für '{query}'.", data=[])

            lines = [f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1)]
            return ToolResult.ok(f"Suchergebnisse für '{query}':\n" + "\n".join(lines), data=results)
        except Exception as e:
            return ToolResult.fail(f"Websuche fehlgeschlagen: {e}")

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        q = (query or kwargs.get("q") or "").strip()
        if not q:
            return ToolResult.fail("Bitte eine Suchanfrage angeben ('query').")
        n = max(1, min(int(kwargs.get("max_results") or 5), 10))
        return self.search_web(q, n)
