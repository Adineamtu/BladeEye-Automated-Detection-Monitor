from __future__ import annotations

import sqlite3
from pathlib import Path

from .smart_functions import DetectionEvent


class SigintLogger:
    def __init__(self, db_path: Path | str = "sessions/bladeeye_pro_sigint.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detections (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              center_freq REAL NOT NULL,
              modulation TEXT NOT NULL,
              baud_rate REAL NOT NULL,
              purpose TEXT NOT NULL,
              label TEXT NOT NULL,
              signal_strength REAL NOT NULL,
              duration_s REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def write_detection(self, event: DetectionEvent) -> None:
        self._conn.execute(
            """
            INSERT INTO detections
            (ts, center_freq, modulation, baud_rate, purpose, label, signal_strength, duration_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.timestamp,
                event.center_freq,
                event.modulation,
                event.baud_rate,
                event.purpose,
                event.label,
                event.signal_strength,
                event.duration_s,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
