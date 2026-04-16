from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .smart_functions import DetectionEvent, ModulationDetector, SignatureClassifier


@dataclass
class DSPFrame:
    fft_db: np.ndarray
    averaged_fft_db: np.ndarray
    energy: float
    threshold: float
    event: DetectionEvent | None


class DSPEngine:
    """Dual-path DSP engine: visual FFT + detection pipeline."""

    def __init__(self, sample_rate: float, center_freq: float, fft_size: int = 2048, averaging: float = 0.22) -> None:
        self.sample_rate = float(sample_rate)
        self.center_freq = float(center_freq)
        self.fft_size = int(fft_size)
        self._avg_alpha = float(np.clip(averaging, 0.01, 0.95))
        self._avg_fft = np.full(self.fft_size, -130.0, dtype=np.float32)

        self._noise_floor = 1e-6
        self._noise_alpha = 0.02
        self._trigger_gain = 4.0
        self._classifier = SignatureClassifier()
        self._last_active_samples = 0

    def set_center_freq(self, center_freq: float) -> None:
        self.center_freq = float(center_freq)

    def set_trigger_gain(self, gain: float) -> None:
        self._trigger_gain = max(1.0, float(gain))

    @staticmethod
    def _estimate_baud_rate(active_edges: np.ndarray, sample_rate: float) -> float:
        if active_edges.size < 2:
            return 0.0
        mean_samples = float(np.mean(np.diff(active_edges)))
        if mean_samples <= 0:
            return 0.0
        return float(sample_rate / mean_samples)

    @staticmethod
    def _protocol_from_modulation(modulation: str, baud_rate: float) -> str:
        if modulation.startswith("FSK"):
            return "FSK-Telemetry"
        if modulation in {"ASK/OOK", "OOK"} and baud_rate > 1000:
            return "OOK-Remote"
        return "Unknown"

    def process(self, iq: np.ndarray) -> DSPFrame:
        iq = np.asarray(iq, dtype=np.complex64)
        if iq.size < self.fft_size:
            padded = np.zeros(self.fft_size, dtype=np.complex64)
            padded[: iq.size] = iq
            iq = padded
        else:
            iq = iq[: self.fft_size]

        win = np.hanning(iq.size).astype(np.float32)
        spec = np.fft.fftshift(np.fft.fft(iq * win))
        power = np.abs(spec) ** 2
        fft_db = 10.0 * np.log10(power + 1e-12)
        self._avg_fft = ((1 - self._avg_alpha) * self._avg_fft) + (self._avg_alpha * fft_db)

        energy = float(np.mean(np.abs(iq) ** 2))
        self._noise_floor = ((1.0 - self._noise_alpha) * self._noise_floor) + (self._noise_alpha * energy)
        threshold = self._noise_floor * self._trigger_gain

        event = None
        if energy > threshold:
            amp = np.abs(iq)
            active = amp > (np.mean(amp) + 0.8 * np.std(amp))
            edges = np.diff(active.astype(np.int8), prepend=0, append=0)
            starts = np.where(edges == 1)[0]
            stops = np.where(edges == -1)[0]
            widths = (stops[: starts.size] - starts[: stops.size]) if starts.size and stops.size else np.array([])
            pulse_width_ms = float(np.mean(widths) / self.sample_rate * 1000.0) if widths.size else 0.0

            mod = ModulationDetector.detect(iq)
            pulse_gap_ms = float(np.mean(np.diff(starts)) / self.sample_rate * 1000.0) if starts.size > 1 else 0.0
            label, purpose = self._classifier.classify(pulse_width_ms, pulse_gap_ms)
            baud_rate = self._estimate_baud_rate(starts, self.sample_rate)
            protocol = self._protocol_from_modulation(mod, baud_rate)
            duration_s = max(pulse_width_ms / 1000.0, 1.0 / self.sample_rate)
            event = DetectionEvent(
                timestamp=__import__("time").time(),
                center_freq=self.center_freq,
                energy=energy,
                signal_strength=float(np.max(np.abs(iq))),
                duration_s=duration_s,
                modulation=mod,
                baud_rate=baud_rate,
                purpose=purpose,
                protocol=protocol,
                label=label,
            )

        return DSPFrame(
            fft_db=fft_db.astype(np.float32),
            averaged_fft_db=self._avg_fft.astype(np.float32),
            energy=energy,
            threshold=threshold,
            event=event,
        )
