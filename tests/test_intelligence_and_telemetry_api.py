from fastapi.testclient import TestClient

import api


def test_intelligence_classify_endpoint_returns_inference_payload():
    client = TestClient(api.app)
    payload = {
        "iq_real": [0.1, 0.2, -0.1, -0.2, 0.1, 0.2, -0.1, -0.2],
        "iq_imag": [0.0, 0.1, 0.0, -0.1, 0.0, 0.1, 0.0, -0.1],
    }
    resp = client.post("/api/intelligence/classify", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "modulation_type" in data
    assert "signal_strength_rssi_db" in data
    assert "confidence" in data


def test_telemetry_and_logs_endpoints_are_available():
    client = TestClient(api.app)
    telemetry = client.get("/api/telemetry")
    assert telemetry.status_code == 200
    tdata = telemetry.json()
    assert "buffer_load_percent" in tdata
    assert "zmq_throughput_bps" in tdata

    logs = client.get("/api/logs?limit=5")
    assert logs.status_code == 200
    assert "items" in logs.json()


def test_auto_actions_endpoint_adds_rule():
    client = TestClient(api.app)
    resp = client.post(
        "/api/actions",
        json={
            "protocol_name": "Test Proto",
            "action": "arm_recording",
            "duration_after": 0.4,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["protocol_name"] == "Test Proto"
