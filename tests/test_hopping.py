import types
import time



def test_start_hopping_cycles_watchlist():
    from HackRF.passive_monitor import PassiveMonitor

    calls: list[float] = []

    obj = types.SimpleNamespace(
        watchlist=[10.0, 20.0],
        set_center_freq=lambda f: calls.append(f),
        hopping_enabled=False,
        _hop_thread=None,
        _hop_stop=None,
        current_watch_freq=None,
    )
    # Bind the hopping helper to the dummy object
    obj._hopping_loop = types.MethodType(PassiveMonitor._hopping_loop, obj)

    # Start hopping with very short dwell for the test
    PassiveMonitor.start_hopping(obj, dwell_time=0.001)  # type: ignore[arg-type]
    time.sleep(0.01)
    PassiveMonitor.stop_hopping(obj)  # type: ignore[arg-type]

    assert 10.0 in calls and 20.0 in calls
    assert obj.hopping_enabled is False
