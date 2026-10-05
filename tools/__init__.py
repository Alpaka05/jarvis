from config import config
from tools.base import BaseTool, Policy, Risk, ToolResult
from tools.browser_tool import BrowserTool
from tools.calendar_tool import CalendarTool
from tools.homeassistant_tool import HomeAssistantTool
from tools.mail_tool import MailTool
from tools.memory_tool import MemoryTool
from tools.obsidian_tool import ObsidianTool
from tools.orb_tool import OrbTool
from tools.search_tool import SearchTool
from tools.spotify_tool import SpotifyTool
from tools.system_tool import SystemTool

__all__ = [
    "BaseTool",
    "Policy",
    "Risk",
    "ToolResult",
    "CalendarTool",
    "MailTool",
    "MemoryTool",
    "ObsidianTool",
    "OrbTool",
    "HomeAssistantTool",
    "SearchTool",
    "SystemTool",
    "SpotifyTool",
    "BrowserTool",
    "default_tools",
]


def default_tools(memory=None) -> list:
    """Alle Standard-Tools in der Reihenfolge, in der sie dem LLM angeboten werden.

    Args:
        memory: MemoryStore für das Langzeitgedächtnis; ohne Store gibt es kein memory-Tool.
    """
    return ([MemoryTool(memory)] if memory is not None else []) + [
        SystemTool(),
        CalendarTool(),
        HomeAssistantTool(),
        SpotifyTool(),
        MailTool(),
        SearchTool(),
        BrowserTool(),
        OrbTool(),
    ] + ([ObsidianTool()] if config.OBSIDIAN_VAULT else [])
