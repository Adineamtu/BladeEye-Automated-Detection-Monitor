#!/usr/bin/env python3
"""Standalone launcher that orchestrates SDR core, API backend and a native webview."""

from __future__ import annotations

import argparse
import atexit
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

import requests

try:
    import tkinter as tk
    from tkinter import messagebox
except Exception:  # pragma: no cover
    tk = None
    messagebox = None

import webview

WINDOW_TITLE = "Reactive Jamming Monitor"
DEFAULT_HOST = "127.0.0.1"
API_HEALTH_ENDPOINT = "/api/config"


class LauncherError(RuntimeError):
    """Raised when the standalone stack cannot be initialized."""


def _resource_root() -> Path:
    """Return runtime root both for source checkout and PyInstaller bundles."""
    if hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def _find_existing_path(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _resolve_binary(name: str, extra_candidates: Iterable[Path] | None = None) -> Path:
    root = _resource_root()
    candidates = [
        root / "bin" / name,
        root / "cpp" / "sdr_core" / "build" / name,
        root / "cpp" / "sdr_core" / name,
        root / name,
    ]
    if extra_candidates:
        candidates.extend(extra_candidates)

    found = _find_existing_path(candidates)
    if found is None:
        raise LauncherError(
            f"Nu am găsit binarul '{name}'. Rulează build-ul C++ înainte de lansare."
        )
    return found


def _resolve_frontend_dist() -> Path:
    root = _resource_root()
    candidates = [
        root / "frontend" / "dist",
        root / "dist" / "frontend",
    ]
    found = _find_existing_path(candidates)
    if found is None:
        raise LauncherError(
            "Nu am găsit frontend/dist. Rulează `npm --prefix frontend run build`."
        )
    return found


def _silent_creation_flags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _show_error_dialog(message: str, title: str = "Reactive Jamming") -> None:
    if tk is None or messagebox is None:
        print(f"{title}: {message}", file=sys.stderr)
        return
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def _start_process(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_silent_creation_flags(),
    )


def _wait_for_api(base_url: str, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = requests.get(base_url + API_HEALTH_ENDPOINT, timeout=0.75)
            if response.ok:
                return
        except Exception as exc:  # pragma: no cover - polling behavior
            last_error = exc
        time.sleep(0.2)
    raise LauncherError(f"API nu a pornit la timp pe {base_url}. Ultima eroare: {last_error}")


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=4)
    except Exception:
        proc.kill()


def _stop_processes(*procs: subprocess.Popen | None) -> None:
    for proc in procs:
        _terminate_process(proc)


def run() -> int:
    parser = argparse.ArgumentParser(description="Standalone launcher for Reactive Jamming")
    parser.add_argument("--port", type=int, default=0, help="Port API; 0 = auto")
    parser.add_argument("--api-host", default=DEFAULT_HOST)
    parser.add_argument("--no-core", action="store_true", help="Skip C++ SDR core startup")
    args = parser.parse_args()

    api_port = args.port if args.port > 0 else _find_free_port()
    api_url = f"http://{args.api_host}:{api_port}"

    core_proc: subprocess.Popen | None = None
    api_proc: subprocess.Popen | None = None

    try:
        frontend_dist = _resolve_frontend_dist()
        if not args.no_core:
            core_binary = _resolve_binary("sdr_core")
            core_proc = _start_process([str(core_binary)])

        launcher_env = os.environ.copy()
        launcher_env["FRONTEND_DIST"] = str(frontend_dist)

        api_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "api:app",
            "--host",
            args.api_host,
            "--port",
            str(api_port),
        ]
        api_proc = _start_process(api_cmd, env=launcher_env)
        _wait_for_api(api_url)

        atexit.register(_stop_processes, api_proc, core_proc)

        webview.create_window(WINDOW_TITLE, api_url, width=1480, height=920)
        webview.start(debug=False)
        return 0
    except LauncherError as exc:
        _show_error_dialog(str(exc))
        return 1
    except Exception as exc:
        _show_error_dialog(f"Eroare la inițializare: {exc}")
        return 1
    finally:
        _stop_processes(api_proc, core_proc)


if __name__ == "__main__":
    raise SystemExit(run())
