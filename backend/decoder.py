from __future__ import annotations

"""Utilities for turning raw I/Q recordings into bit strings.

This module currently provides very small and fairly naive decoders for OOK
(on-off keying) and 2-FSK (frequency shift keying) signals.  They are intended
for use in unit tests and simple development scenarios where having fully blown
GNU Radio style decoding chains would be overkill.

The :class:`Decoder` class wraps loading of I/Q samples from disk and exposes
helper methods to convert decoded bit strings to hexadecimal and ASCII
representations.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.signal import butter, lfilter


def apply_filter(
    iq: Iterable[complex],
    samp_rate: float,
    low: float | None,
    high: float | None,
    order: int | None = None,
) -> np.ndarray:
    """Return ``iq`` passed through a Butterworth filter.

    Parameters are given in Hz and converted to normalized frequencies for the
    underlying :func:`scipy.signal.butter` call.  If neither ``low`` nor
    ``high`` are provided the input is returned unchanged.
    """

    arr = np.asarray(list(iq), dtype=np.complex64)
    if low is None and high is None:
        return arr

    nyq = 0.5 * samp_rate
    norm_low = None if low is None else low / nyq
    norm_high = None if high is None else high / nyq
    ord_val = order or 5
    if norm_low is not None and norm_high is not None:
        b, a = butter(ord_val, [norm_low, norm_high], btype="band")
    elif norm_low is not None:
        b, a = butter(ord_val, norm_low, btype="high")
    elif norm_high is not None:
        b, a = butter(ord_val, norm_high, btype="low")
    else:  # pragma: no cover - defensive, both None handled above
        return arr
    return lfilter(b, a, arr)


@dataclass
class Decoder:
    """Decode simple digital signals from an I/Q recording.

    Parameters
    ----------
    path:
        Filesystem path to the recording containing 32-bit complex float
        samples.
    metadata:
        Dictionary holding information about the signal.  At minimum the
        ``modulation_type`` key should be present and for FSK signals a
        ``baud_rate`` value is required.
    """

    path: Path | str
    metadata: dict

    def _load_iq(self) -> np.ndarray:
        """Return the complex numpy array stored at ``self.path``."""

        data = np.fromfile(self.path, dtype=np.complex64)
        return data

    # ------------------------------------------------------------------
    # Static helpers used by the API and tests
    # ------------------------------------------------------------------
    @staticmethod
    def decode_ook(iq: Iterable[complex], samp_rate: float) -> str:
        """Return a bit string by thresholding the amplitude of ``iq``.

        The implementation is intentionally straightforward: every provided
        sample is treated as one symbol and the median amplitude acts as the
        decision threshold.  This keeps the routine deterministic and makes it
        easy to craft unit tests with synthetic data.
        """

        arr = np.abs(np.asarray(list(iq)))
        if arr.size == 0:
            return ""
        thresh = (arr.max() + arr.min()) / 2.0
        bits = ["1" if v > thresh else "0" for v in arr]
        return "".join(bits)

    @staticmethod
    def decode_fsk(iq: Iterable[complex], samp_rate: float, baud: float) -> str:
        """Return a bit string for a 2-FSK signal.

        The algorithm estimates instantaneous frequency via phase differences
        and groups ``samples_per_symbol`` samples according to ``baud``.  The
        average frequency of each group is compared against the mid point to
        decide between a ``0`` and a ``1``.
        """

        arr = np.asarray(list(iq))
        if arr.size == 0:
            return ""
        # Unwrap the phase to avoid large jumps when differentiating.
        phase = np.unwrap(np.angle(arr))
        # Derive instantaneous frequency from phase differences.
        freq = np.diff(phase)
        freq = np.concatenate([freq, freq[-1:]]) * samp_rate / (2 * np.pi)
        sps = max(int(round(samp_rate / baud)), 1)
        mid = (freq.max() + freq.min()) / 2.0
        bits: list[str] = []
        for i in range(0, len(freq), sps):
            avg = float(np.mean(freq[i : i + sps]))
            bits.append("1" if avg > mid else "0")
        return "".join(bits)

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------
    @staticmethod
    def bits_to_hex(bits: str) -> str:
        """Return hexadecimal representation of ``bits``."""

        if not bits:
            return ""
        # Pad to a multiple of four for clean nibble grouping.
        pad = (-len(bits)) % 4
        if pad:
            bits += "0" * pad
        return "".join(f"{int(bits[i:i+4], 2):x}" for i in range(0, len(bits), 4))

    @staticmethod
    def bits_to_ascii(bits: str) -> str:
        """Return ASCII text for ``bits`` (8-bit, zero padded)."""

        if not bits:
            return ""
        pad = (-len(bits)) % 8
        if pad:
            bits += "0" * pad
        chars = [chr(int(bits[i:i+8], 2)) for i in range(0, len(bits), 8)]
        return "".join(chars)

    # ------------------------------------------------------------------
    def decode(self, samp_rate: float) -> dict[str, str]:
        """Decode the recording and return various representations.

        The returned dictionary always contains the raw ``binary`` bit string
        together with ``hex`` and ``ascii`` forms for convenience.
        """

        iq = self._load_iq()
        iq = apply_filter(
            iq,
            samp_rate,
            self.metadata.get("low_cut"),
            self.metadata.get("high_cut"),
            self.metadata.get("order"),
        )
        mod = (self.metadata.get("modulation_type") or "").upper()
        if mod == "OOK":
            bits = self.decode_ook(iq, samp_rate)
        elif mod == "FSK":
            baud = self.metadata.get("baud_rate") or 1
            bits = self.decode_fsk(iq, samp_rate, float(baud))
        else:
            raise ValueError(f"Unsupported modulation type: {mod}")
        return {
            "binary": bits,
            "hex": self.bits_to_hex(bits),
            "ascii": self.bits_to_ascii(bits),
        }
