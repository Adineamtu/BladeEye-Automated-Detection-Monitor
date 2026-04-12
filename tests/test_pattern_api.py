from pathlib import Path
import numpy as np
from fastapi.testclient import TestClient
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
import api  # noqa: E402


def setup_signal(tmp_path):
    api.signals.clear()
    api.recordings.clear()
    bits = "1010"
    iq = np.array([1 if b == "1" else 0 for b in bits], dtype=np.complex64)
    path = tmp_path / "rec.iq"
    iq.tofile(path)
    api.recordings.append({"freq": 100.0, "path": str(path)})
    api.signals.append(api.Signal(100.0, 0, 0, 0, None, "OOK", None))
    api.config_state["samp_rate"] = 1
    return bits


def test_pattern_matching(tmp_path):
    client = TestClient(api.app)
    patterns_file = Path("sessions/patterns.json")
    if patterns_file.exists():
        patterns_file.unlink()
    bits = setup_signal(tmp_path)
    resp = client.post("/api/patterns/test", json={"bitstrings": [bits]})
    assert resp.status_code == 200
    resp = client.post("/api/signals/100.0/decode")
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "test"
    assert api.signals[0].label == "test"
