from fastapi.testclient import TestClient
import time

import api


def test_sigint_targets_and_log_export():
    client = TestClient(api.app)
    with api.sigint_store._lock:
        api.sigint_store._conn.execute("DELETE FROM sigint_log")
        api.sigint_store._conn.execute("DELETE FROM sigint_targets")
        api.sigint_store._conn.commit()

    target = client.post(
        "/api/sigint/targets",
        json={
            "label": "FSK 433.9",
            "center_frequency": 433_900_000,
            "tolerance_hz": 20_000,
            "modulation_type": "FSK",
        },
    )
    assert target.status_code == 200
    target_payload = target.json()

    sig = api.Signal(
        center_frequency=433_900_100,
        bandwidth=12_500,
        peak_power=-31.2,
        start_time=time.time(),
        end_time=time.time(),
        modulation_type="FSK",
        baud_rate=2400,
        protocol_name="Weather",
        likely_purpose="sensor",
    )
    api._capture_sigint_event(sig)

    rows = client.get("/api/sigint/log?limit=10")
    assert rows.status_code == 200
    items = rows.json()["items"]
    assert items
    assert items[0]["watchlist_hit"] == 1

    csv_export = client.get("/api/sigint/export?format=csv")
    assert csv_export.status_code == 200
    assert "center_frequency" in csv_export.text

    delete_resp = client.delete(f"/api/sigint/targets/{target_payload['id']}")
    assert delete_resp.status_code == 200


def test_sigint_sessionizing_deduplicates_recent_hits():
    with api.sigint_store._lock:
        api.sigint_store._conn.execute("DELETE FROM sigint_log")
        api.sigint_store._conn.commit()
    now = time.time()
    event = api.SigintEvent(
        timestamp=now,
        center_frequency=915_000_000,
        bandwidth=50_000,
        rssi_db=-20.0,
        modulation_type="FSK",
        baud_rate=9600,
        protocol_name="Pager",
        decoded_payload="A1",
        confidence=0.9,
    )
    api.sigint_store.ingest_now(event)
    api.sigint_store.ingest_now(
        api.SigintEvent(
            timestamp=now + 1,
            center_frequency=event.center_frequency,
            bandwidth=event.bandwidth,
            rssi_db=-19.0,
            modulation_type=event.modulation_type,
            baud_rate=event.baud_rate,
            protocol_name=event.protocol_name,
            decoded_payload=event.decoded_payload,
            confidence=event.confidence,
        )
    )

    rows = api.sigint_store.fetch_entries(limit=20, frequency=915_000_000)
    assert rows
    assert rows[0]["hit_count"] >= 2
