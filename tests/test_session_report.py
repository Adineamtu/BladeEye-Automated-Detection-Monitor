from fastapi.testclient import TestClient
import sys
from pathlib import Path
import base64
import json

sys.path.append(str(Path(__file__).resolve().parents[1]))
import api


def test_session_report(tmp_path, monkeypatch):
    client = TestClient(api.app)
    monkeypatch.setattr(api, "SESSIONS_DIR", tmp_path)

    payload = {
        "signals": [
            {
                "center_frequency": 1.0,
                "bandwidth": 2.0,
                "peak_power": 3.0,
                "start_time": 4.0,
                "end_time": 5.0,
                "modulation_type": "FSK",
                "baud_rate": 1000.0,
            }
        ],
        "watchlist": [433_000_000.0],
    }

    resp = client.post("/api/sessions/test", json=payload)
    assert resp.status_code == 200
    data = json.loads((tmp_path / "test.json").read_text())
    assert "likely_purpose" in data["signals"][0]

    # Create dummy waterfall snapshot
    img_path = tmp_path / "test.png"
    img_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAEklEQVR42mP8/5+hHgMDAgAE/wH+HBYDAAAAAElFTkSuQmCC"
    )
    img_path.write_bytes(img_data)

    resp = client.get("/api/sessions/test/report")
    assert resp.status_code == 200
    assert "Session Report" in resp.text
    assert len(resp.text.strip()) > 0
