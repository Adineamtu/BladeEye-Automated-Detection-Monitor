from fastapi.testclient import TestClient
import api


def setup_monitor():
    class DummyMonitor:
        def __init__(self):
            self.center_freq = 100.0
            self.samp_rate = 1e6
            self.fft_size = 128
            self.rx_gain = 10.0
            self.hopping_enabled = False

        def get_config(self):
            return {
                "center_freq": self.center_freq,
                "samp_rate": self.samp_rate,
                "fft_size": self.fft_size,
                "gain": self.rx_gain,
                "hopping_enabled": self.hopping_enabled,
                "current_freq": self.center_freq,
            }

        def start_hopping(self):
            self.hopping_enabled = True

        def stop_hopping(self):
            self.hopping_enabled = False

    api.monitor = DummyMonitor()
    api.config_state = {
        "center_freq": None,
        "samp_rate": None,
        "fft_size": 1024,
        "gain": None,
        "hopping_enabled": False,
    }


def test_hopping_toggle_endpoint():
    setup_monitor()
    client = TestClient(api.app)

    resp = client.post("/api/hopping", json={"enabled": True})
    assert resp.status_code == 200
    assert api.monitor.hopping_enabled is True
    assert resp.json()["hopping_enabled"] is True

    resp = client.post("/api/hopping", json={"enabled": False})
    assert resp.status_code == 200
    assert api.monitor.hopping_enabled is False
    assert api.config_state["hopping_enabled"] is False
