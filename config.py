import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)


def _env(name: str, default: str = "") -> str:
    """Read an env var, treating placeholder values from .env.example as empty."""
    value = os.getenv(name, default).strip()
    if value.startswith("your_") and value.endswith("_here"):
        return default
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in ("1", "true", "yes", "on")


IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


class Config:
    # ── LLM ──────────────────────────────────────────────────────────────────
    LLM_PROVIDER: str = _env("LLM_PROVIDER", "anthropic").lower()
    LLM_FALLBACK_PROVIDER: str = _env("LLM_FALLBACK_PROVIDER", "ollama").lower()
    LLM_EFFORT: str = _env("LLM_EFFORT", "medium").lower()

    ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL: str = _env("ANTHROPIC_MODEL", "claude-opus-5")

    OPENAI_API_KEY: str = _env("OPENAI_API_KEY")
    OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-4o-mini")

    GEMINI_API_KEY: str = _env("GEMINI_API_KEY")
    GEMINI_MODEL: str = _env("GEMINI_MODEL", "gemini-2.5-flash")

    OLLAMA_HOST: str = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL: str = _env("OLLAMA_MODEL", "llama3.1:8b")

    # ── Persönliches ─────────────────────────────────────────────────────────
    USER_NAME: str = _env("USER_NAME")

    # ── Home Assistant ───────────────────────────────────────────────────────
    HA_URL: str = _env("HA_URL", "http://homeassistant.local:8123").rstrip("/")
    HA_TOKEN: str = _env("HA_TOKEN")

    # ── E-Mail ───────────────────────────────────────────────────────────────
    EMAIL_ACCOUNT: str = _env("EMAIL_ACCOUNT")
    EMAIL_PASSWORD: str = _env("EMAIL_PASSWORD")
    IMAP_SERVER: str = _env("IMAP_SERVER")
    IMAP_PORT: int = int(_env("IMAP_PORT", "993"))
    SMTP_SERVER: str = _env("SMTP_SERVER")
    SMTP_PORT: int = int(_env("SMTP_PORT", "587"))

    # ── Spotify ──────────────────────────────────────────────────────────────
    SPOTIFY_CLIENT_ID: str = _env("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET: str = _env("SPOTIFY_CLIENT_SECRET")
    SPOTIFY_REDIRECT_URI: str = _env("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    # ── Daten ────────────────────────────────────────────────────────────────
    DATA_DIR: Path = Path(__file__).parent / "data"

    # ── Sprache ──────────────────────────────────────────────────────────────
    TTS_ENABLED: bool = _bool("TTS_ENABLED", True)
    # edge = neuronale Microsoft-Edge-Stimmen (online, sehr natürlich), system = SAPI/say (offline)
    TTS_ENGINE: str = _env("TTS_ENGINE", "edge").lower()
    EDGE_VOICE: str = _env("EDGE_VOICE", "de-DE-ConradNeural")
    EDGE_RATE: str = _env("EDGE_RATE", "+0%")
    EDGE_PITCH: str = _env("EDGE_PITCH", "+0Hz")
    VOICE_NAME: str = _env("VOICE_NAME")  # Systemstimme (Fallback / TTS_ENGINE=system)
    LANGUAGE: str = _env("LANGUAGE", "de-DE")

    # ── Plattform ────────────────────────────────────────────────────────────
    IS_WINDOWS = IS_WINDOWS
    IS_MAC = IS_MAC
    IS_LINUX = IS_LINUX

    @property
    def platform_name(self) -> str:
        if IS_WINDOWS:
            return "Windows"
        if IS_MAC:
            return "macOS"
        return "Linux"


config = Config()
config.DATA_DIR.mkdir(exist_ok=True)
