"""Deprecated wrapper for backward compatibility."""
import warnings

from .passive_monitor import PassiveMonitor

warnings.warn(
    "HackRF.continuous_jammer is deprecated; use HackRF.passive_monitor instead.",
    DeprecationWarning,
    stacklevel=2,
)

ContinuousJammer = PassiveMonitor
__all__ = ["ContinuousJammer", "PassiveMonitor"]
