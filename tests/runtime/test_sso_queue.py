"""Serial SSO load shedding without a real browser, Jira or network."""
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from app.auth.base import PRIO_BACKGROUND, PRIO_USER, PRIO_WRITE, UpstreamBusy, UpstreamUnavailable
from app.auth.sso_session import SsoSessionProvider


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
def test_running_timeout_retires_provider_and_preserves_only_write_outcome(provider, may_commit):
    started, release, completed = threading.Event(), threading.Event(), threading.Event()

    def hung_operation():
        started.set()
        assert release.wait(2)
        completed.set()
        return {"id": "late-write"}

    done, result, _ = _call(
        lambda: provider._submit(hung_operation, wait=0.04, may_commit=may_commit))
    assert started.wait(1)
    try:
        assert done.wait(1)
        error = result["error"]
        assert isinstance(error, UpstreamUnavailable) and not isinstance(error, UpstreamBusy)
        assert provider.broken
        assert hasattr(error, "pending_operation") is may_commit
        with pytest.raises(UpstreamUnavailable):
            provider._submit(lambda: "must not run", wait=1)
        if may_commit:
            assert not error.pending_operation.done
    finally:
        release.set()
    assert completed.wait(1)
    provider.close()  # joins the owner after its late completion
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


def test_stalled_browser_relaunch_retires_owner_without_late_write(provider, monkeypatch):
    relaunching, release = threading.Event(), threading.Event()
    writes = []
    provider._browser.is_connected = lambda: False

    def relaunch():
        relaunching.set()
        assert release.wait(2)

    monkeypatch.setattr(provider, "_relaunch", relaunch)
    done, result, _ = _call(
        lambda: provider._submit(lambda: writes.append("sent"), wait=.04, may_commit=True))
    assert relaunching.wait(1)
    try:
        assert done.wait(1)
        assert isinstance(result["error"], UpstreamUnavailable)
        assert not isinstance(result["error"], UpstreamBusy)
        assert not hasattr(result["error"], "pending_operation")
        assert provider.broken
    finally:
        release.set()
    provider.close()
    assert not provider._thread.is_alive()
    assert writes == []
