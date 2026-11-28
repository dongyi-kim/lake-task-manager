"""Core liveness, feature degradation and known process death have different policies."""
from types import SimpleNamespace

import pytest

from app.launcher import supervisor


def simulate(probe, *, stop_at=None, **options):
    now, workers, events = [0.0], [], []
    def factory(*args):
        first = not workers
        worker = SimpleNamespace(pid=len(workers) + 1, closed=[],
            poll=lambda: (0 if not first or (stop_at is not None and now[0] >= stop_at) else None))
        worker.close = lambda **kwargs: worker.closed.append(kwargs["clean_exit"])
        workers.append(worker)
        return worker
    result = supervisor.run_supervisor(["worker"], cwd="unused", env={}, base_url="unused",
        worker_factory=factory, probe=lambda *args: probe(now[0]), clock=lambda: now[0],
        sleep=lambda delay: now.__setitem__(0, now[0] + delay), report=lambda *args: None,
        record=lambda event, **fields: events.append((event, fields)), **options)
    return result, now[0], workers, events


def test_native_degradation_never_restarts_responsive_server_even_when_restart_is_recommended():
    result, elapsed, workers, events = simulate(
        lambda now: supervisor.WorkerHealth(True, degraded=True, restart_recommended=True), stop_at=600)
    assert result == 0 and elapsed == 600
    assert len(workers) == 1 and workers[0].closed == [True]
    assert [event for event, _ in events].count("worker_degraded") == 1
    assert "worker_recovery" not in [event for event, _ in events]


def test_temporary_core_stall_recovers_same_worker_after_old_three_miss_limit():
    result, elapsed, workers, events = simulate(lambda now: not 5 <= now < 45, stop_at=50)
    assert result == 0 and elapsed == 50 and len(workers) == 1
    assert workers[0].closed == [True]
    assert [fields["delaySeconds"] for event, fields in events if event == "worker_responsive"] == [40]


@pytest.mark.parametrize("busy,expected", [(False, 125), (True, 1875)])
def test_persistent_core_stall_waits_elapsed_grace_and_preserves_last_known_live_work(busy, expected):
    result, elapsed, workers, events = simulate(
        lambda now: supervisor.WorkerHealth(now == 0, active_work=busy))
    assert result == 0 and elapsed == expected
    assert [worker.closed for worker in workers] == [[False], [True]]
    waiting = [fields for event, fields in events if event == "worker_waiting"]
    assert len(waiting) == 1 and waiting[0]["delaySeconds"] == expected - 5


def test_progress_resets_stall_grace_and_active_work_allowance():
    def probe(now):
        if now == 0:
            return supervisor.WorkerHealth(True, active_work=True)
        if now == 60:
            return supervisor.WorkerHealth(True, active_work=False)
        return False
    result, elapsed, workers, _ = simulate(probe)
    assert result == 0 and elapsed == 185
    assert len(workers) == 2


def test_structured_probe_accepts_owned_degraded_response_without_trusting_foreign_work(monkeypatch):
    import json
    payload = {"status": "ok", "pid": 123, "instance": "ours", "degraded": True,
               "restartRecommended": True, "activeWork": {"http": 1, "sso": 1}}
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps(payload).encode()
    monkeypatch.setattr(supervisor.urllib.request, "build_opener", lambda *args:
        SimpleNamespace(open=lambda *args, **kwargs: Response()))
    owner = SimpleNamespace(owns_pid=lambda pid: pid == 123)
    state = supervisor.probe_worker_health("http://unused", owner, "ours")
    assert state == supervisor.WorkerHealth(True, True, True, True)
    assert supervisor.probe_worker("http://unused", owner, "ours") is True
    payload["instance"] = "someone-else"
    assert supervisor.probe_worker_health("http://unused", owner, "ours") == supervisor.WorkerHealth(False)
