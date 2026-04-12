from fastapi.testclient import TestClient
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
import api


class DummyMonitor:
    def __init__(self):
        self.armed = []
        self.cancelled = []

    def arm_recording(self, freq, duration_after=0.2):
        self.armed.append((freq, duration_after))

    def cancel_recording(self, freq):
        self.cancelled.append(freq)


def test_record_endpoints(monkeypatch):
    client = TestClient(api.app)
    mon = DummyMonitor()
    monkeypatch.setattr(api, "monitor", mon)

    resp = client.post("/api/signals/123.0/record")
    assert resp.status_code == 200
    assert mon.armed == [(123.0, 0.2)]

    resp = client.delete("/api/signals/123.0/record")
    assert resp.status_code == 200
    assert mon.cancelled == [123.0]
