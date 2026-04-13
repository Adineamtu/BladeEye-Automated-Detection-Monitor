#!/usr/bin/env python3
"""Standalone launcher that orchestrates SDR core, API backend and a Qt WebEngine window."""

from __future__ import annotations

import argparse
import atexit
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable

import requests
import uvicorn
from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from api import app

WINDOW_TITLE = "Reactive Jamming Monitor"
DEFAULT_HOST = "127.0.0.1"
API_HEALTH_ENDPOINT = "/api/config"
SHM_BUFFER_PATH = Path("/dev/shm/bladeeye_buffer")


class LauncherError(RuntimeError):
    """Raised when the standalone stack cannot be initialized."""


class APIServerThread(threading.Thread):
    """Runs uvicorn in background and allows graceful shutdown."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.server: uvicorn.Server | None = None

    def run(self) -> None:
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="critical")
        self.server = uvicorn.Server(config)
        self.server.run()

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True


class BladeEyeWindow(QMainWindow):
    """Main application window backed by Qt WebEngine."""

    def __init__(self, app_url: str) -> None:
        super().__init__()
        self._app_url = app_url
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1480, 920)
        self._configure_icon()
        self.web_view = QWebEngineView(self)
        self.setCentralWidget(self.web_view)
        self._configure_webengine()
        self.web_view.setUrl(QUrl("about:blank"))

    def load_app(self) -> None:
        self.web_view.setUrl(QUrl(self._app_url))

    def _configure_icon(self) -> None:
        icon_path = _find_existing_path(
            [
                _resource_root() / "assets" / "icon.ico",
                _resource_root() / "assets" / "icon.png",
            ]
        )
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

    def _configure_webengine(self) -> None:
        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)


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
    app_instance = QCoreApplication.instance()
    if app_instance is None:
        print(f"{title}: {message}", file=sys.stderr)
        return
    QMessageBox.critical(None, title, message)


def _configure_qt_runtime() -> None:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    extra_flags = [
        "--enable-gpu-rasterization",
        "--enable-zero-copy",
        "--ignore-gpu-blocklist",
        "--disable-logging",
    ]
    merged = " ".join(token for token in [flags, *extra_flags] if token).strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = merged
    os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.webenginecontext.debug=false")


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


def _cleanup_orphan_ipc() -> None:
    """Remove stale shared-memory/socket artifacts from previous crashed runs."""
    for stale_socket in (Path("/tmp/sdr_core_cmd.sock"), Path("/tmp/sdr_core_alert.sock")):
        try:
            stale_socket.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        SHM_BUFFER_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def run() -> int:
    parser = argparse.ArgumentParser(description="Standalone launcher for Reactive Jamming")
    parser.add_argument("--port", type=int, default=0, help="Port API; 0 = auto")
    parser.add_argument("--api-host", default=DEFAULT_HOST)
    parser.add_argument("--no-core", action="store_true", help="Skip C++ SDR core startup")
    args = parser.parse_args()

    api_port = args.port if args.port > 0 else _find_free_port()
    api_url = f"http://{args.api_host}:{api_port}"

    core_proc: subprocess.Popen | None = None
    api_thread: APIServerThread | None = None
    engine_log = None
    try:
        _configure_qt_runtime()
        _cleanup_orphan_ipc()
        frontend_dist = _resolve_frontend_dist()
        logs_dir = _resource_root() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        engine_log = open(logs_dir / "engine_error.log", "a", encoding="utf-8")
        if not args.no_core:
            core_binary = _resolve_binary("sdr_core")
            core_proc = subprocess.Popen(
                [str(core_binary)],
                stdout=subprocess.DEVNULL,
                stderr=engine_log,
                creationflags=_silent_creation_flags(),
            )

        launcher_env = os.environ.copy()
        launcher_env["FRONTEND_DIST"] = str(frontend_dist)
        os.environ.update(launcher_env)

        api_thread = APIServerThread(args.api_host, api_port)
        api_thread.start()
        _wait_for_api(api_url)

        qt_app = QApplication(sys.argv)
        window = BladeEyeWindow(api_url)
        QTimer.singleShot(0, window.load_app)
        window.show()
        atexit.register(_stop_processes, core_proc)
        exit_code = qt_app.exec()
        return int(exit_code)
    except LauncherError as exc:
        _show_error_dialog(str(exc))
        return 1
    except Exception as exc:
        _show_error_dialog(f"Eroare la inițializare: {exc}")
        return 1
    finally:
        if api_thread:
            api_thread.stop()
            api_thread.join(timeout=4)
        _stop_processes(core_proc)
        if engine_log is not None:
            engine_log.close()


if __name__ == "__main__":
    raise SystemExit(run())
