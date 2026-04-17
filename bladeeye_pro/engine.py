"""Runtime acquisition engine exports.

This module provides a stable import path for the desktop app while keeping
the implementation in ``hardware.py``.
"""

from .hardware import AcquisitionEngine, HardwareConfig

__all__ = ["AcquisitionEngine", "HardwareConfig"]
