from fastapi.testclient import TestClient
import api


def test_frequency_trace_endpoint():
    class DummyMonitor:
        def get_frequency_track(self, freq):
            return [(0.0, freq), (1.0, freq + 10.0)]

    api.monitor = DummyMonitor()
    client = TestClient(api.app)
    resp = client.get('/api/signals/123.0/trace')
    assert resp.status_code == 200
    assert resp.json() == {"times": [0.0, 1.0], "frequencies": [123.0, 133.0]}
