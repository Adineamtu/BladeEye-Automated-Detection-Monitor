import os
import sys

sys.path.insert(0, os.getcwd())

from bladeeye_pro.reporting import (
    DEFAULT_FHSS_CORRELATION_WINDOW_MS,
    build_full_intelligence_report_html,
    group_detection_events,
    is_urban_noise_label,
)
from bladeeye_pro.smart_functions import DetectionEvent


def _evt(ts: float, freq: float, label: str, baud: float = 12000.0) -> DetectionEvent:
    return DetectionEvent(
        timestamp=ts,
        center_freq=freq,
        energy=1.0,
        signal_strength=0.5,
        duration_s=0.002,
        modulation="FSK",
        baud_rate=baud,
        purpose="Test",
        protocol="proto",
        label=label,
        raw_hex="aa55" * 10,
    )


def test_group_detection_events_identifies_fhss_sequence():
    events = [
        _evt(100.000, 433.920e6, "Mercedes FHSS"),
        _evt(100.100, 434.020e6, "Mercedes FHSS"),
        _evt(100.160, 434.120e6, "Mercedes FHSS"),
        _evt(101.000, 868.300e6, "Other"),
    ]
    groups = group_detection_events(events)
    assert groups[0].is_fhss is True
    assert len(groups[0].freqs_hz) == 3
    assert groups[1].is_fhss is False


def test_build_report_can_hide_urban_noise_labels():
    events = [
        _evt(100.0, 868.3e6, "ANT and ANT+ devices"),
        _evt(101.0, 433.92e6, "EnOcean ERP1"),
    ]
    html = build_full_intelligence_report_html(detections=events, watchlist=[], hide_urban_noise=True)
    assert "ANT and ANT+ devices" not in html
    assert "EnOcean ERP1" in html
    assert "Urban noise filter: ON" in html


def test_is_urban_noise_label():
    assert is_urban_noise_label("ANT and ANT+ devices")
    assert not is_urban_noise_label("Door opener")


def test_default_fhss_window_is_under_half_second():
    assert DEFAULT_FHSS_CORRELATION_WINDOW_MS < 500.0
    events = [
        _evt(200.000, 433.920e6, "Rapid Hopper"),
        _evt(200.190, 434.020e6, "Rapid Hopper"),
        _evt(200.260, 434.120e6, "Rapid Hopper"),
    ]
    groups_default = group_detection_events(events)
    assert len(groups_default) == 2
    groups_500ms = group_detection_events(events, correlation_window_ms=500.0)
    assert len(groups_500ms) == 1
    assert groups_500ms[0].is_fhss is True
