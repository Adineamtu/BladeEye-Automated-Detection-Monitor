from fastapi.testclient import TestClient
import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[1]))
import api  # noqa: E402


def test_session_round_trip_with_recordings_and_report(tmp_path, monkeypatch):
    """Ensure recordings persist and appear in the session report."""

    client = TestClient(api.app)
    monkeypatch.setattr(api, "SESSIONS_DIR", tmp_path)

    rec_file = tmp_path / "rec.iq"
    rec_file.write_bytes(b"iq")

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
        "recordings": [{"freq": 1.0, "path": rec_file.name}],
    }

    resp = client.post("/api/sessions/test", json=payload)
    assert resp.status_code == 200

    resp = client.get("/api/sessions/test")
    data = resp.json()
    assert data["recordings"][0]["freq"] == 1.0
    assert data["recordings"][0]["path"] == rec_file.name

    resp = client.get("/api/sessions/test/report")
    assert resp.status_code == 200
    assert rec_file.name in resp.text
