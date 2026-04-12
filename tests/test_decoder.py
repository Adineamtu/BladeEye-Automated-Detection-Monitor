import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))
import backend.decoder as decmod  # noqa: E402

Decoder = decmod.Decoder


def test_decode_ook(tmp_path):
    bits = "01000001"
    iq = np.array([0, 1, 0, 0, 0, 0, 0, 1], dtype=np.complex64)
    path = tmp_path / "ook.iq"
    iq.tofile(path)
    dec = Decoder(path, {"modulation_type": "OOK"})
    out = dec.decode(1.0)
    assert out["binary"] == bits
    assert out["hex"] == "41"
    assert out["ascii"] == "A"


def test_decode_fsk(tmp_path):
    bits = "10"
    samp_rate = 10
    baud = 1
    sps = int(samp_rate / baud)
    t = np.arange(len(bits) * sps) / samp_rate
    iq = np.empty(len(bits) * sps, dtype=np.complex64)
    for i, b in enumerate(bits):
        freq = 1 if b == "1" else -1
        phi = 2 * np.pi * freq * t[i * sps : (i + 1) * sps]
        iq[i * sps : (i + 1) * sps] = np.exp(1j * phi)
    path = tmp_path / "fsk.iq"
    iq.tofile(path)
    dec = Decoder(path, {"modulation_type": "FSK", "baud_rate": baud})
    out = dec.decode(samp_rate)
    assert out["binary"] == bits


def test_filter_used_for_ook(tmp_path, monkeypatch):
    iq = np.array([0, 1, 0, 1], dtype=np.complex64)
    path = tmp_path / "ook_filter.iq"
    iq.tofile(path)
    called: dict[str, tuple] = {}

    def fake_filter(arr, sr, low, high, order=None):
        called["args"] = (sr, low, high, order)
        return np.array([1, 2, 1, 2], dtype=np.complex64)

    monkeypatch.setattr(decmod, "apply_filter", fake_filter)
    dec = Decoder(
        path,
        {
            "modulation_type": "OOK",
            "low_cut": 1.0,
            "high_cut": 2.0,
            "order": 3,
        },
    )
    out = dec.decode(10.0)
    assert called["args"] == (10.0, 1.0, 2.0, 3)
    assert out["binary"] == "0101"


def test_filter_used_for_fsk(tmp_path, monkeypatch):
    bits = "1010"
    samp_rate = 10
    baud = 1
    sps = int(samp_rate / baud)
    t = np.arange(len(bits) * sps) / samp_rate
    iq = np.empty(len(bits) * sps, dtype=np.complex64)
    for i, b in enumerate(bits):
        freq = 1 if b == "1" else -1
        phi = 2 * np.pi * freq * t[i * sps : (i + 1) * sps]
        iq[i * sps : (i + 1) * sps] = np.exp(1j * phi)
    path = tmp_path / "fsk_filter.iq"
    iq.tofile(path)

    called: dict[str, tuple] = {}

    def fake_filter(arr, sr, low, high, order=None):
        called["args"] = (sr, low, high, order)
        return np.ones_like(arr, dtype=np.complex64)

    monkeypatch.setattr(decmod, "apply_filter", fake_filter)
    dec = Decoder(
        path,
        {
            "modulation_type": "FSK",
            "baud_rate": baud,
            "low_cut": 1.0,
            "high_cut": 2.0,
            "order": 4,
        },
    )
    out = dec.decode(samp_rate)
    assert called["args"] == (samp_rate, 1.0, 2.0, 4)
    assert out["binary"] == "0" * len(bits)
