from tools.base import BaseTool, ToolResult
from tools.browser_tool import BrowserTool
from tools.calendar_tool import CalendarTool
from tools.homeassistant_tool import HomeAssistantTool
from tools.mail_tool import MailTool
from tools.search_tool import SearchTool
from tools.spotify_tool import SpotifyTool
from tools.system_tool import SystemTool

__all__ = [
    "BaseTool",
    "ToolResult",
    "CalendarTool",
    "MailTool",
    "HomeAssistantTool",
    "SearchTool",
    "SystemTool",
    "SpotifyTool",
    "BrowserTool",
    "default_tools",
]


def default_tools() -> list:
    """Alle Standard-Tools in der Reihenfolge, in der sie dem LLM angeboten werden."""
    return [
        SystemTool(),
        CalendarTool(),
        HomeAssistantTool(),
        SpotifyTool(),
        MailTool(),
        SearchTool(),
        BrowserTool(),
    ]
