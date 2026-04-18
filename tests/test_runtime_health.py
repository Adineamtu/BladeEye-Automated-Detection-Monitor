from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getcwd())

from bladeeye_pro.runtime_health import build_heartbeat_payload, should_trigger_watchdog


def test_should_trigger_watchdog_requires_positive_last_activity() -> None:
    assert (
        should_trigger_watchdog(
            now_ts=100.0,
            last_activity_ts=0.0,
            timeout_s=2.5,
            last_recovery_ts=0.0,
            recovery_cooldown_s=8.0,
        )
        is False
    )


def test_should_trigger_watchdog_respects_timeout_and_cooldown() -> None:
    assert (
        should_trigger_watchdog(
            now_ts=100.0,
            last_activity_ts=98.0,
            timeout_s=2.5,
            last_recovery_ts=0.0,
            recovery_cooldown_s=8.0,
        )
        is False
    )
    assert (
        should_trigger_watchdog(
            now_ts=100.0,
            last_activity_ts=96.0,
            timeout_s=2.5,
            last_recovery_ts=95.0,
            recovery_cooldown_s=8.0,
        )
        is False
    )
    assert (
        should_trigger_watchdog(
            now_ts=100.0,
            last_activity_ts=96.0,
            timeout_s=2.5,
            last_recovery_ts=80.0,
            recovery_cooldown_s=8.0,
        )
        is True
    )


def test_build_heartbeat_payload_normalizes_types() -> None:
    payload = build_heartbeat_payload(
        now_ts=123.45,
        mode="MONITOR",
        scanning=True,
        dropped_chunks=7,
        last_error="none",
    )
    assert payload == {
        "timestamp": 123.45,
        "mode": "MONITOR",
        "scanning": True,
        "dropped_chunks": 7,
        "last_error": "none",
    }
