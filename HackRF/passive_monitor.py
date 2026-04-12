#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Passive wideband spectrum monitor.

This proof of concept keeps the SDR source active while looping in Python
to analyse the spectrum. It performs only passive observation and never
transmits. Configuration is provided directly via CLI arguments or the
ControlPanel/API; configuration files are no longer supported.
"""

import argparse
import json
import requests
import matplotlib
matplotlib.use('Agg')  # Use the Agg backend for non-interactive plotting
import matplotlib.pyplot as plt
import time
import numpy as np
from gnuradio import gr, blocks, fft
import pmt
from gnuradio.fft import window
import osmosdr
import os
from datetime import datetime
import sys
import traceback
import threading
import queue
from math import log10
from dataclasses import dataclass, asdict
from typing import Optional
import logging
from collections import deque
from backend.identifier import identify_signal_metadata

log = logging.getLogger(__name__)

# Valid detection modes supported by the scanner
ALLOWED_MODES = {"FSK", "ENERGY", "ASK", "PSK"}

# Try to use Cython optimized helper
try:
    from ._fsk_cython import integrate_and_dump
    _USE_CYTHON = True
except Exception:  # pragma: no cover - fallback when extension not built
    try:
        import pyximport
        pyximport.install(language_level=3, inplace=True)
        from ._fsk_cython import integrate_and_dump  # type: ignore
        _USE_CYTHON = True
    except Exception:
        integrate_and_dump = None
        _USE_CYTHON = False


@dataclass
class Config:
    """Configuration options for passive spectrum monitoring."""

    center_freq: float = 868e6
    device: str = "bladerf=0"
    threshold: float = 0.02
    rx_gain: float = 30
    if_gain: float = 20
    bb_gain: float = 20
    samp_rate: float = 10e6
    bandwidth: float = 10e6
    fft_size: int = 1024
    baud_rate: Optional[float] = None
    min_separation_hz: Optional[float] = None
    pattern: Optional[str] = None
    detection_mode: str = "ENERGY"
    plot_interval: float = 0.0
    debug_logging: bool = False
    watchlist: list[float] | None = None
    alert_threshold: float | None = None
    auto_squelch: bool = True
    analysis_interval: float = 0.05

    def __post_init__(self) -> None:
        allowed_modes = ALLOWED_MODES
        self.detection_mode = self.detection_mode.upper()
        if self.detection_mode not in allowed_modes:
            raise ValueError(
                f"Invalid detection_mode '{self.detection_mode}'. Allowed values: {sorted(allowed_modes)}"
            )
        if self.min_separation_hz is None:
            if self.baud_rate is not None:
                self.min_separation_hz = 2 * self.baud_rate
            else:
                self.min_separation_hz = 10000.0


@dataclass
class Signal:
    """Information about a detected transmission."""

    center_frequency: float
    bandwidth: float
    peak_power: float
    start_time: float
    end_time: float | None
    modulation_type: str | None
    baud_rate: float | None
    protocol_name: str | None = None
    likely_purpose: str | None = None
    label: str | None = None


def export_signals(signals: list[Signal], path: str) -> None:
    """Write *signals* to *path* in JSON format."""
    data = [asdict(sig) for sig in signals]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def import_signals(path: str) -> list[Signal]:
    """Load :class:`Signal` objects previously saved with :func:`export_signals`."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh) or []
    return [Signal(**item) for item in data]


def push_session_to_api(signals: list[Signal], api_url: str) -> None:
    """Push the current session snapshot to the FastAPI backend."""
    payload = {"signals": [asdict(sig) for sig in signals], "watchlist": [], "recordings": []}
    try:
        requests.post(api_url, json=payload, timeout=2)
    except Exception as exc:  # pragma: no cover - network failures are non-deterministic
        log.warning("Failed to push session to API %s: %s", api_url, exc)


def setup_logging(debug: bool) -> None:
    """Configure basic logging for the application."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


# ------------------------------------------------------------------
MONITOR_OFFSET_HZ = 5e6


def compute_frequency_bounds(center_freq: float) -> tuple[float, float]:
    """Return the monitoring span around *center_freq*.

    The scanner observes frequencies within ±5 MHz of the configured centre
    frequency.  This helper centralises that calculation so it can be reused
    by the flowgraph and unit tests.
    """

    return center_freq - MONITOR_OFFSET_HZ, center_freq + MONITOR_OFFSET_HZ



class ThresholdTrigger(gr.sync_block):
    """Pass-through block that notifies when a power threshold is exceeded."""

    def __init__(self, threshold, fft_size, center_freq, bandwidth):
        gr.sync_block.__init__(
            self,
            name="ThresholdTrigger",
            in_sig=[(np.float32, fft_size)],
            out_sig=[(np.float32, fft_size)],
        )
        self.threshold = threshold
        self.fft_size = fft_size
        self.center_freq = center_freq
        self.bandwidth = bandwidth
        self.message_port_register_out(pmt.intern("alert"))
        # Counter to control how often debug statements are printed
        # self._print_counter = 0

    def work(self, input_items, output_items):
        output_items[0][:] = input_items[0]
        max_val = np.max(input_items[0])
        # self._print_counter += 1
        # if self._print_counter % 1000 == 0:
        #     print(
        #         f"DEBUG: ThresholdTrigger input max: {max_val:.6f}, "
        #         f"Threshold: {self.threshold:.6f}"
        #     )

        if max_val > self.threshold:
            idx = np.argmax(input_items[0])
            bin_width = self.bandwidth / self.fft_size
            spectrum_start = self.center_freq - self.bandwidth / 2
            freq = spectrum_start + idx * bin_width + bin_width / 2
            logging.debug(
                "Threshold exceeded at %.2f Hz (max_val=%.6f, threshold=%.6f)",
                freq,
                max_val,
                self.threshold,
            )
            self.message_port_pub(pmt.intern("alert"), pmt.from_double(freq))
        return len(output_items[0])


class PassiveMonitor(gr.top_block):
    """Flowgraph holding persistent SDR source for sensing."""

    def __init__(
        self,
        center_freq,
        samp_rate=10e6,
        bandwidth=10e6,
        device="bladerf=0",
        rx_gain=30,
        if_gain=20,
        bb_gain=20,
        fft_size=1024,
        threshold=0.0,
        detection_mode="FSK",
        watchlist=None,
        rx_buffers=None,
        rx_samples_per_buffer=None,
        alert_threshold=None,
        alert_callback=None,
    ):
        super().__init__()
        self.center_freq = center_freq
        self.samp_rate = samp_rate
        self.bandwidth = bandwidth
        self.device = device
        self.rx_gain = rx_gain
        self.if_gain = if_gain
        self.bb_gain = bb_gain
        self.fft_size = fft_size
        self.threshold = threshold
        self.detection_mode = detection_mode.upper()
        # Preserve watchlist order while avoiding duplicates
        self.watchlist = (
            list(dict.fromkeys(watchlist)) if watchlist else []
        )
        # Frequency hopping management
        self.hopping_enabled = False
        self.current_watch_freq: float | None = None
        self._hop_thread: threading.Thread | None = None
        self._hop_stop: threading.Event | None = None
        self.rx_buffers = rx_buffers
        self.rx_samples_per_buffer = rx_samples_per_buffer
        self.alert_threshold = alert_threshold
        self.alert_callback = alert_callback
        self.auto_squelch = True
        self.analysis_interval = 0.05
        self.dynamic_threshold_db: float | None = None
        self._analysis_thread: threading.Thread | None = None
        self._analysis_stop = threading.Event()
        self._analysis_callback = None
        self._analysis_queue: queue.Queue[list[Signal]] = queue.Queue(maxsize=100)
        self.iq_lookback_seconds = 0.5
        self.iq_lookback_samples = max(1, int(self.samp_rate * self.iq_lookback_seconds))
        self._iq_ring: deque[np.complex64] = deque(maxlen=self.iq_lookback_samples)
        self._triggered_iq: dict[float, np.ndarray] = {}

        # Track currently active signals keyed by center frequency
        self.active_signals: dict[float, Signal] = {}
        self.signal_power_history: dict[float, list[tuple[float, float]]] = {}
        self.signal_baud_values: dict[float, list[float]] = {}
        # Store instantaneous frequency offsets for each tracked signal
        # as ``(time, deviation)`` tuples.
        self.signal_frequency_deviation: dict[
            float, list[tuple[float, float]]
        ] = {}

        # Session recordings and arming state
        self.recordings: list[dict[str, float | str]] = []
        self._armed_recording: dict | None = None

        # SDR source for wideband sensing
        # Use RX1 (channel 0) for the sensing receiver
        src_args = f"{device},channel=0"
        if rx_buffers is not None:
            src_args += f",buffers={rx_buffers}"
        if rx_samples_per_buffer is not None:
            src_args += f",buflen={rx_samples_per_buffer}"
        self.sdr_source = osmosdr.source(args=src_args)
        self.sdr_source.set_sample_rate(self.samp_rate)
        self.sdr_source.set_center_freq(self.center_freq)
        self.sdr_source.set_bandwidth(self.bandwidth)
        self.sdr_source.set_gain(self.rx_gain)
        self.sdr_source.set_if_gain(self.if_gain)
        self.sdr_source.set_bb_gain(self.bb_gain)

        # FFT chain for sensing
        self.stream_to_vector = blocks.stream_to_vector(
            gr.sizeof_gr_complex, self.fft_size
        )
        self.fft = fft.fft_vcc(
            self.fft_size, True, window.blackmanharris(self.fft_size), True
        )
        self.c2mag = blocks.complex_to_mag_squared(self.fft_size)
        self.trigger = ThresholdTrigger(
            self.threshold, self.fft_size, self.center_freq, self.bandwidth
        )
        # Use probe blocks instead of vector sinks to avoid unbounded
        # memory growth. Probes keep only the latest sample/vector.
        self.vector_probe = blocks.probe_signal_vf(self.fft_size)

        # ``sample_probe`` taps the raw IQ stream or the output of a
        # detection block depending on the mode.  Its type must match the
        # block feeding it, so create it after determining the detection
        # chain.

        # FFT sensing chain
        self.connect(
            self.sdr_source,
            self.stream_to_vector,
            self.fft,
            self.c2mag,
            self.trigger,
            self.vector_probe,
        )

        # Detection chain feeding ``sample_probe`` based on mode
        if self.detection_mode == "ASK":
            # Amplitude detection requires a float probe
            self.detect_block = blocks.complex_to_mag(1)
            self.sample_probe = blocks.probe_signal_f()
            self.connect(self.sdr_source, self.detect_block, self.sample_probe)
        elif self.detection_mode == "PSK":
            try:
                from gnuradio import digital

                self.detect_block = digital.costas_loop(0.0628, 2)
                self.sample_probe = blocks.probe_signal_c()
                self.connect(self.sdr_source, self.detect_block, self.sample_probe)
            except Exception:  # pragma: no cover - digital module may be absent
                self.detect_block = None
                self.sample_probe = blocks.probe_signal_c()
                self.connect(self.sdr_source, self.sample_probe)
        else:  # FSK or ENERGY
            self.detect_block = None
            self.sample_probe = blocks.probe_signal_c()
            self.connect(self.sdr_source, self.sample_probe)

        self.iq_probe_vector_len = min(max(int(self.samp_rate * 0.02), 16384), 262144)
        self.iq_stream_to_vector = blocks.stream_to_vector(
            gr.sizeof_gr_complex, self.iq_probe_vector_len
        )
        self.iq_vector_probe = blocks.probe_signal_vc(self.iq_probe_vector_len)
        self.connect(self.sdr_source, self.iq_stream_to_vector, self.iq_vector_probe)

        low_freq, high_freq = compute_frequency_bounds(self.center_freq)
        log.info(
            "Monitoring frequency range: %.0f - %.0f Hz", low_freq, high_freq
        )

    def set_analysis_callback(self, callback) -> None:
        """Set a callback invoked with each analyzed signal batch."""
        self._analysis_callback = callback

    def start(self, *args, **kwargs):
        """Start the GNU Radio flowgraph and background analysis loop."""
        super().start(*args, **kwargs)
        self.start_analysis()

    def stop(self, *args, **kwargs):
        """Stop background analysis and then halt the flowgraph."""
        self.stop_analysis()
        return super().stop(*args, **kwargs)

    def start_analysis(self) -> None:
        """Start the dedicated spectrum analysis thread."""
        if self._analysis_thread is not None and self._analysis_thread.is_alive():
            return
        self._analysis_stop.clear()
        self._analysis_thread = threading.Thread(
            target=self._analysis_loop, daemon=True
        )
        self._analysis_thread.start()

    def stop_analysis(self, timeout: float = 1.0) -> None:
        """Request analysis thread stop and wait briefly for shutdown."""
        self._analysis_stop.set()
        if self._analysis_thread is not None:
            self._analysis_thread.join(timeout=timeout)
        self._analysis_thread = None

    def _analysis_loop(self) -> None:
        """Continuously analyze spectra and publish detection batches."""
        while not self._analysis_stop.is_set():
            try:
                results = self.analyze_spectrum()
                if results:
                    try:
                        self._analysis_queue.put_nowait(results)
                    except queue.Full:
                        try:
                            self._analysis_queue.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._analysis_queue.put_nowait(results)
                        except queue.Full:
                            pass
                    if self._analysis_callback is not None:
                        try:
                            self._analysis_callback(results)
                        except Exception:
                            log.exception("Analysis callback failed")
            except Exception:
                log.exception("Background spectrum analysis failed")
            self._analysis_stop.wait(self.analysis_interval)

    def get_detection_batch(self) -> list[Signal]:
        """Return next analyzed signal batch, if available."""
        try:
            return self._analysis_queue.get_nowait()
        except queue.Empty:
            return []

    # ------------------------------------------------------------------
    def get_config(self):
        """Return current SDR configuration."""
        return {
            "center_freq": self.center_freq,
            "samp_rate": self.samp_rate,
            "fft_size": self.fft_size,
            "gain": self.rx_gain + self.if_gain + self.bb_gain,
            "hopping_enabled": self.hopping_enabled,
            "current_freq": self.current_watch_freq or self.center_freq,
            "alert_threshold": getattr(self, "alert_threshold", None),
        }

    def set_center_freq(self, freq):
        """Adjust the tuned center frequency."""
        self.lock()
        try:
            self.center_freq = freq
            self.sdr_source.set_center_freq(freq)
            self.trigger.center_freq = freq
        finally:
            self.unlock()
        low_freq, high_freq = compute_frequency_bounds(self.center_freq)
        log.info(
            "Monitoring frequency range: %.0f - %.0f Hz", low_freq, high_freq
        )

    # ------------------------------------------------------------------
    def start_hopping(self, dwell_time: float = 0.05) -> None:
        """Begin cycling through the watchlist frequencies."""
        if not self.watchlist or self.hopping_enabled:
            return
        self._hop_stop = threading.Event()
        self.hopping_enabled = True
        self._hop_thread = threading.Thread(
            target=self._hopping_loop, args=(dwell_time,), daemon=True
        )
        self._hop_thread.start()

    def _hopping_loop(self, dwell_time: float) -> None:
        while (
            self.watchlist
            and self._hop_stop is not None
            and not self._hop_stop.is_set()
        ):
            for freq in self.watchlist:
                if self._hop_stop is not None and self._hop_stop.is_set():
                    break
                self.current_watch_freq = freq
                self.set_center_freq(freq)
                # Wait for dwell time or until stop signal
                if self._hop_stop is not None:
                    self._hop_stop.wait(dwell_time)
        self.hopping_enabled = False
        self.current_watch_freq = None

    def stop_hopping(self) -> None:
        """Stop watchlist frequency hopping."""
        if not self.hopping_enabled:
            return
        self.hopping_enabled = False
        if self._hop_stop is not None:
            self._hop_stop.set()
        if self._hop_thread is not None:
            self._hop_thread.join()
        self._hop_thread = None
        self._hop_stop = None
        self.current_watch_freq = None

    def set_sample_rate(self, rate):
        """Adjust the sample rate/bandwidth."""
        self.lock()
        try:
            self.samp_rate = rate
            self.bandwidth = rate
            self.sdr_source.set_sample_rate(rate)
            self.sdr_source.set_bandwidth(rate)
            self.trigger.bandwidth = self.bandwidth
        finally:
            self.unlock()

    def set_fft_size(self, size):
        """Reconfigure the FFT chain for a new size."""
        if size == self.fft_size:
            return
        self.lock()
        try:
            try:
                self.disconnect(self.sdr_source, self.stream_to_vector)
                self.disconnect(self.stream_to_vector, self.fft)
                self.disconnect(self.fft, self.c2mag)
                self.disconnect(self.c2mag, self.trigger)
                self.disconnect(self.trigger, self.vector_probe)
            except Exception:
                pass
            self.fft_size = int(size)
            self.stream_to_vector = blocks.stream_to_vector(
                gr.sizeof_gr_complex, self.fft_size
            )
            self.fft = fft.fft_vcc(
                self.fft_size, True, window.blackmanharris(self.fft_size), True
            )
            self.c2mag = blocks.complex_to_mag_squared(self.fft_size)
            self.trigger = ThresholdTrigger(
                self.threshold, self.fft_size, self.center_freq, self.bandwidth
            )
            self.vector_probe = blocks.probe_signal_vf(self.fft_size)
            self.connect(
                self.sdr_source,
                self.stream_to_vector,
                self.fft,
                self.c2mag,
                self.trigger,
                self.vector_probe,
            )
        finally:
            self.unlock()

    def set_gain(self, gain):
        """Adjust receiver gain across RF/IF/BB stages."""
        total_gain = float(np.clip(gain, 0.0, 90.0))
        rx_gain = min(total_gain, 30.0)
        if_gain = min(max(total_gain - 30.0, 0.0), 30.0)
        bb_gain = min(max(total_gain - 60.0, 0.0), 30.0)
        self.lock()
        try:
            self.rx_gain = rx_gain
            self.if_gain = if_gain
            self.bb_gain = bb_gain
            self.sdr_source.set_gain(rx_gain)
            self.sdr_source.set_if_gain(if_gain)
            self.sdr_source.set_bb_gain(bb_gain)
        finally:
            self.unlock()
        log.info(
            "Applied gain command total=%.1f dB (rx=%.1f if=%.1f bb=%.1f)",
            total_gain,
            rx_gain,
            if_gain,
            bb_gain,
        )

    # ------------------------------------------------------------------
    def set_alert_threshold(self, threshold):
        """Update alert threshold for peak power notifications."""
        self.alert_threshold = threshold

    # ------------------------------------------------------------------
    def get_power_spectrum(self):
        """Return the latest FFT magnitudes."""
        data = np.array(self.vector_probe.level())
        if data.size > self.fft_size:
            data = data[-self.fft_size:]
        data = np.fft.fftshift(data)
        return data

    def get_iq_samples(self):
        """Return the latest baseband samples."""
        if hasattr(self, "iq_vector_probe"):
            vec = np.array(self.iq_vector_probe.level(), dtype=np.complex64)
            if vec.size:
                return vec
        sample = self.sample_probe.level()
        return np.array([sample], dtype=np.complex64)

    def _update_iq_lookback(self) -> np.ndarray:
        """Capture latest vector and maintain a rolling look-back buffer."""
        samples = self.get_iq_samples()
        if samples.size:
            self._iq_ring.extend(samples.tolist())
        return samples

    # Recording -------------------------------------------------------------
    def arm_recording(self, freq: float, duration_after: float = 0.2) -> None:
        """Arm capture of the next burst near ``freq``.

        The flowgraph will store raw I/Q samples starting when a signal is
        detected at the given frequency.  Recording continues until the signal
        disappears and ``duration_after`` seconds have elapsed.
        """

        self._armed_recording = {
            "freq": freq,
            "duration_after": duration_after,
            "capturing": False,
            "samples": [],
            "stop_time": None,
        }

    def cancel_recording(self, freq: float) -> None:
        """Cancel an armed recording for ``freq`` if present."""
        rec = getattr(self, "_armed_recording", None)
        if rec and rec.get("freq") == freq:
            self._armed_recording = None

    def _handle_recording(self, current_freqs: set[float], now: float) -> None:
        """Update recording state based on currently active frequencies."""
        rec = getattr(self, "_armed_recording", None)
        if not rec:
            return

        target = rec.get("freq")
        # Start recording when the target frequency appears
        if not rec["capturing"]:
            for f in current_freqs:
                if abs(f - target) <= 1000:  # within 1 kHz
                    rec["capturing"] = True
                    rec["samples"] = []
                    rec["stop_time"] = None
                    break

        if not rec["capturing"]:
            return

        # Append latest samples
        try:
            samples = self.get_iq_samples()
            rec["samples"].append(samples)
        except Exception:
            pass

        # Determine if the signal is still present
        if any(abs(f - target) <= 1000 for f in current_freqs):
            rec["stop_time"] = None
            return

        if rec["stop_time"] is None:
            rec["stop_time"] = now + rec.get("duration_after", 0.0)
            return

        if now < rec["stop_time"]:
            return

        # Finalise recording
        if not hasattr(self, "recordings"):
            self.recordings = []
        try:
            data = np.concatenate(rec["samples"]) if rec["samples"] else np.array([], dtype=np.complex64)
            filename = f"recording_{int(target)}_{int(now)}.iq"
            data.astype(np.complex64).tofile(filename)
            self.recordings.append({"freq": float(target), "path": filename})
        except Exception:
            log.exception("Failed to save recording")
        finally:
            self._armed_recording = None

    def analyze_spectrum(self, callback=None):
        """Analyse the current power spectrum and report peaks.

        Parameters
        ----------
        callback: callable | None
            Optional function invoked with a list of :class:`Signal` objects
            describing each detected peak.  This allows external consumers to
            process analysis results (e.g. for logging or UI updates).
        """

        spectrum = self.get_power_spectrum()
        if spectrum.size == 0:
            return []

        # Ensure dictionary exists even for lightweight objects in tests
        if not hasattr(self, "active_signals"):
            self.active_signals = {}
        if not hasattr(self, "signal_power_history"):
            self.signal_power_history = {}
        if not hasattr(self, "signal_baud_values"):
            self.signal_baud_values = {}
        if not hasattr(self, "signal_frequency_deviation"):
            self.signal_frequency_deviation = {}
        if not hasattr(self, "_iq_ring"):
            self.iq_lookback_seconds = 0.5
            self.iq_lookback_samples = max(1, int(self.samp_rate * self.iq_lookback_seconds))
            self._iq_ring = deque(maxlen=self.iq_lookback_samples)
        if not hasattr(self, "_triggered_iq"):
            self._triggered_iq = {}

        now = time.time()
        latest_iq = self._update_iq_lookback() if hasattr(self, "_update_iq_lookback") else self.get_iq_samples()
        detection_threshold = self.threshold
        if getattr(self, "auto_squelch", False):
            noise_floor = float(np.mean(np.maximum(spectrum, 1e-12)))
            noise_floor_db = float(10.0 * np.log10(noise_floor))
            self.dynamic_threshold_db = noise_floor_db + 10.0
            detection_threshold = float(10 ** (self.dynamic_threshold_db / 10.0))
            self.threshold = detection_threshold
            if hasattr(self, "trigger"):
                self.trigger.threshold = detection_threshold
            log.debug(
                "Auto-squelch threshold set to %.2f dB over noise floor %.2f dB",
                self.dynamic_threshold_db,
                noise_floor_db,
            )

        bin_width = self.samp_rate / self.fft_size
        freqs = self.center_freq + (
            np.arange(self.fft_size) - self.fft_size / 2
        ) * bin_width

        # Identify peaks above the configured threshold.  Prefer SciPy's
        # ``find_peaks`` when available but fall back to a simple numpy-based
        # detection so the helper works without additional dependencies.
        try:  # pragma: no cover - SciPy may not be installed in tests
            from scipy.signal import find_peaks, peak_widths

            peak_idxs, props = find_peaks(spectrum, height=detection_threshold)
            peak_powers = props.get("peak_heights", [])
            widths = peak_widths(spectrum, peak_idxs, rel_height=0.5)[0]
        except Exception:
            peak_idxs = [
                i
                for i in range(1, len(spectrum) - 1)
                if spectrum[i] > spectrum[i - 1]
                and spectrum[i] > spectrum[i + 1]
                and spectrum[i] > detection_threshold
            ]
            peak_powers = spectrum[peak_idxs]
            widths = np.ones(len(peak_idxs))

        results: list[Signal] = []
        current_freqs = set()
        for idx, power, width_bins in zip(peak_idxs, peak_powers, widths):
            freq = freqs[idx]
            bandwidth = float(width_bins) * bin_width
            current_freqs.add(freq)

            sig = self.active_signals.get(freq)
            if sig is None or sig.end_time is not None:
                # New signal or previously closed one reopened
                sig = Signal(
                    center_frequency=freq,
                    bandwidth=bandwidth,
                    peak_power=float(power),
                    start_time=now,
                    end_time=None,
                    modulation_type=None,
                    baud_rate=None,
                )
                self.active_signals[freq] = sig
                if self._iq_ring:
                    self._triggered_iq[freq] = np.asarray(self._iq_ring, dtype=np.complex64)
            else:
                sig.peak_power = float(power)
                sig.bandwidth = bandwidth
                sig.end_time = None

            log.info(
                "Detected peak at %.2f Hz (power=%.6f, bw=%.2f)",
                freq,
                power,
                bandwidth,
            )
            threshold = getattr(self, "alert_threshold", None)
            callback = getattr(self, "alert_callback", None)
            if threshold is not None and power > threshold and callback is not None:
                try:
                    callback({"frequency": float(freq), "peak_power": float(power)})
                except Exception:  # pragma: no cover - user callback
                    log.exception("Alert callback failed")
            # Analyse modulation characteristics only for watchlisted peaks
            should_analyze = True
            watch = getattr(self, "watchlist", None)
            if watch:
                should_analyze = any(abs(freq - f) <= 5000 for f in watch)
            if should_analyze:
                samples = latest_iq
                mod_type, baud = analyze_signal_modulation(samples, self.samp_rate)
                sig.modulation_type = mod_type
                sig.baud_rate = baud
                # Estimate instantaneous frequency deviation from the latest
                # samples.  Store as a time series so it can be queried via
                # the API for plotting on the front-end.
                dev_hist = self.signal_frequency_deviation.setdefault(freq, [])
                if samples.size >= 2:
                    phase = np.unwrap(np.angle(samples))
                    inst_freq = np.diff(phase) * self.samp_rate / (2 * np.pi)
                    deviation = float(np.mean(inst_freq))
                else:
                    deviation = 0.0
                dev_hist.append((now, deviation))
            else:
                sig.modulation_type = None
                sig.baud_rate = None
            metadata = identify_signal_metadata(sig)
            sig.likely_purpose = metadata.get("likely_purpose")
            sig.protocol_name = metadata.get("protocol_name")
            if not sig.label:
                sig.label = metadata.get("label")
            history = self.signal_power_history.setdefault(freq, [])
            history.append((now, float(power)))
            if sig.baud_rate is not None:
                self.signal_baud_values.setdefault(freq, []).append(sig.baud_rate)
            results.append(sig)

        # Update end_time for signals that disappeared
        for f, sig in self.active_signals.items():
            if f not in current_freqs and sig.end_time is None:
                sig.end_time = now

        # Handle any armed recordings if the helper exists on the object.
        if hasattr(self, "_handle_recording"):
            try:
                self._handle_recording(current_freqs, now)
            except Exception:
                log.exception("Recording handler failed")

        if callback is not None:
            try:
                callback(results)
            except Exception:
                log.exception("Spectrum analysis callback failed")

        return results

    def get_active_signals(self) -> list[Signal]:
        """Return a list of signals that are currently active."""
        # The dictionary may include closed-out signals with ``end_time`` set;
        # filter to only those still ongoing.
        return [sig for sig in self.active_signals.values() if sig.end_time is None]

    # ------------------------------------------------------------------
    def get_power_history(self, frequency: float) -> list[tuple[float, float]]:
        """Return recorded power readings for ``frequency``."""
        return self.signal_power_history.get(frequency, [])

    def get_frequency_deviation(self, frequency: float) -> list[tuple[float, float]]:
        """Return recorded frequency deviations for ``frequency``."""
        return self.signal_frequency_deviation.get(frequency, [])

    def get_baud_rate_histogram(self, frequency: float, bins: int = 10) -> dict:
        """Return histogram data for baud rates at ``frequency``."""
        values = self.signal_baud_values.get(frequency, [])
        if not values:
            return {"hist": [], "bins": []}
        hist, edges = np.histogram(values, bins=bins)
        return {"hist": hist.tolist(), "bins": edges.tolist()}

    def get_iq_export(self, frequency: float, count: int = 1024):
        """Return ``count`` complex samples for ``frequency``."""
        for freq, data in self._triggered_iq.items():
            if abs(freq - frequency) <= 1000:
                return data[-count:]
        if self._iq_ring:
            data = np.asarray(self._iq_ring, dtype=np.complex64)
            return data[-count:]
        return self.get_iq_samples()[:count]

    def shutdown(self, timeout: float = 5.0):
        """Stop the flowgraph and cancel timers."""
        self.stop()
        waiter = threading.Thread(target=self.wait, daemon=True)
        waiter.start()
        waiter.join(timeout)


# ----------------------------------------------------------------------
def plot_spectrum_to_file(spectrum, fft_size, samp_rate, center_freq, plot_dir):
    """Plot *spectrum* to a PNG file inside *plot_dir*."""
    # Ensure spectrum is a numpy array (Added for robustness)
    spectrum = np.array(spectrum)

    # IMPORTANT: Ensure spectrum has the correct size for plotting
    if spectrum.size > fft_size:
        # If the spectrum has accumulated multiple FFT frames, take only the last complete frame
        spectrum = spectrum[-fft_size:]
    elif spectrum.size == 0:
        # Handle cases where spectrum might be empty
        print("Warning: Empty spectrum data received for plotting, skipping.")
        return

    freqs = center_freq + (
        np.arange(fft_size) - fft_size / 2
    ) * (samp_rate / fft_size)
    plt.figure()
    plt.plot(freqs / 1e6, 10 * np.log10(np.maximum(spectrum, 1e-12)))
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Power (dB)")
    plt.title("Power Spectrum")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    filename = os.path.join(plot_dir, f"spectrum_{ts}.png")
    plt.savefig(filename)
    plt.close()


# ----------------------------------------------------------------------
def detect_fsk_signals(
    spectrum,
    threshold,
    bin_width,
    spectrum_start,
    min_separation_hz=10000.0,
    max_power_diff_db=6.0,
):
    """Return list of center frequencies where an FSK signal is detected."""

    active_bins = [i for i, pwr in enumerate(spectrum) if pwr > threshold]
    if len(active_bins) < 2:
        return []

    min_bin_sep = max(1, int(np.ceil(min_separation_hz / bin_width)))
    freqs = []
    for i, idx_i in enumerate(active_bins):
        for idx_j in active_bins[i + 1 :]:
            if idx_j - idx_i < min_bin_sep:
                continue
            pwr_i = spectrum[idx_i]
            pwr_j = spectrum[idx_j]
            power_diff = abs(10 * log10(max(pwr_i, 1e-12) / max(pwr_j, 1e-12)))
            if power_diff <= max_power_diff_db:
                center_bin = int(round((idx_i + idx_j) / 2))
                freq = spectrum_start + center_bin * bin_width + bin_width / 2
                freqs.append(freq)
                break
    return freqs


def fsk_demodulate(iq_samples, samp_rate, baud_rate):
    """Demodulate binary FSK from complex samples."""
    if iq_samples is None or len(iq_samples) < 2 or baud_rate is None:
        return np.array([], dtype=int)
    diff = np.angle(iq_samples[1:] * np.conj(iq_samples[:-1]))
    freq = diff * samp_rate / (2 * np.pi)
    sps = int(round(samp_rate / baud_rate))
    if sps <= 0:
        return np.array([], dtype=int)
    # Simple integrate and dump
    if _USE_CYTHON and integrate_and_dump is not None:
        symbols = integrate_and_dump(freq.astype(np.float64), sps)
    else:
        conv = np.convolve(freq, np.ones(sps) / float(sps), mode="valid")
        symbols = conv[::sps]
    bits = (symbols > 0).astype(int)
    return bits


def search_bit_pattern(bits, pattern):
    """Return True if *pattern* occurs in *bits*."""
    if pattern is None:
        return True
    if isinstance(pattern, str):
        pat = [int(b) for b in pattern if b in "01"]
    else:
        pat = [int(b) for b in pattern]
    if len(pat) == 0 or len(bits) < len(pat):
        return False
    pat = np.array(pat, dtype=int)
    for i in range(len(bits) - len(pat) + 1):
        if np.array_equal(bits[i : i + len(pat)], pat):
            return True
    return False


def detect_energy(iq_samples, threshold):
    """Return True if the average power of *iq_samples* exceeds *threshold*."""
    if iq_samples is None or len(iq_samples) == 0:
        return False
    power = np.abs(iq_samples) ** 2
    return float(np.mean(power)) > threshold


def detect_ask_signals(iq_samples, threshold):
    """Detect amplitude shift keyed signals by envelope magnitude."""
    if iq_samples is None or len(iq_samples) == 0:
        return False
    envelope = np.abs(iq_samples)
    return float(np.max(envelope)) > threshold


def detect_psk_signals(iq_samples, threshold):
    """Detect PSK signals based on phase transitions."""
    if iq_samples is None or len(iq_samples) < 2:
        return False
    phase_diff = np.angle(iq_samples[1:] * np.conj(iq_samples[:-1]))
    return float(np.mean(np.abs(phase_diff))) > threshold

def estimate_baud_rate(iq_samples: np.ndarray, samp_rate: float) -> float | None:
    """Estimate symbol rate using FFT of squared envelope."""
    if iq_samples is None or len(iq_samples) < 16 or samp_rate <= 0:
        return None
    samples = np.asarray(iq_samples, dtype=np.complex64)
    envelope = np.abs(samples)
    envelope_sq = envelope ** 2
    envelope_sq -= np.mean(envelope_sq)
    if not np.any(envelope_sq):
        return None
    windowed = envelope_sq * np.hanning(len(envelope_sq))
    spec = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(windowed), d=1.0 / samp_rate)
    if len(spec) < 4:
        return None
    spec[0] = 0.0
    idx = int(np.argmax(spec))
    baud = float(freqs[idx])
    if baud <= 0:
        return None
    return baud


def analyze_signal_modulation(iq_samples: np.ndarray, samp_rate: float) -> tuple[str | None, float | None]:
    """Infer modulation type and baud rate from raw I/Q samples."""
    if iq_samples is None or len(iq_samples) < 4 or samp_rate <= 0:
        return None, None

    samples = np.asarray(iq_samples, dtype=np.complex64)
    amplitude = np.abs(samples)
    phase = np.unwrap(np.angle(samples))
    inst_freq = np.diff(phase) * samp_rate / (2 * np.pi)
    baud_hint = estimate_baud_rate(samples, samp_rate)

    # Detect ASK/OOK via strong envelope separation (Hilbert-equivalent envelope for IQ)
    if amplitude.max() > 0 and (np.std(amplitude) / amplitude.max()) > 0.25:
        amp_thresh = np.median(amplitude)
        amp_states = amplitude > amp_thresh
        amp_edges = np.where(np.diff(amp_states.astype(int)) != 0)[0] + 1
        if len(amp_edges) >= 1:
            return "ASK", float(baud_hint) if baud_hint else None

    # Detect FSK from multimodal instantaneous frequency clusters
    if len(inst_freq) >= 2:
        finite_freq = inst_freq[np.isfinite(inst_freq)]
        if finite_freq.size:
            hist, edges = np.histogram(finite_freq, bins=16)
            strong_bins = np.sum(hist > max(2, 0.1 * np.max(hist)))
            if strong_bins >= 2:
                return "FSK", float(baud_hint) if baud_hint else None

    # Detect PSK from large phase jumps
    phase_diff = np.diff(phase)
    phase_edges = np.where(np.abs(phase_diff) > (np.pi / 2))[0] + 1
    if len(phase_edges) >= 2 and np.std(amplitude) < 0.2 * max(np.mean(amplitude), 1e-9):
        if baud_hint:
            return "PSK", float(baud_hint)
        baud = samp_rate / np.mean(np.diff(phase_edges))
        return "PSK", float(baud)

    return None, None


# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Passive wideband spectrum monitor"
    )
    parser.add_argument(
        "--center-freq",
        type=float,
        default=None,
        help="SDR center frequency in Hz",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="SDR device string",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Power detection threshold",
    )
    parser.add_argument("--rx-gain", type=float, default=None,
                        help="Receiver gain in dB")
    parser.add_argument("--if-gain", type=float, default=None,
                        help="IF gain in dB")
    parser.add_argument("--bb-gain", type=float, default=None,
                        help="Baseband gain in dB")
    parser.add_argument("--baud-rate", type=float, default=None,
                        help="Symbol rate used for tone spacing")
    parser.add_argument("--plot-interval", type=float, default=None,
                        help="Interval in seconds to save spectrum plots")
    parser.add_argument(
        "--api-session-url",
        type=str,
        default="http://127.0.0.1:8000/api/session",
        help="Endpoint used to push detected session snapshots",
    )
    parser.add_argument("--pattern", type=str, default=None,
                        help="Bit pattern required before detection")
    parser.add_argument(
        "--detection-mode",
        type=str,
        default=None,
        help="Override signal detection mode",
    )
    parser.add_argument(
        "--load-session",
        type=str,
        default=None,
        help="Load a previously saved session JSON and exit",
    )
    parser.add_argument(
        "--plot-session",
        action="store_true",
        help="When loading a session, generate a scatter plot instead of printing",
    )
    args = parser.parse_args()

    if args.load_session:
        signals = import_signals(args.load_session)
        if args.plot_session:
            times = [s.start_time for s in signals]
            freqs = [s.center_frequency for s in signals]
            plt.figure()
            plt.scatter(times, freqs)
            plt.xlabel("Start Time (s)")
            plt.ylabel("Frequency (Hz)")
            plt.title("Detected signals")
            out_file = os.path.splitext(args.load_session)[0] + "_plot.png"
            plt.savefig(out_file)
            plt.close()
            print(f"Saved plot to {out_file}")
        else:
            for sig in signals:
                print(sig)
        return

    cfg = Config(
        baud_rate=args.baud_rate,
        pattern=args.pattern,
        plot_interval=args.plot_interval if args.plot_interval is not None else 0.0,
        detection_mode=args.detection_mode if args.detection_mode else "ENERGY",
    )

    setup_logging(cfg.debug_logging)

    center_freq = args.center_freq if args.center_freq is not None else cfg.center_freq
    threshold = args.threshold if args.threshold is not None else cfg.threshold
    device = args.device if args.device is not None else cfg.device
    rx_gain = args.rx_gain if args.rx_gain is not None else cfg.rx_gain
    if_gain = args.if_gain if args.if_gain is not None else cfg.if_gain
    bb_gain = args.bb_gain if args.bb_gain is not None else cfg.bb_gain
    bandwidth = cfg.bandwidth
    fft_size = cfg.fft_size
    min_separation_hz = cfg.min_separation_hz
    plot_interval = cfg.plot_interval

    detection_mode = cfg.detection_mode
    allowed_modes = ALLOWED_MODES
    if detection_mode not in allowed_modes:
        print(
            f"Invalid detection mode '{detection_mode}'. Valid options are: {sorted(allowed_modes)}"
        )
        return

    plot_dir = "spectrum_plots"
    if plot_interval and plot_interval > 0:
        os.makedirs(plot_dir, exist_ok=True)

    tb = PassiveMonitor(
        center_freq,
        samp_rate=cfg.samp_rate,
        bandwidth=bandwidth,
        device=device,
        rx_gain=rx_gain,
        if_gain=if_gain,
        bb_gain=bb_gain,
        fft_size=fft_size,
        threshold=threshold,
        detection_mode=detection_mode,
    )
    tb.start()

    session_signals: list[Signal] = []

    try:
        while True:
            session_signals.extend(tb.analyze_spectrum())
            if session_signals:
                push_session_to_api(session_signals, args.api_session_url)
            time.sleep(cfg.analysis_interval)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        print(f"Exception encountered: {e}")
    finally:
        filename = (
            f"session_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        )
        try:
            export_signals(session_signals, filename)
            print(f"Saved session to {filename}")
        except Exception:
            log.exception("Failed to save session")
        tb.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
