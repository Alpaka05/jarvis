"""Autostart: was eingerichtet würde – ohne das System anzufassen (HOME und APPDATA umgebogen)."""
import plistlib
from pathlib import Path

import pytest

from scripts import autostart


def test_mac_launch_agent_opens_ui_script_in_terminal_in_background(tmp_path):
    plist = plistlib.loads(autostart.mac_plist(root=tmp_path))
    assert plist["Label"] == "dev.jarvis" and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"] == ["/usr/bin/open", "-g", "-a", "Terminal", str(tmp_path / "launch" / "jarvis-ui.command")]
    assert autostart.mac_plist_path(tmp_path) == tmp_path / "Library" / "LaunchAgents" / "dev.jarvis.plist"


def test_windows_startup_file_starts_ui_script_minimized(tmp_path):
    script = autostart.windows_startup_script(root=tmp_path)
    assert script.startswith("@echo off\r\n") and '/min "' in script and "jarvis-ui.bat" in script
    path = autostart.windows_startup_path(str(tmp_path))
    assert path.name == "Jarvis.bat" and path.parent.name == "Startup"


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_install_status_remove_roundtrip(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: None)  # kein echtes launchctl

    assert "nicht eingerichtet" in autostart.status(platform)[1]
    ok, message = autostart.install(platform)
    path = autostart._path(platform)
    assert ok and path.is_file() and str(path) in message
    assert autostart.status(platform) == (True, f"Autostart ist eingerichtet ({path}).")
    assert autostart.remove(platform)[0] and not path.exists()
    assert autostart.remove(platform) == (True, "Autostart war nicht eingerichtet.")


def test_other_systems_are_told_no():
    assert autostart.install("linux")[0] is False and autostart.status("linux")[0] is False
    assert autostart.main(["quatsch"]) == 1


def test_launch_scripts_exist_and_pass_arguments_through():
    launch = autostart.ROOT / "launch"
    assert '--ui "$@"' in (launch / "jarvis-ui.command").read_text(encoding="utf-8")
    assert "--ui %*" in (launch / "jarvis-ui.bat").read_text(encoding="utf-8")
    assert 'main.py "$@"' in (launch / "jarvis.command").read_text(encoding="utf-8")
    assert "main.py %*" in (launch / "jarvis.bat").read_text(encoding="utf-8")
