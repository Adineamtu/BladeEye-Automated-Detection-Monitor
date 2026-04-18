from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import struct
import threading
import time
from multiprocessing import shared_memory

import numpy as np

from .capture_lab import AsyncRawCaptureLogger
from .dsp import DSPEngine
from .hardware import AcquisitionEngine, HardwareConfig

PROTOCOL_VERSION = 1
FRAME_MAGIC = b'BEF2'
FRAME_TRANSPORT_FILE = "file"
FRAME_TRANSPORT_SHM = "shm"


def _write_frame(frame_path: Path, seq: int, spectrum: np.ndarray) -> None:
    arr = np.asarray(spectrum, dtype=np.float32)
    header = struct.pack('<4sHIdI', FRAME_MAGIC, int(PROTOCOL_VERSION), int(seq), float(time.time()), int(arr.size))
    tmp = frame_path.with_suffix(frame_path.suffix + '.tmp')
    tmp.write_bytes(header + arr.tobytes())
    tmp.replace(frame_path)


class SharedMemoryFramePublisher:
    def __init__(self, bins: int) -> None:
        self._bins = max(128, int(bins))
        self._header_size = struct.calcsize("<4sHIdI")
        self._payload_size = self._header_size + (self._bins * 4)
        self._shm = shared_memory.SharedMemory(create=True, size=self._payload_size)
        self.name = self._shm.name
        self.size = self._payload_size

    def publish(self, seq: int, spectrum: np.ndarray) -> None:
        arr = np.asarray(spectrum, dtype=np.float32)
        if arr.size != self._bins:
            self.close(unlink=True)
            self.__init__(arr.size)
        header = struct.pack("<4sHIdI", FRAME_MAGIC, int(PROTOCOL_VERSION), int(seq), float(time.time()), int(arr.size))
        self._shm.buf[: self._header_size] = header
        self._shm.buf[self._header_size : self._header_size + (arr.size * 4)] = arr.tobytes()

    def close(self, *, unlink: bool = False) -> None:
        try:
            self._shm.close()
        except Exception:
            pass
        if unlink:
            try:
                self._shm.unlink()
            except Exception:
                pass


def _append_sidecar_log(log_path: Path, level: str, message: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    line = f"[{ts}] {level.upper()}: {message}\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


class SidecarRuntime:
    def __init__(self, config: dict[str, float]) -> None:
        self._lock = threading.Lock()
        hw = HardwareConfig(
            center_freq=float(config.get("center_freq", 433_920_000.0)),
            sample_rate=float(config.get("sample_rate", 1_000_000.0)),
            bandwidth=float(config.get("bandwidth", float(config.get("sample_rate", 1_000_000.0)))),
            gain=float(config.get("gain", 20.0)),
        )
        self.acquisition = AcquisitionEngine(hw)
        self.dsp = DSPEngine(
            sample_rate=float(hw.sample_rate),
            center_freq=float(hw.center_freq),
            fft_size=int(config.get("fft_size", 2048)),
        )
        self.last_error = ""
        self.last_chunk_ts = 0.0
        self.chunk_counter = 0
        self.latest_spectrum = np.full(self.dsp.fft_size, -120.0, dtype=np.float32)
        self.active = False
        self.capture_logger: AsyncRawCaptureLogger | None = None
        self.last_capture_file = ""
        self.last_index_file = ""
        self.event_seq = 0
        self.last_event: dict[str, object] | None = None
        self.acquisition.add_sink(self._on_chunk)
        self.acquisition.add_error_sink(self._on_error)

    def _on_error(self, message: str) -> None:
        with self._lock:
            self.last_error = str(message)

    def _on_chunk(self, chunk: np.ndarray) -> None:
        frame = self.dsp.process(chunk, deep_analysis=False)
        with self._lock:
            self.latest_spectrum = np.asarray(frame.averaged_fft_db, dtype=np.float32)
            self.last_chunk_ts = time.time()
            self.chunk_counter += 1
            if frame.event is not None:
                self.event_seq += 1
                self.last_event = asdict(frame.event)
        logger = self.capture_logger
        if logger is not None:
            logger.ingest(chunk)

    def update_config(self, config: dict[str, float]) -> None:
        center = float(config.get("center_freq", self.acquisition.config.center_freq))
        sample_rate = float(config.get("sample_rate", self.acquisition.config.sample_rate))
        bandwidth = float(config.get("bandwidth", sample_rate))
        gain = float(config.get("gain", self.acquisition.config.gain))
        self.acquisition.update_params(center_freq=center, sample_rate=sample_rate, bandwidth=bandwidth, gain=gain)
        with self._lock:
            self.dsp.sample_rate = sample_rate
            self.dsp.set_center_freq(center)

    def start(self) -> None:
        if self.active:
            return
        self.acquisition.start()
        self.active = True

    def stop(self) -> None:
        if not self.active:
            return
        self.stop_capture()
        self.acquisition.stop()
        self.active = False

    def start_capture(self, *, threshold_multiplier: float, output_dir: str) -> None:
        if self.capture_logger is not None:
            return
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        capture_path = out / f'collector_sidecar_{ts}.iq'
        logger = AsyncRawCaptureLogger(
            capture_path,
            sample_rate=float(self.acquisition.config.sample_rate),
            power_threshold=max(1.0, float(threshold_multiplier)),
        )
        logger.start()
        self.capture_logger = logger
        self.last_capture_file = str(capture_path)
        self.last_index_file = str(logger.index_path)

    def stop_capture(self) -> tuple[str, str]:
        logger = self.capture_logger
        if logger is None:
            return self.last_capture_file, self.last_index_file
        logger.stop()
        self.last_capture_file = str(logger.output_path)
        self.last_index_file = str(logger.index_path)
        self.capture_logger = None
        return self.last_capture_file, self.last_index_file

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "last_error": self.last_error,
                "last_chunk_ts": self.last_chunk_ts,
                "chunk_counter": self.chunk_counter,
                "latest_spectrum": self.latest_spectrum.copy(),
                "capture_active": self.capture_logger is not None,
                "capture_file": self.last_capture_file,
                "index_file": self.last_index_file,
                "event_seq": self.event_seq,
                "latest_event": self.last_event if self.last_event is not None else {},
            }


def run_sidecar(control_path: Path, status_path: Path, frame_path: Path) -> int:
    control_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    running = True
    active = False
    last_seq = -1
    last_action = "boot"
    cfg: dict[str, float] = {}
    frame_seq = 0
    runtime: SidecarRuntime | None = None
    protocol_error = ""
    frame_transport = str(os.getenv("BLADEEYE_SIDECAR_FRAME_TRANSPORT", FRAME_TRANSPORT_FILE) or FRAME_TRANSPORT_FILE).lower()
    if frame_transport not in {FRAME_TRANSPORT_FILE, FRAME_TRANSPORT_SHM}:
        frame_transport = FRAME_TRANSPORT_FILE
    shm_publisher: SharedMemoryFramePublisher | None = None
    log_path = status_path.parent / "engine_sidecar.log"
    last_error_log_ts = 0.0
    last_error_message = ""

    stop_flag = {"stop": False}

    def _handle_stop(signum, frame):  # type: ignore[no-untyped-def]
        _ = (signum, frame)
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    while running and not stop_flag["stop"]:
        try:
            snap: dict[str, object] | None = None
            if control_path.exists():
                payload = json.loads(control_path.read_text(encoding="utf-8"))
                seq = int(payload.get("seq", -1))
                if seq > last_seq:
                    payload_version = int(payload.get("protocol_version", 0) or 0)
                    if payload_version != PROTOCOL_VERSION:
                        protocol_error = (
                            f'Protocol mismatch: control={payload_version}, expected={PROTOCOL_VERSION}. Command ignored.'
                        )
                        _append_sidecar_log(log_path, "warning", protocol_error)
                        last_seq = seq
                        continue
                    last_seq = seq
                    last_action = str(payload.get("action", "noop")).lower().strip()
                    cfg = payload.get("config", {}) if isinstance(payload.get("config"), dict) else {}
                    if runtime is None:
                        runtime = SidecarRuntime(cfg)
                    else:
                        runtime.update_config(cfg)
                    if last_action == "start":
                        runtime.start()
                        active = True
                    elif last_action == "stop":
                        runtime.stop()
                        active = False
                    elif last_action == "shutdown":
                        runtime.stop_capture()
                        runtime.stop()
                        active = False
                        running = False
                    elif last_action == "record_start":
                        runtime.start_capture(
                            threshold_multiplier=float(payload.get("threshold_multiplier", 2.5)),
                            output_dir=str(payload.get("output_dir", "sessions")),
                        )
                    elif last_action == "record_stop":
                        runtime.stop_capture()
                    elif last_action not in {"noop", ""}:
                        _append_sidecar_log(log_path, "warning", f"Unknown action ignored: {last_action}")
            if active:
                if runtime is not None:
                    snap = runtime.snapshot()
                    spectrum = snap["latest_spectrum"]
                    if isinstance(spectrum, np.ndarray):
                        if frame_transport == FRAME_TRANSPORT_SHM:
                            if shm_publisher is None:
                                shm_publisher = SharedMemoryFramePublisher(int(spectrum.size))
                            shm_publisher.publish(frame_seq, spectrum)
                        else:
                            _write_frame(frame_path, frame_seq, spectrum)
                        frame_seq += 1
                else:
                    active = False
            else:
                snap = runtime.snapshot() if runtime is not None else None
            status = {
                "pid": os.getpid(),
                "timestamp": time.time(),
                "active": active,
                "last_seq": last_seq,
                "last_action": last_action,
                "config": cfg,
                "frame_seq": frame_seq,
                "source": (runtime.acquisition.source_name if runtime is not None else "uninitialized"),
                "last_chunk_ts": (snap["last_chunk_ts"] if isinstance(snap, dict) else 0.0),
                "chunk_counter": (snap["chunk_counter"] if isinstance(snap, dict) else 0),
                "last_error": (snap["last_error"] if isinstance(snap, dict) else ""),
                "capture_active": (snap["capture_active"] if isinstance(snap, dict) else False),
                "capture_file": (snap["capture_file"] if isinstance(snap, dict) else ""),
                "index_file": (snap["index_file"] if isinstance(snap, dict) else ""),
                "event_seq": (snap["event_seq"] if isinstance(snap, dict) else 0),
                "latest_event": (snap["latest_event"] if isinstance(snap, dict) else {}),
                "protocol_version": PROTOCOL_VERSION,
                "protocol_error": protocol_error,
                "frame_transport": frame_transport,
                "frame_shm_name": (shm_publisher.name if shm_publisher is not None else ""),
                "frame_shm_size": (shm_publisher.size if shm_publisher is not None else 0),
                "last_loop_error": last_error_message,
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")
            last_error_message = ""
        except Exception as exc:
            # keep sidecar alive even if control/status files are temporarily invalid
            exc_msg = str(exc or "unknown sidecar loop error")
            now = time.time()
            if exc_msg != last_error_message or (now - last_error_log_ts) >= 1.0:
                _append_sidecar_log(log_path, "error", exc_msg)
                last_error_log_ts = now
            last_error_message = exc_msg
        time.sleep(0.25)

    if runtime is not None:
        runtime.stop()
    if shm_publisher is not None:
        shm_publisher.close(unlink=True)
    try:
        status_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "timestamp": time.time(),
                    "active": False,
                    "last_seq": last_seq,
                    "last_action": "stopped",
                    "config": cfg,
                    "frame_seq": frame_seq,
                    "source": (runtime.acquisition.source_name if runtime is not None else "uninitialized"),
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BladeEye engine sidecar control-plane worker")
    parser.add_argument("--control", required=True, help="Path to control JSON file")
    parser.add_argument("--status", required=True, help="Path to status JSON file")
    parser.add_argument("--frame", required=True, help="Path to sidecar spectrum frame file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_sidecar(Path(args.control), Path(args.status), Path(args.frame))


if __name__ == "__main__":
    raise SystemExit(main())
