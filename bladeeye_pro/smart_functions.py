from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np


@dataclass
class DetectionEvent:
    timestamp: float
    center_freq: float
    energy: float
    signal_strength: float
    duration_s: float
    modulation: str
    baud_rate: float
    purpose: str
    protocol: str
    label: str


class ModulationDetector:
    @staticmethod
    def detect(iq: np.ndarray) -> str:
        if iq.size < 4:
            return "UNKNOWN"
        amp = np.abs(iq)
        amp_var = float(np.var(amp))
        phase = np.unwrap(np.angle(iq))
        freq_dev = np.diff(phase)
        freq_var = float(np.var(freq_dev)) if freq_dev.size else 0.0

        if freq_var > amp_var * 1.7:
            return "FSK"
        if amp_var > freq_var * 1.7:
            return "ASK/OOK"
        return "OOK" if float(np.mean(amp)) < 0.65 else "ASK"


class SignatureClassifier:
    """Pulse-width/gap classifier backed by a local signature table."""

    def __init__(self) -> None:
        self._signatures = [
            {"label": "Senzor", "pw_ms": 0.35, "gap_ms": 1.1, "purpose": "Telemetrie"},
            {"label": "Telecomanda", "pw_ms": 0.55, "gap_ms": 1.8, "purpose": "Control remote"},
            {"label": "Bruiaj", "pw_ms": 2.5, "gap_ms": 0.2, "purpose": "Posibil interferenta"},
        ]

    def classify(self, pulse_width_ms: float, pulse_gap_ms: float) -> tuple[str, str]:
        best = ("Necunoscut", "Necunoscut", 10e9)
        for sig in self._signatures:
            d_pw = abs(sig["pw_ms"] - pulse_width_ms)
            d_gap = abs(sig["gap_ms"] - pulse_gap_ms)
            dist = d_pw + d_gap
            if dist < best[2]:
                best = (str(sig["label"]), str(sig["purpose"]), dist)
        return best[0], best[1]


class HoppingController:
    """Controls frequency hopping schedule and calls hardware retune callback."""

    def __init__(self, on_hop_callback) -> None:
        self._callback = on_hop_callback
        self.enabled = False
        self._frequencies: list[float] = []
        self._interval_s = 0.25
        self._next_hop_at = 0.0
        self._idx = 0

    def configure(self, freqs: list[float], interval_s: float) -> None:
        self._frequencies = list(freqs)
        self._interval_s = max(0.05, float(interval_s))
        self._idx = 0
        self._next_hop_at = time.monotonic()

    def tick(self) -> None:
        if not self.enabled or not self._frequencies:
            return
        now = time.monotonic()
        if now < self._next_hop_at:
            return
        freq = self._frequencies[self._idx]
        self._callback(freq)
        self._idx = (self._idx + 1) % len(self._frequencies)
        self._next_hop_at = now + self._interval_s
