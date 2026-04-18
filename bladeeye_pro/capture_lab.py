from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from collections import deque
from pathlib import Path
from typing import Iterable

import numpy as np


@dataclass(slots=True)
class PowerIndexEvent:
    """Energy trigger metadata entry for raw capture."""

    byte_offset: int
    sample_index: int
    pre_trigger_start_sample: int
    pre_trigger_samples: int
    timestamp: float
    rssi: float
    peak_power: float


@dataclass(slots=True)
class LabSignalEstimate:
    """Estimated parameters for an extracted IQ burst."""

    baud_rate: float
    modulation: str
    rssi: float
    peak_power: float


@dataclass(slots=True)
class SignatureMatch:
    """Best-match result against local signature database."""

    signature_id: str
    label: str
    score: float
    metadata: dict


@dataclass(slots=True)
class RollingCodeInspection:
    """Rolling-code pattern analysis across related bitstreams."""

    is_rolling: bool
    static_span: tuple[int, int]
    dynamic_span: tuple[int, int]
    confidence: float


class AsyncRawCaptureLogger:
    """High-speed async IQ recorder with power-based indexing.

    The ingestion path only enqueues chunks, while the writer thread handles
    disk I/O and index generation to avoid blocking SDR capture.
    """

    def __init__(
        self,
        output_path: str | Path,
        *,
        sample_rate: float,
        power_threshold: float,
        index_path: str | Path | None = None,
        queue_size: int = 256,
        pre_trigger_ms: float = 100.0,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        self.output_path = Path(output_path)
        self.index_path = Path(index_path) if index_path else self.output_path.with_suffix(self.output_path.suffix + ".index.json")
        self.sample_rate = float(sample_rate)
        self.power_threshold = float(power_threshold)
        self.pre_trigger_ms = max(0.0, float(pre_trigger_ms))
        self._pre_trigger_samples = int((self.pre_trigger_ms / 1000.0) * self.sample_rate)
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=max(8, int(queue_size)))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._bytes_written = 0
        self._sample_cursor = 0
        self._dropped_chunks = 0
        self._events: list[PowerIndexEvent] = []
        self._lock = threading.Lock()
        self._pre_trigger_ring: deque[np.complex64] = deque(maxlen=max(1, self._pre_trigger_samples or 1))

    @property
    def bytes_written(self) -> int:
        with self._lock:
            return self._bytes_written

    @property
    def dropped_chunks(self) -> int:
        with self._lock:
            return self._dropped_chunks

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._writer_loop, name="bladeeye-raw-writer", daemon=True)
        self._thread.start()

    def ingest(self, chunk: np.ndarray) -> None:
        """Queue a chunk for async write; drop oldest work on sustained overload."""
        if not self.is_running:
            return
        samples = np.asarray(chunk, dtype=np.complex64)
        if samples.size and self._pre_trigger_samples > 0:
            self._pre_trigger_ring.extend(samples.tolist())
        try:
            self._queue.put_nowait(samples.copy())
        except queue.Full:
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                pass
            with self._lock:
                self._dropped_chunks += 1
            try:
                self._queue.put_nowait(samples.copy())
            except queue.Full:
                with self._lock:
                    self._dropped_chunks += 1

    def stop(self, timeout: float = 3.0) -> None:
        if not self.is_running:
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        self._persist_index()

    def _writer_loop(self) -> None:
        with self.output_path.open("wb") as fh:
            while not self._stop.is_set():
                try:
                    item = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is None:
                    break
                chunk = np.asarray(item, dtype=np.complex64)
                byte_offset = self._sample_cursor * np.dtype(np.complex64).itemsize
                chunk.tofile(fh)
                fh.flush()
                self._sample_cursor += int(chunk.size)
                with self._lock:
                    self._bytes_written += int(chunk.nbytes)
                rssi = float(np.mean(np.abs(chunk) ** 2)) if chunk.size else 0.0
                peak_power = float(np.max(np.abs(chunk) ** 2)) if chunk.size else 0.0
                if rssi >= self.power_threshold:
                    event_sample_index = self._sample_cursor - int(chunk.size)
                    pre_trigger_start = max(0, event_sample_index - self._pre_trigger_samples)
                    self._events.append(
                        PowerIndexEvent(
                            byte_offset=byte_offset,
                            sample_index=event_sample_index,
                            pre_trigger_start_sample=pre_trigger_start,
                            pre_trigger_samples=self._pre_trigger_samples,
                            timestamp=time.time(),
                            rssi=rssi,
                            peak_power=peak_power,
                        )
                    )

    def _persist_index(self) -> None:
        payload = {
            "version": 1,
            "sample_rate": self.sample_rate,
            "power_threshold": self.power_threshold,
            "pre_trigger_ms": self.pre_trigger_ms,
            "capture_file": str(self.output_path),
            "bytes_written": self.bytes_written,
            "dropped_chunks": self.dropped_chunks,
            "events": [asdict(event) for event in self._events],
        }
        self.index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class PowerIndexAnalyzer:
    """Offline jump-to-signal reader for indexed IQ recordings."""

    def __init__(self, capture_path: str | Path, index_path: str | Path) -> None:
        self.capture_path = Path(capture_path)
        self.index_path = Path(index_path)
        self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.sample_rate = float(self.index.get("sample_rate", 1.0))
        self.pre_trigger_ms = float(self.index.get("pre_trigger_ms", 100.0))
        self.signature_db: list[dict] = []

    def iter_signal_windows(self, pre_seconds: float = 0.1, post_seconds: float = 0.5):
        for event in self.index.get("events", []):
            window = self.extract_event_window(event, pre_seconds=pre_seconds, post_seconds=post_seconds)
            if window.size:
                yield event, window

    def extract_event_window(self, event: dict, *, pre_seconds: float = 0.1, post_seconds: float = 0.5) -> np.ndarray:
        """Read only the requested IQ slice for one indexed event."""
        itemsize = np.dtype(np.complex64).itemsize
        sample_index = int(event.get("sample_index", 0))
        indexed_start = int(event.get("pre_trigger_start_sample", sample_index))
        pre = int(max(0, pre_seconds) * self.sample_rate)
        post = int(max(0, post_seconds) * self.sample_rate)
        start = max(0, indexed_start - pre)
        total_samples = int(self.capture_path.stat().st_size // itemsize)
        stop = min(total_samples, sample_index + post)
        count = max(0, stop - start)
        if count <= 0:
            return np.array([], dtype=np.complex64)
        return np.fromfile(self.capture_path, dtype=np.complex64, count=count, offset=start * itemsize)

    def low_pass_filter(self, iq_window: np.ndarray, cutoff_hz: float) -> np.ndarray:
        """Low-pass filter around DC using FFT masking (offline cleanup)."""
        iq = np.asarray(iq_window, dtype=np.complex64)
        if iq.size < 8:
            return iq
        cutoff = float(np.clip(cutoff_hz, 1.0, self.sample_rate / 2.0))
        spec = np.fft.fft(iq)
        freqs = np.fft.fftfreq(iq.size, d=1.0 / self.sample_rate)
        mask = np.abs(freqs) <= cutoff
        filtered = np.fft.ifft(spec * mask)
        return filtered.astype(np.complex64, copy=False)

    def estimate_bit_rate_and_modulation(self, iq_window: np.ndarray) -> LabSignalEstimate:
        """Estimate baud/modulation via transition timing and energy heuristics."""
        iq = np.asarray(iq_window, dtype=np.complex64)
        if iq.size < 32:
            return LabSignalEstimate(baud_rate=0.0, modulation="UNKNOWN", rssi=0.0, peak_power=0.0)

        power = np.abs(iq) ** 2
        rssi = float(np.mean(power))
        peak = float(np.max(power))
        envelope = np.abs(iq)
        env_norm = (envelope - np.mean(envelope)) / (np.std(envelope) + 1e-9)
        ask_edges = np.flatnonzero(np.abs(np.diff(env_norm)) > 0.75)

        phase = np.unwrap(np.angle(iq))
        inst_freq = np.diff(phase) * (self.sample_rate / (2.0 * np.pi))
        if inst_freq.size == 0:
            return LabSignalEstimate(baud_rate=0.0, modulation="UNKNOWN", rssi=rssi, peak_power=peak)
        freq_norm = (inst_freq - np.mean(inst_freq)) / (np.std(inst_freq) + 1e-9)
        fsk_edges = np.flatnonzero(np.abs(np.diff(freq_norm)) > 0.9)

        transitions = ask_edges if ask_edges.size >= fsk_edges.size else fsk_edges
        baud_rate = 0.0
        if transitions.size > 2:
            spacing = np.diff(transitions)
            spacing = spacing[spacing > 0]
            if spacing.size:
                baud_rate = float(self.sample_rate / np.median(spacing))

        env_var = float(np.var(envelope))
        freq_var = float(np.var(inst_freq))
        if freq_var > env_var * 2.0:
            modulation = "FSK"
        elif env_var > freq_var * 1.2:
            modulation = "ASK"
        else:
            modulation = "UNKNOWN"
        return LabSignalEstimate(baud_rate=baud_rate, modulation=modulation, rssi=rssi, peak_power=peak)

    def analyze_event_window(
        self,
        event: dict,
        iq_window: np.ndarray,
        *,
        lowpass_cutoff_hz: float | None = None,
    ) -> dict:
        """Full offline post-processing: optional squelch + baud/modulation estimation."""
        iq = np.asarray(iq_window, dtype=np.complex64)
        if lowpass_cutoff_hz is not None:
            iq = self.low_pass_filter(iq, lowpass_cutoff_hz)
        estimate = self.estimate_bit_rate_and_modulation(iq)
        out = dict(event)
        out.update(
            {
                "estimated_baud_rate": estimate.baud_rate,
                "estimated_modulation": estimate.modulation,
                "window_rssi": estimate.rssi,
                "window_peak_power": estimate.peak_power,
            }
        )
        return out

    # --- Signature matching -------------------------------------------------
    def load_signature_db(self, db_path: str | Path) -> int:
        """Load local signature DB (json list/dict) for automated lookup."""
        payload = json.loads(Path(db_path).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            candidates = payload.get("signatures", [])
        else:
            candidates = payload
        self.signature_db = [entry for entry in candidates if isinstance(entry, dict)]
        return len(self.signature_db)

    @staticmethod
    def _bit_similarity(bits_a: str, bits_b: str) -> float:
        if not bits_a or not bits_b:
            return 0.0
        n = min(len(bits_a), len(bits_b))
        if n == 0:
            return 0.0
        matches = sum(1 for a, b in zip(bits_a[:n], bits_b[:n]) if a == b)
        length_penalty = n / max(len(bits_a), len(bits_b))
        return float((matches / n) * length_penalty)

    def automated_db_lookup(
        self,
        *,
        bitstream: str,
        frequency_hz: float | None,
        modulation: str | None,
        baud_rate: float | None,
        min_score: float = 0.55,
    ) -> SignatureMatch | None:
        """Compare extracted signal metadata+bitstream with local signature DB."""
        if not self.signature_db:
            return None
        best: SignatureMatch | None = None
        for entry in self.signature_db:
            sig_bits = str(entry.get("bitstream", ""))
            score = self._bit_similarity(bitstream, sig_bits) * 0.7
            sig_mod = str(entry.get("modulation", "")).upper()
            if modulation and sig_mod and modulation.upper() == sig_mod:
                score += 0.15
            sig_freq = entry.get("frequency_hz")
            if frequency_hz is not None and sig_freq is not None:
                if abs(float(frequency_hz) - float(sig_freq)) <= 25_000:
                    score += 0.08
            sig_baud = entry.get("baud_rate")
            if baud_rate and sig_baud:
                rel = abs(float(baud_rate) - float(sig_baud)) / max(float(sig_baud), 1.0)
                score += 0.07 * max(0.0, 1.0 - rel)
            score = float(np.clip(score, 0.0, 1.0))
            if score < min_score:
                continue
            match = SignatureMatch(
                signature_id=str(entry.get("id", entry.get("label", "unknown"))),
                label=str(entry.get("label", "unknown")),
                score=score,
                metadata=dict(entry),
            )
            if best is None or match.score > best.score:
                best = match
        return best

    # --- Encoding toolbox ---------------------------------------------------
    @staticmethod
    def decode_bit_inversion(bits: str) -> str:
        return "".join("1" if b == "0" else "0" for b in bits if b in {"0", "1"})

    @staticmethod
    def decode_manchester(bits: str) -> str:
        clean = "".join(b for b in bits if b in {"0", "1"})
        pairs = [clean[i : i + 2] for i in range(0, len(clean) - 1, 2)]
        out = []
        for p in pairs:
            if p == "01":
                out.append("0")
            elif p == "10":
                out.append("1")
        return "".join(out)

    @staticmethod
    def decode_differential_manchester(bits: str) -> str:
        clean = "".join(b for b in bits if b in {"0", "1"})
        pairs = [clean[i : i + 2] for i in range(0, len(clean) - 1, 2)]
        out = []
        previous = pairs[0] if pairs else "10"
        for pair in pairs:
            out.append("0" if pair[0] != previous[0] else "1")
            previous = pair
        return "".join(out)

    @staticmethod
    def decode_pwm(bits: str, short_run: int = 1, long_run: int = 2) -> str:
        clean = "".join(b for b in bits if b in {"0", "1"})
        if not clean:
            return ""
        runs: list[tuple[str, int]] = []
        current = clean[0]
        length = 1
        for bit in clean[1:]:
            if bit == current:
                length += 1
            else:
                runs.append((current, length))
                current, length = bit, 1
        runs.append((current, length))
        out = []
        for _, run_len in runs:
            if abs(run_len - short_run) <= abs(run_len - long_run):
                out.append("0")
            else:
                out.append("1")
        return "".join(out)

    def apply_encoding_toolbox(self, bitstream: str) -> dict[str, str]:
        """Run all currently supported decoders and return all variants."""
        return {
            "raw": bitstream,
            "bit_inversion": self.decode_bit_inversion(bitstream),
            "manchester": self.decode_manchester(bitstream),
            "differential_manchester": self.decode_differential_manchester(bitstream),
            "pwm": self.decode_pwm(bitstream),
        }

    # --- Rolling-code inspector --------------------------------------------
    @staticmethod
    def bitstream_diff(bits_a: str, bits_b: str) -> list[tuple[int, str, str]]:
        n = min(len(bits_a), len(bits_b))
        diffs = []
        for idx in range(n):
            if bits_a[idx] != bits_b[idx]:
                diffs.append((idx, bits_a[idx], bits_b[idx]))
        for idx in range(n, len(bits_a)):
            diffs.append((idx, bits_a[idx], "-"))
        for idx in range(n, len(bits_b)):
            diffs.append((idx, "-", bits_b[idx]))
        return diffs

    def rolling_code_inspector(self, bitstreams: Iterable[str], min_samples: int = 3) -> RollingCodeInspection:
        clean_streams = ["".join(b for b in s if b in {"0", "1"}) for s in bitstreams]
        clean_streams = [s for s in clean_streams if s]
        if len(clean_streams) < min_samples:
            return RollingCodeInspection(False, (0, 0), (0, 0), 0.0)
        min_len = min(len(s) for s in clean_streams)
        matrix = np.array([[1 if ch == "1" else 0 for ch in s[:min_len]] for s in clean_streams], dtype=np.int8)
        variance = np.var(matrix, axis=0)
        static_positions = np.flatnonzero(variance <= 0.01)
        dynamic_positions = np.flatnonzero(variance > 0.01)
        if static_positions.size == 0 or dynamic_positions.size == 0:
            return RollingCodeInspection(False, (0, 0), (0, 0), 0.0)
        static_span = (int(static_positions[0]), int(static_positions[-1]))
        dynamic_span = (int(dynamic_positions[0]), int(dynamic_positions[-1]))
        dynamic_ratio = dynamic_positions.size / max(min_len, 1)
        confidence = float(np.clip((1.0 - abs(dynamic_ratio - 0.35)) * 1.2, 0.0, 1.0))
        is_rolling = confidence >= 0.6 and dynamic_positions.size >= 8
        return RollingCodeInspection(is_rolling, static_span, dynamic_span, confidence)
