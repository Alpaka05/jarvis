"""LLM-Provider-Factory.

    from core.llm import create_providers
    primary, fallback, notes = create_providers()
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from config import config
from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall

PROVIDER_NAMES = ("anthropic", "openai", "gemini", "ollama")

__all__ = [
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "PROVIDER_NAMES",
    "build_provider",
    "is_configured",
    "create_providers",
]


def is_configured(name: str) -> bool:
    if name == "anthropic":
        return bool(config.ANTHROPIC_API_KEY)
    if name == "openai":
        return bool(config.OPENAI_API_KEY)
    if name == "gemini":
        return bool(config.GEMINI_API_KEY)
    if name == "ollama":
        return _ollama_reachable()
    return False


def _ollama_reachable(timeout: float = 1.5) -> bool:
    try:
        import requests

        r = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def build_provider(name: str) -> LLMProvider:
    """Erzeugt einen Provider oder wirft LLMError mit verständlicher Begründung."""
    name = (name or "").lower()
    if name == "anthropic":
        from core.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(config.ANTHROPIC_API_KEY, config.ANTHROPIC_MODEL, config.LLM_EFFORT)
    if name == "openai":
        from core.llm.openai_provider import OpenAICompatProvider

        return OpenAICompatProvider(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    if name == "gemini":
        from core.llm.gemini_provider import GeminiProvider

        return GeminiProvider(config.GEMINI_API_KEY, config.GEMINI_MODEL)
    if name == "ollama":
        from core.llm.openai_provider import make_ollama_provider

        if not _ollama_reachable():
            raise LLMError(f"Ollama unter {config.OLLAMA_HOST} nicht erreichbar (läuft `ollama serve`?).")
        return make_ollama_provider(config.OLLAMA_HOST, config.OLLAMA_MODEL)
    raise LLMError(f"Unbekannter LLM-Provider '{name}'. Erlaubt: {', '.join(PROVIDER_NAMES)}")


def create_providers() -> Tuple[Optional[LLMProvider], Optional[LLMProvider], List[str]]:
    """Baut Haupt- und Fallback-Provider gemäß Konfiguration.

    Returns:
        (primary, fallback, notes) – notes enthält menschenlesbare Hinweise für den Statusbildschirm.
    """
    notes: List[str] = []
    primary: Optional[LLMProvider] = None
    fallback: Optional[LLMProvider] = None

    try:
        primary = build_provider(config.LLM_PROVIDER)
    except LLMError as e:
        notes.append(f"Haupt-Provider '{config.LLM_PROVIDER}' nicht verfügbar: {e}")

    fb_name = config.LLM_FALLBACK_PROVIDER
    if fb_name and fb_name != config.LLM_PROVIDER:
        try:
            fallback = build_provider(fb_name)
        except LLMError as e:
            notes.append(f"Fallback '{fb_name}' nicht verfügbar: {e}")

    if primary is None and fallback is not None:
        notes.append(f"Nutze Fallback '{fallback.name}' als Haupt-Provider.")
        primary, fallback = fallback, None

    return primary, fallback, notes
