import logging
import threading
import time

from gnuradio import gr
import pmt

log = logging.getLogger(__name__)

try:
    from . import _alert_handler  # type: ignore
    AlertHandler = _alert_handler.alert_handler  # pragma: no cover - prefer compiled version
except Exception:  # pragma: no cover - fallback Python implementation
    class AlertHandler(gr.basic_block):
        """Simple jamming controller triggered by ThresholdTrigger alerts."""

        def __init__(
            self,
            sink,
            selector,
            sweep_source,
            jam_duration,
            cooldown_period,
            tx_gain_db,
            zero_idx,
            noise_idx,
            sweep_idx,
            jamming_mode,
            jamming_bw,
            low_freq,
            high_freq,
            dwell_time,
            target_frequencies=None,
            freq_tolerance_hz=0.0,
        ):
            gr.basic_block.__init__(
                self,
                name="alert_handler",
                in_sig=[],
                out_sig=[],
            )
            self.sink = sink
            self.selector = selector
            self.sweep_source = sweep_source
            self.jam_duration = jam_duration
            self.cooldown_period = cooldown_period
            self.tx_gain_db = tx_gain_db
            self.zero_idx = zero_idx
            self.noise_idx = noise_idx
            self.sweep_idx = sweep_idx
            self.jamming_mode = jamming_mode
            self.jamming_bw = jamming_bw
            self.low_freq = low_freq
            self.high_freq = high_freq
            self.dwell_time = dwell_time
            self.target_frequencies = target_frequencies
            self.freq_tolerance_hz = freq_tolerance_hz
            # Frequencies currently being jammed
            self.frequencies_to_jam: set[float] = set()
            self._freq_lock = threading.Lock()
            self._hopper_thread: threading.Thread | None = None
            self._stop_event = threading.Event()
            self.message_port_register_in(pmt.intern("alert"))
            self.set_msg_handler(pmt.intern("alert"), self._handle_alert)

        # --------------------------------------------------------------
        def _handle_alert(self, msg):  # pragma: no cover - invoked via message port
            freq = pmt.to_double(msg)
            log.debug("Received alert message: %s", freq)
            self.process_alert(freq)

        def process_alert(self, freq):
            """Public helper used by tests to simulate an alert."""
            log.debug("Processing alert for frequency: %s", freq)
            with self._freq_lock:
                already = freq in self.frequencies_to_jam
            if already:
                self.remove_frequency(freq)
            else:
                self._handle(freq)

        # Backwards-compatible alias
        def handle_alert(self, freq):  # pragma: no cover - compatibility wrapper
            self.process_alert(freq)

        def _handle(self, freq):
            if freq <= 0:
                log.debug("Ignoring alert with invalid frequency: %s", freq)
                return
            if not (self.low_freq <= freq <= self.high_freq):
                log.debug(
                    "Ignoring out-of-band alert at %s Hz (range %s-%s)",
                    freq,
                    self.low_freq,
                    self.high_freq,
                )
                return

            if self.target_frequencies is not None:
                if not any(
                    abs(freq - t) <= self.freq_tolerance_hz
                    for t in self.target_frequencies
                ):
                    log.debug(
                        "Ignoring alert at %s Hz (no target within %.1f Hz)",
                        freq,
                        self.freq_tolerance_hz,
                    )
                    return

            self.add_frequency(freq)

        # --------------------------------------------------------------
        def _jam_once(self, freq: float) -> None:
            """Configure sink and selector to jam a single frequency."""
            self.sink.set_center_freq(freq)
            if (
                self.jamming_mode == "SWEEP"
                and self.sweep_source is not None
                and self.jamming_bw
                and self.sweep_idx is not None
            ):
                self.sweep_source.set_frequency(self.jamming_bw / 2.0)
                idx = self.sweep_idx
            else:
                idx = self.noise_idx
            if idx is not None:
                self.sink.set_gain(self.tx_gain_db, 0)
                self.selector.set_input_index(idx)

        # --------------------------------------------------------------
        def rapid_hop_jammer_loop(self) -> None:
            """Continuously hop across all tracked frequencies."""
            log.debug("Rapid hop jammer thread started")
            while not self._stop_event.is_set():
                with self._freq_lock:
                    freqs = list(self.frequencies_to_jam)
                if not freqs:
                    break
                for freq in freqs:
                    if self._stop_event.is_set():
                        break
                    self._jam_once(freq)
                    time.sleep(self.dwell_time)
            log.debug("Rapid hop jammer thread exiting")
            self.sink.set_gain(0, 0)
            self.selector.set_input_index(self.zero_idx)

        def add_frequency(self, freq: float) -> None:
            """Add a frequency to the jam set and ensure hopper is running."""
            with self._freq_lock:
                self.frequencies_to_jam.add(freq)
                start_thread = self._hopper_thread is None
            if start_thread:
                self._stop_event.clear()
                self._hopper_thread = threading.Thread(
                    target=self.rapid_hop_jammer_loop, daemon=True
                )
                self._hopper_thread.start()

        def remove_frequency(self, freq: float) -> None:
            """Remove a frequency from the jam set, stopping thread if empty."""
            with self._freq_lock:
                self.frequencies_to_jam.discard(freq)
                should_stop = not self.frequencies_to_jam and self._hopper_thread is not None
            if should_stop:
                self._stop_event.set()
                thread = self._hopper_thread
                self._hopper_thread = None
                if thread is not None:
                    thread.join()
                self.sink.set_gain(0, 0)
                self.selector.set_input_index(self.zero_idx)

        def stop_jamming(self) -> None:
            """Stop all jamming activity and clear tracked frequencies."""
            with self._freq_lock:
                self.frequencies_to_jam.clear()
                thread = self._hopper_thread
                self._hopper_thread = None
            if thread is not None:
                self._stop_event.set()
                thread.join()
            self.sink.set_gain(0, 0)
            self.selector.set_input_index(self.zero_idx)
