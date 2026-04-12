from __future__ import annotations

"""Utilities for learning and matching bit patterns."""

from pathlib import Path
import json
from typing import Dict, List

PATTERN_FILE = Path("sessions/patterns.json")


def learn_pattern(bitstrings: List[str]) -> Dict[str, str]:
    """Return consensus pattern for *bitstrings*.

    The returned dictionary contains ``mask`` and ``bits`` keys. The mask has a
    ``1`` at positions where all input strings agree on the bit value and ``0``
    otherwise.  The ``bits`` entry stores the expected bits for those positions
    (with don't-care bits set to ``0``).
    """

    if not bitstrings:
        return {"mask": "", "bits": ""}

    max_len = max(len(b) for b in bitstrings)
    mask_chars: List[str] = []
    bit_chars: List[str] = []
    for i in range(max_len):
        chars = [b[i] if i < len(b) else None for b in bitstrings]
        if all(c in "01" for c in chars) and len(set(chars)) == 1:
            mask_chars.append("1")
            bit_chars.append(chars[0] or "0")
        else:
            mask_chars.append("0")
            bit_chars.append("0")
    return {"mask": "".join(mask_chars), "bits": "".join(bit_chars)}


def load_patterns() -> Dict[str, Dict[str, str]]:
    """Load stored patterns from :data:`PATTERN_FILE`."""

    try:
        with open(PATTERN_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
            assert isinstance(data, dict)
            return data
    except Exception:
        return {}


def save_pattern(name: str, bitstrings: List[str]) -> Dict[str, str]:
    """Learn and persist a pattern under ``name``."""

    pattern = learn_pattern(bitstrings)
    patterns = load_patterns()
    patterns[name] = pattern
    PATTERN_FILE.parent.mkdir(exist_ok=True)
    with open(PATTERN_FILE, "w", encoding="utf-8") as fh:
        json.dump(patterns, fh, indent=2)
    return pattern


def match_pattern(bits: str, pattern: Dict[str, str]) -> bool:
    """Return ``True`` if ``bits`` conforms to ``pattern``."""

    mask = pattern.get("mask", "")
    expected = pattern.get("bits", "")
    if len(bits) < len(mask):
        return False
    for i, m in enumerate(mask):
        if m == "1" and bits[i] != expected[i]:
            return False
    return True


def find_label(bits: str) -> str | None:
    """Return the name of the first matching stored pattern for ``bits``."""

    patterns = load_patterns()
    for name, pat in patterns.items():
        try:
            if match_pattern(bits, pat):
                return name
        except Exception:
            continue
    return None
