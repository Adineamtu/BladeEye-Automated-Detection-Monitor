from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
import threading
import time
from typing import Protocol

import numpy as np


class SDRSource(Protocol):
    def configure(self, *, center_freq: float, sample_rate: float, bandwidth: float, gain: float) -> None: ...
    def read(self, count: int) -> np.ndarray: ...
    def close(self) -> None: ...


@dataclass
class HardwareConfig:
    center_freq: float = 868e6
    sample_rate: float = 5e6
    bandwidth: float = 5e6
    gain: float = 32.0
    chunk_size: int = 8192


class SimulatedBladeRFSource:
    """High-speed synthetic source used when hardware/libbladeRF is unavailable."""

    def __init__(self) -> None:
        self._sample_rate = 5e6
        self._center_freq = 868e6
        self._phase = 0.0

    def configure(self, *, center_freq: float, sample_rate: float, bandwidth: float, gain: float) -> None:
        self._center_freq = center_freq
        self._sample_rate = sample_rate

    def read(self, count: int) -> np.ndarray:
        t = (np.arange(count, dtype=np.float32) + self._phase) / self._sample_rate
        tone_a = np.exp(1j * (2.0 * np.pi * 45_000.0 * t))
        tone_b = 0.35 * np.exp(1j * (2.0 * np.pi * -130_000.0 * t))
        burst = (np.sin(2.0 * np.pi * 4.0 * t) > 0.85).astype(np.float32)
        ask = (0.3 + burst) * np.exp(1j * (2.0 * np.pi * 10_000.0 * t))
        noise = (np.random.randn(count) + 1j * np.random.randn(count)).astype(np.complex64) * 0.08
        self._phase += count
        return (tone_a + tone_b + ask + noise).astype(np.complex64)

    def close(self) -> None:
        return


class LibBladeRFSource:
    """Thin libbladeRF loader placeholder for future native stream binding."""

    def __init__(self, library_name: str = "libbladeRF.so") -> None:
        self._lib = ctypes.CDLL(library_name)
        self._closed = False

    @staticmethod
    def available(library_name: str = "libbladeRF.so") -> bool:
        try:
            ctypes.CDLL(library_name)
            return True
        except OSError:
            return False

    def configure(self, *, center_freq: float, sample_rate: float, bandwidth: float, gain: float) -> None:
        # Minimal skeleton by design: binding full bladerf sync API is platform-specific.
        _ = (center_freq, sample_rate, bandwidth, gain)

    def read(self, count: int) -> np.ndarray:
        raise NotImplementedError(
            "libbladeRF stream read is pending platform-specific binding; use simulated source for now."
        )

    def close(self) -> None:
        self._closed = True


class AcquisitionEngine:
    """Dedicated streaming thread: USB->RAM (or simulated source -> RAM)."""

    def __init__(self, config: HardwareConfig, source: SDRSource | None = None) -> None:
        self.config = config
        self._source = source or self._select_source()
        self._callbacks: list = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _select_source(self) -> SDRSource:
        force_sim = os.getenv("BLADEEYE_PRO_SIM", "0") == "1"
        if not force_sim and LibBladeRFSource.available():
            try:
                return LibBladeRFSource()
            except Exception:
                pass
        return SimulatedBladeRFSource()

    def add_sink(self, callback) -> None:
        self._callbacks.append(callback)

    def update_params(self, *, center_freq: float | None = None, bandwidth: float | None = None, gain: float | None = None) -> None:
        with self._lock:
            if center_freq is not None:
                self.config.center_freq = float(center_freq)
            if bandwidth is not None:
                self.config.bandwidth = float(bandwidth)
            if gain is not None:
                self.config.gain = float(gain)
            self._source.configure(
                center_freq=self.config.center_freq,
                sample_rate=self.config.sample_rate,
                bandwidth=self.config.bandwidth,
                gain=self.config.gain,
            )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._source.configure(
            center_freq=self.config.center_freq,
            sample_rate=self.config.sample_rate,
            bandwidth=self.config.bandwidth,
            gain=self.config.gain,
        )
        self._thread = threading.Thread(target=self._run, name="bladeeye-pro-acquisition", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        sleep_s = max(0.001, self.config.chunk_size / self.config.sample_rate)
        while not self._stop.is_set():
            try:
                chunk = self._source.read(self.config.chunk_size)
            except NotImplementedError:
                time.sleep(0.1)
                continue
            except Exception:
                time.sleep(0.01)
                continue

            for cb in self._callbacks:
                cb(chunk)
            time.sleep(sleep_s * 0.15)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._source.close()
