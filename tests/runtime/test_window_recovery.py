"""Window recovery keeps one owner and never replaces an unconfirmed profile owner."""
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app.launcher.window_recovery import BrowserOwnerProbe, WindowSessionFailure, run_window_recovery


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def wait(self, delay):
        self.now += delay
        return False


def test_clean_browser_failure_retries_on_same_thread_then_stops_on_user_close():
    clock, events, phases, owners = Clock(), [], [], []

    def session(*, on_ready):
        owners.append(threading.get_ident())
        if len(owners) == 1:
            raise WindowSessionFailure("renderer_crash", cleanup_confirmed=True)
        on_ready()
        return True  # A recovered app window was intentionally closed.

    assert run_window_recovery(session, should_stop=lambda: False,
        phase=lambda *args: phases.append(args), record=lambda event, **fields: events.append((event, fields)),
        clock=clock, wait=clock.wait)
    assert owners == [threading.get_ident()] * 2
    assert [event for event, _fields in events] == ["browser_recovery_started", "browser_recovery_completed"]
    assert events[0][1]["delaySeconds"] == 1 and clock.now == 1
    assert phases[-1] == ("closed",)


def test_repeated_faults_back_off_to_30_seconds_and_second_owner_never_starts():
    clock, events, attempts, intruders = Clock(), [], [], []

    def session(*, on_ready):
        attempts.append(1)
        if len(attempts) <= 7:
            raise WindowSessionFailure("browser_error", cleanup_confirmed=True)
        return False

    def wait(delay):
        assert not run_window_recovery(lambda **kwargs: intruders.append(1), should_stop=lambda: False)
        return clock.wait(delay)

    assert run_window_recovery(session, should_stop=lambda: False, clock=clock, wait=wait,
        record=lambda event, **fields: events.append((event, fields))) is False
    assert len(attempts) == 8 and intruders == []
    assert [fields["delaySeconds"] for event, fields in events if event == "browser_recovery_started"] == [1, 2, 4, 8, 16, 30, 30]


def test_quit_interrupts_backoff_without_opening_another_session():
    stopped, calls, events, phases = threading.Event(), [], [], []

    def session(*, on_ready):
        calls.append(1)
        raise WindowSessionFailure("renderer_crash", cleanup_confirmed=True)

    def wait(delay):
        stopped.set()
        return True

    assert run_window_recovery(session, should_stop=lambda: False, stop_event=stopped, wait=wait,
        phase=lambda *args: phases.append(args), record=lambda event, **fields: events.append(event)) is False
    assert calls == [1] and events == ["browser_recovery_started", "browser_recovery_cancelled"]
    assert phases[-1] == ("closed",)


def test_unconfirmed_cleanup_requires_process_recovery_and_never_retries():
    events, phases, calls = [], [], []
    owner_lock = threading.Lock()

    def session(*, on_ready):
        calls.append(1)
        raise WindowSessionFailure("cleanup_failed", cleanup_confirmed=False)

    with pytest.raises(WindowSessionFailure):
        run_window_recovery(session, should_stop=lambda: False,
            phase=lambda *args: phases.append(args), record=lambda event, **fields: events.append(event),
            owner_lock=owner_lock)
    assert calls == [1] and events == ["browser_recovery_failed"] and phases[-1] == ("failed",)
    assert owner_lock.locked()
    assert not run_window_recovery(lambda **kwargs: calls.append(2), should_stop=lambda: False, owner_lock=owner_lock)
    assert calls == [1]


def test_diagnostic_observer_failure_does_not_prevent_recovery():
    clock, calls = Clock(), []

    def session(*, on_ready):
        calls.append(1)
        if len(calls) == 1:
            raise WindowSessionFailure("renderer_crash", cleanup_confirmed=True)
        on_ready()
        return True

    def broken_record(*args, **kwargs):
        raise OSError("disk unavailable")

    assert run_window_recovery(session, should_stop=lambda: False, record=broken_record,
        clock=clock, wait=clock.wait)
    assert len(calls) == 2


def test_owned_driver_eof_is_a_fault_but_does_not_prove_browser_cleanup():
    error = Future()
    error.set_exception(RuntimeError("driver pipe closed"))
    transport = SimpleNamespace(_proc=SimpleNamespace(returncode=None), on_error_future=error)
    playwright = SimpleNamespace(_impl_obj=SimpleNamespace(_connection=SimpleNamespace(_transport=transport)))
    probe = BrowserOwnerProbe(playwright, SimpleNamespace(browser=None))
    assert probe.failure_reason() == "driver_disconnected"
    assert not probe.cleanup_confirmed()
    probe.close()
