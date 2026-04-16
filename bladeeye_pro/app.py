from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .circular_buffer import IQCircularBuffer
from .dsp import DSPEngine
from .hardware import AcquisitionEngine, HardwareConfig
from .smart_functions import DetectionEvent, HoppingController


class SpectrumWaterfallWidget(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._spectrum = np.full(2048, -120.0, dtype=np.float32)
        self._waterfall = deque(maxlen=260)
        self.setMinimumHeight(360)

    def update_frame(self, spectrum_db: np.ndarray) -> None:
        self._spectrum = spectrum_db
        row = np.clip((spectrum_db + 120.0) / 80.0, 0.0, 1.0)
        self._waterfall.append(row)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        _ = event
        p = QtGui.QPainter(self)
        rect = self.rect()
        p.fillRect(rect, QtGui.QColor("#0B1020"))

        if self._waterfall:
            w = rect.width()
            h = int(rect.height() * 0.72)
            img = QtGui.QImage(w, len(self._waterfall), QtGui.QImage.Format_RGB32)
            for y, row in enumerate(reversed(self._waterfall)):
                rs = np.interp(np.linspace(0, row.size - 1, w), np.arange(row.size), row)
                for x, v in enumerate(rs):
                    c = QtGui.QColor.fromHsvF(0.66 - 0.66 * float(v), 1.0, float(v))
                    img.setPixelColor(x, y, c)
            p.drawImage(QtCore.QRect(rect.x(), rect.y(), w, h), img)

        y0 = int(rect.height() * 0.75)
        sp = np.interp(np.linspace(0, self._spectrum.size - 1, rect.width()), np.arange(self._spectrum.size), self._spectrum)
        max_db, min_db = -20.0, -120.0
        points = []
        for x, db in enumerate(sp):
            norm = (db - min_db) / (max_db - min_db)
            y = y0 + int((1.0 - norm) * (rect.height() - y0 - 8))
            points.append(QtCore.QPoint(x, y))
        p.setPen(QtGui.QPen(QtGui.QColor("#37D7FF"), 2))
        if points:
            p.drawPolyline(points)


class BladeEyeProWindow(QtWidgets.QMainWindow):
    def __init__(self, config: HardwareConfig) -> None:
        super().__init__()
        self.setWindowTitle("BladeEye Pro")
        self.resize(1300, 820)

        self.buffer = IQCircularBuffer(capacity_samples=int(config.sample_rate * 30))
        self.dsp = DSPEngine(sample_rate=config.sample_rate, center_freq=config.center_freq)
        self.acquisition = AcquisitionEngine(config)
        self.hopping = HoppingController(self._on_hop)
        self.detections: deque[DetectionEvent] = deque(maxlen=500)

        self._build_ui(config)
        self.acquisition.add_sink(self._on_iq_chunk)

        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.timeout.connect(self._refresh_ui)
        self._ui_timer.start(16)

    def _build_ui(self, config: HardwareConfig) -> None:
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)

        self.spectrum = SpectrumWaterfallWidget(self)
        layout.addWidget(self.spectrum, stretch=3)

        controls = QtWidgets.QHBoxLayout()
        self.freq_spin = QtWidgets.QDoubleSpinBox()
        self.freq_spin.setRange(1.0, 6000.0)
        self.freq_spin.setValue(config.center_freq / 1e6)
        self.freq_spin.setSuffix(" MHz")
        self.freq_spin.valueChanged.connect(lambda v: self._retune(v * 1e6))

        self.gain_spin = QtWidgets.QDoubleSpinBox()
        self.gain_spin.setRange(0.0, 70.0)
        self.gain_spin.setValue(config.gain)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.valueChanged.connect(lambda v: self.acquisition.update_params(gain=float(v)))

        self.record_btn = QtWidgets.QPushButton("Record last 30s")
        self.record_btn.clicked.connect(self._record_buffer)

        self.hop_btn = QtWidgets.QPushButton("Enable Hopping")
        self.hop_btn.setCheckable(True)
        self.hop_btn.clicked.connect(self._toggle_hopping)

        for label, widget in (("Center", self.freq_spin), ("Gain", self.gain_spin)):
            controls.addWidget(QtWidgets.QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(self.record_btn)
        controls.addWidget(self.hop_btn)
        controls.addStretch(1)

        layout.addLayout(controls)

        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Time", "Freq (MHz)", "Energy", "Pulse ms", "Gap ms", "Mod", "Class"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table, stretch=2)

        self.setCentralWidget(central)

    def start(self) -> None:
        self.acquisition.start()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.acquisition.stop()
        super().closeEvent(event)

    def _on_iq_chunk(self, chunk: np.ndarray) -> None:
        self.buffer.extend(chunk)
        self._latest_frame = self.dsp.process(chunk)

    def _refresh_ui(self) -> None:
        frame = getattr(self, "_latest_frame", None)
        if frame is None:
            return
        self.spectrum.update_frame(frame.averaged_fft_db)
        if frame.event is not None:
            self.detections.appendleft(frame.event)
            self._render_detections()
        self.hopping.tick()

    def _render_detections(self) -> None:
        rows = list(self.detections)[:150]
        self.table.setRowCount(len(rows))
        for r, evt in enumerate(rows):
            values = [
                f"{evt.timestamp:.3f}",
                f"{evt.center_freq / 1e6:.3f}",
                f"{evt.energy:.5f}",
                f"{evt.pulse_width_ms:.3f}",
                f"{evt.pulse_gap_ms:.3f}",
                evt.modulation,
                evt.label,
            ]
            for c, value in enumerate(values):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(value))

    def _retune(self, freq_hz: float) -> None:
        self.acquisition.update_params(center_freq=freq_hz)
        self.dsp.set_center_freq(freq_hz)

    def _record_buffer(self) -> None:
        out_dir = Path("sessions")
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"pro_capture_{int(QtCore.QDateTime.currentSecsSinceEpoch())}.npy"
        np.save(path, self.buffer.snapshot())
        QtWidgets.QMessageBox.information(self, "Buffer saved", f"Saved: {path}")

    def _toggle_hopping(self, enabled: bool) -> None:
        self.hopping.enabled = enabled
        self.hop_btn.setText("Disable Hopping" if enabled else "Enable Hopping")
        if enabled:
            f0 = self.freq_spin.value() * 1e6
            self.hopping.configure([f0 - 250_000, f0, f0 + 250_000], interval_s=0.15)

    def _on_hop(self, freq: float) -> None:
        self.freq_spin.blockSignals(True)
        self.freq_spin.setValue(freq / 1e6)
        self.freq_spin.blockSignals(False)
        self._retune(freq)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="BladeEye Pro native desktop")
    p.add_argument("--center-freq", type=float, default=868e6)
    p.add_argument("--sample-rate", type=float, default=5e6)
    p.add_argument("--bandwidth", type=float, default=5e6)
    p.add_argument("--gain", type=float, default=32.0)
    return p


def run_desktop_app(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = QtWidgets.QApplication([])
    cfg = HardwareConfig(
        center_freq=args.center_freq,
        sample_rate=args.sample_rate,
        bandwidth=args.bandwidth,
        gain=args.gain,
    )
    win = BladeEyeProWindow(cfg)
    win.show()
    win.start()
    return app.exec()
