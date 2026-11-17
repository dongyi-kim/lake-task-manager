"""Production recovery survives repeated faults until an explicit quit."""

import threading

import pytest

from app.launcher import supervisor


class Clock:
    def __init__(self):
        self.now = 0
        self.after_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.after_sleep()


class Control:
    def __init__(self):
        self.quit_requested = threading.Event()
        self.retry_requested = threading.Event()
        self.cooldowns = []
        self.hidden = self.closed = 0
        self.on_show = lambda: None

    def show_cooldown(self, seconds, reason):
        self.cooldowns.append(seconds)
        self.on_show()

    def hide(self):
        self.hidden += 1

    def close(self):
        self.closed += 1


class Worker:
    def __init__(self, pid, codes):
        self.pid, self.codes, self.closed = pid, iter(codes), []

    def poll(self):
        return next(self.codes)

    def close(self, *, clean_exit=False):
        self.closed.append(clean_exit)


def run(clock, control, factory, **options):
    options.setdefault("report", lambda _: None)
    return supervisor.run_supervisor(["same-worker", "--same-args"], cwd="same-directory", env={"KEEP": "same"},
        base_url="unused", persistent_recovery=True, recovery_control=control,
        worker_factory=factory, clock=clock, sleep=clock.sleep, probe=lambda *_: True,
        **options)


def test_repeated_crashes_cool_down_then_recover_with_same_command():
    clock, control, workers, arguments, ready = Clock(), Control(), [], [], []

    def factory(argv, cwd, env):
        arguments.append((argv, cwd, env))
        worker = Worker(len(workers) + 1, [None, 0] if len(workers) == 3 else [17])
        workers.append(worker)
        return worker

    assert run(clock, control, factory, restart_limit=1, on_ready=lambda: ready.append(True)) == 0
    assert control.cooldowns == [30, 60]
    assert clock.now >= 90 and ready == [True]
    assert [worker.closed for worker in workers] == [[False], [False], [False], [True]]
    assert all(argv == ["same-worker", "--same-args"] and cwd == "same-directory"
               and env["KEEP"] == "same" for argv, cwd, env in arguments)
    assert len({env[supervisor.WORKER_ENV] for _, _, env in arguments}) == 4
    assert control.hidden and control.closed == 1


def test_repeated_fault_backoff_is_capped():
    clock, control, workers = Clock(), Control(), []

    def factory(*_):
        worker = Worker(len(workers) + 1, [0] if len(workers) == 6 else [17])
        workers.append(worker)
        return worker

    assert run(clock, control, factory, restart_limit=0) == 0
    assert control.cooldowns == [30, 60, 120, 240, 300, 300]


def test_spawn_failures_wait_before_retrying():
    clock, control, attempts, notices = Clock(), Control(), [], []

    def factory(*_):
        attempts.append(clock.now)
        if len(attempts) < 3:
            raise OSError("disposable spawn failure")
        return Worker(3, [0])

    assert run(clock, control, factory, on_failure=lambda: notices.append(True)) == 0
    assert attempts == [0, 30, 90] and control.cooldowns == [30, 60]
    assert notices == [True, True]


def test_manual_retry_skips_cooldown_without_changing_retry_payload():
    clock, control, workers = Clock(), Control(), []
    control.on_show = control.retry_requested.set

    def factory(*_):
        worker = Worker(len(workers) + 1, [0] if workers else [17])
        workers.append(worker)
        return worker

    assert run(clock, control, factory, restart_limit=0) == 0
    assert len(workers) == 2 and clock.now == 0
    assert not control.retry_requested.is_set()


def test_quit_during_cooldown_wins_over_manual_retry_and_prevents_respawn():
    clock, control, workers = Clock(), Control(), []

    def quit_and_retry():
        control.quit_requested.set()
        control.retry_requested.set()

    control.on_show = quit_and_retry

    def factory(*_):
        worker = Worker(len(workers) + 1, [17])
        workers.append(worker)
        return worker

    assert run(clock, control, factory, restart_limit=0) == 0
    assert len(workers) == 1 and workers[0].closed == [False]
    assert clock.now == 0 and control.closed == 1


def test_quit_while_monitoring_terminates_only_owned_worker_without_respawn():
    clock, control, workers = Clock(), Control(), []
    clock.after_sleep = control.quit_requested.set

    def factory(*_):
        worker = Worker(len(workers) + 1, [None] * 10)
        workers.append(worker)
        return worker

    assert run(clock, control, factory) == 0
    assert len(workers) == 1 and workers[0].closed == [False]
    assert clock.now <= .25 and control.cooldowns == []


def test_normal_worker_exit_never_resurrects_in_persistent_mode():
    clock, control, workers = Clock(), Control(), []

    def factory(*_):
        worker = Worker(len(workers) + 1, [0])
        workers.append(worker)
        return worker

    assert run(clock, control, factory) == 0
    assert len(workers) == 1 and workers[0].closed == [True]
    assert control.cooldowns == [] and control.closed == 1


@pytest.mark.parametrize("hook", ["on_ready", "on_failure", "report", "record", "show_cooldown", "hide", "close"])
def test_throwing_observer_cannot_kill_parent_or_abandon_owned_worker(monkeypatch, hook):
    clock, control, workers = Clock(), Control(), []

    def broken(*args, **kwargs):
        raise BrokenPipeError("fixture observer unavailable")

    def factory(*_):
        codes = [0] if workers or hook == "close" else ([None, 0] if hook in {"on_ready", "hide"} else [17])
        worker = Worker(len(workers) + 1, codes)
        workers.append(worker)
        return worker

    options = {"restart_limit": 0}
    if hook in {"on_ready", "on_failure", "report", "record"}:
        options[hook] = broken
    else:
        monkeypatch.setattr(control, hook, broken)
    assert run(clock, control, factory, **options) == 0
    assert workers[-1].closed == [True]
    assert all(worker.closed for worker in workers)
