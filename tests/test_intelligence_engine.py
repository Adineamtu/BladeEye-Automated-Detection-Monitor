import asyncio
from pathlib import Path

import numpy as np

from backend.intelligence_engine import IntelligenceEngine


def test_analyze_many_scatter_gather_with_empty_window(monkeypatch):
    monkeypatch.setenv("BLADEEYE_INTEL_EXECUTOR", "thread")
    monkeypatch.setenv("BLADEEYE_INTEL_WORKERS", "2")
    engine = IntelligenceEngine(Path("backend/signatures.json"))

    async def _run():
        windows = [
            np.asarray([], dtype=np.complex64),
            np.asarray([1 + 0j, 0.5 + 0.2j, -0.2 - 0.1j] * 64, dtype=np.complex64),
            np.asarray([0 + 0j] * 32, dtype=np.complex64),
        ]
        return await engine.analyze_many(windows, max_in_flight=2)

    try:
        results = asyncio.run(_run())
    finally:
        engine.shutdown(wait=True)

    assert len(results) == 3
    assert results[0].ignored_as_noise is True
    assert results[0].modulation_type == "UNKNOWN"
    assert all(hasattr(item, "snr_db") for item in results)
