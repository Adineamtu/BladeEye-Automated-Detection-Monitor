"""FastAPI backend for exposing SDR monitoring data."""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import Any, Callable, List, Optional
from collections import deque
from pathlib import Path
from pydantic import BaseModel
from dataclasses import asdict, dataclass
import mmap
import os
import subprocess
import inspect
import asyncio
import time
import json
import socket as _socket
import sys
import struct
from types import SimpleNamespace
import numpy as np
import io
import base64
import datetime
import logging
import zipfile
from logging.handlers import RotatingFileHandler
import matplotlib.pyplot as plt
from weasyprint import HTML
from jinja2 import Environment, FileSystemLoader, select_autoescape
from backend.identifier import identify_signal
from backend.signatures_data import match_rf_signature, capture_to_signature, all_rf_signatures
from backend.decoder import Decoder
from backend.patterns import save_pattern, load_patterns, find_label, PATTERN_FILE
from backend.protocols import (
    identify_protocol,
    PROTOCOLS,
    UserProtocol,
    save_user_protocol,
    load_user_protocols,
)
from backend.execution_board import (
    ExecutionBoard,
    ExecutionTask,
    load_execution_board,
    save_execution_board,
    update_task as update_execution_task,
)
from backend.preflight import PreflightStatus, run_preflight
from backend.zmq_bridge import ZmqSpectrumConsumer
from backend.intelligence_engine import IntelligenceEngine
from backend.sigint_log import SigintLogStore, SigintEvent

# ``Signal`` is defined in ``backend.passive_monitor`` which pulls in heavy
# dependencies like GNU Radio.  Import it lazily and provide a lightweight
# fallback for environments where those libraries are unavailable (e.g. tests).
try:  # pragma: no cover - fallback exercised in tests
    from backend.passive_monitor import Signal
except Exception:  # pragma: no cover
    @dataclass
    class Signal:  # type: ignore
        center_frequency: float
        bandwidth: float
        peak_power: float
        start_time: float
        end_time: float | None
        modulation_type: str | None
        baud_rate: float | None
        protocol_name: str | None = None
        likely_purpose: str | None = None
        label: str | None = None
        sync_word: str | None = None
        short_pulse: float | None = None
        long_pulse: float | None = None
        gap: float | None = None
        detection_status: str | None = None

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
_RUNTIME_ROOT = Path(getattr(sys, "_MEIPASS", BASE_DIR))
_FRONTEND_ENV = os.getenv("FRONTEND_DIST")
FRONTEND_DIST = (
    Path(_FRONTEND_ENV).expanduser().resolve()
    if _FRONTEND_ENV
    else _RUNTIME_ROOT / "frontend" / "dist"
)
# In-memory store of detected signals and the watchlist. Other parts of the
# application can append to ``signals`` when new transmissions are detected and
# modify ``watchlist`` to control which frequencies receive deeper analysis.
signals: List[Signal] = []
watchlist: List[float] = []
recordings: List[dict] = []
recent_signals: deque[Signal] = deque(maxlen=50)
alert_subscribers: set[asyncio.Queue] = set()
log = logging.getLogger(__name__)
socket = SimpleNamespace(socket=_socket.socket, AF_UNIX=_socket.AF_UNIX, SOCK_DGRAM=_socket.SOCK_DGRAM)


def _configure_file_logging() -> None:
    """Write API/runtime errors to disk for post-mortem diagnosis."""
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / "api_error.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)


_configure_file_logging()


class _RuntimeErrorBufferHandler(logging.Handler):
    """Store recent runtime errors for in-app diagnostics panel."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - logging side effect
        if record.levelno < logging.ERROR:
            return
        runtime_errors.append(
            {
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "logger": record.name,
                "level": record.levelname,
                "message": self.format(record),
                "system_snapshot": _runtime_snapshot(),
            }
        )


_runtime_error_handler = _RuntimeErrorBufferHandler(level=logging.ERROR)
_runtime_error_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_runtime_error_handler)

# Directory on disk used for persisting session JSON files.
SESSIONS_DIR = Path("sessions")
AUTOSAVE_FILE = SESSIONS_DIR / "autosave.json"
_recovery_cache: dict | None = None
EXECUTION_BOARD_FILE = SESSIONS_DIR / "execution_board.json"
execution_board: ExecutionBoard | None = None
preflight_status: PreflightStatus | None = None
zmq_consumer: ZmqSpectrumConsumer | None = None
intelligence_engine = IntelligenceEngine(BASE_DIR / "backend" / "signatures.json")
runtime_errors: deque[dict] = deque(maxlen=500)
sigint_store = SigintLogStore(SESSIONS_DIR / "sigint_log.db")
auto_actions: list[dict] = [
    {
        "protocol_name": "Rolling Code (Keeloq)",
        "action": "arm_recording",
        "duration_after": 0.75,
    }
]
_auto_action_state: dict[str, bool] = {}
_last_action_trigger_at: dict[str, float] = {}
_ai_last_activity_ts: float = 0.0
_ai_jobs_processed: int = 0
_live_intel_event_loop: asyncio.AbstractEventLoop | None = None
_live_intel_queue: asyncio.Queue | None = None
_live_intel_task: asyncio.Task | None = None
_LIVE_INTEL_BATCH_SIZE = max(1, int(os.getenv("BLADEEYE_LIVE_INTEL_BATCH_SIZE", "8")))
_LIVE_INTEL_QUEUE_SIZE = max(1, int(os.getenv("BLADEEYE_LIVE_INTEL_QUEUE_SIZE", "32")))


@dataclass
class _LiveIntelBatch:
    signals: list[Signal]
    iq_windows: list[np.ndarray]


def _runtime_snapshot() -> dict:
    """Capture lightweight runtime state for post-mortem diagnostics."""
    cfg = globals().get("config_state", {}) or {}
    consumer = globals().get("zmq_consumer")
    telemetry = consumer.telemetry() if consumer is not None else {}
    return {
        "runtime_mode": cfg.get("runtime_mode"),
        "data_bridge": cfg.get("data_bridge"),
        "center_frequency_hz": cfg.get("center_freq"),
        "gain_db": cfg.get("gain"),
        "sample_rate_hz": cfg.get("samp_rate"),
        "buffer_load_percent": telemetry.get("buffer_load_percent"),
        "dropped_frames": telemetry.get("dropped_frames"),
        "zmq_throughput_bps": telemetry.get("throughput_bps"),
    }


def _mark_ai_activity() -> None:
    """Record a heartbeat for intelligence workload indicators."""
    global _ai_last_activity_ts, _ai_jobs_processed
    _ai_last_activity_ts = time.time()
    _ai_jobs_processed += 1


async def _live_intel_loop() -> None:
    """Background live intelligence worker (scatter-gather classification)."""
    if _live_intel_queue is None:
        return
    while True:
        batch = await _live_intel_queue.get()
        try:
            if batch is None:
                return
            results = await intelligence_engine.analyze_many(batch.iq_windows)
            for sig, result in zip(batch.signals, results):
                sig.modulation_type = result.modulation_type
                sig.baud_rate = result.baud_rate
                if result.protocol_name:
                    sig.protocol_name = result.protocol_name
                if result.likely_purpose:
                    sig.likely_purpose = result.likely_purpose
                setattr(sig, "confidence", result.confidence)
                _mark_ai_activity()
        except Exception:
            log.exception("Live intelligence batch failed")
        finally:
            _live_intel_queue.task_done()


async def _autosave_loop() -> None:
    """Periodically persist session state to ``AUTOSAVE_FILE``."""
    while True:
        await asyncio.sleep(300)
        try:
            SESSIONS_DIR.mkdir(exist_ok=True)
            with open(AUTOSAVE_FILE, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "signals": [asdict(s) for s in signals],
                        "watchlist": watchlist,
                        "recordings": recordings,
                    },
                    fh,
                )
        except Exception:
            # Best effort autosave; ignore errors to avoid crashing the server
            pass

# Directory containing Jinja2 templates for report rendering.
TEMPLATES_DIR = Path("backend/templates")

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


@app.on_event("startup")
async def _startup() -> None:
    """Initialize autosave and preload any recovery data."""
    SESSIONS_DIR.mkdir(exist_ok=True)
    global _recovery_cache, execution_board, preflight_status, zmq_consumer, intelligence_engine
    global _live_intel_event_loop, _live_intel_queue, _live_intel_task
    intelligence_engine = IntelligenceEngine(BASE_DIR / "backend" / "signatures.json")
    if AUTOSAVE_FILE.exists():
        try:
            with open(AUTOSAVE_FILE, "r", encoding="utf-8") as fh:
                _recovery_cache = json.load(fh)
        except Exception:
            _recovery_cache = None
    execution_board = load_execution_board(EXECUTION_BOARD_FILE)
    preflight_status = run_preflight()
    _sync_firmware_warning_on_execution_board()
    config_state["runtime_mode"] = preflight_status.mode
    config_state["hardware_detected"] = preflight_status.hardware_detected
    config_state["usb_access_ok"] = preflight_status.usb_access_ok

    bridge_mode = os.getenv("BLADEEYE_DATA_BRIDGE", "zmq").lower()
    if bridge_mode == "zmq":
        zmq_endpoint = os.getenv("BLADEEYE_ZMQ_ENDPOINT", DEFAULT_ZMQ_SPECTRUM_ENDPOINT)
        zmq_consumer = ZmqSpectrumConsumer(zmq_endpoint)
        config_state["data_bridge"] = "zmq" if zmq_consumer.enabled else "demo"
    else:
        config_state["data_bridge"] = "demo"
    if os.getenv("BLADEEYE_ENABLE_LEGACY_SHM", "0") == "1":
        _safe_startup_probe()
    _bind_monitor_analysis_callback()
    asyncio.create_task(_autosave_loop())
    _live_intel_event_loop = asyncio.get_running_loop()
    _live_intel_queue = asyncio.Queue(maxsize=_LIVE_INTEL_QUEUE_SIZE)
    _live_intel_task = asyncio.create_task(_live_intel_loop())
    await sigint_store.start()
    if os.getenv("SDR_WATCHDOG_ENABLED", "0") == "1":
        asyncio.create_task(_watchdog_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Release optional runtime resources."""
    global zmq_consumer, _live_intel_event_loop, _live_intel_queue, _live_intel_task
    if _live_intel_queue is not None:
        try:
            while True:
                _live_intel_queue.get_nowait()
                _live_intel_queue.task_done()
        except asyncio.QueueEmpty:
            pass
        try:
            _live_intel_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
    if _live_intel_task is not None:
        await _live_intel_task
    _live_intel_task = None
    _live_intel_queue = None
    _live_intel_event_loop = None
    intelligence_engine.shutdown(wait=True)
    await sigint_store.stop()
    sigint_store.close()
    if zmq_consumer is not None:
        zmq_consumer.close()
        zmq_consumer = None


class SignalPayload(BaseModel):
    center_frequency: float
    bandwidth: float
    peak_power: float
    start_time: float
    end_time: float | None = None
    modulation_type: str | None = None
    baud_rate: float | None = None
    protocol_name: str | None = None
    likely_purpose: str | None = None
    label: str | None = None
    sync_word: str | None = None
    short_pulse: float | None = None
    long_pulse: float | None = None
    gap: float | None = None




class CaptureToSignaturePayload(BaseModel):
    name: str
    short_pulse: float
    long_pulse: float
    gap: float | None = None
    modulation: str | None = None
    file_name: str | None = None


class RecordingItem(BaseModel):
    freq: float
    path: str


class SessionPayload(BaseModel):
    signals: List[SignalPayload]
    watchlist: List[float] | None = None
    recordings: List[RecordingItem] | None = None


class WatchlistItem(BaseModel):
    frequency: float


class ConfigPayload(BaseModel):
    center_freq: float | None = None
    samp_rate: float | None = None
    fft_size: int | None = None
    gain: float | None = None
    alert_threshold: float | None = None


class HoppingPayload(BaseModel):
    enabled: bool


class PatternPayload(BaseModel):
    bitstrings: List[str]


class PatternRenamePayload(BaseModel):
    new_name: str


class UserProtocolPayload(BaseModel):
    protocol_name: str
    modulation_type: str
    baud_rate: float
    header_pattern: str
    data_field_structure: dict[str, list[int]]


class ExecutionTaskPatchPayload(BaseModel):
    status: str | None = None
    owner: str | None = None
    notes: str | None = None


class IntelligenceIQPayload(BaseModel):
    iq_real: List[float]
    iq_imag: List[float]


class IntelligenceBatchIQPayload(BaseModel):
    windows: List[IntelligenceIQPayload]


class AutoActionPayload(BaseModel):
    protocol_name: str
    action: str = "arm_recording"
    duration_after: float = 0.75
    trigger_power_dbm: float | None = None
    hysteresis_db: float = 3.0
    cooldown_seconds: float = 1.0


class SigintTargetPayload(BaseModel):
    label: str
    center_frequency: float | None = None
    tolerance_hz: float = 25_000
    modulation_type: str | None = None
    protocol_name: str | None = None


# Optional reference to a running PassiveMonitor instance.  The monitor is now
# created lazily on START and fully released on STOP to avoid holding SDR/USB
# resources while idle.
monitor: Optional[object] = None
monitor_factory: Optional[Callable[[], object]] = None
monitor_runtime_config: dict[str, Any] = {}

# Current SDR configuration.  When ``monitor`` is running these values are
# kept in sync with the hardware via its setter methods.
config_state: dict[str, float | int | bool | str | None] = {
    "center_freq": None,
    "samp_rate": None,
    "fft_size": 1024,
    "gain": None,
    "hopping_enabled": False,
    "alert_threshold": None,
    "runtime_mode": "demo",
    "hardware_detected": False,
    "usb_access_ok": False,
    "data_bridge": "zmq",
}

# Discrete sample-rate values supported by the UI slider and recommended for
# stable operation across common SDR devices.
ALLOWED_SAMPLE_RATES = {
    1_000_000.0,
    2_000_000.0,
    5_000_000.0,
    10_000_000.0,
    20_000_000.0,
}

SDR_CORE_CMD_SOCKET = "/tmp/sdr_core_cmd.sock"
SHM_PATH = "/dev/shm/bladeeye_buffer"
WATCHDOG_CHECK_INTERVAL = 0.5
WATCHDOG_STALE_SECONDS = 1.0
SHM_RING_VERSION = 1
SHM_STARTUP_PROBE_TIMEOUT_SECONDS = 3.0
SHM_STARTUP_POLL_INTERVAL_SECONDS = 0.1
DEFAULT_ZMQ_SPECTRUM_ENDPOINT = "tcp://127.0.0.1:5557"


RING_CONTROL_FORMAT = "<IIQQ"
SPECTRUM_HEADER_FORMAT = "<QQQIIIIfffI"
RING_CONTROL_SIZE = struct.calcsize(RING_CONTROL_FORMAT)
SPECTRUM_HEADER_SIZE = struct.calcsize(SPECTRUM_HEADER_FORMAT)
SPECTRUM_BINS = 2048
MAX_PEAKS = 64
FRAME_SIZE = SPECTRUM_HEADER_SIZE + (SPECTRUM_BINS * 4) + (MAX_PEAKS * 2 * 4)
EXPECTED_RING_CONTROL_SIZE = 24
EXPECTED_SPECTRUM_HEADER_SIZE = 56


def _write_plain_startup_debug(message: str) -> None:
    """Write startup diagnostics without depending on logging bootstrap."""
    try:
        logs_dir = Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        with open(logs_dir / "debug_startup.txt", "a", encoding="utf-8") as fh:
            ts = datetime.datetime.utcnow().isoformat()
            fh.write(f"[{ts}Z] {message}\n")
    except Exception:
        pass


def _read_latest_sdr_core_frame() -> tuple[dict, np.ndarray]:
    """Read latest committed shared-memory slot from the SDR core ring."""
    if not os.path.exists(SHM_PATH):
        raise FileNotFoundError("Shared memory segment not initialized")

    with open(SHM_PATH, "rb") as fh:
        with mmap.mmap(fh.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
            if mm.size() < RING_CONTROL_SIZE + FRAME_SIZE:
                raise RuntimeError("Shared memory segment too small")
            for _ in range(3):
                version, slot_count, _, committed = struct.unpack_from(RING_CONTROL_FORMAT, mm, 0)
                if version != SHM_RING_VERSION:
                    raise RuntimeError(f"Unsupported shared memory version: {version}")
                if slot_count <= 0:
                    raise RuntimeError("Invalid slot_count in shared memory")
                expected_ring_size = RING_CONTROL_SIZE + (slot_count * FRAME_SIZE)
                if mm.size() != expected_ring_size:
                    raise RuntimeError(
                        "Shared memory size mismatch: "
                        f"actual={mm.size()} expected={expected_ring_size} "
                        f"(slot_count={slot_count}, frame_size={FRAME_SIZE})"
                    )
                index = committed % slot_count
                offset = RING_CONTROL_SIZE + index * FRAME_SIZE
                if offset + FRAME_SIZE > mm.size():
                    raise RuntimeError("Shared memory slot offset outside mapped region")
                header_values = struct.unpack_from(SPECTRUM_HEADER_FORMAT, mm, offset)
                (
                    frame_id,
                    center_freq,
                    last_heartbeat,
                    sample_rate,
                    analog_bandwidth,
                    peak_count,
                    dropped_samples,
                    buffer_fill_percent,
                    processing_latency_ms,
                    cpu_usage,
                    state,
                ) = header_values
                if state != 2:
                    raise RuntimeError("Latest shared-memory slot is not ready")
                spectrum_offset = offset + SPECTRUM_HEADER_SIZE
                spectrum = np.frombuffer(mm, dtype="<f4", count=SPECTRUM_BINS, offset=spectrum_offset).copy()
                _, _, _, committed_after = struct.unpack_from(RING_CONTROL_FORMAT, mm, 0)
                if committed_after == committed:
                    break
            else:
                raise RuntimeError("Shared-memory commit moved while reading frame")

    return (
        {
            "state": int(state),
            "frame_id": int(frame_id),
            "sample_rate": int(sample_rate),
            "analog_bandwidth": int(analog_bandwidth),
            "center_freq": int(center_freq),
            "peak_count": int(peak_count),
            "last_heartbeat": int(last_heartbeat),
            "dropped_samples": int(dropped_samples),
            "buffer_fill_percent": float(buffer_fill_percent),
            "processing_latency_ms": float(processing_latency_ms),
            "cpu_usage": float(cpu_usage),
        },
        spectrum,
    )


def _read_sdr_core_header() -> dict:
    """Read health/header fields exported by the C++ SDR core shared memory."""
    header, _ = _read_latest_sdr_core_frame()
    return header


def _safe_startup_probe() -> None:
    """Validate SHM structure sizes and optionally probe mapped memory."""
    try:
        if RING_CONTROL_SIZE != EXPECTED_RING_CONTROL_SIZE:
            raise RuntimeError(
                f"SharedRingControl mismatch: python={RING_CONTROL_SIZE} expected={EXPECTED_RING_CONTROL_SIZE}"
            )
        if SPECTRUM_HEADER_SIZE != EXPECTED_SPECTRUM_HEADER_SIZE:
            raise RuntimeError(
                f"SharedSpectrumHeader mismatch: python={SPECTRUM_HEADER_SIZE} expected={EXPECTED_SPECTRUM_HEADER_SIZE}"
            )
        if os.path.exists(SHM_PATH):
            deadline = time.monotonic() + SHM_STARTUP_PROBE_TIMEOUT_SECONDS
            while True:
                try:
                    _read_latest_sdr_core_frame()
                    break
                except RuntimeError as exc:
                    if "Latest shared-memory slot is not ready" not in str(exc):
                        raise
                    if time.monotonic() >= deadline:
                        # Legitimate case: C++ core is up but stream has not been
                        # started yet (no ready frame to read). Defer hard-failure
                        # until the health endpoint is actually queried.
                        _write_plain_startup_debug(
                            "SHM probe deferred: shared-memory slot not ready yet "
                            f"after {SHM_STARTUP_PROBE_TIMEOUT_SECONDS:.1f}s "
                            "(expected before first START)"
                        )
                        log.info(
                            "SHM startup probe deferred: no ready slot yet after %.1fs",
                            SHM_STARTUP_PROBE_TIMEOUT_SECONDS,
                        )
                        return
                    time.sleep(SHM_STARTUP_POLL_INTERVAL_SECONDS)
    except Exception as exc:
        _write_plain_startup_debug(f"SHM probe failed: {exc!r}")
        log.exception("SHM startup probe failed")


def _recover_sdr_core() -> None:
    """Best-effort restart path when watchdog detects stale heartbeat."""
    proc_name = "sdr_core"
    for proc in ("sdr_core", "sdr_core.bin"):
        try:
            subprocess.run(["pkill", "-9", "-f", proc], check=False)
            proc_name = proc
        except Exception:
            pass
    try:
        subprocess.run(["usbreset", "bladeRF"], check=False)
    except Exception:
        pass
    try:
        subprocess.Popen([proc_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        log.exception("Watchdog could not restart %s", proc_name)


async def _watchdog_loop() -> None:
    """Check heartbeat and trigger auto-recovery when SDR core stalls."""
    while True:
        await asyncio.sleep(WATCHDOG_CHECK_INTERVAL)
        try:
            health = _read_sdr_core_header()
            heartbeat = health["last_heartbeat"]
            if heartbeat <= 0:
                continue
            if time.time() - heartbeat > WATCHDOG_STALE_SECONDS:
                log.error("SDR core heartbeat stale; triggering recovery")
                _recover_sdr_core()
        except FileNotFoundError:
            continue
        except Exception:
            log.exception("Watchdog failure while evaluating SDR core heartbeat")


def _push_sample_rate_command(value: float) -> None:
    """Best-effort push-down command to the C++ SDR core via Unix socket."""
    payload = f"SET_BW:{int(value)}".encode("ascii")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, SDR_CORE_CMD_SOCKET)
    except OSError:
        # Optional integration path: API remains functional if C++ core is absent.
        log.debug("C++ command socket unavailable at %s", SDR_CORE_CMD_SOCKET)
    finally:
        sock.close()


def _push_core_command(command: str) -> None:
    """Send START/STOP commands to C++ core socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(command.encode("ascii"), SDR_CORE_CMD_SOCKET)
    except OSError:
        log.debug("C++ command socket unavailable at %s", SDR_CORE_CMD_SOCKET)
    finally:
        sock.close()


def _validate_sample_rate(value: float) -> float:
    """Validate and normalize sample-rate values to a stable discrete set."""
    if value in ALLOWED_SAMPLE_RATES:
        return value
    raise HTTPException(
        status_code=422,
        detail={
            "message": "Unsupported sample rate. Use one of the discrete stable values.",
            "allowed_values_hz": sorted(ALLOWED_SAMPLE_RATES),
        },
    )


def _apply_sample_rate(value: float) -> None:
    """Apply sample rate to config and monitor, then flush stale buffers."""
    _push_sample_rate_command(value)
    config_state["samp_rate"] = value
    if monitor is not None and hasattr(monitor, "set_sample_rate"):
        monitor.set_sample_rate(value)
    if monitor is not None and hasattr(monitor, "flush_buffers"):
        monitor.flush_buffers()


def publish_alert(alert: dict) -> None:
    """Push an alert notification to all subscribers."""
    for queue in list(alert_subscribers):
        try:
            queue.put_nowait(alert)
        except Exception:
            pass


def _serialize_execution_task(task: ExecutionTask) -> dict:
    """Return JSON-ready task payload."""
    return {
        "id": task.id,
        "phase": task.phase,
        "title": task.title,
        "description": task.description,
        "owner": task.owner,
        "status": task.status,
        "acceptance_criteria": task.acceptance_criteria,
        "notes": task.notes,
    }


def _serialize_execution_board(board: ExecutionBoard) -> dict:
    """Return JSON-ready execution board payload."""
    return {
        "version": board.version,
        "board_name": board.board_name,
        "updated_at": board.updated_at,
        "tasks": [_serialize_execution_task(task) for task in board.tasks],
    }


def _sync_firmware_warning_on_execution_board() -> None:
    """Project preflight firmware warnings onto the hardware task notes."""
    global execution_board, preflight_status
    if execution_board is None or preflight_status is None:
        return
    hardware_task = next((task for task in execution_board.tasks if task.id == "P1-T1"), None)
    if hardware_task is None:
        return
    warning = preflight_status.firmware_warning
    base_notes = hardware_task.notes.split("\nWARNING: firmware ")[0].rstrip()
    if warning:
        hardware_task.notes = f"{base_notes}\nWARNING: firmware {warning}".strip()
    else:
        hardware_task.notes = base_notes
    save_execution_board(EXECUTION_BOARD_FILE, execution_board)


def _apply_rf_signature_match(sig: Signal) -> None:
    """Annotate signal with RF signature match from pulse timings."""
    short_pulse = getattr(sig, "short_pulse", None)
    long_pulse = getattr(sig, "long_pulse", None)
    if short_pulse is None or long_pulse is None:
        return

    match = match_rf_signature(short_pulse, long_pulse, tolerance=0.10)
    if match is not None:
        detected_name = str(match.get("name"))
        sig.label = detected_name
        sig.likely_purpose = detected_name
        sig.detection_status = f"Detected: {detected_name}"
        return

    gap = getattr(sig, "gap", None)
    raw_params = f"short_pulse={short_pulse}, long_pulse={long_pulse}"
    if gap is not None:
        raw_params += f", gap={gap}"
    sig.detection_status = f"Puls detectat: {short_pulse}/{long_pulse} | Unknown Signal ({raw_params})"


def _remember_signals(new_signals: List[Signal]) -> None:
    """Persist latest detections in a circular in-memory buffer."""
    if new_signals:
        _mark_ai_activity()
    _schedule_live_intelligence(new_signals)
    for sig in new_signals:
        _apply_rf_signature_match(sig)
        if getattr(sig, "likely_purpose", None) is None:
            sig.likely_purpose = identify_signal(sig)
        _apply_auto_actions(sig)
        recent_signals.append(sig)
        _capture_sigint_event(sig)
    signals.clear()
    signals.extend(list(recent_signals))


def _signal_needs_live_intelligence(sig: Signal) -> bool:
    """Return ``True`` when live classify should enrich a signal."""
    if getattr(sig, "protocol_name", None):
        return False
    if getattr(sig, "modulation_type", None) and getattr(sig, "baud_rate", None):
        return False
    return True


def _build_live_intel_batch(new_signals: List[Signal]) -> _LiveIntelBatch | None:
    """Build a bounded IQ batch for live scatter-gather intelligence."""
    if not new_signals:
        return None
    if monitor is None or not hasattr(monitor, "get_iq_export"):
        return None

    candidates: list[Signal] = []
    windows: list[np.ndarray] = []
    for sig in new_signals:
        if len(candidates) >= _LIVE_INTEL_BATCH_SIZE:
            break
        if not _signal_needs_live_intelligence(sig):
            continue
        frequency = getattr(sig, "center_frequency", None)
        if frequency is None:
            continue
        try:
            iq = np.asarray(monitor.get_iq_export(float(frequency)), dtype=np.complex64)
        except Exception:
            continue
        if iq.size == 0:
            continue
        candidates.append(sig)
        windows.append(iq)
    if not candidates:
        return None
    return _LiveIntelBatch(signals=candidates, iq_windows=windows)


def _schedule_live_intelligence(new_signals: List[Signal]) -> None:
    """Thread-safe enqueue for async live intelligence worker."""
    batch = _build_live_intel_batch(new_signals)
    if batch is None:
        return
    if _live_intel_event_loop is None or _live_intel_queue is None:
        return

    def _enqueue() -> None:
        if _live_intel_queue is None:
            return
        if _live_intel_queue.full():
            try:
                _live_intel_queue.get_nowait()
                _live_intel_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        try:
            _live_intel_queue.put_nowait(batch)
        except asyncio.QueueFull:
            pass

    _live_intel_event_loop.call_soon_threadsafe(_enqueue)


def _estimate_signal_confidence(sig: Signal) -> float:
    raw = getattr(sig, "confidence", None)
    try:
        if raw is not None:
            return max(0.0, min(1.0, float(raw)))
    except Exception:
        pass
    if getattr(sig, "protocol_name", None):
        return 0.9
    if getattr(sig, "modulation_type", None) and getattr(sig, "baud_rate", None):
        return 0.75
    return 0.4


def _capture_sigint_event(sig: Signal) -> None:
    confidence = _estimate_signal_confidence(sig)
    if confidence < 0.7:
        return
    payload = getattr(sig, "label", None) or getattr(sig, "likely_purpose", None)
    event = SigintEvent(
        timestamp=float(getattr(sig, "end_time", None) or time.time()),
        center_frequency=float(getattr(sig, "center_frequency", 0.0)),
        bandwidth=float(getattr(sig, "bandwidth", 0.0)),
        rssi_db=float(getattr(sig, "peak_power", 0.0)),
        modulation_type=getattr(sig, "modulation_type", None),
        baud_rate=getattr(sig, "baud_rate", None),
        protocol_name=getattr(sig, "protocol_name", None),
        decoded_payload=str(payload) if payload else None,
        confidence=confidence,
        sync_word=getattr(sig, "sync_word", None),
    )
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon(sigint_store.enqueue, event)
    except RuntimeError:
        # If no active loop is available (tests / worker threads), persist directly.
        sigint_store.ingest_now(event)


def _apply_auto_actions(sig: Signal) -> None:
    """Execute deterministic rule-actions for high-confidence matches."""
    for rule in auto_actions:
        protocol_name = rule.get("protocol_name")
        if protocol_name and getattr(sig, "protocol_name", None) != protocol_name:
            continue
        trigger_power = rule.get("trigger_power_dbm")
        hysteresis_db = float(rule.get("hysteresis_db", 3.0))
        cooldown_seconds = float(rule.get("cooldown_seconds", 1.0))
        key = f"{rule.get('action')}::{protocol_name or '*'}::{round(float(sig.center_frequency), 1)}"
        is_active = _auto_action_state.get(key, False)

        if trigger_power is not None:
            try:
                threshold = float(trigger_power)
                signal_power = float(getattr(sig, "peak_power", -999.0))
            except Exception:
                continue
            if is_active and signal_power < (threshold - hysteresis_db):
                _auto_action_state[key] = False
                is_active = False
            if signal_power < threshold:
                continue
            _auto_action_state[key] = True

        now = time.time()
        if now - _last_action_trigger_at.get(key, 0.0) < cooldown_seconds:
            continue
        if rule.get("action") == "arm_recording" and monitor is not None and hasattr(monitor, "arm_recording"):
            try:
                monitor.arm_recording(
                    sig.center_frequency,
                    duration_after=float(rule.get("duration_after", 0.5)),
                )
                _last_action_trigger_at[key] = now
            except Exception:
                log.exception("Failed auto action arm_recording for %s", sig.center_frequency)


def _bind_monitor_analysis_callback() -> None:
    """Attach monitor callback so detections update API signal storage."""
    if monitor is None or not hasattr(monitor, "set_analysis_callback"):
        return
    try:
        monitor.set_analysis_callback(_remember_signals)
    except Exception:
        log.exception("Failed to bind monitor analysis callback")


def _session_path(name: str) -> Path:
    """Return the filesystem path for a given session *name*."""
    if not name.endswith(".json"):
        name += ".json"
    return SESSIONS_DIR / name


def _build_signal(payload: SignalPayload) -> Signal:
    """Construct a ``Signal`` while ignoring unsupported payload fields."""
    data = payload.model_dump()
    params = inspect.signature(Signal).parameters
    accepted = {name for name in params if name != "self"}
    filtered = {key: value for key, value in data.items() if key in accepted}
    return Signal(**filtered)


def _plot_timeseries(x: List[float], y: List[float], xlabel: str, ylabel: str) -> str:
    """Return a base64 PNG for the given series."""
    fig, ax = plt.subplots()
    ax.plot(x, y)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _prepare_report_context(name: str) -> dict:
    """Collect data for report rendering."""
    path = _session_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh) or {}
    signals_data = data.get("signals", [])
    for sig in signals_data:
        freq = sig.get("center_frequency")
        power = get_signal_power_history(freq)
        sig["power_chart"] = _plot_timeseries(
            power.get("times", []),
            power.get("powers", []),
            "Time",
            "Power",
        )
        trace = get_frequency_trace(freq)
        sig["frequency_chart"] = _plot_timeseries(
            trace.get("times", []),
            trace.get("frequencies", []),
            "Time",
            "Frequency",
        )
    snapshot = path.with_suffix(".png")
    waterfall_b64 = None
    if snapshot.exists():
        with open(snapshot, "rb") as fh:
            waterfall_b64 = base64.b64encode(fh.read()).decode("ascii")
    return {
        "name": path.stem,
        "saved_at": datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        "signals": signals_data,
        "watchlist": data.get("watchlist", []),
        "recordings": data.get("recordings", []),
        "waterfall": waterfall_b64,
    }



@app.get("/api/signatures")
def list_signature_catalog() -> list[dict]:
    """Return all known RF signatures (built-in + user-captured)."""
    return all_rf_signatures()


@app.post("/api/signatures/capture")
def capture_signature(payload: CaptureToSignaturePayload) -> dict:
    """Capture unknown pulse timings as a new user signature."""
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="name cannot be empty")
    item = capture_to_signature(
        name=payload.name,
        short_pulse=payload.short_pulse,
        long_pulse=payload.long_pulse,
        gap=payload.gap,
        modulation=payload.modulation,
        file_name=payload.file_name,
    )
    return {"status": "saved", "signature": item}


@app.get("/api/signals")
def get_signals() -> List[dict]:
    """Return currently detected signals in a JSON-friendly format."""
    now = time.time()
    result = []
    source = list(recent_signals) if recent_signals else signals
    for idx, sig in enumerate(source):
        duration = (sig.end_time or now) - sig.start_time
        result.append({
            "id": idx,
            "center_frequency": sig.center_frequency,
            "modulation_type": sig.modulation_type,
            "baud_rate": sig.baud_rate,
            "signal_strength": sig.peak_power,
            "duration": duration,
            "likely_purpose": sig.likely_purpose,
            "label": getattr(sig, "label", None),
            "protocol": getattr(sig, "protocol", None),
            "short_pulse": getattr(sig, "short_pulse", None),
            "long_pulse": getattr(sig, "long_pulse", None),
            "gap": getattr(sig, "gap", None),
            "detection_status": getattr(sig, "detection_status", None),
        })
    return result


def _build_iq_stream(data: np.ndarray | list | tuple, filename: str) -> StreamingResponse:
    """Convert I/Q values to complex64 bytes and return as a downloadable stream."""
    buf = io.BytesIO()
    buf.write(np.array(data, dtype=np.complex64).tobytes())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/session/recover")
def recover_session() -> dict:
    """Return autosaved session data if available and clear it."""
    global _recovery_cache
    if _recovery_cache is None:
        return {}
    data = _recovery_cache
    _recovery_cache = None
    AUTOSAVE_FILE.unlink(missing_ok=True)
    return data


@app.get("/api/sessions")
def list_sessions() -> List[str]:
    """Return a list of available session JSON filenames."""
    SESSIONS_DIR.mkdir(exist_ok=True)
    intelligence_engine = IntelligenceEngine(BASE_DIR / "backend" / "signatures.json")
    return sorted([p.name for p in SESSIONS_DIR.glob("*.json")])


@app.get("/api/sessions/{name}")
def load_session(name: str) -> dict:
    """Load a session from disk and populate the in-memory signal list."""
    path = _session_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh) or {}
    loaded_signals = [Signal(**item) for item in data.get("signals", [])]
    for sig in loaded_signals:
        _apply_rf_signature_match(sig)
        if getattr(sig, "likely_purpose", None) is None:
            sig.likely_purpose = identify_signal(sig)
    signals.clear()
    signals.extend(loaded_signals)
    recent_signals.clear()
    recent_signals.extend(loaded_signals[-50:])
    watchlist.clear()
    watchlist.extend(data.get("watchlist", []))
    recordings.clear()
    recordings.extend(data.get("recordings", []))
    return {
        "signals": [asdict(sig) for sig in loaded_signals],
        "watchlist": watchlist,
        "recordings": recordings,
    }


@app.post("/api/sessions/{name}")
def save_session(name: str, session: SessionPayload) -> dict:
    """Persist a session to disk."""
    SESSIONS_DIR.mkdir(exist_ok=True)
    path = _session_path(name)
    sig_objs: list[Signal] = []
    for payload in session.signals:
        sig = Signal(**payload.model_dump())
        _apply_rf_signature_match(sig)
        if getattr(sig, "likely_purpose", None) is None:
            sig.likely_purpose = identify_signal(sig)
        sig_objs.append(sig)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "signals": [asdict(s) for s in sig_objs],
                "watchlist": session.watchlist or [],
                "recordings": [r.model_dump() for r in session.recordings or []],
            },
            fh,
            indent=2,
        )
    signals.clear()
    signals.extend(sig_objs)
    recent_signals.clear()
    recent_signals.extend(sig_objs[-50:])
    watchlist.clear()
    watchlist.extend(session.watchlist or [])
    recordings.clear()
    recordings.extend([r.model_dump() for r in session.recordings or []])
    return {"status": "ok"}


def _sync_monitor_from_config(m: object) -> None:
    """Apply current API config values to a newly created monitor."""
    if m is None:
        return
    center = config_state.get("center_freq")
    if center is not None and hasattr(m, "set_center_freq"):
        m.set_center_freq(float(center))
    sample_rate = config_state.get("samp_rate")
    if sample_rate is not None and hasattr(m, "set_sample_rate"):
        m.set_sample_rate(float(sample_rate))
    fft_size = config_state.get("fft_size")
    if fft_size is not None and hasattr(m, "set_fft_size"):
        m.set_fft_size(int(fft_size))
    gain = config_state.get("gain")
    if gain is not None and hasattr(m, "set_gain"):
        m.set_gain(float(gain))
    alert_threshold = config_state.get("alert_threshold")
    if alert_threshold is not None:
        if hasattr(m, "set_alert_threshold"):
            m.set_alert_threshold(float(alert_threshold))
        else:
            setattr(m, "alert_threshold", float(alert_threshold))


def _ensure_monitor() -> object | None:
    """Create monitor lazily only when the user starts scanning."""
    global monitor
    if monitor is not None:
        return monitor
    if monitor_factory is None:
        return None
    monitor = monitor_factory()
    _sync_monitor_from_config(monitor)
    _bind_monitor_analysis_callback()
    return monitor


def _destroy_monitor() -> None:
    """Release monitor object and best-effort close SDR resources."""
    global monitor
    if monitor is None:
        return
    if hasattr(monitor, "stop_hopping"):
        try:
            monitor.stop_hopping()
        except Exception:
            log.exception("Failed to stop hopping during monitor teardown")
    monitor = None


@app.post("/api/scan/start")
def start_scan() -> dict:
    """Start or resume the SDR monitor."""
    runtime_monitor = _ensure_monitor()
    monitor_running = bool(runtime_monitor is not None and getattr(runtime_monitor, "is_running", False))
    if monitor_running:
        raise HTTPException(status_code=409, detail="Monitor already running")
    log.info("Received start scan command")
    if runtime_monitor is not None:
        if hasattr(runtime_monitor, "start"):
            runtime_monitor.start()
        elif hasattr(runtime_monitor, "resume"):
            runtime_monitor.resume()
        setattr(runtime_monitor, "is_running", True)
    _push_core_command("START")
    return {"is_running": True}


@app.post("/api/scan/stop")
def stop_scan() -> dict:
    """Stop the SDR monitor and release hardware resources."""
    monitor_running = bool(monitor is not None and getattr(monitor, "is_running", False))
    if monitor is None and not os.path.exists(SDR_CORE_CMD_SOCKET):
        raise HTTPException(status_code=409, detail="Monitor not running")
    if monitor is not None and not monitor_running:
        raise HTTPException(status_code=409, detail="Monitor not running")
    log.info("Received stop scan command")
    if monitor is not None:
        if hasattr(monitor, "stop"):
            monitor.stop()
            if hasattr(monitor, "wait"):
                monitor.wait()
        elif hasattr(monitor, "halt"):
            monitor.halt()
        setattr(monitor, "is_running", False)
    _destroy_monitor()
    _push_core_command("STOP")
    return {"is_running": False}


@app.get("/api/protocols")
def list_protocols() -> List[dict]:
    """Return built-in and user-defined protocol definitions."""

    builtins = [
        {
            "protocol_name": p.name,
            "modulation_type": None,
            "baud_rate": None,
            "header_pattern": p.header,
            "data_field_structure": p.fields,
        }
        for p in PROTOCOLS
    ]
    users = [asdict(p) for p in load_user_protocols()]
    return builtins + users


@app.post("/api/protocols/add_manual")
def add_manual_protocol(payload: UserProtocolPayload) -> dict:
    """Persist a user-defined protocol definition."""

    proto = UserProtocol(**payload.model_dump())
    save_user_protocol(proto)
    return {"status": "ok"}


@app.get("/api/patterns")
def get_patterns() -> dict:
    """Return all stored bit patterns."""
    return load_patterns()


@app.post("/api/patterns/{name}")
def add_pattern(name: str, payload: PatternPayload) -> dict:
    """Learn and store a pattern under ``name``."""
    pattern = save_pattern(name, payload.bitstrings)
    return pattern


@app.put("/api/patterns/{name}")
def rename_pattern(name: str, payload: PatternRenamePayload) -> dict:
    """Rename an existing pattern."""
    patterns = load_patterns()
    if name not in patterns:
        raise HTTPException(status_code=404, detail="Pattern not found")
    if payload.new_name in patterns:
        raise HTTPException(status_code=400, detail="Pattern already exists")
    pattern = patterns.pop(name)
    patterns[payload.new_name] = pattern
    PATTERN_FILE.parent.mkdir(exist_ok=True)
    with open(PATTERN_FILE, "w", encoding="utf-8") as fh:
        json.dump(patterns, fh, indent=2)
    return pattern


@app.delete("/api/patterns/{name}")
def delete_pattern(name: str) -> dict:
    """Delete a stored pattern."""
    patterns = load_patterns()
    if name not in patterns:
        raise HTTPException(status_code=404, detail="Pattern not found")
    pattern = patterns.pop(name)
    PATTERN_FILE.parent.mkdir(exist_ok=True)
    with open(PATTERN_FILE, "w", encoding="utf-8") as fh:
        json.dump(patterns, fh, indent=2)
    return pattern


@app.get("/api/sessions/{name}/report")
def session_report(name: str) -> HTMLResponse:
    """Render an HTML report for session *name*."""
    ctx = _prepare_report_context(name)
    template = jinja_env.get_template("session_report.html")
    html = template.render(**ctx)
    return HTMLResponse(content=html, media_type="text/html")


@app.get("/api/sessions/{name}/report.pdf")
def session_report_pdf(name: str) -> Response:
    """Render a PDF report for session *name*."""
    ctx = _prepare_report_context(name)
    template = jinja_env.get_template("session_report.html")
    html = template.render(**ctx)
    pdf_bytes = HTML(string=html).write_pdf()
    filename = f"{_session_path(name).stem}_report.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/session")
def get_session() -> dict:
    """Return the current in-memory session."""
    return {
        "signals": [asdict(sig) for sig in signals],
        "watchlist": watchlist,
        "recordings": recordings,
    }


@app.post("/api/session")
def set_session(session: SessionPayload) -> dict:
    """Replace the in-memory session with ``session``."""
    signals.clear()
    sig_objs: list[Signal] = []
    for payload in session.signals:
        sig = _build_signal(payload)
        _apply_rf_signature_match(sig)
        if getattr(sig, "likely_purpose", None) is None:
            sig.likely_purpose = identify_signal(sig)
        sig_objs.append(sig)
    signals.extend(sig_objs)
    recent_signals.clear()
    recent_signals.extend(sig_objs[-50:])
    watchlist.clear()
    watchlist.extend(session.watchlist or [])
    recordings.clear()
    recordings.extend([r.model_dump() for r in session.recordings or []])
    return {"status": "ok"}


@app.post("/api/signals/{frequency}/record")
def arm_signal_recording(frequency: float, duration_after: float = 0.2) -> dict:
    """Arm the monitor to record the next burst at ``frequency``."""
    if monitor is None or not hasattr(monitor, "arm_recording"):
        raise HTTPException(status_code=503, detail="Monitor not running")
    monitor.arm_recording(frequency, duration_after=duration_after)
    return {"status": "armed"}


@app.delete("/api/signals/{frequency}/record")
def cancel_signal_recording(frequency: float) -> dict:
    """Cancel a previously armed recording for ``frequency``."""
    if monitor is None or not hasattr(monitor, "cancel_recording"):
        raise HTTPException(status_code=503, detail="Monitor not running")
    monitor.cancel_recording(frequency)
    return {"status": "canceled"}


@app.post("/api/signals/{frequency}/decode")
def decode_signal_recording(
    frequency: float,
    low_cut: float | None = None,
    high_cut: float | None = None,
    order: int | None = None,
) -> dict:
    """Decode the latest recording for ``frequency``.

    The function looks for the most recent entry in the global ``recordings``
    list that matches ``frequency`` and attempts to decode it using the
    :class:`backend.decoder.Decoder` class.  The decoded bit string along with
    hexadecimal and ASCII representations is returned.
    """

    rec = next((r for r in reversed(recordings) if r.get("freq") == frequency), None)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    path = Path(rec["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Recording file missing")

    sig = next((s for s in signals if getattr(s, "center_frequency", None) == frequency), None)
    meta: dict[str, float | str | None] = {
        "low_cut": low_cut,
        "high_cut": high_cut,
        "order": order,
    }
    if sig is not None:
        meta.update(
            {
                "modulation_type": getattr(sig, "modulation_type", None),
                "baud_rate": getattr(sig, "baud_rate", None),
            }
        )
    decoder = Decoder(path, meta)
    samp_rate = float(config_state.get("samp_rate") or 1)
    data = decoder.decode(samp_rate)
    label = find_label(data["binary"])
    if sig is not None and label is not None:
        setattr(sig, "label", label)
    data["label"] = label
    proto = identify_protocol(data["binary"])
    if sig is not None and proto is not None:
        setattr(sig, "protocol", proto)
    data["protocol"] = proto
    return data


@app.get("/api/signals/{frequency}/power")
def get_signal_power_history(frequency: float) -> dict:
    """Return power samples over time for *frequency*."""
    if monitor is not None and hasattr(monitor, "get_power_history"):
        data = monitor.get_power_history(frequency)
        times = [t for t, _ in data]
        powers = [p for _, p in data]
        return {"times": times, "powers": powers}
    times = list(range(10))
    powers = np.random.random(10).tolist()
    return {"times": times, "powers": powers}


@app.get("/api/signals/{frequency}/baud")
def get_signal_baud_metrics(frequency: float) -> dict:
    """Return baud rate stability information for *frequency*."""
    if monitor is not None and hasattr(monitor, "get_baud_rate_histogram"):
        return monitor.get_baud_rate_histogram(frequency)
    hist, bins = np.histogram(np.random.normal(1000, 100, 100), bins=10)
    return {"hist": hist.tolist(), "bins": bins.tolist()}


@app.get("/api/signals/{frequency}/deviation")
def get_frequency_deviation(frequency: float) -> dict:
    """Return frequency deviation samples for *frequency*."""
    if monitor is not None and hasattr(monitor, "get_frequency_deviation"):
        data = monitor.get_frequency_deviation(frequency)
        times = [t for t, _ in data]
        deviations = [d for _, d in data]
        return {"times": times, "deviations": deviations}
    times = list(range(10))
    deviations = np.zeros(10).tolist()
    return {"times": times, "deviations": deviations}


@app.get("/api/signals/{frequency}/trace")
def get_frequency_trace(frequency: float) -> dict:
    """Return frequency vs time samples for *frequency*."""
    if monitor is not None and hasattr(monitor, "get_frequency_track"):
        data = monitor.get_frequency_track(frequency)
        times = [t for t, _ in data]
        freqs = [f for _, f in data]
        return {"times": times, "frequencies": freqs}
    times = list(range(10))
    freqs = (np.ones(10) * frequency + np.random.random(10) * 100).tolist()
    return {"times": times, "frequencies": freqs}


def _build_iq_array(payload: IntelligenceIQPayload) -> np.ndarray:
    """Validate IQ payload parts and return a complex64 array."""
    if len(payload.iq_real) != len(payload.iq_imag):
        raise HTTPException(status_code=422, detail="iq_real and iq_imag must have equal length")
    if not payload.iq_real:
        raise HTTPException(status_code=422, detail="IQ payload cannot be empty")
    return np.asarray(payload.iq_real, dtype=np.float32) + 1j * np.asarray(payload.iq_imag, dtype=np.float32)


@app.post("/api/intelligence/classify")
async def classify_iq(payload: IntelligenceIQPayload) -> dict:
    """Analyze an IQ window and infer modulation, RSSI, baud and fingerprint."""
    iq = _build_iq_array(payload)
    result = await intelligence_engine.analyze(iq)
    _mark_ai_activity()
    return {
        "modulation_type": result.modulation_type,
        "signal_strength_rssi_db": result.rssi_db,
        "baud_rate": result.baud_rate,
        "likely_purpose": result.likely_purpose,
        "protocol_name": result.protocol_name,
        "confidence": result.confidence,
        "snr_db": result.snr_db,
        "ignored_as_noise": result.ignored_as_noise,
    }


@app.post("/api/intelligence/classify-batch")
async def classify_iq_batch(payload: IntelligenceBatchIQPayload) -> dict:
    """Analyze a batch of IQ windows with bounded scatter-gather execution."""
    if not payload.windows:
        raise HTTPException(status_code=422, detail="Batch payload cannot be empty")
    iq_windows = [_build_iq_array(window) for window in payload.windows]
    results = await intelligence_engine.analyze_many(iq_windows)
    for _ in results:
        _mark_ai_activity()
    return {
        "items": [
            {
                "modulation_type": result.modulation_type,
                "signal_strength_rssi_db": result.rssi_db,
                "baud_rate": result.baud_rate,
                "likely_purpose": result.likely_purpose,
                "protocol_name": result.protocol_name,
                "confidence": result.confidence,
                "snr_db": result.snr_db,
                "ignored_as_noise": result.ignored_as_noise,
            }
            for result in results
        ]
    }


@app.post("/api/intelligence/classify-file")
async def classify_iq_file(request: Request, filename: str = Query("uploaded_iq.complex")) -> dict:
    """Analyze uploaded complex64 IQ file in demo/offline workflows."""
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=422, detail="Uploaded IQ file is empty")
    if len(payload) % 8 != 0:
        raise HTTPException(status_code=422, detail="IQ file must contain complex64 samples")
    iq = np.frombuffer(payload, dtype=np.complex64)
    if iq.size == 0:
        raise HTTPException(status_code=422, detail="IQ file has no valid samples")
    result = await intelligence_engine.analyze(iq)
    _mark_ai_activity()
    return {
        "filename": filename,
        "samples": int(iq.size),
        "modulation_type": result.modulation_type,
        "signal_strength_rssi_db": result.rssi_db,
        "baud_rate": result.baud_rate,
        "likely_purpose": result.likely_purpose,
        "protocol_name": result.protocol_name,
        "confidence": result.confidence,
        "snr_db": result.snr_db,
        "ignored_as_noise": result.ignored_as_noise,
    }


@app.get("/api/signals/{frequency}/iq")
def export_signal_iq(frequency: float) -> StreamingResponse:
    """Stream raw I/Q samples for *frequency* as binary data."""
    if monitor is not None and hasattr(monitor, "get_iq_export"):
        data = monitor.get_iq_export(frequency)
    else:
        data = np.zeros(1024, dtype=np.complex64)
    return _build_iq_stream(data, filename=f"iq_{frequency}.complex")


@app.get("/api/signals/{signal_id}/export")
def export_signal_by_id(signal_id: int) -> StreamingResponse:
    """Stream raw I/Q samples by signal list index."""
    source = list(recent_signals) if recent_signals else signals
    if signal_id < 0 or signal_id >= len(source):
        raise HTTPException(status_code=404, detail="Signal not found")
    sig = source[signal_id]
    frequency = getattr(sig, "center_frequency", None)
    if frequency is None:
        raise HTTPException(status_code=400, detail="Signal has no center frequency")
    if monitor is not None and hasattr(monitor, "get_iq_export"):
        data = monitor.get_iq_export(frequency)
    else:
        data = np.zeros(1024, dtype=np.complex64)
    return _build_iq_stream(data, filename=f"signal_{signal_id}.complex")


@app.get("/api/watchlist")
def get_watchlist() -> List[float]:
    """Return the current watchlist."""
    return watchlist


@app.post("/api/watchlist")
def add_watchlist(item: WatchlistItem) -> dict:
    """Append a frequency to the watchlist."""
    if item.frequency not in watchlist:
        watchlist.append(item.frequency)
    return {"status": "ok"}


@app.delete("/api/watchlist/{frequency}")
def remove_watchlist(frequency: float) -> dict:
    """Remove *frequency* from the watchlist."""
    try:
        watchlist.remove(frequency)
    except ValueError as exc:  # pragma: no cover - rare edge case
        raise HTTPException(status_code=404, detail="Frequency not found") from exc
    return {"status": "ok"}


@app.get("/api/sigint/log")
def get_sigint_log(
    limit: int = Query(200, ge=1, le=2000),
    watchlist_only: bool = Query(False),
    frequency: float | None = Query(None),
) -> dict:
    """Return recent SIGINT log rows with optional filtering."""
    rows = sigint_store.fetch_entries(limit=limit, watchlist_only=watchlist_only, frequency=frequency)
    return {"items": rows, "count": len(rows)}


@app.get("/api/sigint/export")
def export_sigint_log(
    format: str = Query("json", pattern="^(json|csv)$"),
    watchlist_only: bool = Query(False),
) -> Response:
    """Export SIGINT log as JSON or CSV."""
    if format == "csv":
        content = sigint_store.export_csv(watchlist_only=watchlist_only)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=sigint_log.csv"},
        )
    content = sigint_store.export_json(watchlist_only=watchlist_only)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=sigint_log.json"},
    )


@app.get("/api/sigint/targets")
def list_sigint_targets() -> list[dict]:
    """Return active watch targets for SIGINT hit detection."""
    return sigint_store.list_targets()


@app.post("/api/sigint/targets")
def create_sigint_target(payload: SigintTargetPayload) -> dict:
    """Create a new SIGINT watch target."""
    return sigint_store.add_target(
        label=payload.label.strip() or "Unnamed target",
        center_frequency=payload.center_frequency,
        tolerance_hz=payload.tolerance_hz,
        modulation_type=payload.modulation_type,
        protocol_name=payload.protocol_name,
    )


@app.delete("/api/sigint/targets/{target_id}")
def delete_sigint_target(target_id: int) -> dict:
    """Delete an existing SIGINT watch target."""
    deleted = sigint_store.delete_target(target_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"status": "deleted", "target_id": target_id}


@app.post("/api/hopping")
def set_hopping(cfg: HoppingPayload) -> dict:
    """Enable or disable watchlist frequency hopping."""
    config_state["hopping_enabled"] = cfg.enabled
    if monitor is not None:
        if cfg.enabled and hasattr(monitor, "start_hopping"):
            monitor.start_hopping()
        elif not cfg.enabled and hasattr(monitor, "stop_hopping"):
            monitor.stop_hopping()
        if hasattr(monitor, "get_config"):
            return monitor.get_config()
    return config_state


@app.get("/api/actions")
def get_auto_actions() -> dict:
    """Return protocol-triggered automatic action rules."""
    return {"items": auto_actions}


@app.post("/api/actions")
def add_auto_action(payload: AutoActionPayload) -> dict:
    """Add a new protocol-triggered action rule."""
    item = payload.model_dump()
    auto_actions.append(item)
    return item


@app.get("/api/config")
def get_config() -> dict:
    """Return current SDR configuration."""
    if monitor is not None and hasattr(monitor, "get_config"):
        return monitor.get_config()
    return config_state


@app.get("/api/health")
def get_health() -> dict:
    """Return runtime health telemetry exported by active data bridge."""
    if config_state.get("runtime_mode") == "demo":
        telemetry = zmq_consumer.telemetry() if zmq_consumer is not None else {}
        return {
            "healthy": True,
            "mode": "demo",
            "heartbeat_age_seconds": None,
            "dropped_samples": int(telemetry.get("dropped_frames", 0)),
            "buffer_fill_percent": float(telemetry.get("buffer_load_percent", 0.0)),
            "throughput_bps": float(telemetry.get("throughput_bps", 0.0)),
        }
    if zmq_consumer is not None and zmq_consumer.enabled:
        telemetry = zmq_consumer.telemetry()
        stale_for = None
        if telemetry.get("last_frame_ts"):
            stale_for = max(0.0, time.time() - float(telemetry["last_frame_ts"]))
        healthy = stale_for is None or stale_for <= WATCHDOG_STALE_SECONDS
        return {
            "healthy": healthy,
            "mode": "hardware",
            "heartbeat_age_seconds": stale_for,
            "dropped_samples": int(telemetry.get("dropped_frames", 0)),
            "buffer_fill_percent": float(telemetry.get("buffer_load_percent", 0.0)),
            "throughput_bps": float(telemetry.get("throughput_bps", 0.0)),
            "frames_received": int(telemetry.get("frames_received", 0)),
        }
    try:
        header = _read_sdr_core_header()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="SDR core shared memory unavailable")
    except Exception as exc:  # pragma: no cover - system dependent failure
        raise HTTPException(status_code=500, detail=f"Unable to read health metrics: {exc}") from exc

    heartbeat = header["last_heartbeat"]
    stale_for = max(0.0, time.time() - heartbeat) if heartbeat else None
    healthy = stale_for is not None and stale_for <= WATCHDOG_STALE_SECONDS
    return {
        **header,
        "heartbeat_age_seconds": stale_for,
        "healthy": healthy,
    }


@app.get("/api/telemetry")
def get_telemetry() -> dict:
    """Return consolidated telemetry for dashboard widgets."""
    zmq_data = zmq_consumer.telemetry() if zmq_consumer is not None else {"enabled": False}
    return {
        "runtime_mode": config_state.get("runtime_mode"),
        "data_bridge": config_state.get("data_bridge"),
        "buffer_load_percent": float(zmq_data.get("buffer_load_percent", 0.0)),
        "zmq_throughput_bps": float(zmq_data.get("throughput_bps", 0.0)),
        "zmq_fps": float(zmq_data.get("fps", 0.0)),
        "zmq_latency_ms": float(zmq_data.get("latency_ms", 0.0)),
        "dropped_frames": int(zmq_data.get("dropped_frames", 0)),
        "alerts_subscribers": len(alert_subscribers),
        "ai_jobs_processed": _ai_jobs_processed,
        "ai_last_activity_ts": _ai_last_activity_ts or None,
    }


@app.get("/api/logs")
def get_runtime_logs(limit: int = Query(100, ge=1, le=500)) -> dict:
    """Return recent consolidated runtime errors (api + preflight)."""
    return {"items": list(runtime_errors)[-limit:]}


@app.get("/api/logs/export")
def export_runtime_logs() -> StreamingResponse:
    """Export runtime error ring-buffer and file logs into one zip archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("runtime_errors.json", json.dumps({"items": list(runtime_errors)}, indent=2))
        api_log = Path("logs") / "api_error.log"
        if api_log.exists():
            zf.write(api_log, arcname="api_error.log")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="bladeeye_runtime_logs.zip"'},
    )


@app.get("/api/preflight")
def get_preflight_status() -> dict:
    """Expose startup hardware/USB validation and runtime mode selection."""
    global preflight_status
    if preflight_status is None:
        preflight_status = run_preflight()
        _sync_firmware_warning_on_execution_board()
    return {
        "hardware_detected": preflight_status.hardware_detected,
        "usb_access_ok": preflight_status.usb_access_ok,
        "runtime_mode": preflight_status.mode,
        "detail": preflight_status.detail,
        "firmware_version": preflight_status.firmware_version,
        "firmware_warning": preflight_status.firmware_warning,
        "data_bridge": config_state.get("data_bridge"),
    }


@app.get("/api/execution-board")
def get_execution_board() -> dict:
    """Return the current implementation execution board."""
    global execution_board
    if execution_board is None:
        execution_board = load_execution_board(EXECUTION_BOARD_FILE)
    return _serialize_execution_board(execution_board)


@app.patch("/api/execution-board/tasks/{task_id}")
def patch_execution_task(task_id: str, payload: ExecutionTaskPatchPayload) -> dict:
    """Update execution task progress (status/owner/notes)."""
    global execution_board
    if execution_board is None:
        execution_board = load_execution_board(EXECUTION_BOARD_FILE)

    status = payload.status
    if status is not None and status not in {"todo", "in_progress", "blocked", "done"}:
        raise HTTPException(
            status_code=422,
            detail="Invalid status. Allowed: todo, in_progress, blocked, done.",
        )

    try:
        task = update_execution_task(
            execution_board,
            task_id,
            status=status,  # type: ignore[arg-type]
            owner=payload.owner,
            notes=payload.notes,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc

    save_execution_board(EXECUTION_BOARD_FILE, execution_board)
    return _serialize_execution_task(task)


@app.post("/api/config")
def update_config(cfg: ConfigPayload) -> dict:
    """Update SDR configuration parameters on the fly."""
    if cfg.center_freq is not None:
        config_state["center_freq"] = cfg.center_freq
        if monitor is not None and hasattr(monitor, "set_center_freq"):
            monitor.set_center_freq(cfg.center_freq)
    if cfg.samp_rate is not None:
        rate = _validate_sample_rate(cfg.samp_rate)
        _apply_sample_rate(rate)
    if cfg.fft_size is not None:
        config_state["fft_size"] = cfg.fft_size
        if monitor is not None and hasattr(monitor, "set_fft_size"):
            monitor.set_fft_size(cfg.fft_size)
    if cfg.gain is not None:
        log.info("Received gain command: %.2f dB", cfg.gain)
        config_state["gain"] = cfg.gain
        if monitor is not None and hasattr(monitor, "set_gain"):
            monitor.set_gain(cfg.gain)
    if cfg.alert_threshold is not None:
        config_state["alert_threshold"] = cfg.alert_threshold
        if monitor is not None:
            if hasattr(monitor, "set_alert_threshold"):
                monitor.set_alert_threshold(cfg.alert_threshold)
            else:
                setattr(monitor, "alert_threshold", cfg.alert_threshold)
    if monitor is not None and not hasattr(monitor, "alert_callback"):
        try:
            setattr(monitor, "alert_callback", publish_alert)
        except Exception:
            pass
    if monitor is not None and hasattr(monitor, "get_config"):
        return monitor.get_config()
    return config_state


@app.put("/api/config/bandwidth")
def set_bandwidth(value: float = Query(..., description="Sample rate in Hz")) -> dict:
    """Update sample rate (bandwidth) using discrete validated values."""
    rate = _validate_sample_rate(float(value))
    _apply_sample_rate(rate)
    if monitor is not None and hasattr(monitor, "get_config"):
        return monitor.get_config()
    return config_state


@app.websocket("/ws/alerts")
async def alerts_stream(websocket: WebSocket) -> None:
    """Relay alert notifications to connected clients."""
    await websocket.accept()
    log.info("WebSocket alert client connected")
    queue: asyncio.Queue = asyncio.Queue()
    alert_subscribers.add(queue)
    try:
        while True:
            data = await queue.get()
            await websocket.send_json(data)
    except WebSocketDisconnect:
        log.info("WebSocket alert client disconnected")
    finally:
        alert_subscribers.discard(queue)


@app.websocket("/ws/spectrum")
async def spectrum_stream(websocket: WebSocket, fps: int = Query(20, ge=5, le=30)) -> None:
    """Stream FFT power spectra to connected clients.

    The endpoint sends lists of power values. If a ``PassiveMonitor`` instance
    has been attached via the global ``monitor`` variable its ``get_power_spectrum``
    method is used.  Otherwise random noise is emitted, which allows the
    front-end to operate in development without SDR hardware.
    """

    await websocket.accept()
    log.info("WebSocket spectrum client connected")
    fft_size = 1024
    frame_delay = 1.0 / float(fps)
    frame_counter = 0
    try:
        while True:
            try:
                spectrum = None
                if zmq_consumer is not None and zmq_consumer.enabled:
                    spectrum = zmq_consumer.recv_latest()
                    if spectrum is not None:
                        spectrum = np.asarray(spectrum, dtype=float)
                if spectrum is None or spectrum.size == 0 or not np.isfinite(spectrum).all():
                    await asyncio.sleep(frame_delay)
                    continue
                fft_size = spectrum.size
            except Exception:
                if monitor is not None and hasattr(monitor, "get_power_spectrum"):
                    spectrum = np.asarray(monitor.get_power_spectrum(), dtype=float)
                    expected_size = int(getattr(monitor, "fft_size", spectrum.size))
                    if (
                        spectrum.size == 0
                        or expected_size <= 0
                        or spectrum.size != expected_size
                        or not np.isfinite(spectrum).all()
                    ):
                        await asyncio.sleep(frame_delay)
                        continue
                    fft_size = spectrum.size
                else:
                    spectrum = np.random.random(fft_size)
            frame_counter += 1
            send_every = 1
            if zmq_consumer is not None and zmq_consumer.enabled:
                telemetry = zmq_consumer.telemetry()
                if telemetry.get("buffer_load_percent", 0.0) >= 70.0:
                    send_every = 4
                elif telemetry.get("buffer_load_percent", 0.0) >= 40.0:
                    send_every = 2
            if frame_counter % send_every != 0:
                await asyncio.sleep(frame_delay)
                continue
            await websocket.send_json(np.asarray(spectrum, dtype=float).tolist())
            await asyncio.sleep(frame_delay)
    except WebSocketDisconnect:
        # Client disconnected; simply exit the loop
        log.info("WebSocket spectrum client disconnected")
        return
    except (BrokenPipeError, ConnectionResetError, RuntimeError):
        log.info("WebSocket spectrum client stream closed")
        return


@app.websocket("/ws/spectrum/binary")
async def spectrum_stream_binary(websocket: WebSocket, fps: int = Query(20, ge=5, le=30)) -> None:
    """Stream FFT spectra as Float32 binary frames for lower overhead."""
    await websocket.accept()
    log.info("WebSocket binary spectrum client connected")
    fft_size = 1024
    frame_delay = 1.0 / float(fps)
    frame_counter = 0
    try:
        while True:
            try:
                spectrum = None
                if zmq_consumer is not None and zmq_consumer.enabled:
                    spectrum = zmq_consumer.recv_latest()
                if spectrum is not None:
                    spectrum = np.asarray(spectrum, dtype=np.float32)
                if spectrum is None or spectrum.size == 0 or not np.isfinite(spectrum).all():
                    await asyncio.sleep(frame_delay)
                    continue
                fft_size = spectrum.size
            except Exception:
                if monitor is not None and hasattr(monitor, "get_power_spectrum"):
                    spectrum = np.asarray(monitor.get_power_spectrum(), dtype=np.float32)
                    expected_size = int(getattr(monitor, "fft_size", spectrum.size))
                    if (
                        spectrum.size == 0
                        or expected_size <= 0
                        or spectrum.size != expected_size
                        or not np.isfinite(spectrum).all()
                    ):
                        await asyncio.sleep(frame_delay)
                        continue
                    fft_size = spectrum.size
                else:
                    spectrum = np.random.random(fft_size).astype(np.float32)
            frame_counter += 1
            send_every = 1
            if zmq_consumer is not None and zmq_consumer.enabled:
                telemetry = zmq_consumer.telemetry()
                if telemetry.get("buffer_load_percent", 0.0) >= 70.0:
                    send_every = 4
                elif telemetry.get("buffer_load_percent", 0.0) >= 40.0:
                    send_every = 2
            if frame_counter % send_every != 0:
                await asyncio.sleep(frame_delay)
                continue
            await websocket.send_bytes(spectrum.tobytes())
            await asyncio.sleep(frame_delay)
    except WebSocketDisconnect:
        log.info("WebSocket binary spectrum client disconnected")
        return
    except (BrokenPipeError, ConnectionResetError, RuntimeError):
        log.info("WebSocket binary spectrum client stream closed")
        return


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("BLADEEYE_API_HOST", "127.0.0.1"),
        port=int(os.getenv("BLADEEYE_API_PORT", "43101")),
        log_level="info",
    )
