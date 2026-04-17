from __future__ import annotations

from dataclasses import dataclass
import ctypes
from collections import deque
import os
import threading
import time
from ctypes.util import find_library
from typing import Any, Callable, Protocol

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
    """Minimal libbladeRF sync-RX source (SC16 Q11 -> complex64)."""

    def __init__(self, library_name: str = "libbladeRF.so") -> None:
        if library_name == "libbladeRF.so":
            library_name = find_library("bladeRF") or library_name
        self._lib = ctypes.CDLL(library_name)
        self._dev = ctypes.c_void_p()
        self._is_streaming = False
        self._closed = False
        self._sample_rate = 5_000_000
        self._prepare_api()

    @staticmethod
    def available(library_name: str = "libbladeRF.so") -> bool:
        try:
            if library_name == "libbladeRF.so":
                library_name = find_library("bladeRF") or library_name
            ctypes.CDLL(library_name)
            return True
        except OSError:
            return False

    def _prepare_api(self) -> None:
        # Function signatures
        self._lib.bladerf_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        self._lib.bladerf_open.restype = ctypes.c_int
        self._lib.bladerf_close.argtypes = [ctypes.c_void_p]
        self._lib.bladerf_close.restype = None
        self._lib.bladerf_strerror.argtypes = [ctypes.c_int]
        self._lib.bladerf_strerror.restype = ctypes.c_char_p
        self._lib.bladerf_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        self._lib.bladerf_set_sample_rate.restype = ctypes.c_int
        self._lib.bladerf_set_frequency.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64]
        self._lib.bladerf_set_frequency.restype = ctypes.c_int
        self._lib.bladerf_set_bandwidth.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        self._lib.bladerf_set_bandwidth.restype = ctypes.c_int
        self._lib.bladerf_set_gain.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._lib.bladerf_set_gain.restype = ctypes.c_int
        self._lib.bladerf_sync_config.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self._lib.bladerf_sync_config.restype = ctypes.c_int
        self._lib.bladerf_enable_module.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_bool]
        self._lib.bladerf_enable_module.restype = ctypes.c_int
        self._lib.bladerf_sync_rx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        self._lib.bladerf_sync_rx.restype = ctypes.c_int

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("BladeRF source is closed")
        if self._dev:
            return
        ret = self._lib.bladerf_open(ctypes.byref(self._dev), None)
        if ret != 0:
            raise RuntimeError(f"bladerf_open failed: {self._err(ret)}")

    def _err(self, code: int) -> str:
        try:
            return (self._lib.bladerf_strerror(int(code)) or b"unknown").decode("utf-8", errors="replace")
        except Exception:
            return f"error={code}"

    def _check(self, code: int, op: str) -> None:
        if code != 0:
            raise RuntimeError(f"{op} failed: {self._err(code)}")

    def configure(self, *, center_freq: float, sample_rate: float, bandwidth: float, gain: float) -> None:
        self._ensure_open()
        ch_rx0 = 0
        actual_sr = ctypes.c_uint(0)
        actual_bw = ctypes.c_uint(0)
        # BLADERF_RX_X1=0, BLADERF_FORMAT_SC16_Q11=0, channel RX0=0.
        # Keep transfer buffers smaller to reduce host-side backpressure and memory pressure.
        self._check(self._lib.bladerf_sync_config(self._dev, 0, 0, 12, 4096, 6, 2500), "bladerf_sync_config")
        self._check(self._lib.bladerf_set_frequency(self._dev, ch_rx0, int(center_freq)), "bladerf_set_frequency")
        sample_rate_ret = self._lib.bladerf_set_sample_rate(self._dev, ch_rx0, int(sample_rate), ctypes.byref(actual_sr))
        if sample_rate_ret != 0 and self._is_streaming:
            # Some firmware/libbladeRF combinations require stream toggle for SR changes.
            self._check(self._lib.bladerf_enable_module(self._dev, 0, False), "bladerf_enable_module(disable)")
            self._is_streaming = False
            self._check(
                self._lib.bladerf_set_sample_rate(self._dev, ch_rx0, int(sample_rate), ctypes.byref(actual_sr)),
                "bladerf_set_sample_rate(retry)",
            )
        self._check(
            self._lib.bladerf_set_bandwidth(self._dev, ch_rx0, int(bandwidth), ctypes.byref(actual_bw)),
            "bladerf_set_bandwidth",
        )
        self._check(self._lib.bladerf_set_gain(self._dev, ch_rx0, int(gain)), "bladerf_set_gain")
        if not self._is_streaming:
            self._check(self._lib.bladerf_enable_module(self._dev, 0, True), "bladerf_enable_module")
            self._is_streaming = True
        self._sample_rate = max(1, int(actual_sr.value) or int(sample_rate))

    def read(self, count: int) -> np.ndarray:
        if self._closed:
            raise RuntimeError("BladeRF source is closed")
        self._ensure_open()
        interleaved = np.empty(int(count) * 2, dtype=np.int16)
        ret = self._lib.bladerf_sync_rx(
            self._dev,
            interleaved.ctypes.data_as(ctypes.c_void_p),
            int(count),
            None,
            2500,
        )
        self._check(ret, "bladerf_sync_rx")
        iq = interleaved.reshape(-1, 2).astype(np.float32, copy=False)
        return (iq[:, 0] + 1j * iq[:, 1]).astype(np.complex64) / 2048.0

    def close(self) -> None:
        if self._closed:
            return
        if self._is_streaming:
            try:
                self._lib.bladerf_enable_module(self._dev, 0, False)
            except Exception:
                pass
            self._is_streaming = False
        if self._dev:
            try:
                self._lib.bladerf_close(self._dev)
            except Exception:
                pass
            self._dev = ctypes.c_void_p()
        self._closed = True


class AcquisitionEngine:
    """Dedicated streaming thread: USB->RAM (or simulated source -> RAM)."""

    def __init__(self, config: HardwareConfig, source: SDRSource | None = None) -> None:
        self.config = config
        self._startup_error: str | None = None
        self._source: SDRSource | None = source
        self._source_name = source.__class__.__name__ if source is not None else "Not initialized"
        self._callbacks: list = []
        self._error_callbacks: list = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _select_source(self) -> SDRSource:
        force_sim = os.getenv("BLADEEYE_PRO_SIM", "0") == "1"
        force_lib = os.getenv("BLADEEYE_PRO_USE_LIB", "0") == "1"
        if force_sim:
            return SimulatedBladeRFSource()
        if LibBladeRFSource.available():
            try:
                return LibBladeRFSource()
            except Exception as exc:
                self._startup_error = f"LibBladeRF init failed, falling back to simulated source: {exc}"
                return SimulatedBladeRFSource()
        if force_lib:
            self._startup_error = "BLADEEYE_PRO_USE_LIB=1 set but libbladeRF library was not found; using simulated source."
        return SimulatedBladeRFSource()

    def add_sink(self, callback) -> None:
        self._callbacks.append(callback)

    def add_error_sink(self, callback) -> None:
        self._error_callbacks.append(callback)
        startup_error = getattr(self, "_startup_error", None)
        if startup_error:
            callback(startup_error)

    def _emit_error(self, message: str) -> None:
        for cb in self._error_callbacks:
            cb(message)

    @property
    def source_name(self) -> str:
        return self._source_name

    def update_params(
        self,
        *,
        center_freq: float | None = None,
        sample_rate: float | None = None,
        bandwidth: float | None = None,
        gain: float | None = None,
    ) -> None:
        with self._lock:
            if center_freq is not None:
                self.config.center_freq = float(center_freq)
            if sample_rate is not None:
                self.config.sample_rate = float(sample_rate)
            if bandwidth is not None:
                self.config.bandwidth = float(bandwidth)
            if gain is not None:
                self.config.gain = float(gain)
            source = self._source
            center = self.config.center_freq
            sample_rate = self.config.sample_rate
            bandwidth_hz = self.config.bandwidth
            gain_db = self.config.gain
        if source is not None:
            source.configure(
                center_freq=center,
                sample_rate=sample_rate,
                bandwidth=bandwidth_hz,
                gain=gain_db,
            )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._source = self._source or self._select_source()
        self._source_name = self._source.__class__.__name__
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
                if self._source is None:
                    raise RuntimeError("Acquisition source is not initialized")
                chunk = self._source.read(self.config.chunk_size)
            except NotImplementedError:
                self._emit_error(
                    "libbladeRF read path is not implemented for the selected backend. "
                    "Use BLADEEYE_PRO_SIM=1 or provide a complete SDRSource implementation."
                )
                time.sleep(0.1)
                continue
            except Exception as exc:
                self._emit_error(f"Acquisition read failed: {exc}")
                time.sleep(0.01)
                continue

            for cb in self._callbacks:
                cb(chunk)
            time.sleep(sleep_s * 0.15)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._source is not None:
            self._source.close()
            self._source = None
            self._source_name = "Not initialized"


class SDRWorker:
    """Asynchronous DSP worker backed by bounded deques.

    The worker keeps ingestion and processing decoupled:
    acquisition pushes chunks quickly with ``submit_chunk`` and the worker thread
    processes them in FIFO order. When queues overflow, the oldest elements are
    evicted, keeping the runtime pinned to near real-time behavior.
    """

    def __init__(
        self,
        process_chunk: Callable[[np.ndarray], Any],
        *,
        on_error: Callable[[str], None] | None = None,
        max_pending_chunks: int = 16,
        max_ready_frames: int = 3,
    ) -> None:
        if max_pending_chunks <= 0:
            raise ValueError("max_pending_chunks must be > 0")
        if max_ready_frames <= 0:
            raise ValueError("max_ready_frames must be > 0")
        self._process_chunk = process_chunk
        self._on_error = on_error
        self._pending_chunks: deque[np.ndarray] = deque(maxlen=max_pending_chunks)
        self._ready_frames: deque[Any] = deque(maxlen=max_ready_frames)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped_input_chunks = 0
        self.dropped_ready_frames = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.dropped_input_chunks = 0
        self.dropped_ready_frames = 0
        self._thread = threading.Thread(target=self._run, name="bladeeye-pro-dsp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=1.0)
        with self._lock:
            self._pending_chunks.clear()
            self._ready_frames.clear()

    def submit_chunk(self, chunk: np.ndarray) -> bool:
        dropped = False
        with self._cv:
            if len(self._pending_chunks) == self._pending_chunks.maxlen:
                self._pending_chunks.popleft()
                self.dropped_input_chunks += 1
                dropped = True
            self._pending_chunks.append(chunk)
            self._cv.notify()
        return dropped

    def pop_latest_frame(self) -> Any | None:
        with self._lock:
            if not self._ready_frames:
                return None
            latest = self._ready_frames.pop()
            if self._ready_frames:
                self.dropped_ready_frames += len(self._ready_frames)
                self._ready_frames.clear()
            return latest

    def pending_chunks(self) -> int:
        with self._lock:
            return len(self._pending_chunks)

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while not self._pending_chunks and not self._stop.is_set():
                    self._cv.wait(timeout=0.1)
                if self._stop.is_set():
                    break
                chunk = self._pending_chunks.popleft()
            try:
                frame = self._process_chunk(chunk)
            except Exception as exc:
                if self._on_error is not None:
                    self._on_error(f"DSP worker processing failed: {exc}")
                continue
            with self._lock:
                if len(self._ready_frames) == self._ready_frames.maxlen:
                    self._ready_frames.popleft()
                    self.dropped_ready_frames += 1
                self._ready_frames.append(frame)
