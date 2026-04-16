import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())

from bladeeye_pro.hardware import HardwareConfig
from bladeeye_pro.session import ProSession, SessionStore
from bladeeye_pro.sigint_logger import SigintLogger
from bladeeye_pro.smart_functions import DetectionEvent


def test_session_store_roundtrip(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    session = ProSession.from_runtime(
        name="opd_case",
        config=HardwareConfig(center_freq=868e6, sample_rate=5e6, gain=32.0),
        watchlist=[433_920_000.0, 868_300_000.0],
        detections=[],
    )
    store.save(session)
    loaded = store.load("opd_case")
    assert loaded.name == "opd_case"
    assert loaded.watchlist == [433_920_000.0, 868_300_000.0]


def test_sigint_logger_persists_detection(tmp_path: Path):
    db_path = tmp_path / "sigint.db"
    logger = SigintLogger(db_path)
    evt = DetectionEvent(
        timestamp=1.0,
        center_freq=868_300_000.0,
        energy=12.2,
        signal_strength=4.4,
        duration_s=0.02,
        modulation="FSK",
        baud_rate=1200.0,
        purpose="Telemetrie",
        protocol="FSK-Telemetry",
        label="Senzor",
    )
    logger.write_detection(evt)
    rows = logger._conn.execute("SELECT modulation, baud_rate, purpose FROM detections").fetchall()
    logger.close()
    assert rows == [("FSK", 1200.0, "Telemetrie")]
