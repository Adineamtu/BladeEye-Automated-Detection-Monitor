"""Optional ZeroMQ spectrum bridge for SDR data ingestion."""

from __future__ import annotations

import numpy as np

try:  # pragma: no cover - exercised only where pyzmq exists
    import zmq
except Exception:  # pragma: no cover
    zmq = None  # type: ignore


class ZmqSpectrumConsumer:
    """Receive float32 spectrum frames from a ZeroMQ PULL endpoint."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._ctx = None
        self._sock = None
        self.enabled = False
        if zmq is None:
            return
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.connect(endpoint)
        self.enabled = True

    def recv_latest(self) -> np.ndarray | None:
        """Return latest frame if available, otherwise None."""
        if not self.enabled or self._sock is None or zmq is None:
            return None

        latest = None
        while True:
            try:
                payload = self._sock.recv(flags=zmq.NOBLOCK)
                latest = payload
            except zmq.Again:
                break

        if latest is None:
            return None

        frame = np.frombuffer(latest, dtype=np.float32).copy()
        if frame.size == 0 or not np.isfinite(frame).all():
            return None
        return frame

    def close(self) -> None:
        """Close socket resources."""
        if self._sock is not None:
            self._sock.close(linger=0)
            self._sock = None
