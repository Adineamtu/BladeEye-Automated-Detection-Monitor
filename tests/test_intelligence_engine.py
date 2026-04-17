import asyncio
from pathlib import Path

import numpy as np


def _tone_iq(samples: int = 4096, tone_hz: float = 15_000.0, sample_rate: float = 1_000_000.0) -> np.ndarray:
    t = np.arange(samples, dtype=np.float32) / sample_rate
    return np.exp(1j * (2.0 * np.pi * tone_hz * t)).astype(np.complex64)


def test_intelligence_engine_scatter_gather_returns_all_items(monkeypatch):
    from backend.intelligence_engine import IntelligenceEngine

    monkeypatch.setenv("BLADEEYE_INTEL_EXECUTOR", "thread")
    monkeypatch.setenv("BLADEEYE_INTEL_WORKERS", "2")
    engine = IntelligenceEngine(Path("backend/signatures.json"))

    windows = [_tone_iq(4096, tone_hz=10_000.0 + i * 2500.0) for i in range(4)]
    results = asyncio.run(engine.analyze_many(windows, max_in_flight=2))

    assert len(results) == 4
    assert all(result.modulation_type for result in results)
    engine.shutdown()
