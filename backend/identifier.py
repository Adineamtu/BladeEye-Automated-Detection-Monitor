"""Identify likely signal metadata using signature rules."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_BAUD_TOLERANCE = 0.15
_rules: list[dict[str, Any]] | None = None


def _normalize_rules(data: Any) -> list[dict[str, Any]]:
    """Return a list of rule dictionaries from supported JSON schemas."""
    if isinstance(data, dict):
        signatures = data.get("signatures")
        if isinstance(signatures, list):
            return [item for item in signatures if isinstance(item, dict)]
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _load_rules() -> list[dict[str, Any]]:
    """Load signature rules from ``signatures.json``/``signal_rules.json`` once."""
    global _rules
    if _rules is None:
        candidates = [
            Path(__file__).with_name("signatures.json"),
            Path(__file__).with_name("signal_rules.json"),
        ]
        loaded: list[dict[str, Any]] = []
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    loaded.extend(_normalize_rules(json.load(fh)))
            except Exception:
                continue
        _rules = loaded
    return _rules


def _frequency_mhz(value: float | int | None) -> float | None:
    """Convert a frequency value to MHz if it appears to be in Hz."""
    if value is None:
        return None
    freq = float(value)
    if freq > 10_000:  # likely Hz
        return freq / 1_000_000
    return freq


def _baud_matches(actual: float | int | None, target: float | int | None) -> bool:
    """Check if baud rates match within configured tolerance."""
    if target is None:
        return True
    if actual is None:
        return False
    target_f = float(target)
    actual_f = float(actual)
    if target_f == 0:
        return actual_f == 0
    margin = target_f * _BAUD_TOLERANCE
    return abs(actual_f - target_f) <= margin


def identify_signal_metadata(signal: Any) -> dict[str, str | None]:
    """Return matched metadata for ``signal`` based on signature rules."""
    rules = _load_rules()
    detected_freq = _frequency_mhz(getattr(signal, "center_frequency", None))
    detected_mod = getattr(signal, "modulation_type", None)
    detected_baud = getattr(signal, "baud_rate", None)

    for rule in rules:
        freq_range = (
            rule.get("frequency_range")
            or rule.get("frequency_rate")
            or rule.get("center_frequency")
        )
        if freq_range and detected_freq is not None:
            low, high = float(freq_range[0]), float(freq_range[1])
            if low > 10_000 or high > 10_000:
                low /= 1_000_000
                high /= 1_000_000
            if not (low <= detected_freq <= high):
                continue

        rule_mod = rule.get("modulation") or rule.get("modulation_type")
        if rule_mod and detected_mod is not None:
            if str(detected_mod).upper() != str(rule_mod).upper():
                continue

        rule_baud = rule.get("baud_rate")
        if isinstance(rule_baud, list):
            if detected_baud is None:
                continue
            if not (float(rule_baud[0]) <= float(detected_baud) <= float(rule_baud[1])):
                continue
        elif not _baud_matches(detected_baud, rule_baud):
            continue

        return {
            "name": rule.get("name"),
            "likely_purpose": rule.get("likely_purpose"),
            "protocol_name": rule.get("protocol_name") or rule.get("protocol"),
            "label": rule.get("label"),
        }

    return {"name": None, "likely_purpose": None, "protocol_name": None, "label": None}


def identify_signal(signal: Any) -> str | None:
    """Backward compatible helper returning only likely purpose."""
    return identify_signal_metadata(signal).get("likely_purpose")
