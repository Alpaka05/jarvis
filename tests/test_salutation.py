import config as config_module
from core.agent import JarvisAgent


def test_salutation_is_prepended_when_missing(monkeypatch):
    monkeypatch.setattr(config_module.config, "SALUTATION", "Sir")
    assert JarvisAgent._apply_salutation("Es ist 1:44 Uhr.") == "Sir, es ist 1:44 Uhr."
    assert JarvisAgent._apply_salutation("Spotify spielt jetzt Queen.") == "Sir, Spotify spielt jetzt Queen."
    assert JarvisAgent._apply_salutation("Sir, alles erledigt.") == "Sir, alles erledigt."
    assert JarvisAgent._apply_salutation("sir, alles erledigt.") == "sir, alles erledigt."
    assert JarvisAgent._apply_salutation("") == ""


def test_salutation_disabled_when_empty(monkeypatch):
    monkeypatch.setattr(config_module.config, "SALUTATION", "")
    assert JarvisAgent._apply_salutation("Es ist spät.") == "Es ist spät."


def test_gemini_retry_parsing():
    from core.llm.gemini_provider import GeminiProvider

    assert GeminiProvider._retry_seconds("... Please retry in 43.2s. ...") == 43.2
    assert GeminiProvider._retry_seconds("kein Hinweis") == 60.0
    assert GeminiProvider._is_rate_limited("429 RESOURCE_EXHAUSTED")
    assert GeminiProvider._is_rate_limited("503 UNAVAILABLE high demand")
    assert not GeminiProvider._is_rate_limited("404 NOT_FOUND")
