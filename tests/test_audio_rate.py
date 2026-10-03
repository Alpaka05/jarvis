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
