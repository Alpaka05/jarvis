import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)


def _env(name: str, default: str = "", allow_empty: bool = False) -> str:
    """Liest eine Umgebungsvariable. Leere Werte (`KEY=`) und Platzhalter aus .env.example
    zählen als nicht gesetzt – sonst überschriebe z.B. `ANTHROPIC_MODEL=` den Standard mit "".
    Mit allow_empty bedeutet ein gesetzter, leerer Wert bewusst „nichts“ (z.B. keine Rotation)."""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if value.startswith("your_") and value.endswith("_here"):
        return default
    return value if (value or allow_empty) else default


def _number(name: str, default: str, kind: type):
    raw = _env(name, default)
    try:
        return kind(raw.replace(",", ".") if kind is float else raw)
    except ValueError:
        # Klare Meldung statt Traceback beim Start
        expected = "eine ganze Zahl" if kind is int else "eine Zahl (z.B. 0.5)"
        raise SystemExit(f"Konfigurationsfehler in .env: {name}={raw!r} – erwartet wird {expected}.") from None


def _int(name: str, default: str) -> int:
    return _number(name, default, int)


def _float(name: str, default: str) -> float:
    return _number(name, default, float)


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
    ANTHROPIC_MODEL: str = _env("ANTHROPIC_MODEL", "claude-sonnet-5")

    OPENAI_API_KEY: str = _env("OPENAI_API_KEY")
    OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-4o-mini")

    GEMINI_API_KEY: str = _env("GEMINI_API_KEY")
    GEMINI_MODEL: str = _env("GEMINI_MODEL", "gemini-3.5-flash-lite")
    # Weitere Modelle, auf die bei erschöpftem Minuten-Kontingent rotiert wird (jedes Modell hat ein eigenes).
    # Reihenfolge nach Antwortzeit (gemessen 10/2026): 3.6-flash war oft überlastet, 3.1-flash-lite braucht 12–24 s.
    GEMINI_FALLBACK_MODELS: str = _env("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-3.6-flash,gemini-3.1-flash-lite", allow_empty=True)
    # Wie lange das Modell vor der Antwort nachdenkt: minimal, low, medium, high (leer = Modell-Standard).
    # „low“ halbiert die Antwortzeit grob, für Assistenten-Aufgaben reicht das meist.
    GEMINI_THINKING_LEVEL: str = _env("GEMINI_THINKING_LEVEL", "low").lower()
    # Sekunden ohne Daten vom Modell, bis auf das nächste Modell gewechselt wird
    GEMINI_TIMEOUT: float = _float("GEMINI_TIMEOUT", "20")

    OLLAMA_HOST: str = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL: str = _env("OLLAMA_MODEL", "llama3.1:8b")

    # ── Persönliches ─────────────────────────────────────────────────────────
    USER_NAME: str = _env("USER_NAME")
    SALUTATION: str = _env("SALUTATION")  # z.B. "Sir" – jede Antwort beginnt damit

    # ── Home Assistant ───────────────────────────────────────────────────────
    HA_URL: str = _env("HA_URL", "http://homeassistant.local:8123").rstrip("/")
    HA_TOKEN: str = _env("HA_TOKEN")

    # ── E-Mail ───────────────────────────────────────────────────────────────
    EMAIL_ACCOUNT: str = _env("EMAIL_ACCOUNT")
    EMAIL_PASSWORD: str = _env("EMAIL_PASSWORD")
    IMAP_SERVER: str = _env("IMAP_SERVER")
    IMAP_PORT: int = _int("IMAP_PORT", "993")
    SMTP_SERVER: str = _env("SMTP_SERVER")
    SMTP_PORT: int = _int("SMTP_PORT", "587")

    # ── Websuche ─────────────────────────────────────────────────────────────
    TAVILY_API_KEY: str = _env("TAVILY_API_KEY")  # optional; ohne Key wird ddgs genutzt

    # ── Spotify ──────────────────────────────────────────────────────────────
    SPOTIFY_CLIENT_ID: str = _env("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET: str = _env("SPOTIFY_CLIENT_SECRET")
    SPOTIFY_REDIRECT_URI: str = _env("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    SPOTIFY_DEVICE_NAME: str = _env("SPOTIFY_DEVICE_NAME")  # bevorzugtes Gerät (Namensteil); leer = dieser Rechner

    # ── Obsidian ─────────────────────────────────────────────────────────────
    OBSIDIAN_VAULT: str = _env("OBSIDIAN_VAULT")  # Pfad zum Vault-Ordner; leer = Tool nicht anbieten

    # ── Daten & Gedächtnis ───────────────────────────────────────────────────
    DATA_DIR: Path = Path(__file__).parent / "data"
    MEMORY_DB: Path = Path(_env("MEMORY_DB") or (Path(__file__).parent / "data" / "jarvis.db"))
    MEMORY_MAX_FACTS: int = _int("MEMORY_MAX_FACTS", "60")  # Fakten, die in den System-Prompt wandern

    # ── Sprache ──────────────────────────────────────────────────────────────
    TTS_ENABLED: bool = _bool("TTS_ENABLED", True)
    # edge = neuronale Microsoft-Edge-Stimmen (online, sehr natürlich), system = SAPI/say (offline)
    TTS_ENGINE: str = _env("TTS_ENGINE", "edge").lower()
    EDGE_VOICE: str = _env("EDGE_VOICE", "de-DE-ConradNeural")
    EDGE_RATE: str = _env("EDGE_RATE", "+0%")
    EDGE_PITCH: str = _env("EDGE_PITCH", "+0Hz")
    VOICE_NAME: str = _env("VOICE_NAME")  # Systemstimme (Fallback / TTS_ENGINE=system)
    LANGUAGE: str = _env("LANGUAGE", "de-DE")

    # ── Sprachmodus (Wake-Word) ──────────────────────────────────────────────
    VOICE_MODE_ON_START: bool = _bool("VOICE_MODE_ON_START", False)
    WAKE_WORD_MODEL: str = _env("WAKE_WORD_MODEL", "hey_jarvis")
    WAKE_WORD_THRESHOLD: float = _float("WAKE_WORD_THRESHOLD", "0.5")
    FOLLOW_UP_SECONDS: float = _float("FOLLOW_UP_SECONDS", "6")
    ACK_STYLE: str = _env("ACK_STYLE", "both").lower()  # chime | voice | both
    ACK_PHRASE: str = _env("ACK_PHRASE", "Ja?", allow_empty=True)
    BARGE_IN_THRESHOLD: float = _float("BARGE_IN_THRESHOLD", "0.06")
    SILENCE_LIMIT_SECONDS: float = _float("SILENCE_LIMIT_SECONDS", "1.4")  # Pause, die den Satz beendet
    VAD_THRESHOLD: float = _float("VAD_THRESHOLD", "0.5")  # Silero-VAD: ab wann gilt ein Block als Sprache

    # ── Orb (schwebendes Overlay) ────────────────────────────────────────────
    ORB_ENABLED: bool = _bool("ORB_ENABLED", False)  # WebSocket-Server für das Orb-Fenster starten
    ORB_PORT: int = _int("ORB_PORT", "8765")  # lauscht nur auf 127.0.0.1

    # ─── Bildschirm ──────────────────────────────────────────────────────
    SCREEN_ENABLED: bool = _bool("SCREEN_ENABLED", True)  # screen-Tool: Screenshot auf Nachfrage ans LLM

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
