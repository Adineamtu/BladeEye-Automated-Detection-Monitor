from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
from pathlib import Path
import threading
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtPrintSupport, QtWidgets

from .circular_buffer import IQCircularBuffer
from .capture_lab import AsyncRawCaptureLogger, PowerIndexAnalyzer
from .dsp import DSPEngine
from .engine import AcquisitionEngine, HardwareConfig, SDRWorker
from .reporting import build_full_intelligence_report_html, is_urban_noise_label
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
        self._intensity_offset = -0.12
        self._intensity_gain = 1.05
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

    def set_intensity_offset(self, offset: float) -> None:
        self._intensity_offset = float(np.clip(offset, -0.8, 0.8))
        self.update()

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
                    val = float(np.clip((v + self._intensity_offset) * self._intensity_gain, 0.0, 1.0))
                    c = QtGui.QColor.fromHsvF(0.69 - 0.69 * val, 0.95, val)
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
        self.setWindowTitle('BladeEye')
        self.resize(1500, 900)

        self.buffer = IQCircularBuffer(capacity_samples=int(config.sample_rate * 30))
        self.dsp = DSPEngine(sample_rate=config.sample_rate, center_freq=config.center_freq)
        self._dsp_lock = threading.Lock()
        self.acquisition = AcquisitionEngine(config)
        self.sdr_worker = SDRWorker(self._process_chunk, on_error=self._on_worker_error, max_pending_chunks=16, max_ready_frames=3)
        self.hopping = HoppingController(self._on_hop)
        self.logger = SigintLogger()
        self.session_store = SessionStore()
        self.watchlist: list[float] = []
        self.detections: deque[DetectionEvent] = deque(maxlen=500)
        self._detection_iq_snippets: deque[np.ndarray] = deque(maxlen=500)
        self._state_lock = threading.Lock()
        self._is_scanning = False
        self._last_chunk_ts = 0.0
        self._last_latency_ms = 0.0
        self._dropped_chunks = 0
        self._last_error_message = 'None'
        self._error_log: deque[str] = deque(maxlen=5000)
        self._raw_hex_max_chars = 64
        self._visible_detection_indices: list[int] = []
        self._table_refresh_interval_ms = 200
        self._pending_detection_table_refresh = False
        self._capture_logger: AsyncRawCaptureLogger | None = None
        self._lab_analyzer: PowerIndexAnalyzer | None = None
        self._lab_events: list[dict] = []
        self._lab_signature_db_loaded = False
        self._recording_mode_enabled = False
        self._recording_ui_counter = 0
        self._waterfall_suspended_for_lab = False
        self._record_blink_on = False

        self._build_ui(config)
        self.acquisition.add_sink(self._on_iq_chunk)
        self.acquisition.add_error_sink(self._on_acquisition_error)

        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.timeout.connect(self._refresh_ui)
        self._ui_timer.start(16)
        self._detections_timer = QtCore.QTimer(self)
        self._detections_timer.timeout.connect(self._flush_detection_table)
        self._detections_timer.start(self._table_refresh_interval_ms)
        self._record_blink_timer = QtCore.QTimer(self)
        self._record_blink_timer.timeout.connect(self._blink_record_button)
        self._record_blink_timer.start(450)

    def _build_ui(self, config: HardwareConfig) -> None:
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)

        self._apply_theme()

        self.status_label = QtWidgets.QLabel('SDR Core Health: Idle')
        self.status_label.setObjectName('statusLabel')
        self.scan_status = QtWidgets.QLabel('Scan Status: Stopped')
        self.dropped_label = QtWidgets.QLabel('Dropped: 0')
        self.error_label = QtWidgets.QLabel('Errors: None')
        self.latency_label = QtWidgets.QLabel('Latency: 0ms')
        header = QtWidgets.QHBoxLayout()
        for widget in (
            self.status_label,
            self.scan_status,
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
        self.start_btn = QtWidgets.QPushButton('START / STOP PREVIEW')
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QtWidgets.QPushButton('STOP PREVIEW')
        self.stop_btn.clicked.connect(self.stop)
        self.open_lab_btn = QtWidgets.QPushButton('Open LAB')
        self.open_lab_btn.clicked.connect(self._toggle_lab_tab)
        self.error_log_btn = QtWidgets.QPushButton('Error Log')
        self.error_log_btn.clicked.connect(self._show_error_log)
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
            self.open_lab_btn,
            self.error_log_btn,
        ):
            session_bar.addWidget(w)
        session_bar.addStretch(1)
        layout.addLayout(session_bar)

        self.tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(self.tabs, stretch=1)
        monitor_tab = QtWidgets.QWidget(self)
        monitor_layout = QtWidgets.QVBoxLayout(monitor_tab)
        lab_tab = QtWidgets.QWidget(self)
        lab_layout = QtWidgets.QVBoxLayout(lab_tab)

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
        self.alert_threshold.setValue(50_000.0)
        self.alert_threshold.valueChanged.connect(self._set_trigger_gain_from_threshold)
        with self._dsp_lock:
            self.dsp.set_trigger_gain(max(1.0, self.alert_threshold.value() / 125000.0))

        self.active_freq = QtWidgets.QLabel(f'Active Frequency: {config.center_freq:.0f} Hz')
        self.hop_check = QtWidgets.QCheckBox('Enable Hopping')
        self.hop_check.toggled.connect(self._toggle_hopping)

        self.record_btn = QtWidgets.QPushButton('ARM & RECORD')
        self.record_btn.clicked.connect(self._toggle_capture_recording)
        self.record_btn.setEnabled(False)
        self.hide_noise_check = QtWidgets.QCheckBox('Hide Urban Noise')
        self.hide_noise_check.toggled.connect(lambda _: self._render_detections())
        self.wf_intensity_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.wf_intensity_slider.setRange(-80, 80)
        self.wf_intensity_slider.setValue(-12)
        self.wf_intensity_slider.valueChanged.connect(self._set_waterfall_intensity)

        for label, widget in (
            ('Preset', self.preset_combo),
            ('Center', self.freq_spin),
            ('Sample Rate (MHz)', self.sample_slider),
            ('Gain (dB)', self.gain_slider),
            ('Alert Threshold', self.alert_threshold),
            ('', self.hop_check),
            ('', self.hide_noise_check),
            ('Intensity/Offset', self.wf_intensity_slider),
            ('', self.record_btn),
        ):
            if label:
                controls.addWidget(QtWidgets.QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(self.active_freq)
        controls.addStretch(1)
        self.spectrum = SpectrumWaterfallWidget(self)
        monitor_layout.addWidget(self.spectrum, stretch=5)
        monitor_layout.addLayout(controls)

        self.iq_drop = DropIqWidget(self)
        self.iq_drop.fileDropped.connect(self._analyze_offline_iq)
        self.iq_info = QtWidgets.QLabel('No offline file loaded.')
        self.lab_load_btn = QtWidgets.QPushButton('LOAD SESSION')
        self.lab_load_btn.clicked.connect(self._load_lab_session)
        self.encoding_compare_label = QtWidgets.QLabel('Encoding Toolbox (comparativ)')
        self.encoding_compare = QtWidgets.QPlainTextEdit(self)
        self.encoding_compare.setReadOnly(True)
        self.encoding_compare.setMaximumHeight(170)
        self.encoding_compare.setPlaceholderText('Rezultatele Manchester/PWM/Bit inversion vor apărea aici.')
        self.lab_events_list = QtWidgets.QListWidget(self)
        self.lab_events_list.itemSelectionChanged.connect(self._on_lab_event_selected)
        self.lab_events_list.setMaximumHeight(180)
        self.lab_events_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        lab_layout.addWidget(self.iq_drop)
        lab_layout.addWidget(self.lab_load_btn)
        lab_layout.addWidget(QtWidgets.QLabel('Energy Events (click-to-analyze)'))
        lab_layout.addWidget(self.lab_events_list)
        lab_layout.addWidget(self.iq_info)
        lab_layout.addWidget(self.encoding_compare_label)
        lab_layout.addWidget(self.encoding_compare)

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
        monitor_layout.addLayout(watch_bar)
        monitor_layout.addWidget(self.watch_list)

        detections_area = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._on_detection_double_clicked)
        self.table.itemSelectionChanged.connect(self._update_signal_details_panel)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_detection_context_menu)
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
        detections_area.addWidget(self.table)

        details_panel = QtWidgets.QWidget(self)
        details_layout = QtWidgets.QVBoxLayout(details_panel)
        details_layout.addWidget(QtWidgets.QLabel('Signal Details'))
        self.signal_details = QtWidgets.QPlainTextEdit(self)
        self.signal_details.setReadOnly(True)
        self.signal_details.setPlaceholderText('Selectează un rând pentru a vedea datele brute.')
        details_layout.addWidget(self.signal_details, stretch=1)
        details_panel.setMinimumWidth(350)
        detections_area.addWidget(details_panel)
        detections_area.setSizes([1100, 380])
        lab_layout.addWidget(detections_area, stretch=2)

        self.tabs.addTab(monitor_tab, 'MONITOR')
        self.tabs.addTab(lab_tab, 'THE LAB')
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.tabs.currentIndex())

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
        sample_rate = float(mhz) * 1e6
        self.acquisition.update_params(sample_rate=sample_rate, bandwidth=sample_rate)
        self.buffer = IQCircularBuffer(capacity_samples=int(self.acquisition.config.sample_rate * 30))
        with self._dsp_lock:
            self.dsp.sample_rate = self.acquisition.config.sample_rate

    def _set_waterfall_intensity(self, value: int) -> None:
        self.spectrum.set_intensity_offset(float(value) / 100.0)

    def _set_trigger_gain_from_threshold(self, threshold: float) -> None:
        with self._dsp_lock:
            self.dsp.set_trigger_gain(max(1.0, float(threshold) / 125000.0))

    def _apply_preset(self, text: str) -> None:
        presets = {
            'wideband 433 MHz': 433_920_000.0,
            'Europe 868 MHz': 868_300_000.0,
            '915 MHz ISM': 915_000_000.0,
        }
        if text in presets:
            self.freq_spin.setValue(presets[text] / 1e6)
            self.alert_threshold.setValue(50_000.0)

    def start(self) -> None:
        self._log('INFO', 'Start requested from UI.')
        if self._is_scanning:
            self._log('INFO', 'Start ignored: already scanning.')
            return
        if not self._configure_from_ui():
            return
        try:
            self.sdr_worker.start()
            self.acquisition.start()
        except Exception as exc:
            self.sdr_worker.stop()
            self._set_error(f'Start failed: {exc}')
            self.scan_status.setText('Scan Status: Error')
            return
        self._is_scanning = True
        with self._state_lock:
            self._last_chunk_ts = 0.0
            self._last_latency_ms = 0.0
            self._dropped_chunks = 0
        self.scan_status.setText('Scan Status: Running')
        self.status_label.setText('SDR Core Health: Healthy')
        self.status_label.setStyleSheet('color: #00ff99; font-weight: 600;')
        self.record_btn.setEnabled(True)
        self._log('INFO', f'Acquisition started successfully using source={self.acquisition.source_name}.')

    def stop(self) -> None:
        if self._capture_logger is not None:
            self._stop_capture_recording()
        self.acquisition.stop()
        self.sdr_worker.stop()
        self._is_scanning = False
        with self._state_lock:
            self._last_chunk_ts = 0.0
            self._last_latency_ms = 0.0
        self.scan_status.setText('Scan Status: Stopped')
        self.status_label.setText('SDR Core Health: Idle')
        self.status_label.setStyleSheet('')
        self.record_btn.setEnabled(False)
        self.record_btn.setText('ARM & RECORD')
        self.record_btn.setStyleSheet('')
        self._log('INFO', 'Acquisition stopped.')

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.stop()
        self.logger.close()
        super().closeEvent(event)

    def _on_iq_chunk(self, chunk: np.ndarray) -> None:
        try:
            with self._state_lock:
                now = time.time()
                if self._last_chunk_ts:
                    self._last_latency_ms = (now - self._last_chunk_ts) * 1000.0
                self._last_chunk_ts = now
            self.buffer.extend(chunk)
            capture_logger = self._capture_logger
            if capture_logger is not None:
                capture_logger.ingest(chunk)
                return
            if self._waterfall_suspended_for_lab:
                return
            if self.sdr_worker.submit_chunk(chunk):
                with self._state_lock:
                    self._dropped_chunks += 1
        except Exception as exc:
            self._set_error(f'Chunk processing failed: {exc}')

    def _on_acquisition_error(self, message: str) -> None:
        self._set_error(message)

    def _on_worker_error(self, message: str) -> None:
        self._set_error(message)

    def _process_chunk(self, chunk: np.ndarray):
        with self._dsp_lock:
            return self.dsp.process(chunk)

    def _refresh_ui(self) -> None:
        frame = self.sdr_worker.pop_latest_frame()
        with self._state_lock:
            dropped_chunks = self._dropped_chunks + self.sdr_worker.dropped_ready_frames
            latency_ms = self._last_latency_ms
        self.dropped_label.setText(f'Dropped: {dropped_chunks}')
        self.latency_label.setText(f'Latency: {latency_ms:.1f}ms')
        self.error_label.setText(f'Errors: {self._last_error_message}')
        self.status_label.setText(f'SDR Core Health: {"Healthy" if self._is_scanning else "Idle"}')
        if self._capture_logger is not None:
            gb = self._capture_logger.bytes_written / (1024 ** 3)
            self.record_btn.setText(f'Stop Capture ({gb:.2f} GB)')
        elif self._recording_mode_enabled:
            self.record_btn.setText('ARM & RECORD')
        if self._waterfall_suspended_for_lab:
            return
        if frame is None:
            return
        self.spectrum.update_frame(frame.averaged_fft_db)

        if frame.event is not None:
            self.detections.appendleft(frame.event)
            self._detection_iq_snippets.appendleft(frame.detection_iq if frame.detection_iq is not None else np.array([], dtype=np.complex64))
            self.logger.write_detection(frame.event)
            self._pending_detection_table_refresh = True
        self.hopping.tick()

    def _flush_detection_table(self) -> None:
        if not self._pending_detection_table_refresh:
            return
        self._render_detections()
        self._pending_detection_table_refresh = False

    def _render_detections(self) -> None:
        all_rows = list(self.detections)
        if self.hide_noise_check.isChecked():
            self._visible_detection_indices = [idx for idx, evt in enumerate(all_rows) if not is_urban_noise_label(evt.label or "")]
        else:
            self._visible_detection_indices = list(range(len(all_rows)))
        self._visible_detection_indices = self._visible_detection_indices[:150]
        rows = [all_rows[idx] for idx in self._visible_detection_indices]
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
                datetime.fromtimestamp(evt.timestamp).strftime('%H:%M:%S.%f')[:-3],
            ]
            for c, value in enumerate(values):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(value))
            export_btn = QtWidgets.QPushButton('Export I/Q')
            export_btn.clicked.connect(lambda _, i=r: self._export_detection_iq(i))
            self.table.setCellWidget(r, 8, export_btn)
        self._update_signal_details_panel()

    def _export_detection_iq(self, idx: int) -> None:
        if idx >= len(self._visible_detection_indices):
            return
        source_idx = self._visible_detection_indices[idx]
        event = list(self.detections)[source_idx]
        if source_idx < len(self._detection_iq_snippets):
            iq = list(self._detection_iq_snippets)[source_idx]
        else:
            iq = np.array([], dtype=np.complex64)
        if iq.size == 0:
            iq = self.buffer.latest(int(max(2048, event.duration_s * self.acquisition.config.sample_rate)))
        out_dir = Path('exports')
        out_dir.mkdir(exist_ok=True)
        timestamp = datetime.fromtimestamp(event.timestamp).strftime('%Y%m%d_%H%M%S_%f')
        path = out_dir / f'signal_{event.center_freq / 1e6:.3f}MHz_{timestamp}.iq'
        iq.astype(np.complex64).tofile(path)
        QtWidgets.QMessageBox.information(self, 'Export I/Q', f'Fragment IQ salvat:\n{path}')

    def _on_detection_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        self._copy_detection_frequency_to_watch_input(item.row())

    def _copy_detection_frequency_to_watch_input(self, row: int) -> None:
        if row < 0 or row >= len(self._visible_detection_indices):
            return
        event = list(self.detections)[self._visible_detection_indices[row]]
        self.watch_input.setText(f'{event.center_freq:.0f}')
        self.watch_input.setFocus()
        self._log('INFO', f'Copied {event.center_freq:.0f} Hz to watchlist input from row {row}.')

    def _send_detection_to_offline_analyzer(self, row: int) -> None:
        if row < 0 or row >= len(self._visible_detection_indices):
            return
        source_idx = self._visible_detection_indices[row]
        iq = (
            list(self._detection_iq_snippets)[source_idx]
            if source_idx < len(self._detection_iq_snippets)
            else np.array([], dtype=np.complex64)
        )
        if iq.size == 0:
            self.iq_info.setText('Nu există fragment IQ disponibil pentru această detecție.')
            return
        with self._dsp_lock:
            frame = self.dsp.process(iq[: max(self.dsp.fft_size, 4096)])
        event = list(self.detections)[source_idx]
        mod = frame.event.modulation if frame.event else event.modulation
        snr = float(np.max(frame.fft_db) - np.median(frame.fft_db))
        baud = frame.event.baud_rate if frame.event else event.baud_rate
        self.iq_info.setText(
            f'Detection @{event.center_freq:.0f} Hz | Samples: {iq.size} | Modulation: {mod} | SNR: {snr:.2f} dB | Baud: {baud:.1f}'
        )

    def _show_detection_context_menu(self, pos: QtCore.QPoint) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        menu = QtWidgets.QMenu(self.table)
        watch_action = menu.addAction('Trimite în Watchlist')
        analyzer_action = menu.addAction('Trimite în Offline Analyzer')
        export_action = menu.addAction('Exportă fragment IQ')
        identify_action = menu.addAction('Identify as...')
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == watch_action:
            self._copy_detection_frequency_to_watch_input(row)
        elif chosen == analyzer_action:
            self._send_detection_to_offline_analyzer(row)
        elif chosen == export_action:
            self._export_detection_iq(row)
        elif chosen == identify_action:
            self._identify_detection_as(row)

    def _pulse_metrics_from_detection(self, row: int) -> tuple[float, float]:
        if row < 0 or row >= len(self._visible_detection_indices):
            return 0.001, 0.001
        source_idx = self._visible_detection_indices[row]
        event = list(self.detections)[source_idx]
        pulse_width_ms = max(event.duration_s * 1000.0, 0.001)
        pulse_gap_ms = (1000.0 / event.baud_rate) if event.baud_rate > 0 else pulse_width_ms
        return pulse_width_ms, pulse_gap_ms

    def _identify_detection_as(self, row: int) -> None:
        if row < 0 or row >= len(self._visible_detection_indices):
            return
        event = list(self.detections)[self._visible_detection_indices[row]]
        current_name = event.label if event.label and "Unknown" not in event.label else ""
        name, ok = QtWidgets.QInputDialog.getText(
            self,
            'Identify Signal',
            'Label this signal as:',
            text=current_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        pulse_width_ms, pulse_gap_ms = self._pulse_metrics_from_detection(row)
        with self._dsp_lock:
            self.dsp.save_user_label(
                name=name,
                pulse_width_ms=pulse_width_ms,
                pulse_gap_ms=pulse_gap_ms,
                modulation=event.modulation,
            )
        event.label = name
        event.purpose = "User Tagged"
        event.confidence = 1.0
        self._render_detections()
        QtWidgets.QMessageBox.information(self, 'Identify Signal', f'Semnal salvat în DB locală ca: {name}')

    def _update_signal_details_panel(self) -> None:
        selected = self.table.currentRow()
        if selected < 0 or selected >= len(self._visible_detection_indices):
            self.signal_details.setPlainText('')
            return
        event = list(self.detections)[self._visible_detection_indices[selected]]
        details = [
            f'Time: {datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}',
            f'Frequency: {event.center_freq / 1e6:.6f} MHz ({event.center_freq:.0f} Hz)',
            f'Modulation: {event.modulation}',
            f'Baud: {event.baud_rate:.1f}',
            f'RSSI/Strength: {event.signal_strength:.5f}',
            f'Duration: {event.duration_s:.6f} s',
            f'Label: {event.label}',
            f'Purpose: {event.purpose}',
            f'Protocol: {event.protocol}',
            f'Confidence: {event.confidence * 100:.1f}%',
            f'Raw Hex: {event.raw_hex or "(not available)"}',
        ]
        self.signal_details.setPlainText('\n'.join(details))

    def _retune(self, freq_hz: float) -> None:
        self.acquisition.update_params(center_freq=freq_hz)
        with self._dsp_lock:
            self.dsp.set_center_freq(freq_hz)
        self.active_freq.setText(f'Active Frequency: {freq_hz:.0f} Hz')

    def _record_buffer(self) -> None:
        out_dir = Path('sessions')
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'pro_capture_{int(QtCore.QDateTime.currentSecsSinceEpoch())}.iq'
        self.buffer.snapshot().astype(np.complex64).tofile(path)
        QtWidgets.QMessageBox.information(self, 'Buffer saved', f'Saved: {path}')

    def _toggle_capture_recording(self) -> None:
        if self._capture_logger is not None:
            self._stop_capture_recording()
            return
        self._start_capture_recording()

    def _start_capture_recording(self) -> None:
        if not self._is_scanning:
            QtWidgets.QMessageBox.information(self, 'ARM & RECORD', 'Pornește întâi MONITOR (Start).')
            return
        out_dir = Path('sessions')
        out_dir.mkdir(exist_ok=True)
        ts = int(QtCore.QDateTime.currentSecsSinceEpoch())
        capture_path = out_dir / f'collector_{ts}.iq'
        threshold = max(1.0, self.alert_threshold.value() / 125000.0)
        self._capture_logger = AsyncRawCaptureLogger(
            capture_path,
            sample_rate=self.acquisition.config.sample_rate,
            power_threshold=threshold,
        )
        self._capture_logger.start()
        self._recording_mode_enabled = True
        self._recording_ui_counter = 0
        self.spectrum.setStyleSheet('border: 2px solid #D71818;')
        self.record_btn.setText('Stop Capture (0.00 GB)')
        self.iq_info.setText(
            f'THE COLLECTOR active: {capture_path.name}. Live AI throttled; only lightweight waterfall updates remain.'
        )

    def _stop_capture_recording(self) -> None:
        logger = self._capture_logger
        if logger is None:
            return
        logger.stop()
        capture_path = logger.output_path
        index_path = logger.index_path
        self._capture_logger = None
        self._recording_mode_enabled = False
        self.spectrum.setStyleSheet('')
        self.record_btn.setText('ARM & RECORD')
        self.record_btn.setStyleSheet('')
        self._set_lab_session(capture_path, index_path)
        QtWidgets.QMessageBox.information(
            self,
            'Capture complete',
            f'Raw capture: {capture_path}\\nIndex: {index_path}',
        )

    def _load_lab_session(self) -> None:
        capture_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            'Load capture session',
            str(Path.cwd() / 'sessions'),
            'IQ Files (*.iq *.complex)',
        )
        if not capture_path:
            return
        capture = Path(capture_path)
        guessed_index = capture.with_suffix(capture.suffix + '.index.json')
        if guessed_index.exists():
            index_path = guessed_index
        else:
            selected_index, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Select index file',
                str(capture.parent),
                'Index Files (*.json)',
            )
            if not selected_index:
                return
            index_path = Path(selected_index)
        self._set_lab_session(capture, index_path)

    def _set_lab_session(self, capture_path: Path, index_path: Path) -> None:
        self._lab_analyzer = PowerIndexAnalyzer(capture_path, index_path)
        self._lab_events = list(self._lab_analyzer.index.get('events', []))
        self.lab_events_list.clear()
        for idx, event in enumerate(self._lab_events, start=1):
            ts = datetime.fromtimestamp(float(event.get('timestamp', 0.0))).strftime('%H:%M:%S') if event.get('timestamp') else '--:--:--'
            rssi = float(event.get('rssi', 0.0))
            peak = float(event.get('peak_power', 0.0))
            self.lab_events_list.addItem(f'#{idx:03d}  t={ts}  RSSI={rssi:.4f}  Peak={peak:.4f}')
        self._lab_signature_db_loaded = False
        self.encoding_compare.setPlainText('')
        self.iq_info.setText(
            f"THE LAB ready: {len(self._lab_events)} indexed events loaded from {Path(index_path).name}. "
            "Select one event to run AI/decode."
        )

    def _on_lab_event_selected(self) -> None:
        analyzer = self._lab_analyzer
        row = self.lab_events_list.currentRow()
        if analyzer is None or row < 0 or row >= len(self._lab_events):
            return
        event = self._lab_events[row]
        iq = analyzer.extract_event_window(event, pre_seconds=0.02, post_seconds=0.12)
        if iq.size == 0:
            self.iq_info.setText(f'Event #{row + 1}: empty IQ slice.')
            self.encoding_compare.setPlainText('')
            return
        report = analyzer.analyze_event_window(event, iq, lowpass_cutoff_hz=min(120_000.0, analyzer.sample_rate / 2.0))
        bitstream = self._iq_to_bitstream(iq)
        self._render_encoding_toolbox(bitstream)
        if not self._lab_signature_db_loaded:
            sig_db_path = Path(__file__).resolve().parents[1] / 'backend' / 'signatures.json'
            if sig_db_path.exists():
                analyzer.load_signature_db(sig_db_path)
            self._lab_signature_db_loaded = True
        match = analyzer.automated_db_lookup(
            bitstream=bitstream,
            frequency_hz=None,
            modulation=report.get('estimated_modulation'),
            baud_rate=report.get('estimated_baud_rate'),
        )
        match_line = 'No signature match'
        if match is not None:
            match_line = f'Match: {match.label} ({match.score * 100:.1f}%)'
        self.iq_info.setText(
            f'Event #{row + 1} | Samples: {iq.size} | Modulation: {report.get("estimated_modulation", "UNKNOWN")} | '
            f'Baud: {float(report.get("estimated_baud_rate", 0.0)):.1f} | {match_line}'
        )

    def _on_tab_changed(self, index: int) -> None:
        lab_active = index == 1
        self._waterfall_suspended_for_lab = lab_active
        self.open_lab_btn.setText('Back to MONITOR' if lab_active else 'Open LAB')
        self.start_btn.setEnabled(not lab_active)
        self.stop_btn.setEnabled(not lab_active)
        self.record_btn.setEnabled((not lab_active) and self._is_scanning)
        if lab_active:
            self.scan_status.setText('Scan Status: LAB mode (waterfall paused)')
        elif self._is_scanning:
            self.scan_status.setText('Scan Status: Running')
        else:
            self.scan_status.setText('Scan Status: Stopped')

    def _blink_record_button(self) -> None:
        if self._capture_logger is None:
            self.record_btn.setStyleSheet('')
            self._record_blink_on = False
            return
        self._record_blink_on = not self._record_blink_on
        color = '#A90000' if self._record_blink_on else '#D71818'
        self.record_btn.setStyleSheet(f'QPushButton {{ background-color: {color}; border: 1px solid #ff8c8c; font-weight: 700; }}')

    def _toggle_lab_tab(self) -> None:
        target = 0 if self.tabs.currentIndex() == 1 else 1
        self.tabs.setCurrentIndex(target)

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
            with self._dsp_lock:
                frame = self.dsp.process(data[: max(self.dsp.fft_size, 4096)])
            mod = frame.event.modulation if frame.event else 'NOISE'
            snr = float(np.max(frame.fft_db) - np.median(frame.fft_db))
            baud = frame.event.baud_rate if frame.event else 0.0
            bitstream = self._iq_to_bitstream(data[: min(data.size, int(self.acquisition.config.sample_rate * 0.12))])
            self._render_encoding_toolbox(bitstream)
            self.iq_info.setText(
                f'File: {Path(path).name} | Samples: {data.size} | Modulation: {mod} | SNR: {snr:.2f} dB | Baud: {baud:.1f}'
            )
        except Exception as exc:
            self.iq_info.setText(f'Offline IQ analyze failed: {exc}')
            self._log('ERROR', f'Offline IQ analyze failed: {exc}')
            self.encoding_compare.setPlainText('')

    def _iq_to_bitstream(self, iq: np.ndarray) -> str:
        """Convert IQ window to rough binary stream for encoding toolbox preview."""
        if iq.size < 16:
            return ""
        envelope = np.abs(np.asarray(iq, dtype=np.complex64))
        threshold = float(np.median(envelope))
        raw = (envelope > threshold).astype(np.uint8)
        # Downsample into symbol-ish bins so display is compact and human-readable.
        stride = max(1, raw.size // 256)
        bits = []
        for i in range(0, raw.size, stride):
            chunk = raw[i : i + stride]
            bits.append("1" if np.mean(chunk) >= 0.5 else "0")
        return "".join(bits)

    def _render_encoding_toolbox(self, bitstream: str) -> None:
        analyzer = self._lab_analyzer
        if analyzer is None:
            self.encoding_compare.setPlainText("Encoding toolbox indisponibil: pornește întâi o captură THE COLLECTOR.")
            return
        variants = analyzer.apply_encoding_toolbox(bitstream)
        lines = [
            "Raw:",
            variants.get("raw", ""),
            "",
            "Bit Inversion:",
            variants.get("bit_inversion", ""),
            "",
            "Manchester:",
            variants.get("manchester", ""),
            "",
            "Differential Manchester:",
            variants.get("differential_manchester", ""),
            "",
            "PWM:",
            variants.get("pwm", ""),
        ]
        self.encoding_compare.setPlainText("\n".join(lines))

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
        return build_full_intelligence_report_html(
            detections=list(self.detections),
            watchlist=self.watchlist,
            raw_hex_max_chars=self._raw_hex_max_chars,
            hide_urban_noise=self.hide_noise_check.isChecked(),
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
        doc.print_(printer)
        QtWidgets.QMessageBox.information(self, 'Export PDF', f'PDF salvat:\n{path}')

    def _set_error(self, message: str) -> None:
        self._last_error_message = message
        self.error_label.setText(f'Errors: {message}')
        self._log('ERROR', message)

    def _log(self, level: str, message: str) -> None:
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        self._error_log.appendleft(f'[{ts} UTC] {level}: {message}')

    def _show_error_log(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle('BladeEye Error Log')
        dialog.resize(1100, 600)
        layout = QtWidgets.QVBoxLayout(dialog)
        log_view = QtWidgets.QPlainTextEdit(dialog)
        log_view.setReadOnly(True)
        log_view.setPlainText('\n'.join(self._error_log) if self._error_log else 'No log entries yet.')
        layout.addWidget(log_view)
        clear_btn = QtWidgets.QPushButton('Clear Log')
        close_btn = QtWidgets.QPushButton('Close')
        clear_btn.clicked.connect(lambda: (self._error_log.clear(), log_view.setPlainText('No log entries yet.')))
        close_btn.clicked.connect(dialog.accept)
        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(clear_btn)
        footer.addStretch(1)
        footer.addWidget(close_btn)
        layout.addLayout(footer)
        dialog.exec()

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
        with self._dsp_lock:
            self.dsp.sample_rate = self.acquisition.config.sample_rate
            self.dsp.set_center_freq(center_freq)

        try:
            self.acquisition.update_params(center_freq=center_freq, bandwidth=sample_rate, gain=gain)
        except Exception as exc:
            self._set_error(f'Hardware configure failed: {exc}')
            return False

        self.active_freq.setText(f'Active Frequency: {center_freq:.0f} Hz')
        self._log(
            'INFO',
            f'Configured center={center_freq:.0f}Hz sample_rate={sample_rate:.0f}Hz gain={gain:.1f}dB threshold={self.alert_threshold.value():.1f}.',
        )
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
