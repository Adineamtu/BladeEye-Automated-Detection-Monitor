from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
from pathlib import Path
import queue
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtPrintSupport, QtWidgets

from .circular_buffer import IQCircularBuffer
from .dsp import DSPEngine
from .hardware import AcquisitionEngine, HardwareConfig
from .session import ProSession, SessionStore
from .sigint_logger import SigintLogger
from .smart_functions import DetectionEvent, HoppingController


class SpectrumWaterfallWidget(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._spectrum = np.full(2048, -120.0, dtype=np.float32)
        self._waterfall = deque(maxlen=280)
        self._zoom = 1.0
        self._pan = 0.0
        self._drag_start_x: int | None = None
        self.setMinimumHeight(360)

    def update_frame(self, spectrum_db: np.ndarray) -> None:
        self._spectrum = spectrum_db
        row = np.clip((spectrum_db + 120.0) / 80.0, 0.0, 1.0)
        self._waterfall.append(row)
        self.update()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802
        factor = 1.1 if event.angleDelta().y() > 0 else 0.9
        self._zoom = float(np.clip(self._zoom * factor, 1.0, 12.0))
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_x = int(event.position().x())

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._drag_start_x is None:
            return
        dx = int(event.position().x()) - self._drag_start_x
        self._drag_start_x = int(event.position().x())
        self._pan = float(np.clip(self._pan + dx / max(1, self.width()), -1.0, 1.0))
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_x = None

    def _visible_slice(self, arr: np.ndarray) -> np.ndarray:
        n = arr.size
        win = int(max(16, n / self._zoom))
        center = int((n * 0.5) + (self._pan * (n * 0.5 - win * 0.5)))
        start = int(np.clip(center - win // 2, 0, n - win))
        return arr[start : start + win]

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        _ = event
        p = QtGui.QPainter(self)
        rect = self.rect()
        p.fillRect(rect, QtGui.QColor('#0A1020'))

        if self._waterfall:
            w = rect.width()
            h = int(rect.height() * 0.72)
            img = QtGui.QImage(w, len(self._waterfall), QtGui.QImage.Format_RGB32)
            for y, row in enumerate(reversed(self._waterfall)):
                vis = self._visible_slice(row)
                rs = np.interp(np.linspace(0, vis.size - 1, w), np.arange(vis.size), vis)
                for x, v in enumerate(rs):
                    c = QtGui.QColor.fromHsvF(0.69 - 0.69 * float(v), 0.95, float(np.clip(v + 0.2, 0.0, 1.0)))
                    img.setPixelColor(x, y, c)
            p.drawImage(QtCore.QRect(rect.x(), rect.y(), w, h), img)

        y0 = int(rect.height() * 0.75)
        visible = self._visible_slice(self._spectrum)
        sp = np.interp(np.linspace(0, visible.size - 1, rect.width()), np.arange(visible.size), visible)
        max_db, min_db = -20.0, -120.0
        points = []
        for x, db in enumerate(sp):
            norm = (db - min_db) / (max_db - min_db)
            y = y0 + int((1.0 - norm) * (rect.height() - y0 - 8))
            points.append(QtCore.QPoint(x, y))
        p.setPen(QtGui.QPen(QtGui.QColor('#2EE0FF'), 2))
        if points:
            p.drawPolyline(points)


class DropIqWidget(QtWidgets.QFrame):
    fileDropped = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setStyleSheet('QFrame { border: 1px dashed #4FA2FF; padding: 8px; }')
        self.label = QtWidgets.QLabel('Offline IQ Analyzer: drag .iq/.complex file here or Browse')
        btn = QtWidgets.QPushButton('Browse')
        btn.clicked.connect(self._browse)
        layout = QtWidgets.QHBoxLayout(self)
        layout.addWidget(self.label, stretch=1)
        layout.addWidget(btn)

    def _browse(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Choose IQ file', str(Path.cwd()), 'IQ Files (*.iq *.complex)')
        if path:
            self.fileDropped.emit(path)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.fileDropped.emit(path)
                break


class BladeEyeProWindow(QtWidgets.QMainWindow):
    def __init__(self, config: HardwareConfig) -> None:
        super().__init__()
        self.setWindowTitle('BladeEye Option D - Unified Desktop')
        self.resize(1500, 900)

        self.buffer = IQCircularBuffer(capacity_samples=int(config.sample_rate * 30))
        self.dsp = DSPEngine(sample_rate=config.sample_rate, center_freq=config.center_freq)
        self.acquisition = AcquisitionEngine(config)
        self.hopping = HoppingController(self._on_hop)
        self.logger = SigintLogger()
        self.session_store = SessionStore()
        self.watchlist: list[float] = []
        self.detections: deque[DetectionEvent] = deque(maxlen=500)
        self._frames: queue.Queue = queue.Queue(maxsize=3)
        self._is_scanning = False
        self._last_chunk_ts = 0.0
        self._last_latency_ms = 0.0
        self._dropped_chunks = 0
        self._last_error_message = 'None'

        self._build_ui(config)
        self.acquisition.add_sink(self._on_iq_chunk)

        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.timeout.connect(self._refresh_ui)
        self._ui_timer.start(16)

    def _build_ui(self, config: HardwareConfig) -> None:
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)

        self._apply_theme()

        self.status_label = QtWidgets.QLabel('SDR Core Health: Idle')
        self.status_label.setObjectName('statusLabel')
        self.scan_status = QtWidgets.QLabel('Scan Status: Stopped')
        self.ws_status = QtWidgets.QLabel('WebSocket: N/A (Desktop mode)')
        self.dropped_label = QtWidgets.QLabel('Dropped: 0')
        self.error_label = QtWidgets.QLabel('Errors: None')
        self.latency_label = QtWidgets.QLabel('Latency: 0ms')
        header = QtWidgets.QHBoxLayout()
        for widget in (
            self.status_label,
            self.scan_status,
            self.ws_status,
            self.dropped_label,
            self.error_label,
            self.latency_label,
        ):
            header.addWidget(widget)
        layout.addLayout(header)

        self.session_combo = QtWidgets.QComboBox()
        self.session_combo.addItems(self.session_store.list_sessions())
        self.load_btn = QtWidgets.QPushButton('Load Session')
        self.load_btn.clicked.connect(self._load_session)
        self.save_btn = QtWidgets.QPushButton('Save Session')
        self.save_btn.clicked.connect(self._save_session)
        self.export_report_btn = QtWidgets.QPushButton('Download Report')
        self.export_report_btn.clicked.connect(self._export_report)
        self.export_pdf_btn = QtWidgets.QPushButton('Export as PDF')
        self.export_pdf_btn.clicked.connect(self._export_pdf)
        self.start_btn = QtWidgets.QPushButton('Start')
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QtWidgets.QPushButton('Stop')
        self.stop_btn.clicked.connect(self.stop)
        session_bar = QtWidgets.QHBoxLayout()
        for w in (
            QtWidgets.QLabel('Session'),
            self.session_combo,
            self.load_btn,
            self.save_btn,
            self.export_report_btn,
            self.export_pdf_btn,
            self.start_btn,
            self.stop_btn,
        ):
            session_bar.addWidget(w)
        session_bar.addStretch(1)
        layout.addLayout(session_bar)

        self.spectrum = SpectrumWaterfallWidget(self)
        layout.addWidget(self.spectrum, stretch=3)

        controls = QtWidgets.QHBoxLayout()
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItems(['wideband 433 MHz', 'Europe 868 MHz', '915 MHz ISM'])
        self.preset_combo.currentTextChanged.connect(self._apply_preset)

        self.freq_spin = QtWidgets.QDoubleSpinBox()
        self.freq_spin.setRange(1.0, 6000.0)
        self.freq_spin.setValue(config.center_freq / 1e6)
        self.freq_spin.setSuffix(' MHz')
        self.freq_spin.valueChanged.connect(lambda v: self._retune(v * 1e6))

        self.sample_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sample_slider.setRange(1, 40)
        self.sample_slider.setValue(int(config.sample_rate / 1e6))
        self.sample_slider.valueChanged.connect(self._change_sample_rate)

        self.gain_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 70)
        self.gain_slider.setValue(int(config.gain))
        self.gain_slider.valueChanged.connect(lambda v: self.acquisition.update_params(gain=float(v)))

        self.alert_threshold = QtWidgets.QDoubleSpinBox()
        self.alert_threshold.setRange(1.0, 1_000_000.0)
        self.alert_threshold.setDecimals(2)
        self.alert_threshold.setValue(500000.0)
        self.alert_threshold.valueChanged.connect(lambda v: self.dsp.set_trigger_gain(max(1.0, v / 125000.0)))

        self.active_freq = QtWidgets.QLabel(f'Active Frequency: {config.center_freq:.0f} Hz')
        self.hop_check = QtWidgets.QCheckBox('Enable Hopping')
        self.hop_check.toggled.connect(self._toggle_hopping)

        self.record_btn = QtWidgets.QPushButton('Record last 30s')
        self.record_btn.clicked.connect(self._record_buffer)

        for label, widget in (
            ('Preset', self.preset_combo),
            ('Center', self.freq_spin),
            ('Sample Rate (MHz)', self.sample_slider),
            ('Gain (dB)', self.gain_slider),
            ('Alert Threshold', self.alert_threshold),
            ('', self.hop_check),
            ('', self.record_btn),
        ):
            if label:
                controls.addWidget(QtWidgets.QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(self.active_freq)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.iq_drop = DropIqWidget(self)
        self.iq_drop.fileDropped.connect(self._analyze_offline_iq)
        self.iq_info = QtWidgets.QLabel('No offline file loaded.')
        layout.addWidget(self.iq_drop)
        layout.addWidget(self.iq_info)

        watch_bar = QtWidgets.QHBoxLayout()
        self.watch_input = QtWidgets.QLineEdit()
        self.watch_input.setPlaceholderText('Add frequency Hz')
        self.watch_add_btn = QtWidgets.QPushButton('Add to Watchlist')
        self.watch_add_btn.clicked.connect(self._add_watch)
        self.watch_list = QtWidgets.QListWidget()
        self.watch_remove_btn = QtWidgets.QPushButton('Remove Selected')
        self.watch_remove_btn.clicked.connect(self._remove_watch)
        watch_bar.addWidget(self.watch_input)
        watch_bar.addWidget(self.watch_add_btn)
        watch_bar.addWidget(self.watch_remove_btn)
        layout.addLayout(watch_bar)
        layout.addWidget(self.watch_list)

        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                'Center Frequency',
                'Modulation Type',
                'Baud Rate',
                'Detection / Likely Purpose',
                'Label / Protocol',
                'Signal Strength',
                'Duration (s)',
                'Time',
                'Actions',
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table, stretch=2)

        self.setCentralWidget(central)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background-color: #101727; color: #ECF3FF; }
            QPushButton { background-color: #1C2E4B; border: 1px solid #335A86; padding: 6px; }
            QPushButton:hover { background-color: #24406A; }
            QLineEdit, QComboBox, QDoubleSpinBox, QListWidget { background-color: #0A1322; border: 1px solid #335A86; }
            #statusLabel { color: #80FFE0; font-weight: 600; }
            """
        )

    def _change_sample_rate(self, mhz: int) -> None:
        self.acquisition.config.sample_rate = float(mhz) * 1e6
        self.buffer = IQCircularBuffer(capacity_samples=int(self.acquisition.config.sample_rate * 30))
        self.dsp.sample_rate = self.acquisition.config.sample_rate

    def _apply_preset(self, text: str) -> None:
        presets = {
            'wideband 433 MHz': 433_920_000.0,
            'Europe 868 MHz': 868_300_000.0,
            '915 MHz ISM': 915_000_000.0,
        }
        if text in presets:
            self.freq_spin.setValue(presets[text] / 1e6)

    def start(self) -> None:
        if self._is_scanning:
            return
        if not self._configure_from_ui():
            return
        try:
            self.acquisition.start()
        except Exception as exc:
            self._set_error(f'Start failed: {exc}')
            self.scan_status.setText('Scan Status: Error')
            return
        self._is_scanning = True
        self.scan_status.setText('Scan Status: Running')
        self.status_label.setText('SDR Core Health: Healthy')
        self.status_label.setStyleSheet('color: #00ff99; font-weight: 600;')

    def stop(self) -> None:
        self.acquisition.stop()
        self._is_scanning = False
        self.scan_status.setText('Scan Status: Stopped')
        self.status_label.setText('SDR Core Health: Idle')
        self.status_label.setStyleSheet('')

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.stop()
        self.logger.close()
        super().closeEvent(event)

    def _on_iq_chunk(self, chunk: np.ndarray) -> None:
        try:
            now = time.time()
            if self._last_chunk_ts:
                self._last_latency_ms = (now - self._last_chunk_ts) * 1000.0
            self._last_chunk_ts = now

            self.buffer.extend(chunk)
            frame = self.dsp.process(chunk)
            if self._frames.full():
                self._dropped_chunks += 1
                try:
                    self._frames.get_nowait()
                except queue.Empty:
                    pass
            self._frames.put_nowait(frame)
        except Exception as exc:
            self._set_error(f'Chunk processing failed: {exc}')

    def _refresh_ui(self) -> None:
        frame = None
        while not self._frames.empty():
            frame = self._frames.get_nowait()
        self.dropped_label.setText(f'Dropped: {self._dropped_chunks}')
        self.latency_label.setText(f'Latency: {self._last_latency_ms:.1f}ms')
        self.error_label.setText(f'Errors: {self._last_error_message}')
        self.status_label.setText(f'SDR Core Health: {"Healthy" if self._is_scanning else "Idle"}')
        if frame is None:
            return
        self.spectrum.update_frame(frame.averaged_fft_db)

        if frame.event is not None:
            self.detections.appendleft(frame.event)
            self.logger.write_detection(frame.event)
            self._render_detections()
        self.hopping.tick()

    def _render_detections(self) -> None:
        rows = list(self.detections)[:150]
        self.table.setRowCount(len(rows))
        for r, evt in enumerate(rows):
            values = [
                f'{evt.center_freq:.0f}',
                evt.modulation,
                f'{evt.baud_rate:.1f}',
                evt.purpose,
                f'{evt.label} / {evt.protocol}',
                f'{evt.signal_strength:.5f}',
                f'{evt.duration_s:.4f}',
                datetime.fromtimestamp(evt.timestamp).strftime('%H:%M:%S'),
            ]
            for c, value in enumerate(values):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(value))
            export_btn = QtWidgets.QPushButton('Export I/Q')
            export_btn.clicked.connect(lambda _, i=r: self._export_detection_iq(i))
            self.table.setCellWidget(r, 8, export_btn)

    def _export_detection_iq(self, idx: int) -> None:
        if idx >= len(self.detections):
            return
        event = list(self.detections)[idx]
        iq = self.buffer.latest(int(max(2048, event.duration_s * self.acquisition.config.sample_rate)))
        out_dir = Path('sessions')
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'detection_{int(event.timestamp)}_{int(event.center_freq)}.iq'
        iq.astype(np.complex64).tofile(path)

    def _retune(self, freq_hz: float) -> None:
        self.acquisition.update_params(center_freq=freq_hz)
        self.dsp.set_center_freq(freq_hz)
        self.active_freq.setText(f'Active Frequency: {freq_hz:.0f} Hz')

    def _record_buffer(self) -> None:
        out_dir = Path('sessions')
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'pro_capture_{int(QtCore.QDateTime.currentSecsSinceEpoch())}.iq'
        self.buffer.snapshot().astype(np.complex64).tofile(path)
        QtWidgets.QMessageBox.information(self, 'Buffer saved', f'Saved: {path}')

    def _toggle_hopping(self, enabled: bool) -> None:
        self.hopping.enabled = enabled
        if enabled:
            f0 = self.freq_spin.value() * 1e6
            self.hopping.configure([f0 - 250_000, f0, f0 + 250_000], interval_s=0.2)

    def _on_hop(self, freq: float) -> None:
        self.freq_spin.blockSignals(True)
        self.freq_spin.setValue(freq / 1e6)
        self.freq_spin.blockSignals(False)
        self._retune(freq)

    def _analyze_offline_iq(self, path: str) -> None:
        try:
            data = np.fromfile(path, dtype=np.complex64)
            if data.size == 0:
                raise ValueError('file empty')
            frame = self.dsp.process(data[: max(self.dsp.fft_size, 4096)])
            mod = frame.event.modulation if frame.event else 'NOISE'
            snr = float(np.max(frame.fft_db) - np.median(frame.fft_db))
            baud = frame.event.baud_rate if frame.event else 0.0
            self.iq_info.setText(
                f'File: {Path(path).name} | Samples: {data.size} | Modulation: {mod} | SNR: {snr:.2f} dB | Baud: {baud:.1f}'
            )
        except Exception as exc:
            self.iq_info.setText(f'Offline IQ analyze failed: {exc}')

    def _add_watch(self) -> None:
        try:
            freq = float(self.watch_input.text())
        except ValueError:
            return
        self.watchlist.append(freq)
        self.watchlist = sorted(set(self.watchlist))
        self.watch_list.clear()
        self.watch_list.addItems([f'{f:.0f}' for f in self.watchlist])

    def _remove_watch(self) -> None:
        item = self.watch_list.currentItem()
        if not item:
            return
        freq = float(item.text())
        self.watchlist = [f for f in self.watchlist if f != freq]
        self.watch_list.takeItem(self.watch_list.row(item))

    def _save_session(self) -> None:
        default_name = f'session_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'
        name, ok = QtWidgets.QInputDialog.getText(self, 'Save Session', 'Session name:', text=default_name)
        if not ok or not name:
            return
        session = ProSession.from_runtime(
            name=name,
            config=self.acquisition.config,
            watchlist=self.watchlist,
            detections=list(self.detections),
        )
        self.session_store.save(session)
        self.session_combo.clear()
        self.session_combo.addItems(self.session_store.list_sessions())

    def _load_session(self) -> None:
        name = self.session_combo.currentText().strip()
        if not name:
            return
        session = self.session_store.load(name)
        cfg = session.config
        self.freq_spin.setValue(float(cfg.get('center_freq', 868e6)) / 1e6)
        self.sample_slider.setValue(int(float(cfg.get('sample_rate', 5e6)) / 1e6))
        self.gain_slider.setValue(int(float(cfg.get('gain', 32.0))))
        self.watchlist = [float(v) for v in session.watchlist]
        self.watch_list.clear()
        self.watch_list.addItems([f'{f:.0f}' for f in self.watchlist])

    def _report_html(self) -> str:
        return (
            '<h1>BladeEye Option D Report</h1>'
            f'<p>Generated: {datetime.utcnow().isoformat()}Z</p>'
            f'<p>Detections: {len(self.detections)}</p>'
            f'<p>Watchlist: {", ".join(f"{f:.0f}" for f in self.watchlist) or "none"}</p>'
        )

    def _export_report(self) -> None:
        out_dir = Path('sessions')
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'bladeeye_report_{int(time.time())}.html'
        path.write_text(self._report_html(), encoding='utf-8')

    def _export_pdf(self) -> None:
        out_dir = Path('sessions')
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'bladeeye_report_{int(time.time())}.pdf'
        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        printer.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
        printer.setOutputFileName(str(path))
        doc = QtGui.QTextDocument()
        doc.setHtml(self._report_html())
        doc.print(printer)

    def _set_error(self, message: str) -> None:
        self._last_error_message = message
        self.error_label.setText(f'Errors: {message}')

    def _configure_from_ui(self) -> bool:
        try:
            center_freq = float(self.freq_spin.value() * 1e6)
            sample_rate = float(self.sample_slider.value() * 1e6)
            gain = float(self.gain_slider.value())
        except Exception as exc:
            self._set_error(f'Invalid UI parameters: {exc}')
            return False

        self.acquisition.config.center_freq = center_freq
        self.acquisition.config.sample_rate = sample_rate
        self.acquisition.config.bandwidth = sample_rate
        self.acquisition.config.gain = gain
        self.buffer = IQCircularBuffer(capacity_samples=int(self.acquisition.config.sample_rate * 30))
        self.dsp.sample_rate = self.acquisition.config.sample_rate
        self.dsp.set_center_freq(center_freq)

        try:
            self.acquisition.update_params(center_freq=center_freq, bandwidth=sample_rate, gain=gain)
        except Exception as exc:
            self._set_error(f'Hardware configure failed: {exc}')
            return False

        self.active_freq.setText(f'Active Frequency: {center_freq:.0f} Hz')
        return True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='BladeEye Option D unified desktop runtime')
    p.add_argument('--center-freq', type=float, default=433_920_000.0)
    p.add_argument('--sample-rate', type=float, default=1_000_000.0)
    p.add_argument('--bandwidth', type=float, default=1_000_000.0)
    p.add_argument('--gain', type=float, default=20.0)
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
    return app.exec()
