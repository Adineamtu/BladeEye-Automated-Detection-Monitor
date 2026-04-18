from __future__ import annotations


def should_trigger_watchdog(
    *,
    now_ts: float,
    last_activity_ts: float,
    timeout_s: float,
    last_recovery_ts: float,
    recovery_cooldown_s: float,
) -> bool:
    if last_activity_ts <= 0.0:
        return False
    if (now_ts - last_activity_ts) <= timeout_s:
        return False
    if (now_ts - last_recovery_ts) < recovery_cooldown_s:
        return False
    return True


def build_heartbeat_payload(
    *,
    now_ts: float,
    mode: str,
    scanning: bool,
    dropped_chunks: int,
    last_error: str,
) -> dict[str, object]:
    return {
        "timestamp": float(now_ts),
        "mode": str(mode),
        "scanning": bool(scanning),
        "dropped_chunks": int(dropped_chunks),
        "last_error": str(last_error),
    }
