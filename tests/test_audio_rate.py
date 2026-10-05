import numpy as np

from core import platform_utils
from core.platform_utils import resample


def test_resample_keeps_duration_and_dtype():
    t = np.arange(16000) / 16000
    tone = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
    out = resample(tone, 16000, 48000)
    assert out.dtype == np.int16
    assert len(out) == 48000
    assert abs(int(np.abs(out).max()) - 8000) < 50


def test_resample_handles_float_columns():
    data = np.linspace(-1, 1, 441, dtype=np.float32).reshape(-1, 1)
    out = resample(data, 44100, 48000)
    assert out.shape == (480, 1)
    assert out.dtype == np.float32
    assert out[0, 0] == -1 and out[-1, 0] == 1


def test_resample_same_rate_is_noop():
    data = np.arange(10, dtype=np.int16)
    assert resample(data, 24000, 24000) is data


def test_output_sample_rate_only_on_mac(monkeypatch):
    monkeypatch.setattr(platform_utils, "IS_MAC", False)
    assert platform_utils.output_sample_rate() is None


def test_silence_native_output_keeps_python_output(tmp_path):
    """C-Ausgaben (direkt auf fd 1/2) verschwinden, print() und sys.stderr bleiben sichtbar."""
    import os
    import subprocess
    import sys

    code = (
        "import os, sys; from core import platform_utils as p; p.silence_native_output();"
        "os.write(1, b'C-STDOUT\\n'); os.write(2, b'C-STDERR\\n');"
        "print('PY-STDOUT'); print('PY-STDERR', file=sys.stderr)"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=30)
    assert "PY-STDOUT" in out.stdout and "C-STDOUT" not in out.stdout
    assert "PY-STDERR" in out.stderr and "C-STDERR" not in out.stderr


def test_audio_devices_changed_compares_with_last_refresh(monkeypatch):
    sig = {"value": ((1, 2), (1,), (2,))}
    monkeypatch.setattr(platform_utils, "audio_device_signature", lambda: sig["value"])
    monkeypatch.setattr(platform_utils, "_device_signature", None)
    assert not platform_utils.audio_devices_changed()  # erster Aufruf merkt sich den Stand
    assert not platform_utils.audio_devices_changed()
    sig["value"] = ((1, 3), (1,), (3,))  # z.B. Monitor-Lautsprecher weg, neues Standardgerät
    assert platform_utils.audio_devices_changed()


def test_audio_devices_changed_is_false_without_coreaudio(monkeypatch):
    monkeypatch.setattr(platform_utils, "audio_device_signature", lambda: None)
    assert not platform_utils.audio_devices_changed()
