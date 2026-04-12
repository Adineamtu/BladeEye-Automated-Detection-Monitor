from fastapi.testclient import TestClient
import api


def test_frequency_deviation_endpoint():
    class DummyMonitor:
        def get_frequency_deviation(self, freq):
            return [(0.0, 10.0), (1.0, -5.0)]

    api.monitor = DummyMonitor()
    client = TestClient(api.app)
    resp = client.get('/api/signals/123.0/deviation')
    assert resp.status_code == 200
    assert resp.json() == {"times": [0.0, 1.0], "deviations": [10.0, -5.0]}
