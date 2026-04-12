"""FastAPI backend for exposing SDR monitoring data."""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from collections import deque
from pathlib import Path
from pydantic import BaseModel
from dataclasses import asdict
import ctypes
import mmap
import os
import subprocess
import inspect
import asyncio
import time
import json
import socket
import sys
import numpy as np
import io
import base64
import datetime
import logging
import matplotlib.pyplot as plt
from weasyprint import HTML
from jinja2 import Environment, FileSystemLoader, select_autoescape
from backend.identifier import identify_signal
from backend.decoder import Decoder
from backend.patterns import save_pattern, load_patterns, find_label, PATTERN_FILE
from backend.protocols import (
    identify_protocol,
    PROTOCOLS,
    UserProtocol,
    save_user_protocol,
    load_user_protocols,
)

# ``Signal`` is defined in ``HackRF.passive_monitor`` which pulls in heavy
# dependencies like GNU Radio.  Import it lazily and provide a lightweight
# fallback for environments where those libraries are unavailable (e.g. tests).
try:  # pragma: no cover - fallback exercised in tests
    from HackRF.passive_monitor import Signal
except Exception:  # pragma: no cover
    from dataclasses import dataclass

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

# Directory on disk used for persisting session JSON files.
SESSIONS_DIR = Path("sessions")
AUTOSAVE_FILE = SESSIONS_DIR / "autosave.json"
_recovery_cache: dict | None = None


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
    global _recovery_cache
    if AUTOSAVE_FILE.exists():
        try:
            with open(AUTOSAVE_FILE, "r", encoding="utf-8") as fh:
                _recovery_cache = json.load(fh)
        except Exception:
            _recovery_cache = None
    _bind_monitor_analysis_callback()
    asyncio.create_task(_autosave_loop())
    if os.getenv("SDR_WATCHDOG_ENABLED", "0") == "1":
        asyncio.create_task(_watchdog_loop())


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

# Optional reference to a running PassiveMonitor instance.  When present the
# WebSocket spectrum endpoint will pull FFT slices from this monitor.  External
# code is expected to set this variable after creating the PassiveMonitor.
monitor: Optional[object] = None

# Current SDR configuration.  When ``monitor`` is running these values are
# kept in sync with the hardware via its setter methods.
config_state: dict[str, float | int | bool | None] = {
    "center_freq": None,
    "samp_rate": None,
    "fft_size": 1024,
    "gain": None,
    "hopping_enabled": False,
    "alert_threshold": None,
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
SHM_PATH = "/dev/shm/sdr_core_spectrum"
WATCHDOG_CHECK_INTERVAL = 0.5
WATCHDOG_STALE_SECONDS = 1.0


class _SharedSpectrumHeader(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_uint32),
        ("frame_id", ctypes.c_uint64),
        ("sample_rate", ctypes.c_uint32),
        ("analog_bandwidth", ctypes.c_uint32),
        ("center_freq", ctypes.c_uint64),
        ("peak_count", ctypes.c_uint32),
        ("last_heartbeat", ctypes.c_uint64),
        ("dropped_samples", ctypes.c_uint32),
        ("buffer_fill_percent", ctypes.c_float),
        ("processing_latency_ms", ctypes.c_float),
        ("cpu_usage", ctypes.c_float),
    ]


def _read_sdr_core_header() -> dict:
    """Read health/header fields exported by the C++ SDR core shared memory."""
    if not os.path.exists(SHM_PATH):
        raise FileNotFoundError("Shared memory segment not initialized")

    size = ctypes.sizeof(_SharedSpectrumHeader)
    with open(SHM_PATH, "rb") as fh:
        with mmap.mmap(fh.fileno(), length=size, access=mmap.ACCESS_READ) as mm:
            header = _SharedSpectrumHeader.from_buffer_copy(mm[:size])

    return {
        "state": int(header.state),
        "frame_id": int(header.frame_id),
        "sample_rate": int(header.sample_rate),
        "analog_bandwidth": int(header.analog_bandwidth),
        "center_freq": int(header.center_freq),
        "peak_count": int(header.peak_count),
        "last_heartbeat": int(header.last_heartbeat),
        "dropped_samples": int(header.dropped_samples),
        "buffer_fill_percent": float(header.buffer_fill_percent),
        "processing_latency_ms": float(header.processing_latency_ms),
        "cpu_usage": float(header.cpu_usage),
    }


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


def _remember_signals(new_signals: List[Signal]) -> None:
    """Persist latest detections in a circular in-memory buffer."""
    for sig in new_signals:
        recent_signals.append(sig)
    signals.clear()
    signals.extend(list(recent_signals))


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


@app.post("/api/scan/start")
def start_scan() -> dict:
    """Start or resume the SDR monitor."""
    if monitor is None:
        raise HTTPException(status_code=503, detail="Monitor not configured")
    if getattr(monitor, "is_running", False):
        raise HTTPException(status_code=409, detail="Monitor already running")
    log.info("Received start scan command")
    _bind_monitor_analysis_callback()
    if hasattr(monitor, "start"):
        monitor.start()
    elif hasattr(monitor, "resume"):
        monitor.resume()
    setattr(monitor, "is_running", True)
    return {"is_running": True}


@app.post("/api/scan/stop")
def stop_scan() -> dict:
    """Stop the SDR monitor."""
    if monitor is None:
        raise HTTPException(status_code=503, detail="Monitor not configured")
    if not getattr(monitor, "is_running", False):
        raise HTTPException(status_code=409, detail="Monitor not running")
    log.info("Received stop scan command")
    if hasattr(monitor, "stop"):
        monitor.stop()
        if hasattr(monitor, "wait"):
            monitor.wait()
    elif hasattr(monitor, "halt"):
        monitor.halt()
    setattr(monitor, "is_running", False)
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


@app.get("/api/config")
def get_config() -> dict:
    """Return current SDR configuration."""
    if monitor is not None and hasattr(monitor, "get_config"):
        return monitor.get_config()
    return config_state


@app.get("/api/health")
def get_health() -> dict:
    """Return runtime health telemetry exported by the SDR C++ core."""
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
async def spectrum_stream(websocket: WebSocket) -> None:
    """Stream FFT power spectra to connected clients.

    The endpoint sends lists of power values. If a ``PassiveMonitor`` instance
    has been attached via the global ``monitor`` variable its ``get_power_spectrum``
    method is used.  Otherwise random noise is emitted, which allows the
    front-end to operate in development without SDR hardware.
    """

    await websocket.accept()
    log.info("WebSocket spectrum client connected")
    fft_size = 1024
    frame_delay = 0.04
    try:
        while True:
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
            await websocket.send_json(spectrum.tolist())
            await asyncio.sleep(frame_delay)
    except WebSocketDisconnect:
        # Client disconnected; simply exit the loop
        log.info("WebSocket spectrum client disconnected")
        return


@app.websocket("/ws/spectrum/binary")
async def spectrum_stream_binary(websocket: WebSocket) -> None:
    """Stream FFT spectra as Float32 binary frames for lower overhead."""
    await websocket.accept()
    log.info("WebSocket binary spectrum client connected")
    fft_size = 1024
    frame_delay = 0.04
    try:
        while True:
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

            await websocket.send_bytes(spectrum.tobytes())
            await asyncio.sleep(frame_delay)
    except WebSocketDisconnect:
        log.info("WebSocket binary spectrum client disconnected")
        return


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app)
