from fastapi.testclient import TestClient
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
import api


def test_session_endpoints(tmp_path, monkeypatch):
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
        "recordings": [
            {"freq": 1.0, "path": "foo.iq"}
        ],
    }

    resp = client.post("/api/sessions/test.json", json=payload)
    assert resp.status_code == 200
    assert (tmp_path / "test.json").exists()

    resp = client.get("/api/sessions")
    assert resp.json() == ["test.json"]

    resp = client.get("/api/sessions/test.json")
    data = resp.json()
    assert data["watchlist"] == [433_000_000.0]
    assert data["signals"][0]["center_frequency"] == 1.0
    assert "likely_purpose" in data["signals"][0]
    assert data["recordings"][0]["path"] == "foo.iq"
    assert api.recordings[0]["path"] == "foo.iq"

    resp = client.get("/api/signals")
    sigs = resp.json()
    assert "likely_purpose" in sigs[0]

