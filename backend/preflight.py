"""Hardware pre-flight checks for BladeEye startup."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess

BLADERF_VENDOR_ID = "2cf0"


@dataclass
class PreflightStatus:
    """Result of startup checks used to decide runtime mode."""

    hardware_detected: bool
    usb_access_ok: bool
    mode: str
    detail: str


def detect_bladerf() -> bool:
    """Return True if a BladeRF VID is present in lsusb output."""
    try:
        proc = subprocess.run(
            ["lsusb"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False

    if proc.returncode != 0:
        return False
    pattern = re.compile(r"ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})")
    for line in proc.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        vendor, _product = match.group(1).lower(), match.group(2).lower()
        if vendor == BLADERF_VENDOR_ID:
            return True
    return False


def check_usb_permissions() -> bool:
    """Best-effort check for USB read/write access in common Linux paths."""
    if os.geteuid() == 0:
        return True

    usb_roots = ["/dev/bus/usb", "/dev/bladerf0"]
    for root in usb_roots:
        if os.path.exists(root):
            return os.access(root, os.R_OK | os.W_OK)
    return False


def run_preflight() -> PreflightStatus:
    """Run startup checks and choose operational mode."""
    hw = detect_bladerf()
    usb_ok = check_usb_permissions() if hw else False

    if hw and usb_ok:
        return PreflightStatus(
            hardware_detected=True,
            usb_access_ok=True,
            mode="hardware",
            detail="BladeRF detectat și acces USB valid.",
        )
    if hw and not usb_ok:
        return PreflightStatus(
            hardware_detected=True,
            usb_access_ok=False,
            mode="demo",
            detail="BladeRF detectat, dar permisiuni USB insuficiente. Comutare automată pe demo.",
        )
    return PreflightStatus(
        hardware_detected=False,
        usb_access_ok=False,
        mode="demo",
        detail="BladeRF nu a fost detectat. Comutare automată pe demo.",
    )
