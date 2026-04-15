from types import SimpleNamespace

import api


def test_apply_rf_signature_match_sets_detected_status() -> None:
    sig = SimpleNamespace(short_pulse=270, long_pulse=1300, gap=2650, label=None, likely_purpose=None, detection_status=None)
    api._apply_rf_signature_match(sig)  # pylint: disable=protected-access
    assert sig.detection_status == "Detected: Nexa"
    assert sig.label == "Nexa"


def test_apply_rf_signature_match_sets_unknown_status_with_raw_params() -> None:
    sig = SimpleNamespace(short_pulse=1, long_pulse=99999, gap=123, label=None, likely_purpose=None, detection_status=None)
    api._apply_rf_signature_match(sig)  # pylint: disable=protected-access
    assert sig.detection_status == "Puls detectat: 1/99999 | Unknown Signal (short_pulse=1, long_pulse=99999, gap=123)"
