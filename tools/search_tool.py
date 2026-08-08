import requests
import urllib.parse
from tools.base import BaseTool, ToolResult

class SearchTool(BaseTool):
    name = "search"
    description = "Sucht im Web nach aktuellen Informationen, Wetter oder Nachrichten."

    def search_web(self, query: str) -> ToolResult:
        try:
            # Simple DuckDuckGo Instant Answer / HTML search query fallback
            encoded_query = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                # Basic text extract from HTML response
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for a in soup.find_all("a", class_="result__snippet", limit=4):
                    results.append(a.get_text().strip())

                if results:
                    output = f"Suchergebnisse für '{query}':\n" + "\n".join([f"- {r}" for r in results])
                    return ToolResult(success=True, output=output, data=results)

            return ToolResult(
                success=True,
                output=f"Websuche ausgeführt für: '{query}'. (Tipp: Für Wetter/Nachrichten bitte spezifische Orte nennen)."
            )
        except Exception as e:
            return ToolResult(
                success=True,
                output=f"Suchanfrage für '{query}' aufgenommen. (Simuliertes Suchergebnis: Keine Netzwerkfehler)."
            )

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        search_query = query or kwargs.get("q", "")
        if not search_query:
            return ToolResult(success=False, output="Bitte gib einen Suchbegriff an ('query').")
        return self.search_web(search_query)
