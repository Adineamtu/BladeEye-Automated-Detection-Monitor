"""Hardware pre-flight checks for BladeEye startup."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
from typing import Optional

BLADERF_VENDOR_ID = "2cf0"


@dataclass
class PreflightStatus:
    """Result of startup checks used to decide runtime mode."""

    hardware_detected: bool
    usb_access_ok: bool
    mode: str
    detail: str
    firmware_version: Optional[str] = None
    firmware_warning: Optional[str] = None


MIN_BLADE_RF_FIRMWARE = (2, 4, 0)


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


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


def detect_firmware_version() -> str | None:
    """Best-effort firmware detection via bladeRF-cli probe output."""
    try:
        proc = subprocess.run(
            ["bladeRF-cli", "-p"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if "firmware version" in line.lower():
            parsed = _parse_version(line)
            if parsed is not None:
                return ".".join(str(p) for p in parsed)
    parsed = _parse_version(proc.stdout)
    if parsed is None:
        return None
    return ".".join(str(p) for p in parsed)


def run_preflight() -> PreflightStatus:
    """Run startup checks and choose operational mode."""
    hw = detect_bladerf()
    usb_ok = check_usb_permissions() if hw else False
    firmware_version = detect_firmware_version() if hw else None
    firmware_warning = None
    parsed_fw = _parse_version(firmware_version or "")
    if hw and parsed_fw is not None and parsed_fw < MIN_BLADE_RF_FIRMWARE:
        firmware_warning = (
            f"Firmware {firmware_version} detectat; recomandat >= "
            f"{'.'.join(str(v) for v in MIN_BLADE_RF_FIRMWARE)} pentru stabilitate la MSPS ridicat."
        )

    if hw and usb_ok:
        return PreflightStatus(
            hardware_detected=True,
            usb_access_ok=True,
            mode="hardware",
            detail=firmware_warning or "BladeRF detectat și acces USB valid.",
            firmware_version=firmware_version,
            firmware_warning=firmware_warning,
        )
    if hw and not usb_ok:
        return PreflightStatus(
            hardware_detected=True,
            usb_access_ok=False,
            mode="demo",
            detail="BladeRF detectat, dar permisiuni USB insuficiente. Comutare automată pe demo.",
            firmware_version=firmware_version,
            firmware_warning=firmware_warning,
        )
    return PreflightStatus(
        hardware_detected=False,
        usb_access_ok=False,
        mode="demo",
        detail="BladeRF nu a fost detectat. Comutare automată pe demo.",
        firmware_version=None,
        firmware_warning=None,
    )
