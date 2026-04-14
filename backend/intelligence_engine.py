"""Async intelligence helpers for modulation, baud and fingerprint inference."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import json
import math

import numpy as np


@dataclass
class IntelligenceResult:
    """Output of intelligence inference for one I/Q snapshot."""

    modulation_type: str
    rssi_db: float
    baud_rate: float | None
    likely_purpose: str | None
    protocol_name: str | None
    confidence: float
    snr_db: float
    ignored_as_noise: bool


class IntelligenceEngine:
    """Best-effort asynchronous classifier for low-latency SDR UX updates."""

    def __init__(self, signatures_path: Path, snr_guard_db: float = 6.0) -> None:
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bladeeye-intel")
        self._signatures = self._load_signatures(signatures_path)
        self._snr_guard_db = float(snr_guard_db)

    @staticmethod
    def _load_signatures(path: Path) -> list[dict]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return payload.get("signatures", []) if isinstance(payload, dict) else []
        except Exception:
            return []

    async def analyze(self, iq: np.ndarray) -> IntelligenceResult:
        """Run CPU-heavy inference in thread-pool executor."""
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._analyze_sync, iq)

    def _analyze_sync(self, iq: np.ndarray) -> IntelligenceResult:
        iq = np.asarray(iq, dtype=np.complex64)
        if iq.size == 0:
            return IntelligenceResult("UNKNOWN", -120.0, None, None, None, 0.0, 0.0, True)

        power = np.abs(iq) ** 2
        rssi_db = float(10.0 * np.log10(float(np.mean(power)) + 1e-12))
        noise_power = float(np.percentile(power, 20))
        signal_power = float(np.percentile(power, 90))
        snr_db = float(10.0 * np.log10((signal_power + 1e-12) / (noise_power + 1e-12)))
        if snr_db < self._snr_guard_db:
            return IntelligenceResult("NOISE", rssi_db, None, None, None, 0.0, snr_db, True)

        amp_var = float(np.var(np.abs(iq)))
        phase = np.unwrap(np.angle(iq))
        freq_dev = np.diff(phase)
        freq_var = float(np.var(freq_dev)) if freq_dev.size else 0.0
        cyclo_score = self._cyclostationary_score(iq)

        if freq_var > amp_var * 1.8:
            modulation = "FSK"
        elif amp_var > freq_var * 1.8:
            modulation = "ASK"
        elif freq_var > 0.02:
            modulation = "FM"
        else:
            modulation = "AM"

        baud = self._estimate_baud_rate(iq)
        likely_purpose, protocol_name, confidence = self._fingerprint(modulation, baud)
        confidence = round(min(1.0, max(confidence, cyclo_score)), 3)

        return IntelligenceResult(
            modulation_type=modulation,
            rssi_db=rssi_db,
            baud_rate=baud,
            likely_purpose=likely_purpose,
            protocol_name=protocol_name,
            confidence=confidence,
            snr_db=snr_db,
            ignored_as_noise=False,
        )

    @staticmethod
    def _cyclostationary_score(iq: np.ndarray) -> float:
        """Return a lightweight periodicity score for symbol-like structures."""
        if iq.size < 64:
            return 0.0
        amp = np.abs(iq).astype(np.float64)
        amp -= float(np.mean(amp))
        var = float(np.var(amp))
        if var <= 1e-12:
            return 0.0
        max_lag = min(32, amp.size // 4)
        corrs: list[float] = []
        for lag in range(1, max_lag + 1):
            left = amp[:-lag]
            right = amp[lag:]
            if left.size == 0 or right.size == 0:
                continue
            corrs.append(float(np.dot(left, right) / (left.size * var)))
        if not corrs:
            return 0.0
        return max(0.0, min(1.0, float(np.max(corrs))))

    @staticmethod
    def _estimate_baud_rate(iq: np.ndarray) -> float | None:
        if iq.size < 32:
            return None
        envelope = np.abs(iq)
        envelope = envelope - float(np.mean(envelope))
        crossings = np.where(np.diff(np.signbit(envelope)))[0]
        if crossings.size < 4:
            return None
        avg_samples = float(np.mean(np.diff(crossings)))
        if avg_samples <= 0 or not math.isfinite(avg_samples):
            return None
        # normalized symbol-rate estimate in samples^-1 scaled for UI readability
        return round(1_000_000.0 / max(avg_samples, 1.0), 2)

    def _fingerprint(self, modulation: str, baud_rate: float | None) -> tuple[str | None, str | None, float]:
        if baud_rate is None:
            return None, None, 0.0
        best: tuple[dict, float] | None = None
        for signature in self._signatures:
            if str(signature.get("modulation_type", "")).upper() != modulation.upper():
                continue
            target = signature.get("baud_rate")
            if target is None:
                continue
            try:
                delta = abs(float(target) - float(baud_rate))
            except Exception:
                continue
            score = max(0.0, 1.0 - (delta / max(float(target), 1.0)))
            if best is None or score > best[1]:
                best = (signature, score)
        if best is None:
            return None, None, 0.0
        sig, confidence = best
        return sig.get("likely_purpose"), sig.get("protocol"), round(float(confidence), 3)
