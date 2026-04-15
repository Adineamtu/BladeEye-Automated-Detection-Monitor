from fastapi.testclient import TestClient
import api


def setup_monitor():
    class DummyMonitor:
        def __init__(self):
            self.is_running = False

        def start(self):
            self.is_running = True

        def stop(self):
            self.is_running = False

    api.monitor = None
    api.monitor_factory = DummyMonitor


def test_start_stop_scan():
    setup_monitor()
    client = TestClient(api.app)

    resp = client.post("/api/scan/start")
    assert resp.status_code == 200
    assert api.monitor.is_running is True
    assert resp.json()["is_running"] is True

    resp = client.post("/api/scan/start")
    assert resp.status_code == 409

    resp = client.post("/api/scan/stop")
    assert resp.status_code == 200
    assert api.monitor is None
    assert resp.json()["is_running"] is False

    resp = client.post("/api/scan/stop")
    assert resp.status_code == 409
