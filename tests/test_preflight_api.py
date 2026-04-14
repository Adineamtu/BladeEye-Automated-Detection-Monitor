from fastapi.testclient import TestClient

import api
from backend.preflight import PreflightStatus


def test_preflight_endpoint_exposes_runtime_mode():
    api.preflight_status = PreflightStatus(
        hardware_detected=False,
        usb_access_ok=False,
        mode='demo',
        detail='Fallback demo',
    )
    api.config_state['data_bridge'] = 'zmq'
    client = TestClient(api.app)

    resp = client.get('/api/preflight')
    assert resp.status_code == 200
    data = resp.json()
    assert data['runtime_mode'] == 'demo'
    assert data['hardware_detected'] is False
    assert data['data_bridge'] == 'zmq'
    assert 'firmware_version' in data
    assert 'firmware_warning' in data


def test_health_returns_demo_payload_when_runtime_mode_is_demo():
    api.config_state['runtime_mode'] = 'demo'
    client = TestClient(api.app)

    resp = client.get('/api/health')
    assert resp.status_code == 200
    data = resp.json()
    assert data['healthy'] is True
    assert data['mode'] == 'demo'
