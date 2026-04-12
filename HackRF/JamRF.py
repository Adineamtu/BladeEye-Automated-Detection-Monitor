"""Minimal JamRF module used for tests."""


class HackRF:
    """Base SDR configuration class."""

    def __init__(self, device="hackrf=0"):
        self.device = device


class Jammer(HackRF):
    """Dummy jammer for unit tests."""

    def __init__(self, waveform, power, jam_duration, device="hackrf=0"):
        super().__init__(device)
        self.waveform = waveform
        self.power = power
        self.jam_duration = jam_duration


class Sensor(HackRF):
    """Dummy sensor for unit tests."""

    def __init__(self, device="hackrf=0"):
        super().__init__(device)

