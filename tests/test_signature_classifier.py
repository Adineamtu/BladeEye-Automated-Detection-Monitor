import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())

import backend.signatures_data as signatures_data
from bladeeye_pro.smart_functions import SignatureClassifier


def test_classifier_returns_unknown_below_confidence_threshold():
    clf = SignatureClassifier(confidence_threshold=0.95)
    label, purpose, confidence = clf.classify(
        pulse_width_ms=99.0,
        pulse_gap_ms=99.0,
        modulation="FSK",
    )
    assert label == "Unknown / Raw Signal"
    assert purpose == "Necunoscut"
    assert confidence < 0.95


def test_classifier_can_store_user_label(tmp_path: Path):
    signatures_data.USER_SIGNATURES_FILE = tmp_path / "signatures_user.json"
    clf = SignatureClassifier(confidence_threshold=0.90)
    name = "pytest_user_label"
    clf.save_user_label(
        name=name,
        pulse_width_ms=1.2,
        pulse_gap_ms=2.0,
        modulation="FSK",
    )
    label, _, confidence = clf.classify(
        pulse_width_ms=1.2,
        pulse_gap_ms=2.0,
        modulation="FSK",
    )
    assert label == name
    assert confidence >= 0.90
