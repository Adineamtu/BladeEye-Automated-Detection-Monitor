import types
import numpy as np
import pytest


def test_get_power_spectrum_returns_fftshifted_data():
    from backend.passive_monitor import PassiveMonitor

    class DummyProbe:
        def __init__(self, data):
            self._data = data
        def level(self):
            return self._data

    # Provide more data than fft_size to ensure truncation works
    data = list(range(16))
    obj = types.SimpleNamespace(vector_probe=DummyProbe(data), fft_size=8)

    spectrum = PassiveMonitor.get_power_spectrum(obj)
    expected = np.fft.fftshift(np.array(data[-8:]))
    assert np.array_equal(spectrum, expected)


def test_analyze_spectrum_detects_peaks():
    from backend.passive_monitor import PassiveMonitor

    spectrum = np.array([0, 0, 5, 0, 0, 7, 0, 0])
    obj = types.SimpleNamespace(
        fft_size=len(spectrum),
        samp_rate=8000.0,
        center_freq=50_000.0,
        threshold=1.0,
        get_power_spectrum=lambda: spectrum,
        get_iq_samples=lambda: np.ones(64, dtype=np.complex64),
        active_signals={},
    )

    results = PassiveMonitor.analyze_spectrum(obj)
    # Expected frequencies: indices 2 and 5 -> offsets -2kHz and +1kHz
    expected_freqs = [48_000.0, 51_000.0]
    expected_powers = [5.0, 7.0]
    assert [s.center_frequency for s in results] == expected_freqs
    assert [s.peak_power for s in results] == expected_powers


def test_signal_persistence_and_closing(monkeypatch):
    from backend.passive_monitor import PassiveMonitor
    import time

    spectra = [
        np.array([0, 5, 0]),  # first call has a peak
        np.array([0, 0, 0]),  # second call has none -> signal ends
    ]

    class DummyObj:
        def __init__(self, specs):
            self.specs = iter(specs)
            self.fft_size = len(specs[0])
            self.samp_rate = 8000.0
            self.center_freq = 1_000.0
            self.threshold = 1.0
            self.active_signals = {}

        def get_power_spectrum(self):
            return next(self.specs)

        def get_iq_samples(self):
            return np.ones(64, dtype=np.complex64)

    obj = DummyObj(spectra)
    times = iter([1000.0, 1001.0])
    monkeypatch.setattr(time, "time", lambda: next(times))

    PassiveMonitor.analyze_spectrum(obj)
    assert len(obj.active_signals) == 1
    freq = next(iter(obj.active_signals))
    sig = obj.active_signals[freq]
    assert sig.end_time is None

    PassiveMonitor.analyze_spectrum(obj)
    sig = obj.active_signals[freq]
    assert sig.end_time == 1001.0
    assert PassiveMonitor.get_active_signals(obj) == []


def test_get_active_signals_filters_closed_signals():
    from backend.passive_monitor import PassiveMonitor, Signal

    sig1 = Signal(1.0, 1.0, 1.0, 0.0, None, None, None)
    sig2 = Signal(2.0, 1.0, 1.0, 0.0, 5.0, None, None)
    obj = types.SimpleNamespace(active_signals={1.0: sig1, 2.0: sig2})

    active = PassiveMonitor.get_active_signals(obj)
    assert active == [sig1]


def test_modulation_analysis_fsk():
    from backend.passive_monitor import PassiveMonitor

    samp_rate = 10_000.0
    baud = 1_000.0
    sps = int(samp_rate / baud)
    bits = np.tile([0, 1], 6)
    freq_dev = 1_000.0
    freqs = np.repeat(np.where(bits > 0, freq_dev, -freq_dev), sps)
    phase = 2 * np.pi * np.cumsum(freqs) / samp_rate
    iq = np.exp(1j * phase)

    spectrum = np.array([0, 5, 0])
    obj = types.SimpleNamespace(
        fft_size=len(spectrum),
        samp_rate=samp_rate,
        center_freq=0.0,
        threshold=1.0,
        get_power_spectrum=lambda: spectrum,
        get_iq_samples=lambda: iq,
        active_signals={},
    )

    results = PassiveMonitor.analyze_spectrum(obj)
    assert len(results) == 1
    sig = results[0]
    assert sig.modulation_type == "FSK"
    if sig.baud_rate is not None:
        assert abs(sig.baud_rate - baud) < baud * 0.2


def test_watchlist_controls_modulation(monkeypatch):
    from backend.passive_monitor import PassiveMonitor

    spectrum = np.array([0, 0, 5, 0, 0, 7, 0, 0])
    calls = []

    def fake_modulation(samples, samp_rate):
        calls.append(1)
        return "MOCK", 123.0

    monkeypatch.setattr(
        "backend.passive_monitor.analyze_signal_modulation", fake_modulation
    )

    obj = types.SimpleNamespace(
        fft_size=len(spectrum),
        samp_rate=8000.0,
        center_freq=50_000.0,
        threshold=1.0,
        get_power_spectrum=lambda: spectrum,
        get_iq_samples=lambda: np.ones(64, dtype=np.complex64),
        active_signals={},
        watchlist={43_000.0},
    )

    results = PassiveMonitor.analyze_spectrum(obj)
    assert len(results) == 2
    # Only one call to heavy analysis for the watchlisted peak
    assert len(calls) == 1
    wl_sig = next(s for s in results if s.center_frequency == 48_000.0)
    other_sig = next(s for s in results if s.center_frequency != 48_000.0)
    assert wl_sig.modulation_type == "MOCK"
    assert wl_sig.baud_rate == 123.0
    assert other_sig.modulation_type is None
    assert other_sig.baud_rate is None
