from pathlib import Path
import sys

import numpy as np
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))
import api  # noqa: E402
import backend.decoder as decmod  # noqa: E402


def test_decode_endpoint(tmp_path):
    client = TestClient(api.app)
    api.signals.clear()
    api.recordings.clear()

    bits = "01000001"
    iq = np.array([0, 1, 0, 0, 0, 0, 0, 1], dtype=np.complex64)
    path = tmp_path / "rec.iq"
    iq.tofile(path)
    api.recordings.append({"freq": 100.0, "path": str(path)})
    api.signals.append(api.Signal(100.0, 0, 0, 0, None, "OOK", None))
    api.config_state["samp_rate"] = 1

    resp = client.post("/api/signals/100.0/decode")
    assert resp.status_code == 200
    data = resp.json()
    assert data["binary"] == bits
    assert data["hex"] == "41"
    assert data["ascii"] == "A"


def test_decode_endpoint_with_filter(tmp_path, monkeypatch):
    client = TestClient(api.app)
    api.signals.clear()
    api.recordings.clear()

    iq = np.array([0, 1, 0, 1], dtype=np.complex64)
    path = tmp_path / "rec2.iq"
    iq.tofile(path)
    api.recordings.append({"freq": 200.0, "path": str(path)})
    api.signals.append(api.Signal(200.0, 0, 0, 0, None, "OOK", None))
    api.config_state["samp_rate"] = 10

    called: dict[str, tuple] = {}

    def fake_filter(arr, sr, low, high, order=None):
        called["args"] = (sr, low, high, order)
        return np.array([1, 2, 1, 2], dtype=np.complex64)

    monkeypatch.setattr(decmod, "apply_filter", fake_filter)

    resp = client.post(
        "/api/signals/200.0/decode", params={"low_cut": 1, "high_cut": 2, "order": 3}
    )
    assert resp.status_code == 200
    assert called["args"] == (10, 1.0, 2.0, 3)
    data = resp.json()
    assert data["binary"] == "0101"
