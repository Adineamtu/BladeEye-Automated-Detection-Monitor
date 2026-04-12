from fastapi.testclient import TestClient
import api
import numpy as np


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

        def set_center_freq(self, v):
            self.center_freq = v

        def set_sample_rate(self, v):
            self.samp_rate = v

        def set_fft_size(self, v):
            self.fft_size = v

        def set_gain(self, v):
            self.rx_gain = v

        def get_power_spectrum(self):
            return np.zeros(int(self.fft_size))

    api.monitor = DummyMonitor()
    api.config_state = {
        "center_freq": None,
        "samp_rate": None,
        "fft_size": 1024,
        "gain": None,
        "hopping_enabled": False,
    }


def test_config_roundtrip():
    setup_monitor()
    client = TestClient(api.app)

    resp = client.get('/api/config')
    assert resp.status_code == 200
    assert resp.json()['center_freq'] == 100.0

    resp = client.post('/api/config', json={'center_freq': 200.0, 'fft_size': 256})
    assert resp.status_code == 200
    assert api.monitor.center_freq == 200.0
    assert api.monitor.fft_size == 256


def test_fft_size_reflected_in_spectrum_stream():
    setup_monitor()
    client = TestClient(api.app)

    with client.websocket_connect('/ws/spectrum') as ws:
        first = ws.receive_json()
        assert len(first) == 128
        client.post('/api/config', json={'fft_size': 64})
        second = ws.receive_json()
        assert len(second) == 64


def test_bandwidth_endpoint_accepts_discrete_values():
    setup_monitor()
    client = TestClient(api.app)
    resp = client.put('/api/config/bandwidth?value=5000000')
    assert resp.status_code == 200
    assert api.monitor.samp_rate == 5_000_000


def test_bandwidth_endpoint_rejects_non_discrete_values():
    setup_monitor()
    client = TestClient(api.app)
    resp = client.put('/api/config/bandwidth?value=3000000')
    assert resp.status_code == 422


def test_bandwidth_endpoint_pushes_command_to_cpp_socket(monkeypatch):
    setup_monitor()
    client = TestClient(api.app)
    called = {}

    class DummySocket:
        def sendto(self, payload, path):
            called['payload'] = payload
            called['path'] = path

        def close(self):
            called['closed'] = True

    monkeypatch.setattr(api.socket, 'socket', lambda *_args, **_kwargs: DummySocket())

    resp = client.put('/api/config/bandwidth?value=2000000')
    assert resp.status_code == 200
    assert called['payload'] == b'SET_BW:2000000'
    assert called['path'] == api.SDR_CORE_CMD_SOCKET
    assert called['closed'] is True
