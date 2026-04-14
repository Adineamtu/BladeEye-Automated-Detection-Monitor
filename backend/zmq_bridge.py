"""Optional ZeroMQ spectrum bridge for SDR data ingestion."""

from __future__ import annotations

import numpy as np
import time

try:  # pragma: no cover - exercised only where pyzmq exists
    import zmq
except Exception:  # pragma: no cover
    zmq = None  # type: ignore


class ZmqSpectrumConsumer:
    """Receive float32 spectrum frames from a ZeroMQ PULL endpoint."""

    def __init__(self, endpoint: str, max_pending_frames: int = 100) -> None:
        self.endpoint = endpoint
        self.max_pending_frames = max(1, int(max_pending_frames))
        self._ctx = None
        self._sock = None
        self.enabled = False
        self.frames_received = 0
        self.bytes_received = 0
        self.dropped_frames = 0
        self.last_frame_ts = 0.0
        self._window_started = time.time()
        self._window_frames = 0
        self._window_bytes = 0
        self.fps = 0.0
        self.throughput_bps = 0.0
        self.latency_ms = 0.0
        self._spectrum_bins = 512
        if zmq is None:
            return
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.setsockopt(zmq.RCVHWM, self.max_pending_frames)
        self._sock.connect(endpoint)
        self.enabled = True

    def recv_latest(self) -> np.ndarray | None:
        """Return latest frame if available, otherwise None."""
        if not self.enabled or self._sock is None or zmq is None:
            return None

        loop_started = time.time()
        latest = None
        dropped = 0
        while dropped < self.max_pending_frames:
            try:
                payload = self._sock.recv(flags=zmq.NOBLOCK)
                if latest is not None:
                    dropped += 1
                latest = payload
            except zmq.Again:
                break
        else:
            # Queue pressure is high; prefer fresh data over adding latency.
            self.dropped_frames += 1

        if latest is None:
            return None
        if dropped:
            self.dropped_frames += dropped

        frames = self._extract_frames(latest)
        if not frames:
            return None
        if len(frames) > 1:
            self.dropped_frames += len(frames) - 1
        frame = frames[-1]
        if frame.size == 0 or not np.isfinite(frame).all():
            return None
        now = time.time()
        self.frames_received += 1
        self.bytes_received += len(latest)
        self._window_frames += 1
        self._window_bytes += len(latest)
        self.last_frame_ts = now
        elapsed = now - self._window_started
        if elapsed >= 1.0:
            self.fps = self._window_frames / elapsed
            self.throughput_bps = (self._window_bytes * 8.0) / elapsed
            self._window_started = now
            self._window_frames = 0
            self._window_bytes = 0
        self.latency_ms = max(0.0, (now - loop_started) * 1000.0)
        return frame

    def _extract_frames(self, payload: bytes) -> list[np.ndarray]:
        """Decode one or more float32 spectrum frames from one payload."""
        if len(payload) % 4 != 0:
            return []
        values = np.frombuffer(payload, dtype=np.float32)
        if values.size == 0:
            return []
        if values.size % self._spectrum_bins == 0 and values.size > self._spectrum_bins:
            chunks = values.reshape((-1, self._spectrum_bins))
            return [np.array(chunk, dtype=np.float32, copy=True) for chunk in chunks]
        return [np.array(values, dtype=np.float32, copy=True)]

    def telemetry(self) -> dict:
        """Return current throughput and queue pressure estimations."""
        if not self.enabled:
            return {
                "enabled": False,
                "frames_received": 0,
                "dropped_frames": 0,
                "throughput_bps": 0.0,
                "fps": 0.0,
                "buffer_load_percent": 0.0,
                "latency_ms": 0.0,
            }
        buffer_load = min(100.0, max(0.0, float(self.dropped_frames) * 2.0))
        return {
            "enabled": True,
            "endpoint": self.endpoint,
            "frames_received": self.frames_received,
            "bytes_received": self.bytes_received,
            "dropped_frames": self.dropped_frames,
            "throughput_bps": round(self.throughput_bps, 2),
            "fps": round(self.fps, 2),
            "latency_ms": round(self.latency_ms, 3),
            "buffer_load_percent": round(buffer_load, 2),
            "last_frame_ts": self.last_frame_ts,
        }

    def close(self) -> None:
        """Close socket resources."""
        if self._sock is not None:
            self._sock.close(linger=0)
            self._sock = None
