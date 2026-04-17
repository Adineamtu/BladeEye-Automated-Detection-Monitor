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
    assert "snr_db" in data
    assert "ignored_as_noise" in data


def test_intelligence_classify_file_endpoint_accepts_complex64_upload():
    client = TestClient(api.app)
    iq_bytes = (b"\x00\x00\x80?\x00\x00\x00\x00" * 16)  # complex64(1+0j) * 16
    resp = client.post(
        "/api/intelligence/classify-file?filename=demo.complex",
        content=iq_bytes,
        headers={"content-type": "application/octet-stream"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["samples"] == 16
    assert "snr_db" in data


def test_intelligence_classify_batch_endpoint_returns_items():
    client = TestClient(api.app)
    payload = {
        "windows": [
            {
                "iq_real": [0.1, 0.2, -0.1, -0.2],
                "iq_imag": [0.0, 0.1, 0.0, -0.1],
            },
            {
                "iq_real": [0.5, 0.4, 0.3, 0.2],
                "iq_imag": [0.2, 0.1, 0.0, -0.1],
            },
        ]
    }
    resp = client.post("/api/intelligence/classify-batch", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert len(body["items"]) == 2
    assert all("modulation_type" in item for item in body["items"])


def test_telemetry_and_logs_endpoints_are_available():
    client = TestClient(api.app)
    telemetry = client.get("/api/telemetry")
    assert telemetry.status_code == 200
    tdata = telemetry.json()
    assert "buffer_load_percent" in tdata
    assert "zmq_throughput_bps" in tdata
    assert "ai_jobs_processed" in tdata

    logs = client.get("/api/logs?limit=5")
    assert logs.status_code == 200
    assert "items" in logs.json()

    zipped = client.get("/api/logs/export")
    assert zipped.status_code == 200
    assert zipped.headers["content-type"] == "application/zip"


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
