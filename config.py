import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

class Config:
    # LLM Settings
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")


    # Home Assistant Settings
    HA_URL: str = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip('/')
    HA_TOKEN: str = os.getenv("HA_TOKEN", "")

    # Email Settings
    EMAIL_ACCOUNT: str = os.getenv("EMAIL_ACCOUNT", "")
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
    IMAP_SERVER: str = os.getenv("IMAP_SERVER", "")
    IMAP_PORT: int = int(os.getenv("IMAP_PORT", "993"))
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))

    # Calendar Settings
    CALENDAR_TYPE: str = os.getenv("CALENDAR_TYPE", "local")
    DATA_DIR: Path = Path(__file__).parent / "data"

    # Voice Output Settings
    TTS_ENABLED: bool = os.getenv("TTS_ENABLED", "true").lower() in ("true", "1", "yes")
    VOICE_NAME: str = os.getenv("VOICE_NAME", "Anna")

config = Config()
# Ensure data directory exists
config.DATA_DIR.mkdir(exist_ok=True)
