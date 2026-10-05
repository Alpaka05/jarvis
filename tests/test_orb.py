"""Orb-Ereignisse: Pegel-Skala, Zustandslogik und echter Versand über WebSocket."""
import json
import os
import socket
import sys
import time

import numpy as np
import pytest

from core.orb import OrbEvents, load_or_create_token, normalize_rms


def test_normalize_rms_db_scale():
    assert normalize_rms(0) == 0.0
    assert normalize_rms(10 ** (-60 / 20)) == 0.0  # unter -50 dB
    assert normalize_rms(10 ** (-30 / 20)) == pytest.approx(0.5)
    assert normalize_rms(0.1) == pytest.approx(0.75)  # -20 dB
    assert normalize_rms(1.0) == 1.0


def test_without_server_calls_are_noops_and_state_is_tracked():
    bus = OrbEvents()
    bus.state("thinking")
    bus.tool("spotify")
    bus.level(0.3)
    bus.pcm_level(np.full(800, 3000, dtype=np.int16), "tts")
    bus.error("x")
    bus.transcript("user", "hallo")
    assert bus._queue.empty()
    bus.state("idle")
    assert bus._state == "idle"


@pytest.fixture
def server():
    pytest.importorskip("websockets")
    bus = OrbEvents(idle_delay=0.2)
    assert bus.start(port=0) is None  # freier Port
    yield bus
    bus.stop()


def _connect(bus, token=None):
    from websockets.sync.client import connect

    return connect(f"{bus.address}/?token={bus.token if token is None else token}", open_timeout=2)


def _recv(ws, timeout=2.0):
    return json.loads(ws.recv(timeout=timeout))


def test_new_client_gets_current_state_then_events(server):
    server.state("listening")
    with _connect(server) as ws:
        assert _recv(ws) == {"type": "state", "state": "listening"}
        server.tool("spotify")
        server.transcript("assistant", "Sehr wohl, Sir. Das Licht im Bad ist aus.")
        assert _recv(ws) == {"type": "tool", "name": "spotify"}
        assert _recv(ws) == {"type": "transcript", "role": "assistant", "text": "Sehr wohl, Sir. Das Licht im Bad ist aus."}


def test_idle_is_dropped_when_another_state_follows_quickly(server):
    with _connect(server) as ws:
        assert _recv(ws)["state"] == "idle"
        server.state("speaking")
        server.state("idle")  # Sprachausgabe endet …
        server.state("listening")  # … und das Nachfrage-Fenster folgt sofort
        assert _recv(ws) == {"type": "state", "state": "speaking"}
        assert _recv(ws) == {"type": "state", "state": "listening"}

        start = time.monotonic()
        server.state("idle")
        assert _recv(ws) == {"type": "state", "state": "idle"}
        assert time.monotonic() - start >= 0.15


def test_levels_are_normalized_and_throttled(server):
    with _connect(server) as ws:
        _recv(ws)
        for _ in range(50):
            server.level(0.1, "tts")
        assert _recv(ws) == {"type": "level", "source": "tts", "value": 0.75}
        with pytest.raises(TimeoutError):
            ws.recv(timeout=0.1)


@pytest.mark.parametrize("token", ["", "falsch"])
def test_connection_without_valid_token_is_rejected(server, token):
    from websockets.exceptions import InvalidStatus

    with pytest.raises(InvalidStatus) as exc:
        _connect(server, token=token)
    assert exc.value.response.status_code == 403
    assert not server.active


def test_token_file_is_private_and_reused(tmp_path):
    path = tmp_path / "data" / "orb.token"
    token = load_or_create_token(path)
    assert len(token) >= 20 and load_or_create_token(path) == token
    if not sys.platform.startswith("win"):
        assert (path.stat().st_mode & 0o777) == 0o600
    path.write_text("kaputt")
    assert load_or_create_token(path) != "kaputt"
    assert load_or_create_token(None) != load_or_create_token(None)


def test_port_in_use_is_reported_not_raised(server):
    port = int(server.address.rsplit(":", 1)[1])
    other = OrbEvents()
    error = other.start(port=port)
    assert error and error.startswith("Orb-Server nicht gestartet")
    assert not other.enabled


# ── Orb-Fenster auf Befehl ('orb') ───────────────────────────────────────────

from core.orb import OrbWindow, find_window_binary  # noqa: E402


def _fake_binary(path, out_file, body=None):
    """Ersatz-Fenster: schreibt Port, Token und Prozessgruppe, wartet wie das echte Fenster auf EOF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = body or (
        f'echo "$ORB_PORT $ORB_TOKEN $JARVIS_ORB_CHILD $(ps -o pgid= -p $$)" > "{out_file}"\n'
        f'cat > /dev/null\necho eof >> "{out_file}"\n'
    )
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _wait_for(path, lines=1, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and len(path.read_text().splitlines()) >= lines:
            return path.read_text().splitlines()
        time.sleep(0.05)
    raise AssertionError(f"{path} nicht geschrieben")


def test_find_window_binary_prefers_release(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert find_window_binary(tmp_path) is None
    debug = _fake_binary(tmp_path / "debug" / "jarvis-orb", tmp_path / "x")
    assert find_window_binary(tmp_path) == debug
    release = _fake_binary(tmp_path / "release" / "jarvis-orb", tmp_path / "x")
    assert find_window_binary(tmp_path) == release


def test_open_window_without_build_explains_how_to_build(tmp_path, monkeypatch):
    monkeypatch.setattr("core.orb.ORB_TARGET_DIR", tmp_path)
    bus = OrbEvents()
    ok, message = OrbWindow(bus).open()
    assert not ok and "cargo build --release" in message
    assert not bus.enabled  # ohne Fenster kein Server


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Shell-Skript als Ersatz-Fenster")
def test_open_window_starts_server_and_passes_port(tmp_path):
    pytest.importorskip("websockets")
    port_file = tmp_path / "port"
    binary = _fake_binary(tmp_path / "jarvis-orb", port_file)
    bus = OrbEvents()
    window = OrbWindow(bus, log_path=tmp_path / "orb.log")
    try:
        ok, _ = window.open(port=0, binary=binary)
        assert ok and bus.enabled and window.running
        port, token, child, pgid = _wait_for(port_file)[0].split()
        assert port == bus.address.rsplit(":", 1)[-1]
        assert token == bus.token and child == "1"
        # eigene Prozessgruppe: Strg+C im Terminal trifft das Fenster nicht
        assert int(pgid) != os.getpgid(0)
        assert window.open(binary=binary) == (True, "Der Orb läuft schon.")
        start = time.monotonic()
        window.close()
        assert not window.running
        # über EOF auf stdin beendet, nicht per terminate()
        assert _wait_for(port_file, lines=2)[1] == "eof"
        assert time.monotonic() - start < 1.5
    finally:
        window.close()
        bus.stop()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Shell-Skript als Ersatz-Fenster")
def test_orb_tool_switches_window_on_and_off(tmp_path, monkeypatch):
    pytest.importorskip("websockets")
    import core.orb as orb_module
    from config import config
    from tools.orb_tool import OrbTool

    bus = OrbEvents()
    window = OrbWindow(bus, log_path=tmp_path / "orb.log")
    binary = _fake_binary(tmp_path / "release" / "jarvis-orb", tmp_path / "port")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(orb_module, "ORB_TARGET_DIR", tmp_path)
    monkeypatch.setattr(orb_module, "bus", bus)
    monkeypatch.setattr(orb_module, "window", window)
    monkeypatch.setattr(orb_module, "open_window", window.open)
    monkeypatch.setattr(orb_module, "close_window", window.shut)
    monkeypatch.setattr(config, "ORB_PORT", 0)
    tool = OrbTool()
    try:
        assert tool.execute(action="status").output == "Der Orb ist aus."
        result = tool.execute(action="on")
        assert result.success and window.running and binary.exists()
        assert tool.execute(action="status").output == "Der Orb läuft."
        result = tool.execute(action="off")
        assert result.success and result.output == "Orb ausgeschaltet." and not window.running
        assert tool.execute(action="off").output == "Der Orb ist schon aus."
        assert not tool.execute(action="blinken").success
    finally:
        window.close()
        bus.stop()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Shell-Skript als Ersatz-Fenster")
def test_window_that_exits_immediately_is_reported_with_its_output(tmp_path):
    pytest.importorskip("websockets")
    binary = _fake_binary(tmp_path / "jarvis-orb", None, body='echo "WebView2 fehlt" >&2\nexit 3\n')
    bus = OrbEvents()
    window = OrbWindow(bus, log_path=tmp_path / "orb.log")
    try:
        ok, message = window.open(port=0, binary=binary)
        assert not ok and not window.running
        assert "Code 3" in message and "WebView2 fehlt" in message
    finally:
        window.close()
        bus.stop()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Shell-Skript als Ersatz-Fenster")
def test_busy_port_falls_back_to_free_port(tmp_path):
    pytest.importorskip("websockets")
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    busy = blocker.getsockname()[1]
    port_file = tmp_path / "port"
    bus = OrbEvents()
    window = OrbWindow(bus, log_path=tmp_path / "orb.log")
    try:
        ok, _ = window.open(port=busy, binary=_fake_binary(tmp_path / "jarvis-orb", port_file))
        assert ok
        port = _wait_for(port_file)[0].split()[0]
        assert int(port) != busy and port == bus.address.rsplit(":", 1)[-1]
    finally:
        window.close()
        bus.stop()
        blocker.close()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Shell-Skript als Ersatz-Fenster")
def test_window_exits_when_jarvis_dies(tmp_path):
    """Lebensader: stirbt Jarvis (kill -9), bekommt das Fenster EOF und beendet sich."""
    import subprocess

    pytest.importorskip("websockets")
    port_file = tmp_path / "port"
    _fake_binary(tmp_path / "jarvis-orb", port_file)
    script = (
        "import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from pathlib import Path; from core.orb import OrbEvents, OrbWindow;"
        "w = OrbWindow(OrbEvents(), log_path=Path(sys.argv[2]) / 'orb.log');"
        "print(w.open(port=0, binary=Path(sys.argv[2]) / 'jarvis-orb'), flush=True); time.sleep(30)"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    jarvis = subprocess.Popen([sys.executable, "-c", script, root, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert "True" in jarvis.stdout.readline()
        _wait_for(port_file)
        jarvis.kill()  # kein atexit, kein close()
        assert _wait_for(port_file, lines=2)[1] == "eof"
    finally:
        jarvis.kill()
