#!/usr/bin/env python3
"""Build script for producing a standalone launcher package."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
CPP_DIR = ROOT / "cpp" / "sdr_core"
CPP_BUILD_DIR = CPP_DIR / "build"
# Setează căile relative față de locația acestui script
ROOT = Path(__file__).parent.parent.absolute()
APP_WRAPPER_DIR = ROOT / "app_wrapper"
SPEC_FILE = APP_WRAPPER_DIR / "reactive_jam.spec"

print(f"DEBUG: Looking for spec file at: {SPEC_FILE}")
DIST_DIR = ROOT / "dist"
RELEASE_DIR = ROOT / "release"


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd or ROOT, check=True)


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main() -> int:
    os.chdir(ROOT)

    _run(["npm", "ci"], cwd=FRONTEND_DIR)
    _run(["npm", "run", "build"], cwd=FRONTEND_DIR)

    CPP_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    _run(["cmake", ".."], cwd=CPP_BUILD_DIR)
    _run(["cmake", "--build", ".", "--config", "Release", "-j4"], cwd=CPP_BUILD_DIR)

    _run([sys.executable, "-m", "PyInstaller", "--clean", str(SPEC_FILE)])

    RELEASE_DIR.mkdir(exist_ok=True)
    launcher_name = "reactive_jam.exe" if os.name == "nt" else "reactive_jam"
    _copy_if_exists(DIST_DIR / launcher_name, RELEASE_DIR / launcher_name)

    archive_base = ROOT / "reactive_jam_standalone"
    if os.name == "nt":
        shutil.make_archive(str(archive_base), "zip", root_dir=RELEASE_DIR)
        print(f"Created {archive_base}.zip")
    else:
        shutil.make_archive(str(archive_base), "gztar", root_dir=RELEASE_DIR)
        print(f"Created {archive_base}.tar.gz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
