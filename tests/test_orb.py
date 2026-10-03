"""Orb-Ereignisse: Pegel-Skala, Zustandslogik und echter Versand über WebSocket."""
import json
import time

import numpy as np
import pytest

from core.orb import OrbEvents, normalize_rms


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


def _connect(bus):
    from websockets.sync.client import connect

    return connect(bus.address, open_timeout=2)


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


def test_port_in_use_is_reported_not_raised(server):
    port = int(server.address.rsplit(":", 1)[1])
    other = OrbEvents()
    error = other.start(port=port)
    assert error and error.startswith("Orb-Server nicht gestartet")
    assert not other.enabled
