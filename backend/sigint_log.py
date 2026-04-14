"""SQLite-backed SIGINT log with async ingestion and watch targets."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import csv
import io
import json
import sqlite3
import threading
import time
from pathlib import Path


@dataclass(slots=True)
class SigintEvent:
    timestamp: float
    center_frequency: float
    bandwidth: float | None
    rssi_db: float | None
    modulation_type: str | None
    baud_rate: float | None
    protocol_name: str | None
    decoded_payload: str | None
    confidence: float


class SigintLogStore:
    """Small SQLite data store optimized for append/update signal intelligence rows."""

    def __init__(self, db_path: Path, dedupe_window_seconds: float = 5.0) -> None:
        self.db_path = Path(db_path)
        self.dedupe_window_seconds = float(dedupe_window_seconds)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._queue: asyncio.Queue[SigintEvent] = asyncio.Queue(maxsize=2048)
        self._worker_task: asyncio.Task | None = None
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sigint_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_seen_ts REAL NOT NULL,
                    last_seen_ts REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 1,
                    center_frequency REAL NOT NULL,
                    bandwidth REAL,
                    rssi_db REAL,
                    modulation_type TEXT,
                    baud_rate REAL,
                    protocol_name TEXT,
                    decoded_payload TEXT,
                    confidence REAL NOT NULL DEFAULT 0,
                    watchlist_hit INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sigint_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    center_frequency REAL,
                    tolerance_hz REAL NOT NULL DEFAULT 25000,
                    modulation_type TEXT,
                    protocol_name TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sigint_last_seen ON sigint_log(last_seen_ts DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sigint_freq ON sigint_log(center_frequency)")
            self._conn.commit()

    async def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker(), name="sigint-log-worker")

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def enqueue(self, event: SigintEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                _ = self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                return
            self._queue.put_nowait(event)

    async def _worker(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                self._upsert_event(event)
            finally:
                self._queue.task_done()

    def ingest_now(self, event: SigintEvent) -> None:
        self._upsert_event(event)

    def _upsert_event(self, event: SigintEvent) -> None:
        with self._lock:
            cur = self._conn.cursor()
            modulation = (event.modulation_type or "").upper()
            protocol = event.protocol_name or ""
            payload = event.decoded_payload or ""
            cur.execute(
                """
                SELECT id, rssi_db, confidence, hit_count
                FROM sigint_log
                WHERE ABS(center_frequency - ?) <= 500
                  AND IFNULL(modulation_type, '') = ?
                  AND IFNULL(protocol_name, '') = ?
                  AND IFNULL(decoded_payload, '') = ?
                  AND (? - last_seen_ts) <= ?
                ORDER BY last_seen_ts DESC
                LIMIT 1
                """,
                (event.center_frequency, modulation, protocol, payload, event.timestamp, self.dedupe_window_seconds),
            )
            row = cur.fetchone()
            watch_hit = 1 if self._target_match(cur, event) else 0
            if row:
                cur.execute(
                    """
                    UPDATE sigint_log
                    SET last_seen_ts = ?,
                        hit_count = hit_count + 1,
                        rssi_db = CASE WHEN rssi_db IS NULL THEN ? ELSE MAX(rssi_db, ?) END,
                        confidence = MAX(confidence, ?),
                        bandwidth = COALESCE(?, bandwidth),
                        baud_rate = COALESCE(?, baud_rate),
                        watchlist_hit = CASE WHEN watchlist_hit = 1 OR ? = 1 THEN 1 ELSE 0 END
                    WHERE id = ?
                    """,
                    (
                        event.timestamp,
                        event.rssi_db,
                        event.rssi_db,
                        event.confidence,
                        event.bandwidth,
                        event.baud_rate,
                        watch_hit,
                        int(row["id"]),
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO sigint_log (
                        first_seen_ts, last_seen_ts, center_frequency, bandwidth, rssi_db,
                        modulation_type, baud_rate, protocol_name, decoded_payload, confidence,
                        watchlist_hit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.timestamp,
                        event.timestamp,
                        event.center_frequency,
                        event.bandwidth,
                        event.rssi_db,
                        modulation or None,
                        event.baud_rate,
                        event.protocol_name,
                        event.decoded_payload,
                        event.confidence,
                        watch_hit,
                    ),
                )
            self._conn.commit()

    def _target_match(self, cur: sqlite3.Cursor, event: SigintEvent) -> bool:
        modulation = (event.modulation_type or "").upper()
        protocol = event.protocol_name or ""
        cur.execute(
            """
            SELECT id FROM sigint_targets
            WHERE (center_frequency IS NULL OR ABS(center_frequency - ?) <= tolerance_hz)
              AND (IFNULL(modulation_type, '') = '' OR UPPER(modulation_type) = ?)
              AND (IFNULL(protocol_name, '') = '' OR protocol_name = ?)
            LIMIT 1
            """,
            (event.center_frequency, modulation, protocol),
        )
        return cur.fetchone() is not None

    def fetch_entries(self, *, limit: int = 250, watchlist_only: bool = False, frequency: float | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 2000))
        with self._lock:
            cur = self._conn.cursor()
            clauses: list[str] = []
            params: list[float | int] = []
            if watchlist_only:
                clauses.append("watchlist_hit = 1")
            if frequency is not None:
                clauses.append("ABS(center_frequency - ?) <= 1000")
                params.append(float(frequency))
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"""
                SELECT *
                FROM sigint_log
                {where}
                ORDER BY last_seen_ts DESC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = [dict(row) for row in cur.fetchall()]
        return rows

    def export_csv(self, *, watchlist_only: bool = False) -> str:
        rows = self.fetch_entries(limit=2000, watchlist_only=watchlist_only)
        headers = [
            "id",
            "first_seen_ts",
            "last_seen_ts",
            "hit_count",
            "center_frequency",
            "bandwidth",
            "rssi_db",
            "modulation_type",
            "baud_rate",
            "protocol_name",
            "decoded_payload",
            "confidence",
            "watchlist_hit",
        ]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()

    def export_json(self, *, watchlist_only: bool = False) -> str:
        rows = self.fetch_entries(limit=2000, watchlist_only=watchlist_only)
        return json.dumps(rows, ensure_ascii=False, indent=2)

    def list_targets(self) -> list[dict]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM sigint_targets ORDER BY created_at DESC")
            return [dict(row) for row in cur.fetchall()]

    def add_target(
        self,
        *,
        label: str,
        center_frequency: float | None,
        tolerance_hz: float,
        modulation_type: str | None,
        protocol_name: str | None,
    ) -> dict:
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO sigint_targets (label, center_frequency, tolerance_hz, modulation_type, protocol_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    label,
                    center_frequency,
                    max(1.0, float(tolerance_hz)),
                    (modulation_type or "").upper() or None,
                    protocol_name,
                    now,
                ),
            )
            target_id = int(cur.lastrowid)
            self._conn.commit()
        return {
            "id": target_id,
            "label": label,
            "center_frequency": center_frequency,
            "tolerance_hz": max(1.0, float(tolerance_hz)),
            "modulation_type": (modulation_type or "").upper() or None,
            "protocol_name": protocol_name,
            "created_at": now,
        }

    def delete_target(self, target_id: int) -> bool:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM sigint_targets WHERE id = ?", (int(target_id),))
            changed = cur.rowcount > 0
            self._conn.commit()
            return changed
