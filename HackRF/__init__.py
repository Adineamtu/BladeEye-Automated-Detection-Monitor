"""HackRF package."""

from .passive_monitor import PassiveMonitor

# Backwards compatibility alias
ContinuousJammer = PassiveMonitor

__all__ = ["PassiveMonitor", "ContinuousJammer"]

