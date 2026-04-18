from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import threading
import time
from multiprocessing import shared_memory

import numpy as np
from PySide6 import QtCore, QtGui, QtPrintSupport, QtWidgets

from .circular_buffer import IQCircularBuffer
from .capture_lab import AsyncRawCaptureLogger, PowerIndexAnalyzer
from .dsp import DSPEngine
from .engine import AcquisitionEngine, HardwareConfig, SDRWorker
from .reporting import build_full_intelligence_report_html, is_urban_noise_label
from .runtime_health import build_heartbeat_payload, should_trigger_watchdog
from .session import ProSession, SessionStore
from .sigint_logger import SigintLogger
from .smart_functions import DetectionEvent, HoppingController

PROTOCOL_VERSION = 1


class RuntimeMode(str, Enum):
    IDLE = 'IDLE'
    MONITOR = 'MONITOR'
    RECORD = 'RECORD'
    LAB = 'LAB'
    ERROR = 'ERROR'


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
        self._classic_palette = True
        self.setMinimumHeight(220)

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

    def set_palette_mode(self, classic: bool) -> None:
        self._classic_palette = bool(classic)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        _ = event
        p = QtGui.QPainter(self)
        rect = self.rect()
        p.fillRect(rect, QtGui.QColor('#FFFFFF' if self._classic_palette else '#0A1020'))

        if self._waterfall:
            w = rect.width()
            h = int(rect.height() * 0.72)
            img = QtGui.QImage(w, len(self._waterfall), QtGui.QImage.Format_RGB32)
            for y, row in enumerate(reversed(self._waterfall)):
                vis = self._visible_slice(row)
                rs = np.interp(np.linspace(0, vis.size - 1, w), np.arange(vis.size), vis)
                for x, v in enumerate(rs):
                    val = float(np.clip((v + self._intensity_offset) * self._intensity_gain, 0.0, 1.0))
                    if self._classic_palette:
                        shade = int(np.clip(255 - (val * 200.0), 20, 255))
                        c = QtGui.QColor(shade, shade, 255)
                    else:
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
        p.setPen(QtGui.QPen(QtGui.QColor('#003E9A' if self._classic_palette else '#2EE0FF'), 2))
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
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = max(980, int(available.width() * 0.92))
            height = max(680, int(available.height() * 0.88))
            self.resize(width, height)
        else:
            self.resize(1280, 820)

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
        self._last_presence_detection_ts = 0.0
        self._presence_detection_cooldown_s = 0.35
        self._waterfall_suspended_for_lab = False
        self._record_blink_on = False
        self._runtime_mode = RuntimeMode.IDLE
        self._logs_dir = Path('logs')
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._engine_log_path = self._logs_dir / 'engine_error.log'
        self._api_log_path = self._logs_dir / 'api_error.log'
        self._runtime_lock_path = self._logs_dir / 'acquisition.lock'
        self._heartbeat_path = self._logs_dir / 'engine_heartbeat.json'
        self._watchdog_timeout_s = 2.5
        self._watchdog_recovery_cooldown_s = 8.0
        self._last_watchdog_recovery_ts = 0.0
        self._last_heartbeat_flush_ts = 0.0
        self._sidecar_enabled = os.getenv('BLADEEYE_ENGINE_SIDECAR', '1') == '1'
        self._sidecar_data_mode = os.getenv('BLADEEYE_USE_SIDECAR_DATA', '0') == '1'
        self._sidecar_control_path = self._logs_dir / 'engine_control.json'
        self._sidecar_status_path = self._logs_dir / 'engine_status.json'
        self._sidecar_frame_path = self._logs_dir / 'engine_spectrum_frame.bin'
        self._sidecar_process: subprocess.Popen | None = None
        self._sidecar_command_seq = 0
        self._sidecar_last_frame_seq = -1
        self._sidecar_capture_active = False
        self._sidecar_last_loaded_capture_key = ""
        self._sidecar_last_event_seq = 0
        self._sidecar_protocol_warned = False
        self._sidecar_last_protocol_error = ""
        self._sidecar_frame_transport = 'file'
        self._sidecar_frame_shm_name = ''
        self._sidecar_frame_shm_size = 0
        self._sidecar_frame_shm: shared_memory.SharedMemory | None = None
        self._preflight_runtime_cleanup()
        if self._sidecar_enabled:
            self._ensure_sidecar_running()

        self._build_ui(config)
        self.acquisition.add_sink(self._on_iq_chunk)
        self.acquisition.add_error_sink(self._on_acquisition_error)

        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.timeout.connect(self._refresh_ui)
        self._ui_timer.start(33)
        self._detections_timer = QtCore.QTimer(self)
        self._detections_timer.timeout.connect(self._flush_detection_table)
        self._detections_timer.start(self._table_refresh_interval_ms)
        self._record_blink_timer = QtCore.QTimer(self)
        self._record_blink_timer.timeout.connect(self._blink_record_button)
        self._record_blink_timer.start(450)
        self._watchdog_timer = QtCore.QTimer(self)
        self._watchdog_timer.timeout.connect(self._watchdog_tick)
        self._watchdog_timer.start(1000)

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
        self.start_btn = QtWidgets.QPushButton('START PREVIEW')
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
        self.tabs.tabBar().hide()
        monitor_tab = QtWidgets.QWidget(self)
        monitor_layout = QtWidgets.QVBoxLayout(monitor_tab)
        lab_tab = QtWidgets.QWidget(self)
        lab_layout = QtWidgets.QVBoxLayout(lab_tab)

        controls = QtWidgets.QHBoxLayout()
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItems(['wideband 433 MHz', 'Europe 868 MHz', '915 MHz ISM', 'Manual / Custom'])
        self.preset_combo.currentTextChanged.connect(self._apply_preset)

        self.freq_spin = QtWidgets.QDoubleSpinBox()
        self.freq_spin.setRange(1.0, 6000.0)
        self.freq_spin.setValue(config.center_freq / 1e6)
        self.freq_spin.setSuffix(' MHz')
        self.freq_spin.lineEdit().setReadOnly(True)
        self.freq_spin.valueChanged.connect(lambda v: self._retune(v * 1e6))

        self.sample_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sample_slider.setRange(1, 40)
        self.sample_slider.setValue(int(config.sample_rate / 1e6))
        self.sample_slider.setMinimumWidth(120)
        self.sample_slider.valueChanged.connect(self._change_sample_rate)

        self.gain_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 70)
        self.gain_slider.setValue(int(config.gain))
        self.gain_slider.setMinimumWidth(120)
        self.gain_slider.valueChanged.connect(lambda v: self.acquisition.update_params(gain=float(v)))

        self.alert_threshold = QtWidgets.QDoubleSpinBox()
        self.alert_threshold.setRange(0.8, 12.0)
        self.alert_threshold.setDecimals(2)
        self.alert_threshold.setSingleStep(0.1)
        self.alert_threshold.setValue(2.5)
        self.alert_threshold.valueChanged.connect(self._set_trigger_gain_from_threshold)
        with self._dsp_lock:
            self.dsp.set_trigger_gain(max(1.0, self.alert_threshold.value()))

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
        self.wf_intensity_slider.setMinimumWidth(120)
        self.wf_intensity_slider.valueChanged.connect(self._set_waterfall_intensity)
        self.classic_palette_check = QtWidgets.QCheckBox('Classic Waterfall')
        self.classic_palette_check.setChecked(True)
        self.classic_palette_check.toggled.connect(self.spectrum.set_palette_mode)
        self.runtime_source_combo = QtWidgets.QComboBox()
        self.runtime_source_combo.addItem('Local pipeline', 'local')
        self.runtime_source_combo.addItem('Sidecar pipeline', 'sidecar')
        self.runtime_source_combo.setCurrentIndex(1 if self._sidecar_data_mode else 0)
        self.runtime_source_combo.currentIndexChanged.connect(self._on_runtime_source_changed)
        if not self._sidecar_enabled:
            idx = self.runtime_source_combo.findData('sidecar')
            if idx >= 0:
                self.runtime_source_combo.setItemData(
                    idx,
                    'Enable BLADEEYE_ENGINE_SIDECAR=1 to use sidecar data mode.',
                    QtCore.Qt.ToolTipRole,
                )

        for label, widget in (
            ('Preset', self.preset_combo),
            ('Center', self.freq_spin),
            ('Sample Rate (MHz)', self.sample_slider),
            ('Gain (dB)', self.gain_slider),
            ('Alert Multiplier', self.alert_threshold),
            ('Runtime Source', self.runtime_source_combo),
            ('', self.hop_check),
            ('', self.hide_noise_check),
            ('Intensity/Offset', self.wf_intensity_slider),
            ('', self.classic_palette_check),
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
        self.lab_scan_btn = QtWidgets.QPushButton('Scan Entire File for Energy')
        self.lab_scan_btn.clicked.connect(self._scan_lab_capture_for_energy)
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
        lab_layout.addWidget(self.lab_scan_btn)
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
        self.table.setToolTip(
            'Baud/Protocol sunt estimate avansate. În PREVIEW pot apărea N/A, iar în REC apar doar marker-e "Captured/Pending Lab".'
        )
        detections_area.addWidget(self.table)

        details_panel = QtWidgets.QWidget(self)
        details_layout = QtWidgets.QVBoxLayout(details_panel)
        details_layout.addWidget(QtWidgets.QLabel('Signal Details'))
        self.signal_details = QtWidgets.QPlainTextEdit(self)
        self.signal_details.setReadOnly(True)
        self.signal_details.setPlaceholderText('Selectează un rând pentru a vedea datele brute.')
        details_layout.addWidget(self.signal_details, stretch=1)
        details_panel.setMinimumWidth(240)
        detections_area.addWidget(details_panel)
        detections_area.setStretchFactor(0, 4)
        detections_area.setStretchFactor(1, 1)
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
            self.dsp.set_trigger_gain(max(1.0, float(threshold)))

    def _apply_preset(self, text: str) -> None:
        presets = {
            'wideband 433 MHz': 433_920_000.0,
            'Europe 868 MHz': 868_300_000.0,
            '915 MHz ISM': 915_000_000.0,
        }
        is_manual = text == 'Manual / Custom'
        self.freq_spin.lineEdit().setReadOnly(not is_manual)
        if text in presets:
            self.freq_spin.setValue(presets[text] / 1e6)
            self.alert_threshold.setValue(2.5)

    def start(self) -> None:
        self._log('INFO', 'Start requested from UI.')
        if self._is_scanning:
            self._log('INFO', 'Start ignored: already scanning.')
            return
        if self._sidecar_data_mode:
            self._ensure_sidecar_running()
            self._send_sidecar_command('start')
            self._is_scanning = True
            with self._state_lock:
                self._last_chunk_ts = time.time()
                self._last_latency_ms = 0.0
                self._dropped_chunks = 0
            self._set_runtime_mode(RuntimeMode.MONITOR, 'Acquisition started (sidecar data mode)')
            self._write_runtime_lock()
            self.status_label.setText('SDR Core Health: Healthy (sidecar)')
            self.status_label.setStyleSheet('color: #00ff99; font-weight: 600;')
            self.record_btn.setEnabled(True)
            self.record_btn.setText('ARM & RECORD')
            self._log('INFO', 'Started in sidecar data mode: local acquisition pipeline bypassed.')
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
        self._set_runtime_mode(RuntimeMode.MONITOR, 'Acquisition started')
        self._write_runtime_lock()
        self._send_sidecar_command('start')
        self.status_label.setText('SDR Core Health: Healthy')
        self.status_label.setStyleSheet('color: #00ff99; font-weight: 600;')
        self.record_btn.setEnabled(True)
        self._log('INFO', f'Acquisition started successfully using source={self.acquisition.source_name}.')

    def stop(self) -> None:
        if self._capture_logger is not None:
            self._stop_capture_recording()
        if self._sidecar_data_mode and self._sidecar_capture_active:
            self._send_sidecar_command('record_stop')
        if not self._sidecar_data_mode:
            self.acquisition.stop()
            self.sdr_worker.stop()
        self._is_scanning = False
        with self._state_lock:
            self._last_chunk_ts = 0.0
            self._last_latency_ms = 0.0
            self._dropped_chunks = 0
        self.sdr_worker.dropped_input_chunks = 0
        self.sdr_worker.dropped_ready_frames = 0
        self.dropped_label.setText('Dropped: 0')
        self._set_runtime_mode(RuntimeMode.IDLE, 'Acquisition stopped')
        self._clear_runtime_lock()
        self._send_sidecar_command('stop')
        self.status_label.setText('SDR Core Health: Idle')
        self.status_label.setStyleSheet('')
        self.record_btn.setEnabled(False)
        self.record_btn.setText('ARM & RECORD')
        self.record_btn.setStyleSheet('')
        self._sidecar_capture_active = False
        self._log('INFO', 'Acquisition stopped.')

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.stop()
        self._clear_runtime_lock()
        self._stop_sidecar()
        self.logger.close()
        super().closeEvent(event)

    def _on_iq_chunk(self, chunk: np.ndarray) -> None:
        try:
            with self._state_lock:
                now = time.time()
                if self._last_chunk_ts:
                    self._last_latency_ms = (now - self._last_chunk_ts) * 1000.0
                self._last_chunk_ts = now
                if (now - self._last_heartbeat_flush_ts) >= 0.75:
                    self._flush_engine_heartbeat(now)
                    self._last_heartbeat_flush_ts = now
            self.buffer.extend(chunk)
            capture_logger = self._capture_logger
            if capture_logger is not None:
                capture_logger.ingest(chunk)
                self._append_presence_detection(chunk)
                return
            if self._waterfall_suspended_for_lab:
                return
            if self.sdr_worker.submit_chunk(chunk):
                with self._state_lock:
                    self._dropped_chunks += 1
        except Exception as exc:
            self._set_error(f'Chunk processing failed: {exc}')

    def _append_presence_detection(self, chunk: np.ndarray) -> None:
        """Lightweight REC path: only mark signal presence for later LAB analysis."""
        if chunk.size == 0:
            return
        now = time.time()
        if now - self._last_presence_detection_ts < self._presence_detection_cooldown_s:
            return
        rssi = float(np.mean(np.abs(chunk) ** 2))
        trigger = max(1.0, float(self.alert_threshold.value()))
        if rssi < trigger:
            return
        self._last_presence_detection_ts = now
        sr = max(float(self.acquisition.config.sample_rate), 1.0)
        duration_s = max(float(chunk.size) / sr, 1.0 / sr)
        event = DetectionEvent(
            timestamp=now,
            center_freq=float(self.acquisition.config.center_freq),
            energy=rssi,
            signal_strength=float(np.max(np.abs(chunk))),
            duration_s=duration_s,
            modulation='Captured',
            baud_rate=0.0,
            purpose='Pending Lab',
            protocol='Pending Lab',
            label='Captured',
            confidence=0.0,
            raw_hex='',
        )
        self.detections.appendleft(event)
        self._detection_iq_snippets.appendleft(np.array([], dtype=np.complex64))
        self._pending_detection_table_refresh = True

    def _on_acquisition_error(self, message: str) -> None:
        self._set_error(message, channel='engine')

    def _on_worker_error(self, message: str) -> None:
        self._set_error(message, channel='engine')

    def _process_chunk(self, chunk: np.ndarray):
        with self._dsp_lock:
            return self.dsp.process(chunk, deep_analysis=False)

    def _refresh_ui(self) -> None:
        frame = self.sdr_worker.pop_latest_frame() if not self._sidecar_data_mode else None
        with self._state_lock:
            dropped_chunks = self._dropped_chunks + self.sdr_worker.dropped_ready_frames
            latency_ms = self._last_latency_ms
        if self._capture_logger is not None:
            dropped_chunks += self._capture_logger.dropped_chunks
        self.dropped_label.setText(f'Dropped: {dropped_chunks}')
        self.latency_label.setText(f'Latency: {latency_ms:.1f}ms')
        self.error_label.setText(f'Errors: {self._last_error_message}')
        self.status_label.setText(f'SDR Core Health: {"Healthy" if self._is_scanning else "Idle"}')
        if self._sidecar_data_mode:
            self._sync_sidecar_runtime_status()
        if self._capture_logger is not None:
            gb = self._capture_logger.bytes_written / (1024 ** 3)
            self.record_btn.setText(f'Stop Capture ({gb:.2f} GB)')
        elif self._recording_mode_enabled:
            self.record_btn.setText('ARM & RECORD')
        if self._waterfall_suspended_for_lab:
            return
        if frame is None:
            sidecar_spectrum = self._read_sidecar_spectrum_frame()
            if sidecar_spectrum is not None:
                self.spectrum.update_frame(sidecar_spectrum)
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
            baud_display = f'{evt.baud_rate:.1f}' if evt.baud_rate > 0 else '---'
            purpose_display = evt.purpose if evt.purpose else 'Record to Analyze'
            protocol_display = evt.protocol if evt.protocol else 'Record to Analyze'
            values = [
                f'{evt.center_freq:.0f}',
                evt.modulation,
                baud_display,
                purpose_display,
                f'{evt.label} / {protocol_display}',
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
            event = list(self.detections)[source_idx]
            iq = self.buffer.latest(int(max(4096, event.duration_s * self.acquisition.config.sample_rate * 3)))
            if iq.size == 0:
                self.iq_info.setText('Nu există fragment IQ disponibil pentru această detecție.')
                return
        with self._dsp_lock:
            frame = self.dsp.process(iq[: max(self.dsp.fft_size, 4096)], deep_analysis=True)
        event = list(self.detections)[source_idx]
        mod = frame.event.modulation if frame.event else event.modulation
        snr = float(np.max(frame.fft_db) - np.median(frame.fft_db))
        baud = frame.event.baud_rate if frame.event else event.baud_rate
        bitstream = self._iq_to_bitstream(iq)
        self._render_encoding_toolbox(bitstream)
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
            (f'Baud: {event.baud_rate:.1f}' if event.baud_rate > 0 else 'Baud: ---'),
            f'RSSI/Strength: {event.signal_strength:.5f}',
            f'Duration: {event.duration_s:.6f} s',
            f'Label: {event.label}',
            f'Purpose: {event.purpose}',
            f'Protocol: {event.protocol or "Record to Analyze"}',
            f'Confidence: {event.confidence * 100:.1f}%',
            f'Raw Hex: {event.raw_hex or "(not available)"}',
        ]
        self.signal_details.setPlainText('\n'.join(details))

    def _retune(self, freq_hz: float) -> None:
        self.acquisition.update_params(center_freq=freq_hz)
        with self._dsp_lock:
            self.dsp.set_center_freq(freq_hz)
        self.active_freq.setText(f'Active Frequency: {freq_hz:.0f} Hz')

    def _on_runtime_source_changed(self, index: int) -> None:
        requested = str(self.runtime_source_combo.itemData(index) or 'local').strip().lower()
        target_sidecar_mode = requested == 'sidecar'
        if target_sidecar_mode and not self._sidecar_enabled:
            QtWidgets.QMessageBox.information(
                self,
                'Runtime Source',
                'Sidecar mode is disabled. Set BLADEEYE_ENGINE_SIDECAR=1 and restart.',
            )
            self.runtime_source_combo.blockSignals(True)
            self.runtime_source_combo.setCurrentIndex(0)
            self.runtime_source_combo.blockSignals(False)
            return
        if target_sidecar_mode == self._sidecar_data_mode:
            return
        if self._is_scanning:
            QtWidgets.QMessageBox.information(
                self,
                'Runtime Source',
                'Stop preview before changing runtime source.',
            )
            self.runtime_source_combo.blockSignals(True)
            self.runtime_source_combo.setCurrentIndex(1 if self._sidecar_data_mode else 0)
            self.runtime_source_combo.blockSignals(False)
            return
        self._sidecar_data_mode = target_sidecar_mode
        mode_name = 'sidecar' if self._sidecar_data_mode else 'local'
        self.status_label.setText(f'SDR Core Health: Idle ({mode_name})')
        self._log('INFO', f'Runtime source switched to {mode_name} mode.')

    def _record_buffer(self) -> None:
        out_dir = Path('sessions')
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'pro_capture_{int(QtCore.QDateTime.currentSecsSinceEpoch())}.iq'
        self.buffer.snapshot().astype(np.complex64).tofile(path)
        QtWidgets.QMessageBox.information(self, 'Buffer saved', f'Saved: {path}')

    def _toggle_capture_recording(self) -> None:
        if self._sidecar_data_mode:
            if not self._is_scanning:
                QtWidgets.QMessageBox.information(self, 'ARM & RECORD', 'Pornește întâi MONITOR (Start).')
                return
            if self._sidecar_capture_active:
                self._send_sidecar_command('record_stop')
                self._sidecar_capture_active = False
                self.record_btn.setText('ARM & RECORD')
            else:
                self._send_sidecar_command(
                    'record_start',
                    {
                        'threshold_multiplier': float(self.alert_threshold.value()),
                        'output_dir': 'sessions',
                    },
                )
                self._sidecar_capture_active = True
                self.record_btn.setText('Stop Capture (sidecar)')
            return
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
        threshold = max(1.0, float(self.alert_threshold.value()))
        self._capture_logger = AsyncRawCaptureLogger(
            capture_path,
            sample_rate=self.acquisition.config.sample_rate,
            power_threshold=threshold,
        )
        self._capture_logger.start()
        self._recording_mode_enabled = True
        self._recording_ui_counter = 0
        self._last_presence_detection_ts = 0.0
        self._set_runtime_mode(RuntimeMode.RECORD, f'Recording to {capture_path.name}')
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
        self._set_runtime_mode(RuntimeMode.MONITOR if self._is_scanning else RuntimeMode.IDLE, 'Capture stopped')
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

    def _scan_lab_capture_for_energy(self) -> None:
        analyzer = self._lab_analyzer
        if analyzer is None:
            self.iq_info.setText('Încarcă mai întâi o sesiune/captură în THE LAB.')
            return
        try:
            events = analyzer.scan_entire_file_for_energy(
                threshold_multiplier=max(1.0, float(self.alert_threshold.value())),
                min_event_gap_ms=80.0,
                chunk_samples=8192,
                persist=True,
            )
        except Exception as exc:
            self.iq_info.setText(f'Energy scan failed: {exc}')
            self._log('ERROR', f'Energy scan failed: {exc}')
            return
        self._lab_events = events
        self.lab_events_list.clear()
        for idx, event in enumerate(self._lab_events, start=1):
            ts = datetime.fromtimestamp(float(event.get('timestamp', 0.0))).strftime('%H:%M:%S') if event.get('timestamp') else '--:--:--'
            rssi = float(event.get('rssi', 0.0))
            peak = float(event.get('peak_power', 0.0))
            self.lab_events_list.addItem(f'#{idx:03d}  t={ts}  RSSI={rssi:.4f}  Peak={peak:.4f}')
        self.iq_info.setText(f'THE LAB reindexed capture: {len(self._lab_events)} energy events found.')

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
            self._set_runtime_mode(RuntimeMode.LAB, 'LAB tab active')
        elif self._is_scanning:
            with self._state_lock:
                self._dropped_chunks = 0
            self.sdr_worker.dropped_input_chunks = 0
            self.sdr_worker.dropped_ready_frames = 0
            self.dropped_label.setText('Dropped: 0')
            if self._capture_logger is not None:
                self._set_runtime_mode(RuntimeMode.RECORD, 'Recording active')
            else:
                self._set_runtime_mode(RuntimeMode.MONITOR, 'Monitoring active')
        else:
            self._set_runtime_mode(RuntimeMode.IDLE, 'Stopped')

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
                frame = self.dsp.process(data[: max(self.dsp.fft_size, 4096)], deep_analysis=True)
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
        variants = analyzer.apply_encoding_toolbox(bitstream) if analyzer is not None else self._basic_encoding_toolbox(bitstream)
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

    @staticmethod
    def _basic_encoding_toolbox(bitstream: str) -> dict[str, str]:
        bits = ''.join(ch for ch in bitstream if ch in {'0', '1'})
        if not bits:
            return {
                'raw': '',
                'bit_inversion': '',
                'manchester': '',
                'differential_manchester': '',
                'pwm': '',
            }
        inverted = ''.join('1' if b == '0' else '0' for b in bits)
        manchester = ''.join('10' if b == '1' else '01' for b in bits[:128])
        prev = '1'
        diff = []
        for b in bits:
            prev = prev if b == '1' else ('0' if prev == '1' else '1')
            diff.append(prev)
        pwm = ''.join('1100' if b == '1' else '1000' for b in bits[:64])
        return {
            'raw': bits,
            'bit_inversion': inverted,
            'manchester': manchester,
            'differential_manchester': ''.join(diff),
            'pwm': pwm,
        }

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
            runtime_source=('sidecar' if self._sidecar_data_mode else 'local'),
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
        runtime_source = str(getattr(session, 'runtime_source', 'local') or 'local').strip().lower()
        idx = self.runtime_source_combo.findData('sidecar' if runtime_source == 'sidecar' else 'local')
        if idx >= 0:
            self.runtime_source_combo.setCurrentIndex(idx)

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

    def _set_error(self, message: str, *, channel: str = 'api') -> None:
        self._last_error_message = message
        self.error_label.setText(f'Errors: {message}')
        self._set_runtime_mode(RuntimeMode.ERROR, message)
        self._log('ERROR', message, channel=channel)

    def _set_runtime_mode(self, mode: RuntimeMode, reason: str = '') -> None:
        self._runtime_mode = mode
        if mode == RuntimeMode.MONITOR:
            self.scan_status.setText('Scan Status: Running')
        elif mode == RuntimeMode.RECORD:
            self.scan_status.setText('Scan Status: RECORD mode (collector active)')
        elif mode == RuntimeMode.LAB:
            self.scan_status.setText('Scan Status: LAB mode (waterfall paused)')
        elif mode == RuntimeMode.ERROR:
            self.scan_status.setText('Scan Status: Error')
        else:
            self.scan_status.setText('Scan Status: Stopped')
        if reason:
            self._log('INFO', f'Mode -> {mode.value}: {reason}')

    def _log(self, level: str, message: str, *, channel: str = 'api') -> None:
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        line = f'[{ts} UTC] {level}: {message}'
        self._error_log.appendleft(line)
        path = self._engine_log_path if channel == 'engine' else self._api_log_path
        self._append_rotating_log(path, line)

    def _preflight_runtime_cleanup(self) -> None:
        now = time.time()
        try:
            if self._runtime_lock_path.exists():
                age = now - self._runtime_lock_path.stat().st_mtime
                if age > 20.0:
                    self._runtime_lock_path.unlink(missing_ok=True)
                    self._log('INFO', f'Cleanup: removed stale runtime lock ({age:.1f}s old).')
            if self._heartbeat_path.exists():
                age = now - self._heartbeat_path.stat().st_mtime
                if age > 20.0:
                    self._heartbeat_path.unlink(missing_ok=True)
                    self._log('INFO', f'Cleanup: removed stale heartbeat file ({age:.1f}s old).')
            if self._sidecar_control_path.exists():
                age = now - self._sidecar_control_path.stat().st_mtime
                if age > 120.0:
                    self._sidecar_control_path.unlink(missing_ok=True)
                    self._log('INFO', f'Cleanup: removed stale sidecar control file ({age:.1f}s old).')
            if self._sidecar_status_path.exists():
                age = now - self._sidecar_status_path.stat().st_mtime
                if age > 120.0:
                    self._sidecar_status_path.unlink(missing_ok=True)
                    self._log('INFO', f'Cleanup: removed stale sidecar status file ({age:.1f}s old).')
            if self._sidecar_frame_path.exists():
                age = now - self._sidecar_frame_path.stat().st_mtime
                if age > 120.0:
                    self._sidecar_frame_path.unlink(missing_ok=True)
                    self._log('INFO', f'Cleanup: removed stale sidecar frame file ({age:.1f}s old).')
        except Exception as exc:
            self._log('ERROR', f'Preflight cleanup failed: {exc}', channel='engine')

    def _write_runtime_lock(self) -> None:
        payload = {
            'pid': os.getpid(),
            'started_at': time.time(),
            'mode': self._runtime_mode.value,
        }
        try:
            self._runtime_lock_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        except Exception as exc:
            self._log('ERROR', f'Failed to write runtime lock: {exc}', channel='engine')

    def _clear_runtime_lock(self) -> None:
        try:
            self._runtime_lock_path.unlink(missing_ok=True)
            self._heartbeat_path.unlink(missing_ok=True)
        except Exception as exc:
            self._log('ERROR', f'Failed to clear runtime lock/heartbeat: {exc}', channel='engine')

    def _flush_engine_heartbeat(self, now_ts: float) -> None:
        payload = build_heartbeat_payload(
            now_ts=now_ts,
            mode=self._runtime_mode.value,
            scanning=self._is_scanning,
            dropped_chunks=self._dropped_chunks,
            last_error=self._last_error_message,
        )
        try:
            self._heartbeat_path.write_text(json.dumps(payload), encoding='utf-8')
        except Exception as exc:
            self._log('ERROR', f'Failed to write heartbeat: {exc}', channel='engine')

    def _watchdog_tick(self) -> None:
        if not self._is_scanning:
            return
        if self._sidecar_data_mode:
            self._watchdog_sidecar_tick()
            return
        now = time.time()
        with self._state_lock:
            last_chunk = self._last_chunk_ts
        if not should_trigger_watchdog(
            now_ts=now,
            last_activity_ts=last_chunk,
            timeout_s=self._watchdog_timeout_s,
            last_recovery_ts=self._last_watchdog_recovery_ts,
            recovery_cooldown_s=self._watchdog_recovery_cooldown_s,
        ):
            return
        self._last_watchdog_recovery_ts = now
        self._log(
            'ERROR',
            f'Watchdog timeout: no IQ chunk for {(now - last_chunk):.2f}s. Attempting acquisition recovery.',
            channel='engine',
        )
        try:
            self.acquisition.stop()
            self.sdr_worker.stop()
            self.sdr_worker.start()
            self.acquisition.start()
            with self._state_lock:
                self._last_chunk_ts = time.time()
            self._set_runtime_mode(RuntimeMode.MONITOR if self._capture_logger is None else RuntimeMode.RECORD, 'Watchdog recovery complete')
            self._log('INFO', 'Watchdog recovery successful.', channel='engine')
        except Exception as exc:
            self._set_error(f'Watchdog recovery failed: {exc}', channel='engine')

    def _watchdog_sidecar_tick(self) -> None:
        now = time.time()
        status = self._read_sidecar_status()
        if not status:
            return
        last_ts = float(status.get('timestamp', 0.0) or 0.0)
        if not should_trigger_watchdog(
            now_ts=now,
            last_activity_ts=last_ts,
            timeout_s=self._watchdog_timeout_s,
            last_recovery_ts=self._last_watchdog_recovery_ts,
            recovery_cooldown_s=self._watchdog_recovery_cooldown_s,
        ):
            return
        self._last_watchdog_recovery_ts = now
        self._log(
            'ERROR',
            f'Sidecar watchdog timeout: status stale for {(now - last_ts):.2f}s. Restarting sidecar.',
            channel='engine',
        )
        self._stop_sidecar()
        self._ensure_sidecar_running()
        self._send_sidecar_command('start')
        self._log('INFO', 'Sidecar watchdog recovery requested.', channel='engine')

    def _ensure_sidecar_running(self) -> None:
        if not self._sidecar_enabled:
            return
        if self._sidecar_process is not None and self._sidecar_process.poll() is None:
            return
        cmd = [
            sys.executable,
            '-m',
            'bladeeye_pro.engine_sidecar',
            '--control',
            str(self._sidecar_control_path),
            '--status',
            str(self._sidecar_status_path),
            '--frame',
            str(self._sidecar_frame_path),
        ]
        try:
            self._sidecar_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(Path.cwd()),
            )
            self._log('INFO', f'Engine sidecar started (pid={self._sidecar_process.pid}).', channel='engine')
        except Exception as exc:
            self._log('ERROR', f'Failed to start engine sidecar: {exc}', channel='engine')

    def _send_sidecar_command(self, action: str, extra: dict[str, object] | None = None) -> None:
        if not self._sidecar_enabled:
            return
        self._ensure_sidecar_running()
        self._sidecar_command_seq += 1
        payload = {
            'seq': self._sidecar_command_seq,
            'timestamp': time.time(),
            'protocol_version': PROTOCOL_VERSION,
            'action': action,
            'config': {
                'center_freq': float(self.acquisition.config.center_freq),
                'sample_rate': float(self.acquisition.config.sample_rate),
                'bandwidth': float(self.acquisition.config.bandwidth),
                'gain': float(self.acquisition.config.gain),
                'fft_size': int(self.dsp.fft_size),
            },
        }
        if extra:
            payload.update(extra)
        try:
            self._sidecar_control_path.write_text(json.dumps(payload), encoding='utf-8')
        except Exception as exc:
            self._log('ERROR', f'Failed to write sidecar command: {exc}', channel='engine')

    def _stop_sidecar(self) -> None:
        if not self._sidecar_enabled:
            return
        self._close_sidecar_frame_shm()
        self._send_sidecar_command('shutdown')
        proc = self._sidecar_process
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._sidecar_process = None

    def _close_sidecar_frame_shm(self) -> None:
        shm = self._sidecar_frame_shm
        self._sidecar_frame_shm = None
        if shm is None:
            return
        try:
            shm.close()
        except Exception:
            pass

    def _read_sidecar_spectrum_frame(self) -> np.ndarray | None:
        if self._sidecar_frame_transport == 'shm' and self._sidecar_frame_shm_name:
            try:
                if self._sidecar_frame_shm is None or self._sidecar_frame_shm.name != self._sidecar_frame_shm_name:
                    self._close_sidecar_frame_shm()
                    self._sidecar_frame_shm = shared_memory.SharedMemory(name=self._sidecar_frame_shm_name, create=False)
                payload = self._sidecar_frame_shm.buf
                if len(payload) < 22:
                    return None
                magic, protocol_version, seq, _, bins = struct.unpack('<4sHIdI', payload[:22])
                if magic != b'BEF2' or protocol_version != PROTOCOL_VERSION or bins <= 0:
                    return None
                if seq == self._sidecar_last_frame_seq:
                    return None
                expected = 22 + (bins * 4)
                if len(payload) < expected:
                    return None
                frame = np.frombuffer(payload, dtype=np.float32, count=bins, offset=22).copy()
                self._sidecar_last_frame_seq = int(seq)
                return frame
            except Exception:
                self._close_sidecar_frame_shm()
                return None
        path = self._sidecar_frame_path
        if not path.exists():
            return None
        try:
            payload = path.read_bytes()
            if len(payload) < 20:
                return None
            header_size = 20
            protocol_version = PROTOCOL_VERSION
            magic = payload[:4]
            if magic == b'BEF2':
                if len(payload) < 22:
                    return None
                _, protocol_version, seq, _, bins = struct.unpack('<4sHIdI', payload[:22])
                header_size = 22
            elif magic == b'BEF1':
                _, seq, _, bins = struct.unpack('<4sIdI', payload[:20])
                header_size = 20
            else:
                return None
            if protocol_version != PROTOCOL_VERSION or bins <= 0:
                return None
            if seq == self._sidecar_last_frame_seq:
                return None
            expected = header_size + (bins * 4)
            if len(payload) < expected:
                return None
            frame = np.frombuffer(payload, dtype=np.float32, count=bins, offset=header_size).copy()
            self._sidecar_last_frame_seq = int(seq)
            return frame
        except Exception:
            return None

    def _read_sidecar_status(self) -> dict[str, object] | None:
        path = self._sidecar_status_path
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                return None
            version = int(payload.get('protocol_version', 0) or 0)
            if version != PROTOCOL_VERSION:
                if not self._sidecar_protocol_warned:
                    self._sidecar_protocol_warned = True
                    self._log(
                        'ERROR',
                        f'Sidecar protocol mismatch: status={version}, expected={PROTOCOL_VERSION}.',
                        channel='engine',
                    )
                return None
            protocol_error = str(payload.get('protocol_error', '') or '')
            if protocol_error and protocol_error != self._sidecar_last_protocol_error:
                self._sidecar_last_protocol_error = protocol_error
                self._log('ERROR', f'Sidecar protocol error: {protocol_error}', channel='engine')
            transport = str(payload.get('frame_transport', 'file') or 'file').strip().lower()
            if transport not in {'file', 'shm'}:
                transport = 'file'
            self._sidecar_frame_transport = transport
            self._sidecar_frame_shm_name = str(payload.get('frame_shm_name', '') or '')
            self._sidecar_frame_shm_size = int(payload.get('frame_shm_size', 0) or 0)
            if self._sidecar_frame_transport != 'shm' or not self._sidecar_frame_shm_name:
                self._close_sidecar_frame_shm()
            return payload
        except Exception:
            return None

    def _sync_sidecar_runtime_status(self) -> None:
        status = self._read_sidecar_status()
        if not status:
            return
        event_seq = int(status.get('event_seq', 0) or 0)
        if event_seq > self._sidecar_last_event_seq:
            event_payload = status.get('latest_event', {})
            if isinstance(event_payload, dict) and event_payload:
                try:
                    evt = DetectionEvent(
                        timestamp=float(event_payload.get('timestamp', time.time())),
                        center_freq=float(event_payload.get('center_freq', self.acquisition.config.center_freq)),
                        energy=float(event_payload.get('energy', 0.0)),
                        signal_strength=float(event_payload.get('signal_strength', 0.0)),
                        duration_s=float(event_payload.get('duration_s', 0.0)),
                        modulation=str(event_payload.get('modulation', 'ENERGY')),
                        baud_rate=float(event_payload.get('baud_rate', 0.0)),
                        purpose=str(event_payload.get('purpose', 'Record to Analyze')),
                        protocol=str(event_payload.get('protocol', '')),
                        label=str(event_payload.get('label', 'Energy Peak')),
                        confidence=float(event_payload.get('confidence', 0.0)),
                        raw_hex=str(event_payload.get('raw_hex', '')),
                    )
                    self.detections.appendleft(evt)
                    self._detection_iq_snippets.appendleft(np.array([], dtype=np.complex64))
                    self._pending_detection_table_refresh = True
                except Exception as exc:
                    self._log('ERROR', f'Failed to decode sidecar event: {exc}', channel='engine')
            self._sidecar_last_event_seq = event_seq

        capture_active = bool(status.get('capture_active', False))
        self._sidecar_capture_active = capture_active
        if capture_active:
            self.record_btn.setText('Stop Capture (sidecar)')
        elif self._sidecar_data_mode and self._is_scanning:
            self.record_btn.setText('ARM & RECORD')

        capture_file = str(status.get('capture_file', '') or '')
        index_file = str(status.get('index_file', '') or '')
        key = f'{capture_file}|{index_file}'
        if not capture_active and capture_file and index_file and key != self._sidecar_last_loaded_capture_key:
            capture_path = Path(capture_file)
            index_path = Path(index_file)
            if capture_path.exists() and index_path.exists():
                self._set_lab_session(capture_path, index_path)
                self._sidecar_last_loaded_capture_key = key
                self.iq_info.setText(
                    f'Sidecar capture ready: {capture_path.name} + {index_path.name}. Loaded into THE LAB.'
                )

    def _append_rotating_log(self, path: Path, line: str, *, max_bytes: int = 2_000_000, backups: int = 3) -> None:
        try:
            if path.exists() and path.stat().st_size > max_bytes:
                for idx in range(backups, 0, -1):
                    src = path.with_suffix(path.suffix + f'.{idx}')
                    dst = path.with_suffix(path.suffix + f'.{idx + 1}')
                    if src.exists():
                        if idx == backups:
                            src.unlink(missing_ok=True)
                        else:
                            src.replace(dst)
                path.replace(path.with_suffix(path.suffix + '.1'))
            with path.open('a', encoding='utf-8') as fh:
                fh.write(line + '\n')
        except Exception:
            # avoid recursive logging in case of filesystem issues
            pass

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
