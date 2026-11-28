"""Serial SSO load shedding without a real browser, Jira or network."""
import sys
import threading
import time
import gc
import weakref
from types import ModuleType, SimpleNamespace

import pytest

from app.auth.base import PRIO_BACKGROUND, PRIO_USER, PRIO_WRITE, SessionExpired, UpstreamBusy, UpstreamError, UpstreamUnavailable
from app.auth.sso_session import SsoSessionProvider, active_sso_executions


def _call(fn):
    done, outcome = threading.Event(), {}

    def run():
        try:
            outcome["value"] = fn()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return done, outcome, thread


def _queued(provider, count):
    with provider._jobs.not_empty:
        assert provider._jobs.not_empty.wait_for(
            lambda: provider._jobs._qsize() >= count, timeout=1)


def _until(predicate):
    deadline = time.monotonic() + 1
    while not predicate():
        assert time.monotonic() < deadline, "SSO state did not settle"
        time.sleep(.001)


@pytest.fixture
def provider(monkeypatch):
    browser = SimpleNamespace(
        new_context=lambda **_: object(), is_connected=lambda: True, close=lambda: None)
    driver = SimpleNamespace(stop=lambda: None)
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: driver)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr("app.auth.sso_session._launch", lambda *_args, **_kwargs: browser)
    store = SimpleNamespace(any_exists=lambda: True, merged=lambda: {"cookies": [], "origins": []})
    result = SsoSessionProvider("https://jira.example", store)
    yield result
    result.close()


def test_hung_retired_owners_bound_replacement_until_real_disposal(provider, monkeypatch):
    # The fixture owns one real owner slot; a second can recover it, never a third.
    first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
    store = provider._store
    second = None
    calls = []
    def hold(entered):
        entered.set()
        assert release.wait(5)
        return "finished"
    first_done, first_result, _ = _call(lambda: provider._submit(lambda: hold(first_entered), wait=.03))
    try:
        assert first_entered.wait(1) and first_done.wait(1)
        assert isinstance(first_result["error"], UpstreamUnavailable)
        provider.close()
        second = SsoSessionProvider("https://jira.example", store)
        second_done, second_result, _ = _call(lambda: second._submit(lambda: hold(second_entered), wait=.03))
        assert second_entered.wait(1) and second_done.wait(1)
        assert isinstance(second_result["error"], UpstreamUnavailable)
        second.close()
        independent_store = SimpleNamespace(any_exists=lambda: True, merged=lambda: {"cookies": [], "origins": []})
        independent = SsoSessionProvider("https://other-jira.example", independent_store)
        independent.close()
        independent._thread.join(timeout=1)
        assert not independent._thread.is_alive()
        monkeypatch.setattr("app.auth.sso_session._launch", lambda *_args, **_kwargs: calls.append("spawned"))
        with pytest.raises(UpstreamBusy):
            SsoSessionProvider("https://jira.example", store)
        assert calls == []
    finally:
        release.set()
        provider._thread.join(timeout=2)
        if second is not None:
            second._thread.join(timeout=2)
    assert not provider._thread.is_alive() and not second._thread.is_alive()
    # Only completed owner disposal reopens construction.
    with monkeypatch.context() as patch:
        patch.setattr("app.auth.sso_session._launch", lambda *_args, **_kwargs: SimpleNamespace(
            new_context=lambda **_: object(), is_connected=lambda: True, close=lambda: None))
        recovered = SsoSessionProvider("https://jira.example", store)
        recovered.close()
        recovered._thread.join(timeout=2)
        assert not recovered._thread.is_alive()


@pytest.mark.parametrize("priority", [PRIO_BACKGROUND, PRIO_USER, PRIO_WRITE])
def test_expired_queued_job_never_runs_or_retires_active_upload(provider, priority):
    started, release = threading.Event(), threading.Event()

    def upload():
        started.set()
        assert release.wait(2)
        return "uploaded"

    upload_done, upload_result, _ = _call(
        lambda: provider._submit(upload, PRIO_WRITE, wait=3, may_commit=True))
    assert started.wait(1)
    ran = []
    try:
        with pytest.raises(UpstreamBusy) as error:
            provider._submit(lambda: ran.append(1), priority, wait=0.02,
                             may_commit=priority == PRIO_WRITE)
        assert not hasattr(error.value, "pending_operation")
        assert not provider.broken
        assert not upload_done.is_set()
    finally:
        release.set()
    assert upload_done.wait(1)
    assert upload_result == {"value": "uploaded"}
    assert provider._submit(lambda: "next", wait=1) == "next"
    assert ran == []


def test_full_queue_refuses_immediately_then_accepts_work_after_drain(provider):
    started, release = threading.Event(), threading.Event()

    def hold_owner():
        started.set()
        assert release.wait(3)

    owner_done, _, _ = _call(lambda: provider._submit(hold_owner, wait=4))
    assert started.wait(1)
    calls = [_call(lambda: provider._submit(lambda: "queued", wait=4))
             for _ in range(provider.MAX_QUEUED_JOBS)]
    _queued(provider, provider.MAX_QUEUED_JOBS)
    try:
        rejected, result, _ = _call(lambda: provider._submit(lambda: "must not run", wait=4,
                                                            may_commit=True))
        assert rejected.wait(0.5), "full admission must not occupy another caller until timeout"
        assert isinstance(result.get("error"), UpstreamBusy)
        assert not hasattr(result["error"], "pending_operation")
        assert provider._jobs.qsize() == provider.MAX_QUEUED_JOBS
        assert not provider.broken
    finally:
        release.set()
    assert owner_done.wait(1)
    for done, result, _ in calls:
        assert done.wait(1)
        assert result == {"value": "queued"}
    assert provider._submit(lambda: "recovered", wait=1) == "recovered"


def test_execution_gets_its_own_budget_after_queue_wait(provider):
    owner_started, release_owner = threading.Event(), threading.Event()
    next_started, release_next = threading.Event(), threading.Event()

    def owner():
        owner_started.set()
        assert release_owner.wait(2)

    def next_job():
        next_started.set()
        assert release_next.wait(2)
        return "finished"

    owner_done, _, _ = _call(lambda: provider._submit(owner, wait=3))
    assert owner_started.wait(1)
    done, result, _ = _call(lambda: provider._submit(next_job, wait=0.4))
    _queued(provider, 1)
    try:
        assert not done.wait(0.22)
        release_owner.set()
        assert next_started.wait(1)
        # Total elapsed time now exceeds 0.4s, while actual execution is still within its budget.
        assert not done.wait(0.23)
        assert not provider.broken
    finally:
        release_owner.set()
        release_next.set()
    assert owner_done.wait(1) and done.wait(1)
    assert result == {"value": "finished"}


@pytest.mark.parametrize("wait", [None, 930], ids=["normal", "maximum-upload"])
def test_default_queue_budget_does_not_grow_with_running_or_upload_budget(provider, monkeypatch, wait):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(provider, "JOB_TIMEOUT", 2)
    dispatched = []
    def hold():
        started.set()
        assert release.wait(3)
    owner_done, owner_result, _ = _call(lambda: provider._submit(hold))
    try:
        assert started.wait(1)
        monkeypatch.setattr(provider, "QUEUE_TIMEOUT", .03)
        queued_done, queued_result, _ = _call(lambda: provider._submit(
            lambda: dispatched.append("late write"), wait=wait, may_commit=True))
        assert queued_done.wait(.5), "the queue budget must expire before the much longer running budget"
        assert isinstance(queued_result.get("error"), UpstreamBusy)
        assert not hasattr(queued_result["error"], "pending_operation")
        assert not provider.broken and not owner_done.is_set()
    finally:
        release.set()
    assert owner_done.wait(1) and owner_result == {"value": None}
    assert dispatched == []


def test_default_running_job_can_outlive_its_short_queue_budget(provider, monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(provider, "QUEUE_TIMEOUT", .25)
    monkeypatch.setattr(provider, "JOB_TIMEOUT", 2)
    def work():
        started.set()
        assert release.wait(2)
        return "completed"
    done, result, _ = _call(lambda: provider._submit(work, may_commit=True))
    try:
        assert started.wait(1)
        assert not done.wait(.35)
        assert not provider.broken
    finally:
        release.set()
    assert done.wait(1) and result == {"value": "completed"}


@pytest.mark.parametrize("may_commit", [False, True])
def test_running_timeout_settles_without_retiring_and_preserves_only_write_outcome(provider, may_commit):
    started, release, completed = threading.Event(), threading.Event(), threading.Event()

    def hung_operation():
        started.set()
        assert release.wait(4)
        completed.set()
        return {"id": "late-write"}

    done, result, _ = _call(
        lambda: provider._submit(hung_operation, wait=1, may_commit=may_commit))
    assert started.wait(1)
    try:
        assert done.wait(2)
        error = result["error"]
        assert isinstance(error, UpstreamUnavailable) and not isinstance(error, UpstreamBusy)
        assert not provider.broken and provider.settling
        assert hasattr(error, "pending_operation") is may_commit
        timer = provider._settling_timer
        with pytest.raises(UpstreamBusy):
            provider._submit(lambda: "must not run", wait=1)
        if may_commit:
            assert not error.pending_operation.done
    finally:
        release.set()
    assert completed.wait(1)
    _until(lambda: not provider.settling)
    timer.function()  # A cancelled timer racing completion cannot retire this healthy owner.
    assert not provider.broken
    assert provider._submit(lambda: "same owner", wait=1) == "same owner"
    if may_commit:
        assert error.pending_operation.done
        assert error.pending_operation.result_now() == {"id": "late-write"}


def test_late_startup_after_constructor_timeout_releases_browser_and_owner(monkeypatch):
    launching, release, closed, stopped = (threading.Event() for _ in range(4))
    browser = SimpleNamespace(new_context=lambda **_: object(), close=closed.set)
    driver = SimpleNamespace(stop=stopped.set)
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: driver)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(SsoSessionProvider, "START_TIMEOUT", 0.04)

    def launch(*_args, **_kwargs):
        launching.set()
        assert release.wait(2)
        return browser

    monkeypatch.setattr("app.auth.sso_session._launch", launch)
    store = SimpleNamespace(any_exists=lambda: True, merged=lambda: {"cookies": []})
    provider = object.__new__(SsoSessionProvider)
    done, result, _ = _call(lambda: provider.__init__("https://jira.example", store))
    assert launching.wait(1)
    try:
        assert done.wait(1)
        assert isinstance(result["error"], UpstreamUnavailable)
    finally:
        release.set()
    provider._thread.join(timeout=1)
    assert not provider._thread.is_alive()
    assert closed.is_set() and stopped.is_set()


def test_partial_startup_failure_stops_driver_even_when_browser_close_fails(monkeypatch):
    stopped = threading.Event()

    def fail_context(**_kwargs):
        raise RuntimeError("context creation failed")

    def fail_close():
        raise RuntimeError("browser already disconnected")

    browser = SimpleNamespace(new_context=fail_context, close=fail_close)
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(
        start=lambda: SimpleNamespace(stop=stopped.set))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr("app.auth.sso_session._launch", lambda *_args, **_kwargs: browser)
    store = SimpleNamespace(any_exists=lambda: True, merged=lambda: {"cookies": []})
    with pytest.raises(RuntimeError, match="context creation failed"):
        SsoSessionProvider("https://jira.example", store)
    assert stopped.wait(1)


def test_stalled_browser_relaunch_settles_without_late_write(provider, monkeypatch):
    relaunching, release = threading.Event(), threading.Event()
    writes = []
    provider._browser.is_connected = lambda: False

    def relaunch():
        relaunching.set()
        assert release.wait(4)

    monkeypatch.setattr(provider, "_relaunch", relaunch)
    done, result, _ = _call(
        lambda: provider._submit(lambda: writes.append("sent"), wait=1, may_commit=True))
    assert relaunching.wait(1)
    try:
        assert done.wait(2)
        assert isinstance(result["error"], UpstreamUnavailable)
        assert not isinstance(result["error"], UpstreamBusy)
        assert not hasattr(result["error"], "pending_operation")
        assert not provider.broken and provider.settling
    finally:
        release.set()
    provider.close()
    assert not provider._thread.is_alive()
    assert writes == []


@pytest.mark.parametrize("native", [False, True], ids=["python-timeout", "playwright-timeout"])
def test_completed_native_timeout_preserves_connected_owner_and_unrelated_queued_job(provider, native):
    entered, release = threading.Event(), threading.Event()
    timeout_type = pytest.importorskip("playwright._impl._errors").TimeoutError if native else TimeoutError
    def timed_out():
        entered.set()
        assert release.wait(2)
        raise timeout_type("one native request expired")
    first_done, first, _ = _call(lambda: provider._submit(timed_out, wait=2))
    try:
        assert entered.wait(1)
        second_done, second, _ = _call(lambda: provider._submit(lambda: "unrelated success", wait=2))
        _queued(provider, 1)
    finally:
        release.set()
    assert first_done.wait(1) and isinstance(first.get("error"), UpstreamUnavailable)
    assert second_done.wait(1) and second == {"value": "unrelated success"}
    assert not provider.broken and not provider.settling
    assert provider._thread.is_alive()


def test_already_queued_unrelated_job_survives_running_write_settling(provider):
    entered, release = threading.Event(), threading.Event()
    calls = []
    owner_ident = provider._thread.ident
    def write():
        entered.set()
        assert release.wait(5)
        calls.append("write")
        return {"id": "one commit"}
    def unrelated():
        calls.append("unrelated")
        return "queued result"
    first_done, first, _ = _call(lambda: provider._submit(write, wait=1, may_commit=True))
    try:
        assert entered.wait(1)
        second_done, second, _ = _call(lambda: provider._submit(unrelated, wait=3))
        _queued(provider, 1)
        assert first_done.wait(2)
        operation = first["error"].pending_operation
        assert provider.settling and not provider.broken and not operation.done
        assert not second_done.is_set() and calls == []
        release.set()
        assert second_done.wait(1) and second == {"value": "queued result"}
        assert operation.done and operation.result_now() == {"id": "one commit"}
        assert calls == ["write", "unrelated"]
        assert not provider.settling and not provider.broken
        assert provider._thread.ident == owner_ident
    finally:
        release.set()


@pytest.mark.parametrize("failure", [UpstreamError(400, "/issue", "invalid timeout field"),
                                     SessionExpired("session timeout"), ValueError("timeout argument invalid")],
                         ids=["jira-validation", "session-expiry", "invalid-argument"])
def test_domain_errors_are_not_reclassified_by_message_or_retire_owner(provider, failure):
    def fail():
        raise failure
    with pytest.raises(type(failure)) as result:
        provider._submit(fail, wait=1)
    assert result.value is failure
    assert not provider.broken
    assert provider._submit(lambda: "still usable", wait=1) == "still usable"


def test_only_unfinished_work_after_settling_grace_retires_owner(provider, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    timers = []
    class ManualTimer:
        def __init__(self, interval, function):
            self.interval, self.function, self.cancelled = interval, function, False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
    monkeypatch.setattr("app.auth.sso_session.threading.Timer", ManualTimer)
    def held():
        entered.set()
        assert release.wait(4)
        return {"id": "late write"}
    done, result, _ = _call(lambda: provider._submit(held, wait=1, may_commit=True))
    try:
        assert entered.wait(1) and done.wait(2)
        operation = result["error"].pending_operation
        assert provider.settling and not provider.broken and not operation.done
        assert len(timers) == 1 and timers[0].interval == 60
        timers[0].function()  # Advance the settling timer without a real minute of waiting.
        assert provider.broken
        assert not operation.done
        with pytest.raises(UpstreamUnavailable):
            provider._submit(lambda: pytest.fail("retired owner dispatched another write"))
    finally:
        release.set()
    provider.close()
    assert operation.done and operation.result_now() == {"id": "late write"}


def test_known_disconnected_browser_retires_without_waiting_for_settling_grace(provider):
    def lost():
        provider._browser.is_connected = lambda: False
        raise ConnectionError("driver disconnected")
    with pytest.raises(UpstreamUnavailable):
        provider._submit(lost, wait=1)
    assert provider.broken


def test_known_closed_context_retires_even_when_browser_is_still_connected(provider):
    closed_type = pytest.importorskip("playwright._impl._errors").TargetClosedError
    assert provider._browser.is_connected()
    def closed_context():
        raise closed_type("native request context closed")
    with pytest.raises(UpstreamUnavailable):
        provider._submit(closed_context, wait=1)
    assert provider._browser.is_connected()
    assert provider.broken and not provider.settling
    with pytest.raises(UpstreamUnavailable):
        provider._submit(lambda: pytest.fail("closed request context was reused"), wait=1)


def test_settling_observer_failure_does_not_drop_pending_write_ownership(provider, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    class UnavailableTimer:
        def __init__(self, *_args):
            pass
        def start(self):
            raise RuntimeError("no observer thread available")
    monkeypatch.setattr("app.auth.sso_session.threading.Timer", UnavailableTimer)
    def held():
        entered.set()
        assert release.wait(4)
        return "committed once"
    done, result, _ = _call(lambda: provider._submit(held, wait=1, may_commit=True))
    try:
        assert entered.wait(1) and done.wait(2)
        operation = result["error"].pending_operation
        assert not operation.done and provider.settling and not provider.broken
        with pytest.raises(UpstreamBusy):
            provider._submit(lambda: pytest.fail("pending write was dispatched twice"))
    finally:
        release.set()
    _until(lambda: operation.done)
    assert not provider.settling and operation.result_now() == "committed once"


def test_active_count_covers_late_detached_work_on_all_owners(provider):
    baseline = active_sso_executions()
    second = SsoSessionProvider("https://jira.example", provider._store)
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    def held(index):
        entered[index].set()
        assert release.wait(4)
        return index
    calls = [_call(lambda p=p, i=i: p._submit(lambda: held(i), wait=1, may_commit=True))
             for i, p in enumerate((provider, second))]
    try:
        assert all(signal.wait(1) for signal in entered)
        assert all(done.wait(2) for done, _result, _thread in calls)
        assert all(not result["error"].pending_operation.done for _done, result, _thread in calls)
        assert active_sso_executions() == baseline + 2
        provider.close()  # Retirement cannot hide its still-running write from process liveness.
        assert active_sso_executions() == baseline + 2
    finally:
        release.set()
        provider.close()
        second.close()
        provider._thread.join(timeout=1)
        second._thread.join(timeout=1)
    assert active_sso_executions() == baseline


def test_cancelled_settling_observer_does_not_retain_disposed_owner(provider):
    extra = SsoSessionProvider("https://jira.example", provider._store)
    reference, holder = weakref.ref(extra), {"owner": extra}
    entered, release = threading.Event(), threading.Event()
    def held():
        entered.set()
        assert release.wait(4)
    done, outcome, caller = _call(lambda: holder["owner"]._submit(held, wait=1))
    try:
        assert entered.wait(1) and done.wait(2)
        timer = extra._settling_timer
        assert timer is not None and extra.settling
    finally:
        release.set()
        extra.close()
        extra._thread.join(timeout=1)
    caller.join(timeout=1)
    timer.join(timeout=1)
    assert not timer.is_alive()
    outcome.clear()  # Do not retain the caller exception's traceback/frame.
    holder.clear()
    del extra
    gc.collect()
    assert reference() is None, "a retained cancelled observer must not retain its disposed SSO owner"


def test_startup_budget_covers_native_launch_without_abandoning_usable_owner(monkeypatch):
    from app.auth.sso_session import _launch
    assert SsoSessionProvider.START_TIMEOUT == 60
    launching, release = threading.Event(), threading.Event()
    browser = SimpleNamespace(new_context=lambda **_: object(), is_connected=lambda: True, close=lambda: None)
    launch_options = []
    native = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: launch_options.append(kwargs) or browser))
    assert _launch(native, True) is browser
    assert launch_options == [{"headless": True, "timeout": 30000}]
    assert launch_options[0]["timeout"] < SsoSessionProvider.START_TIMEOUT * 1000
    driver = SimpleNamespace(stop=lambda: None)
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: driver)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(SsoSessionProvider, "START_TIMEOUT", 2)
    def launch(*_args, **_kwargs):
        launching.set()
        assert release.wait(2)
        return browser
    monkeypatch.setattr("app.auth.sso_session._launch", launch)
    store = SimpleNamespace(any_exists=lambda: True, merged=lambda: {"cookies": []})
    done, result, _ = _call(lambda: SsoSessionProvider("https://jira.example", store))
    try:
        assert launching.wait(1)
        assert not done.is_set()
    finally:
        release.set()
    assert done.wait(1)
    recovered = result["value"]
    try:
        assert not recovered.broken
        assert recovered._submit(lambda: "adopted", wait=1) == "adopted"
    finally:
        recovered.close()
