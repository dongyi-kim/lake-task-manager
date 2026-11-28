"""Strict component health and fixed native-owner escalation deadlines are distinct."""
from types import SimpleNamespace

import pytest

from app.launcher import runtime_health


@pytest.fixture
def native_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(runtime_health, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(runtime_health, "_window_runtime", ("closed", 0.0))
    return clock


@pytest.mark.parametrize("phase,timeout", [("running", 15), ("recovering", 90), ("login", 360)])
def test_native_window_grace_starts_after_its_deadline_and_health_polls_do_not_extend_it(native_clock, phase, timeout):
    runtime_health.window_phase(phase, timeout)
    deadline = native_clock[0] + timeout
    native_clock[0] = deadline - 1
    assert runtime_health.runtime_status()["healthy"]
    assert not runtime_health.runtime_status()["restartRecommended"]
    native_clock[0] = deadline + 1
    state = runtime_health.runtime_status()
    assert not state["healthy"] and not state["windowHealthy"]
    assert not state["restartRecommended"]
    native_clock[0] = deadline + 119
    assert not runtime_health.runtime_status()["restartRecommended"]
    native_clock[0] = deadline + 120
    assert runtime_health.runtime_status()["restartRecommended"]
    runtime_health.window_phase("running", 15)
    assert runtime_health.runtime_status()["healthy"]
    assert not runtime_health.runtime_status()["restartRecommended"]


def test_repeated_failed_window_reports_keep_the_first_failure_origin(native_clock):
    runtime_health.window_phase("failed")
    native_clock[0] += 119
    runtime_health.window_phase("failed")
    assert not runtime_health.runtime_status()["restartRecommended"]
    native_clock[0] += 1
    assert runtime_health.runtime_status()["restartRecommended"]
    runtime_health.window_phase("closed")
    assert runtime_health.runtime_status()["healthy"]
    assert not runtime_health.runtime_status()["restartRecommended"]


def test_tray_failure_grace_is_continuous_and_success_resets_it(native_clock):
    probe = {"healthy": True, "at": native_clock[0]}
    runtime_health.update_tray_probe(probe, False)
    origin = probe["unhealthySince"]
    native_clock[0] += 119
    runtime_health.update_tray_probe(probe, False)
    state = runtime_health.runtime_status(probe)
    assert probe["unhealthySince"] == origin
    assert not state["healthy"] and not state["trayHealthy"]
    assert not state["restartRecommended"]
    native_clock[0] += 1
    runtime_health.update_tray_probe(probe, False)
    assert runtime_health.runtime_status(probe)["restartRecommended"]
    runtime_health.update_tray_probe(probe, True)
    assert probe["unhealthySince"] is None
    assert runtime_health.runtime_status(probe)["healthy"]
    native_clock[0] += 1
    runtime_health.update_tray_probe(probe, False)
    assert probe["unhealthySince"] == native_clock[0]
    assert not runtime_health.runtime_status(probe)["restartRecommended"]


def test_stale_tray_probe_gets_grace_after_its_existing_heartbeat_deadline(native_clock):
    probe = {"healthy": True, "at": native_clock[0]}
    native_clock[0] += 15
    assert not runtime_health.runtime_status(probe)["healthy"]
    assert not runtime_health.runtime_status(probe)["restartRecommended"]
    native_clock[0] += 119
    assert not runtime_health.runtime_status(probe)["restartRecommended"]
    native_clock[0] += 1
    assert runtime_health.runtime_status(probe)["restartRecommended"]
