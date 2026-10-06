"""Konfiguration: leere Werte, Platzhalter, Zahlen mit klarer Fehlermeldung."""
import pytest

import config as config_module


def test_empty_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST", "")
    assert config_module._env("JARVIS_TEST", "standard") == "standard"


def test_empty_value_can_mean_nothing_where_allowed(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST", "")
    assert config_module._env("JARVIS_TEST", "standard", allow_empty=True) == ""
    monkeypatch.delenv("JARVIS_TEST")
    assert config_module._env("JARVIS_TEST", "standard", allow_empty=True) == "standard"


def test_placeholder_counts_as_unset(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST", "your_api_key_here")
    assert config_module._env("JARVIS_TEST", "") == ""


def test_numbers_accept_decimal_comma(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST", "4,5")
    assert config_module._float("JARVIS_TEST", "6") == 4.5
    monkeypatch.setenv("JARVIS_TEST", "")
    assert config_module._int("JARVIS_TEST", "993") == 993


def test_invalid_number_gives_clear_message(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST", "99x")
    with pytest.raises(SystemExit) as exc:
        config_module._int("JARVIS_TEST", "993")
    assert "JARVIS_TEST='99x'" in str(exc.value) and "ganze Zahl" in str(exc.value)
