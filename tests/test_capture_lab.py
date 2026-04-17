from pathlib import Path
import sys

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from bladeeye_pro.capture_lab import AsyncRawCaptureLogger, PowerIndexAnalyzer
from bladeeye_pro.hardware import AcquisitionEngine, HardwareConfig


class DummySource:
    def __init__(self) -> None:
        self.calls = []

    def configure(self, *, center_freq: float, sample_rate: float, bandwidth: float, gain: float) -> None:
        self.calls.append((center_freq, sample_rate, bandwidth, gain))

    def read(self, count: int) -> np.ndarray:
        return np.zeros(count, dtype=np.complex64)

    def close(self) -> None:
        return


def test_acquisition_update_params_applies_sample_rate() -> None:
    src = DummySource()
    engine = AcquisitionEngine(HardwareConfig(), source=src)
    engine.update_params(sample_rate=2_000_000.0, center_freq=915e6, gain=25.0)
    assert engine.config.sample_rate == 2_000_000.0
    assert src.calls[-1][0] == 915e6
    assert src.calls[-1][1] == 2_000_000.0
    assert src.calls[-1][3] == 25.0


def test_async_raw_logger_creates_index_and_analyzer(tmp_path: Path) -> None:
    capture = tmp_path / "capture.iq"
    logger = AsyncRawCaptureLogger(capture, sample_rate=1_000_000.0, power_threshold=0.05, pre_trigger_ms=120.0)
    logger.start()
    burst = (np.ones(8192, dtype=np.float32) + 1j * np.ones(8192, dtype=np.float32)).astype(np.complex64)
    logger.ingest(burst)
    logger.ingest(burst * 0.1)
    logger.stop()

    assert capture.exists()
    assert logger.index_path.exists()
    assert logger.bytes_written > 0

    analyzer = PowerIndexAnalyzer(capture, logger.index_path)
    windows = list(analyzer.iter_signal_windows(pre_seconds=0.0, post_seconds=0.01))
    assert windows
    assert windows[0][1].dtype == np.complex64
    first_event = windows[0][0]
    assert "peak_power" in first_event
    assert "pre_trigger_start_sample" in first_event
    assert first_event["pre_trigger_samples"] == int(0.12 * 1_000_000.0)


def test_lab_estimation_and_lowpass(tmp_path: Path) -> None:
    sample_rate = 200_000.0
    t = np.arange(4000, dtype=np.float32) / sample_rate
    # Synthetic FSK-like burst.
    tone_a = np.exp(1j * 2.0 * np.pi * 3_000.0 * t)
    tone_b = np.exp(1j * 2.0 * np.pi * 8_000.0 * t)
    pattern = (np.sin(2.0 * np.pi * 1200.0 * t) > 0).astype(np.float32)
    iq = np.where(pattern > 0, tone_a, tone_b).astype(np.complex64)

    # Build minimal analyzer context from temp files.
    capture = tmp_path / "test_lab_estimation.iq"
    index = tmp_path / "test_lab_estimation.index.json"
    iq.tofile(capture)
    index.write_text(
        '{"sample_rate": 200000.0, "events": [{"sample_index": 0, "pre_trigger_start_sample": 0}]}',
        encoding="utf-8",
    )
    analyzer = PowerIndexAnalyzer(capture, index)
    filtered = analyzer.low_pass_filter(iq, cutoff_hz=20_000.0)
    assert filtered.size == iq.size
    estimate = analyzer.estimate_bit_rate_and_modulation(filtered)
    assert estimate.baud_rate >= 0.0
    assert estimate.modulation in {"FSK", "ASK", "UNKNOWN"}
    report = analyzer.analyze_event_window({"sample_index": 0}, filtered, lowpass_cutoff_hz=15_000.0)
    assert "estimated_baud_rate" in report
    assert "window_peak_power" in report


def test_encoding_toolbox_and_bitstream_diff(tmp_path: Path) -> None:
    capture = tmp_path / "dummy.iq"
    index = tmp_path / "dummy.index.json"
    np.zeros(128, dtype=np.complex64).tofile(capture)
    index.write_text('{"sample_rate": 1000000.0, "events": []}', encoding="utf-8")
    analyzer = PowerIndexAnalyzer(capture, index)

    toolbox = analyzer.apply_encoding_toolbox("01100110")
    assert set(toolbox.keys()) == {
        "raw",
        "bit_inversion",
        "manchester",
        "differential_manchester",
        "pwm",
    }
    diffs = analyzer.bitstream_diff("101010", "100011")
    assert diffs
    assert diffs[0][0] >= 0


def test_signature_lookup_and_rolling_inspector(tmp_path: Path) -> None:
    capture = tmp_path / "dummy.iq"
    index = tmp_path / "dummy.index.json"
    sig_db = tmp_path / "signals.json"
    np.zeros(128, dtype=np.complex64).tofile(capture)
    index.write_text('{"sample_rate": 1000000.0, "events": []}', encoding="utf-8")
    sig_db.write_text(
        """
[
  {"id": "came_1", "label": "Poarta CAME", "bitstream": "101100111000", "modulation": "FSK", "frequency_hz": 433920000, "baud_rate": 2000},
  {"id": "other", "label": "Other", "bitstream": "000011110000", "modulation": "ASK", "frequency_hz": 868300000, "baud_rate": 1200}
]
""".strip(),
        encoding="utf-8",
    )
    analyzer = PowerIndexAnalyzer(capture, index)
    assert analyzer.load_signature_db(sig_db) == 2
    match = analyzer.automated_db_lookup(
        bitstream="101100111001",
        frequency_hz=433_920_500.0,
        modulation="FSK",
        baud_rate=1990.0,
    )
    assert match is not None
    assert match.label == "Poarta CAME"
    assert match.score >= 0.55

    rolling = analyzer.rolling_code_inspector(
        [
            "11001100110011110000",
            "11001100110010101100",
            "11001100110001100011",
        ]
    )
    assert rolling.dynamic_span[1] >= rolling.dynamic_span[0]
