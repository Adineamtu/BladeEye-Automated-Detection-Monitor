from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np
from backend.signatures_data import all_rf_signatures, capture_to_signature


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
    confidence: float = 0.0
    raw_hex: str = ""


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

    def __init__(self, confidence_threshold: float = 0.90) -> None:
        self._signatures = all_rf_signatures()
        self._confidence_threshold = float(np.clip(confidence_threshold, 0.0, 1.0))

    def refresh(self) -> None:
        self._signatures = all_rf_signatures()

    @staticmethod
    def _normalize_modulation(modulation: str) -> str:
        normalized = modulation.upper().replace("-", "/")
        if normalized in {"ASK/OOK", "OOK/ASK"}:
            return "OOK/ASK"
        if "FSK" in normalized:
            return "FSK"
        return normalized

    @staticmethod
    def _purpose_from_modulation(modulation: str) -> str:
        if modulation == "FSK":
            return "Telemetrie"
        if modulation == "OOK/ASK":
            return "Control remote"
        return "Necunoscut"

    def _confidence_from_distance(self, distance_us: float, pulse_width_us: float, pulse_gap_us: float) -> float:
        scale = max(200.0, pulse_width_us + pulse_gap_us)
        return float(np.clip(1.0 - (distance_us / scale), 0.0, 1.0))

    def classify(self, pulse_width_ms: float, pulse_gap_ms: float, modulation: str) -> tuple[str, str, float]:
        modulation = self._normalize_modulation(modulation)
        best = ("Necunoscut", self._purpose_from_modulation(modulation), 10e9)
        pulse_width_us = pulse_width_ms * 1000.0
        pulse_gap_us = pulse_gap_ms * 1000.0
        for sig in self._signatures:
            sig_mod = self._normalize_modulation(str(sig.get("modulation", "")))
            if sig_mod and modulation and sig_mod != modulation:
                continue
            short_pulse = float(sig.get("short_pulse") or 0.0)
            long_pulse = float(sig.get("long_pulse") or short_pulse)
            gap = float(sig.get("gap") or 0.0)
            sig_pulse = short_pulse if pulse_width_us <= (short_pulse + long_pulse) / 2.0 else long_pulse
            d_pw = abs(sig_pulse - pulse_width_us)
            d_gap = abs(gap - pulse_gap_us)
            dist = d_pw + d_gap
            if dist < best[2]:
                best = (str(sig.get("name", "Necunoscut")), self._purpose_from_modulation(modulation), dist)
        confidence = self._confidence_from_distance(best[2], pulse_width_us, pulse_gap_us)
        if confidence < self._confidence_threshold:
            return "Unknown / Raw Signal", "Necunoscut", confidence
        return best[0], best[1], confidence

    def save_user_label(
        self,
        *,
        name: str,
        pulse_width_ms: float,
        pulse_gap_ms: float,
        modulation: str,
    ) -> dict[str, object]:
        pulse_width_us = max(1.0, pulse_width_ms * 1000.0)
        gap_us = max(0.0, pulse_gap_ms * 1000.0)
        sig = capture_to_signature(
            name=name,
            short_pulse=pulse_width_us,
            long_pulse=pulse_width_us,
            gap=gap_us,
            modulation=self._normalize_modulation(modulation) or "UNKNOWN",
            file_name="user_labeled",
        )
        self.refresh()
        return sig


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
