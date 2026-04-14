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
import uuid
from pathlib import Path
from typing import Iterable


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
    sync_word: str | None = None


@dataclass(slots=True)
class ActiveSignalSession:
    uid: str
    signature: str
    first_seen_ts: float
    last_seen_ts: float
    base_frequency: float
    min_frequency: float
    max_frequency: float
    hop_count: int = 0
    dwell_time_ms: float | None = None
    frequencies: list[float] | None = None

    def __post_init__(self) -> None:
        if self.frequencies is None:
            self.frequencies = [self.base_frequency]


class SigintLogStore:
    """Small SQLite data store optimized for append/update signal intelligence rows."""

    def __init__(
        self,
        db_path: Path,
        dedupe_window_seconds: float = 5.0,
        session_timeout_seconds: float = 2.0,
        hop_correlation_window_ms: float = 250.0,
        max_active_sessions: int = 20,
        write_batch_size: int = 64,
        write_flush_interval_ms: float = 40.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.dedupe_window_seconds = float(dedupe_window_seconds)
        self.session_timeout_seconds = float(session_timeout_seconds)
        self.hop_correlation_window_ms = float(hop_correlation_window_ms)
        self.max_active_sessions = max(1, int(max_active_sessions))
        self.write_batch_size = max(1, int(write_batch_size))
        self.write_flush_interval_s = max(0.001, float(write_flush_interval_ms) / 1000.0)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._queue: asyncio.Queue[SigintEvent] = asyncio.Queue(maxsize=2048)
        self._worker_task: asyncio.Task | None = None
        self._active_sessions: dict[str, ActiveSignalSession] = {}
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA temp_store=MEMORY")
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
                    watchlist_hit INTEGER NOT NULL DEFAULT 0,
                    session_uid TEXT,
                    signal_signature TEXT,
                    session_state TEXT NOT NULL DEFAULT 'ACTIVE',
                    base_frequency REAL,
                    hop_min_frequency REAL,
                    hop_max_frequency REAL,
                    hop_count INTEGER NOT NULL DEFAULT 0,
                    dwell_time_ms REAL,
                    hop_frequencies_json TEXT
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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sigint_session_uid ON sigint_log(session_uid)")
            self._ensure_optional_columns(cur)
            self._conn.commit()

    @staticmethod
    def _ensure_optional_columns(cur: sqlite3.Cursor) -> None:
        cur.execute("PRAGMA table_info(sigint_log)")
        existing = {str(row[1]) for row in cur.fetchall()}
        for column, ddl in (
            ("session_uid", "TEXT"),
            ("signal_signature", "TEXT"),
            ("session_state", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
            ("base_frequency", "REAL"),
            ("hop_min_frequency", "REAL"),
            ("hop_max_frequency", "REAL"),
            ("hop_count", "INTEGER NOT NULL DEFAULT 0"),
            ("dwell_time_ms", "REAL"),
            ("hop_frequencies_json", "TEXT"),
        ):
            if column not in existing:
                cur.execute(f"ALTER TABLE sigint_log ADD COLUMN {column} {ddl}")

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
            batch: list[SigintEvent] = [event]
            started = time.monotonic()
            while len(batch) < self.write_batch_size:
                remaining = self.write_flush_interval_s - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    next_event = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    batch.append(next_event)
                except TimeoutError:
                    break
            self._upsert_batch(batch)
            for _ in batch:
                self._queue.task_done()

    def ingest_now(self, event: SigintEvent) -> None:
        self._upsert_event(event)

    def _upsert_event(self, event: SigintEvent) -> None:
        self._upsert_batch([event])

    def _upsert_batch(self, events: Iterable[SigintEvent]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            for event in events:
                self._expire_sessions(cur, event.timestamp)
                modulation = (event.modulation_type or "").upper()
                protocol = event.protocol_name or ""
                payload = event.decoded_payload or ""
                signature = self._build_signature(event, modulation, protocol, payload)
                session = self._bind_session(signature, event)
                cur.execute(
                    """
                    SELECT id, rssi_db, confidence, hit_count
                    FROM sigint_log
                    WHERE IFNULL(session_uid, '') = ?
                    AND (? - last_seen_ts) <= ?
                    ORDER BY last_seen_ts DESC
                    LIMIT 1
                    """,
                    (session.uid, event.timestamp, self.session_timeout_seconds),
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
                            center_frequency = ?,
                            base_frequency = ?,
                            hop_min_frequency = ?,
                            hop_max_frequency = ?,
                            hop_count = ?,
                            dwell_time_ms = ?,
                            hop_frequencies_json = ?,
                            signal_signature = ?,
                            session_state = 'ACTIVE',
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
                            event.center_frequency,
                            session.base_frequency,
                            session.min_frequency,
                            session.max_frequency,
                            session.hop_count,
                            session.dwell_time_ms,
                            json.dumps(session.frequencies),
                            signature,
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
                            watchlist_hit, session_uid, signal_signature, session_state,
                            base_frequency, hop_min_frequency, hop_max_frequency, hop_count,
                            dwell_time_ms, hop_frequencies_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?)
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
                            session.uid,
                            signature,
                            session.base_frequency,
                            session.min_frequency,
                            session.max_frequency,
                            session.hop_count,
                            session.dwell_time_ms,
                            json.dumps(session.frequencies),
                        ),
                    )
            self._conn.commit()

    def _build_signature(self, event: SigintEvent, modulation: str, protocol: str, payload: str) -> str:
        sync_word = (event.sync_word or "").strip().upper()
        key = {
            "modulation": modulation,
            "baud_rate": round(float(event.baud_rate), 2) if event.baud_rate is not None else None,
            "protocol": protocol,
            "sync_word": sync_word,
            "payload_hint": payload[:32],
        }
        return json.dumps(key, sort_keys=True, separators=(",", ":"))

    def _bind_session(self, signature: str, event: SigintEvent) -> ActiveSignalSession:
        session = self._active_sessions.get(signature)
        if session is None:
            self._trim_active_sessions()
            session = ActiveSignalSession(
                uid=f"sig-{uuid.uuid4().hex[:12]}",
                signature=signature,
                first_seen_ts=event.timestamp,
                last_seen_ts=event.timestamp,
                base_frequency=event.center_frequency,
                min_frequency=event.center_frequency,
                max_frequency=event.center_frequency,
            )
            self._active_sessions[signature] = session
            return session
        previous_ts = session.last_seen_ts
        previous_freq = session.frequencies[-1] if session.frequencies else session.base_frequency
        session.last_seen_ts = event.timestamp
        session.min_frequency = min(session.min_frequency, event.center_frequency)
        session.max_frequency = max(session.max_frequency, event.center_frequency)
        if abs(previous_freq - event.center_frequency) > 500:
            delta_ms = max(0.0, (event.timestamp - previous_ts) * 1000.0)
            if delta_ms <= self.hop_correlation_window_ms:
                session.hop_count += 1
                session.dwell_time_ms = delta_ms
            if not session.frequencies or abs(session.frequencies[-1] - event.center_frequency) > 1:
                session.frequencies.append(event.center_frequency)
                if len(session.frequencies) > 128:
                    session.frequencies = session.frequencies[-128:]
        return session

    def _trim_active_sessions(self) -> None:
        overflow = len(self._active_sessions) - self.max_active_sessions + 1
        if overflow <= 0:
            return
        ordered = sorted(self._active_sessions.items(), key=lambda item: item[1].last_seen_ts)
        for signature, _ in ordered[:overflow]:
            self._active_sessions.pop(signature, None)

    def _expire_sessions(self, cur: sqlite3.Cursor, now_ts: float) -> None:
        stale_signatures = [
            signature
            for signature, session in self._active_sessions.items()
            if (now_ts - session.last_seen_ts) > self.session_timeout_seconds
        ]
        for signature in stale_signatures:
            session = self._active_sessions.pop(signature, None)
            if session is None:
                continue
            cur.execute(
                """
                UPDATE sigint_log
                SET session_state = 'CLOSED'
                WHERE session_uid = ?
                """,
                (session.uid,),
            )

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
            "session_uid",
            "signal_signature",
            "session_state",
            "base_frequency",
            "hop_min_frequency",
            "hop_max_frequency",
            "hop_count",
            "dwell_time_ms",
            "hop_frequencies_json",
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
