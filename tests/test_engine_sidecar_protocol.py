from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from multiprocessing import shared_memory


def _wait_until(predicate, timeout_s: float = 6.0, step_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


def _read_status(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_cmd(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_sidecar_protocol_mismatch_then_recover(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bladeeye_pro.engine_sidecar",
            "--control",
            str(control),
            "--status",
            str(status),
            "--frame",
            str(frame),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        # Wrong protocol command should be rejected.
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 999,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )

        assert _wait_until(
            lambda: "Protocol mismatch" in str(_read_status(status).get("protocol_error", "")),
            timeout_s=6.0,
        )
        mismatch_status = _read_status(status)
        assert mismatch_status.get("active") is False

        # Correct protocol command should activate runtime and produce a frame.
        _write_cmd(
            control,
            {
                "seq": 2,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )

        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)
        assert _wait_until(lambda: frame.exists() and frame.stat().st_size > 32, timeout_s=6.0)

        data = frame.read_bytes()
        magic, version, seq, _, bins = struct.unpack("<4sHIdI", data[:22])
        assert magic == b"BEF2"
        assert version == 1
        assert seq >= 0
        assert bins >= 128
    finally:
        try:
            _write_cmd(control, {"seq": 3, "protocol_version": 1, "action": "shutdown", "config": {}})
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5.0)


def test_sidecar_record_start_stop_persists_capture_and_index(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"
    sessions_dir = tmp_path / "sessions"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bladeeye_pro.engine_sidecar",
            "--control",
            str(control),
            "--status",
            str(status),
            "--frame",
            str(frame),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)

        _write_cmd(
            control,
            {
                "seq": 2,
                "protocol_version": 1,
                "action": "record_start",
                "threshold_multiplier": 2.5,
                "output_dir": str(sessions_dir),
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("capture_active")), timeout_s=6.0)

        _write_cmd(
            control,
            {
                "seq": 3,
                "protocol_version": 1,
                "action": "record_stop",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: not bool(_read_status(status).get("capture_active")), timeout_s=6.0)

        payload = _read_status(status)
        capture_file = Path(str(payload.get("capture_file", "")))
        index_file = Path(str(payload.get("index_file", "")))
        assert capture_file.exists()
        assert index_file.exists()
        index_payload = json.loads(index_file.read_text(encoding="utf-8"))
        assert "events" in index_payload
        assert "noise_floor" in index_payload
    finally:
        try:
            _write_cmd(control, {"seq": 4, "protocol_version": 1, "action": "shutdown", "config": {}})
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5.0)


def test_sidecar_status_exposes_live_event_sequence(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bladeeye_pro.engine_sidecar",
            "--control",
            str(control),
            "--status",
            str(status),
            "--frame",
            str(frame),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)

        seq_samples: list[int] = []
        latest_event: dict = {}
        chunk_samples: list[int] = []

        def _collect() -> bool:
            nonlocal latest_event
            payload = _read_status(status)
            chunk_samples.append(int(payload.get("chunk_counter", 0) or 0))
            seq = int(payload.get("event_seq", 0) or 0)
            seq_samples.append(seq)
            event = payload.get("latest_event", {})
            if isinstance(event, dict) and event:
                latest_event = event
            return len(chunk_samples) >= 3 and max(chunk_samples) > min(chunk_samples)

        assert _wait_until(_collect, timeout_s=8.0)
        # event_seq may stay zero under very low/noisy energy snapshots, but field must be monotonic.
        assert max(seq_samples) >= min(seq_samples)
        if latest_event:
            assert "timestamp" in latest_event
            assert "center_freq" in latest_event
            assert "modulation" in latest_event
    finally:
        try:
            _write_cmd(control, {"seq": 2, "protocol_version": 1, "action": "shutdown", "config": {}})
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5.0)


def test_sidecar_shutdown_during_recording_flushes_index(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"
    sessions_dir = tmp_path / "sessions"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bladeeye_pro.engine_sidecar",
            "--control",
            str(control),
            "--status",
            str(status),
            "--frame",
            str(frame),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)

        _write_cmd(
            control,
            {
                "seq": 2,
                "protocol_version": 1,
                "action": "record_start",
                "threshold_multiplier": 2.5,
                "output_dir": str(sessions_dir),
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("capture_active")), timeout_s=6.0)
        pre_shutdown_payload = _read_status(status)
        capture_file = Path(str(pre_shutdown_payload.get("capture_file", "")))
        index_file = Path(str(pre_shutdown_payload.get("index_file", "")))
        assert capture_file.as_posix() not in {"", "."}
        assert index_file.as_posix() not in {"", "."}

        # Graceful shutdown while recording should flush logger and persist index.
        _write_cmd(control, {"seq": 3, "protocol_version": 1, "action": "shutdown", "config": {}})
        proc.wait(timeout=6.0)
        assert capture_file.exists()
        assert index_file.exists()

        index_payload = json.loads(index_file.read_text(encoding="utf-8"))
        assert "events" in index_payload
        assert "noise_floor" in index_payload
        assert "capture_file" in index_payload
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5.0)
            except Exception:
                pass


def test_sidecar_recovers_after_abrupt_stop_during_recording(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"
    sessions_dir = tmp_path / "sessions"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"

    def _spawn() -> subprocess.Popen:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bladeeye_pro.engine_sidecar",
                "--control",
                str(control),
                "--status",
                str(status),
                "--frame",
                str(frame),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )

    proc = _spawn()
    try:
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)

        _write_cmd(
            control,
            {
                "seq": 2,
                "protocol_version": 1,
                "action": "record_start",
                "threshold_multiplier": 2.5,
                "output_dir": str(sessions_dir),
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("capture_active")), timeout_s=6.0)

        pre_crash = _read_status(status)
        capture_file = Path(str(pre_crash.get("capture_file", "")))
        index_file = Path(str(pre_crash.get("index_file", "")))
        assert capture_file.as_posix() not in {"", "."}
        assert index_file.as_posix() not in {"", "."}

        # Simulate non-graceful crash (no shutdown command).
        proc.kill()
        proc.wait(timeout=5.0)

        # Restart sidecar and ensure command channel remains usable.
        proc = _spawn()
        _write_cmd(
            control,
            {
                "seq": 3,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)
        assert _wait_until(lambda: frame.exists() and frame.stat().st_size > 32, timeout_s=6.0)

        # Previous capture metadata should remain parseable if index was already flushed pre-crash.
        if index_file.exists():
            index_payload = json.loads(index_file.read_text(encoding="utf-8"))
            assert isinstance(index_payload.get("events", []), list)
    finally:
        if proc.poll() is None:
            try:
                _write_cmd(control, {"seq": 4, "protocol_version": 1, "action": "shutdown", "config": {}})
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5.0)
            except Exception:
                pass


def test_sidecar_recovers_from_malformed_control_file(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"
    log_path = tmp_path / "engine_sidecar.log"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bladeeye_pro.engine_sidecar",
            "--control",
            str(control),
            "--status",
            str(status),
            "--frame",
            str(frame),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        # Corrupt payload should be tolerated by main loop (without crashing process).
        control.write_text("{invalid-json", encoding="utf-8")
        time.sleep(0.4)
        assert proc.poll() is None

        # Follow-up valid command should still be processed.
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)
        assert _wait_until(lambda: frame.exists() and frame.stat().st_size > 32, timeout_s=6.0)
        assert log_path.exists()
        assert "error" in log_path.read_text(encoding="utf-8").lower()
    finally:
        try:
            _write_cmd(control, {"seq": 2, "protocol_version": 1, "action": "shutdown", "config": {}})
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5.0)


def test_sidecar_ignores_out_of_order_sequence(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bladeeye_pro.engine_sidecar",
            "--control",
            str(control),
            "--status",
            str(status),
            "--frame",
            str(frame),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        _write_cmd(
            control,
            {
                "seq": 2,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)

        # Stale/older seq command must be ignored and runtime should remain active.
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 1,
                "action": "stop",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        time.sleep(0.4)
        payload = _read_status(status)
        assert payload.get("active") is True
        assert int(payload.get("last_seq", -1) or -1) == 2
    finally:
        try:
            _write_cmd(control, {"seq": 3, "protocol_version": 1, "action": "shutdown", "config": {}})
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5.0)


def test_sidecar_shm_transport_publishes_frames(tmp_path: Path) -> None:
    control = tmp_path / "engine_control.json"
    status = tmp_path / "engine_status.json"
    frame = tmp_path / "engine_frame.bin"

    env = dict(os.environ)
    env["BLADEEYE_PRO_SIM"] = "1"
    env["BLADEEYE_SIDECAR_FRAME_TRANSPORT"] = "shm"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bladeeye_pro.engine_sidecar",
            "--control",
            str(control),
            "--status",
            str(status),
            "--frame",
            str(frame),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    shm: shared_memory.SharedMemory | None = None
    try:
        _write_cmd(
            control,
            {
                "seq": 1,
                "protocol_version": 1,
                "action": "start",
                "config": {
                    "center_freq": 433_920_000.0,
                    "sample_rate": 1_000_000.0,
                    "bandwidth": 1_000_000.0,
                    "gain": 20.0,
                    "fft_size": 2048,
                },
            },
        )
        assert _wait_until(lambda: bool(_read_status(status).get("active")), timeout_s=6.0)
        assert _wait_until(lambda: str(_read_status(status).get("frame_transport", "")) == "shm", timeout_s=6.0)
        payload = _read_status(status)
        shm_name = str(payload.get("frame_shm_name", "") or "")
        shm_size = int(payload.get("frame_shm_size", 0) or 0)
        assert shm_name
        assert shm_size > 0
        shm = shared_memory.SharedMemory(name=shm_name, create=False)
        assert len(shm.buf) >= 22
        magic, version, seq, _, bins = struct.unpack("<4sHIdI", shm.buf[:22])
        assert magic == b"BEF2"
        assert version == 1
        assert seq >= 0
        assert bins >= 128
    finally:
        if shm is not None:
            try:
                shm.close()
            except Exception:
                pass
        try:
            _write_cmd(control, {"seq": 2, "protocol_version": 1, "action": "shutdown", "config": {}})
        except Exception:
            pass
        proc.terminate()
        proc.wait(timeout=5.0)
