"""Recovery evidence persists without exposing request data or blocking liveness."""

import json
from types import SimpleNamespace

from app.infra.runtime_diagnostics import RuntimeLog, RuntimeMonitor, thread_stacks
from app.launcher import runtime_health, supervisor


def test_monitor_distinguishes_stalled_loop_and_native_window_and_records_once():
    now, events, pending = [0.0], [], {}
    components = {"healthy": True, "windowPhase": "closed"}
    log = SimpleNamespace(record=lambda event, **fields: events.append((event, fields)))
    def loop(name):
        return SimpleNamespace(is_closed=lambda: False,
            call_soon_threadsafe=lambda callback, key: pending.update({name: lambda: callback(key)}))
    monitor = RuntimeMonitor(log, {"ui": loop("ui"), "mcp": loop("mcp")},
                             lambda: dict(components), clock=lambda: now[0])
    monitor.sample()
    now[0] = 4
    pending["ui"]()
    monitor.sample()
    assert events[0][0] == "runtime_stalled"
    assert events[0][1]["loopLagSeconds"] == {"ui": 0, "mcp": 4}
    assert events[0][1]["stacks"]
    monitor.sample()
    assert len(events) == 1  # Persistent failure does not fill the disk every second.
    pending["mcp"]()
    monitor.sample()
    assert events[-1][0] == "runtime_responsive"
    components.update(healthy=False, windowPhase="login-save")
    monitor.sample()
    assert events[-1][1]["components"]["windowPhase"] == "login-save"


def test_log_survives_reopen_and_rotates_without_frame_locals(tmp_path):
    log = RuntimeLog(tmp_path, "worker")
    secret_request_body = "do-not-log-the-request-body"
    log.record("stalled", stacks=thread_stacks())
    saved = log.path.read_text(encoding="utf-8")
    assert secret_request_body not in saved
    assert json.loads(saved)["event"] == "stalled"
    assert all(set(frame) == {"file", "line", "function"}
               for thread in json.loads(saved)["stacks"] for frame in thread["frames"])
    log._handler.maxBytes = 256
    for i in range(20):
        log.record("sample", sequence=i)
    log.close()
    assert len(list(tmp_path.glob("worker.jsonl*"))) == 3
    reopened = RuntimeLog(tmp_path, "worker")
    reopened.record("restarted")
    reopened.close()
    assert json.loads(log.path.read_text(encoding="utf-8").splitlines()[-1])["event"] == "restarted"


def test_unwritable_diagnostics_do_not_crash_runtime(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    log = RuntimeLog(blocked, "worker")
    log.record("started")
    log.close()


def test_supervisor_saves_crash_before_terminating_worker(tmp_path):
    events = []
    created = []
    def factory(*args):
        code = 17 if not created else 0
        worker = SimpleNamespace(pid=123 + len(created), poll=lambda: code,
            close=lambda **kwargs: events.append(("closed", kwargs)))
        created.append(worker)
        return worker
    assert supervisor.run_supervisor(["worker"], cwd=str(tmp_path), env={}, base_url="unused",
        worker_factory=factory, report=lambda _: None,
        record=lambda event, **fields: events.append((event, fields))) == 0
    assert events[1][0] == "worker_recovery" and events[2][0] == "closed"
    assert events[1][1]["exitCode"] == 17
    assert events[-2] == ("worker_exit", {"workerPid": 124, "exitCode": 0})


def test_native_health_reports_window_and_tray_separately(monkeypatch):
    monkeypatch.setattr(runtime_health.time, "monotonic", lambda: 100)
    monkeypatch.setattr(runtime_health, "_window_runtime", ("running", 95))
    state = runtime_health.runtime_status({"healthy": True, "at": 99})
    assert not state["healthy"] and not state["windowHealthy"] and state["trayHealthy"]
    assert state["windowOverdueSeconds"] == 5


def test_monitor_samples_current_mcp_generation_instead_of_closed_old_loop():
    seen = []
    def loop(name):
        return SimpleNamespace(is_closed=lambda: False,
            call_soon_threadsafe=lambda callback, key: seen.append(name))
    current = [loop("old")]
    monitor = RuntimeMonitor(SimpleNamespace(record=lambda *args, **kwargs: None),
        {"mcp": lambda: current[0]})
    monitor.sample()
    current[0] = loop("replacement")
    monitor.sample()
    assert seen == ["old", "replacement"]
