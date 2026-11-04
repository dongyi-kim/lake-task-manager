"""Provider construction remains single-flight through failure and a concurrent new login."""
import gc
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from app.auth.base import LoginRequired, SessionExpired, UpstreamBusy, UpstreamError, UpstreamUnavailable
from app.infra.cache import Cache
from app.jira.jira_client import JiraClient


def _call(fn):
    done, result = threading.Event(), {}

    def run():
        try:
            result["value"] = fn()
        except BaseException as exc:
            result["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return done, result


@pytest.fixture
def client():
    cache = Cache(":memory:")
    result = JiraClient(SimpleNamespace(jira_env="prod"), cache)
    yield result
    result.close()
    cache.close()


def test_ready_injected_provider_does_not_require_startup_state():
    client = object.__new__(JiraClient)
    healthy = SimpleNamespace(get_json=lambda *_args, **_kwargs: {"name": "tester"})
    client._provider, client._provider_built = healthy, True
    assert client.provider is healthy


def test_ready_provider_does_not_wait_for_startup_state_lock(client):
    healthy = SimpleNamespace(close=lambda: None)
    client._provider, client._provider_built = healthy, True
    client._provider_lock.acquire()
    try:
        done, result = _call(lambda: client.provider)
        assert done.wait(0.5)
        assert result == {"value": healthy}
    finally:
        client._provider_lock.release()


def test_ready_provider_changed_during_health_check_uses_new_login(client):
    old_closed = []
    healthy = SimpleNamespace(close=lambda: None)

    class ReplacedProvider:
        @property
        def broken(self):
            client.reset_provider()
            return False

        def close(self):
            old_closed.append(1)

    client._provider, client._provider_built = ReplacedProvider(), True
    client._make_provider = lambda: healthy
    assert client.provider is healthy
    assert old_closed == [1]


@pytest.mark.parametrize("failure, expected", [
    (LoginRequired("saved session missing"), LoginRequired),
    (SessionExpired("authentication failed"), SessionExpired),
    (UpstreamBusy("initialization busy"), UpstreamBusy),
    (UpstreamError(401, "/myself", "denied"), UpstreamError),
    (UpstreamUnavailable("startup timeout"), UpstreamUnavailable),
    (RuntimeError("driver failed"), UpstreamUnavailable),
])
def test_failed_launch_is_shared_with_forty_callers_and_cools_down(client, failure, expected):
    barrier, started, release = threading.Barrier(41), threading.Event(), threading.Event()
    launches = []

    def make():
        launches.append(1)
        started.set()
        assert release.wait(2)
        raise failure

    client._make_provider = make

    def request():
        barrier.wait(timeout=2)
        return client.provider

    calls = [_call(request) for _ in range(40)]
    barrier.wait(timeout=2)
    assert started.wait(1)
    release.set()
    for done, result in calls:
        assert done.wait(1)
        assert type(result["error"]) is expected
    assert len({id(result["error"]) for _, result in calls}) == 40
    if expected is UpstreamError:
        assert all(result["error"].status == 401 for _, result in calls)
    assert launches == [1]
    for _ in range(10):
        with pytest.raises(expected):
            _ = client.provider
    assert launches == [1]

    # Only the first caller after the failure cooldown makes another attempt.
    client._provider_startup_failure = (time.monotonic() - 1, client._provider_startup_failure[1])
    recovered = SimpleNamespace(close=lambda: None)
    client._make_provider = lambda: launches.append(1) or recovered
    assert client.provider is recovered
    assert launches == [1, 1]


def test_followers_have_bounded_wait_without_holding_state_lock_or_restarting(client):
    started, release = threading.Event(), threading.Event()
    launches, healthy = [], SimpleNamespace(close=lambda: None)
    client.PROVIDER_WAIT_SECONDS = 0.04

    def make():
        launches.append(1)
        started.set()
        assert release.wait(2)
        return healthy

    client._make_provider = make
    owner_done, owner_result = _call(lambda: client.provider)
    assert started.wait(1)
    calls = [_call(lambda: client.provider) for _ in range(40)]
    try:
        for done, result in calls:
            assert done.wait(0.5)
            assert type(result["error"]) is UpstreamBusy
        assert not owner_done.is_set()
        assert launches == [1]
        assert client._provider_lock.acquire(blocking=False)
        client._provider_lock.release()
        assert not client.upstream_down()
        assert not getattr(client, "_session_dead", False)
    finally:
        release.set()
    assert owner_done.wait(1)
    assert owner_result == {"value": healthy}
    assert client.provider is healthy
    assert launches == [1]


@pytest.mark.parametrize("old_fails", [False, True])
def test_new_login_fences_old_launch_and_wakes_followers(client, old_fails):
    started, release, old_closed, new_closed = (threading.Event() for _ in range(4))
    old = SimpleNamespace(close=old_closed.set)
    new = SimpleNamespace(close=new_closed.set)

    def old_launch():
        started.set()
        assert release.wait(2)
        if old_fails:
            raise LoginRequired("obsolete missing session")
        return old

    client._make_provider = old_launch
    old_done, old_result = _call(lambda: client.provider)
    assert started.wait(1)
    old_future = client._provider_startup[1]
    try:
        reset_done, reset_result = _call(client.reset_provider)
        assert reset_done.wait(0.5), "new login must not wait for old Chromium startup"
        assert "error" not in reset_result
        assert isinstance(old_future.result(timeout=0.5).new_exception(), UpstreamBusy)
        assert old_future.exception() is None
        client._make_provider = lambda: new
        assert client.provider is new
    finally:
        release.set()
    assert old_done.wait(1)
    assert type(old_result["error"]) is UpstreamBusy
    assert old_closed.is_set() is (not old_fails)
    assert not new_closed.is_set()
    assert client.provider is new
    assert client._provider_startup_failure is None
    assert not client._session_dead


def test_new_login_clears_cached_auth_failure_immediately(client):
    def missing():
        raise LoginRequired("no session")

    client._make_provider = missing
    with pytest.raises(LoginRequired):
        _ = client.provider
    client.reset_provider()
    healthy = SimpleNamespace(close=lambda: None)
    client._make_provider = lambda: healthy
    assert client.provider is healthy


def test_cached_startup_failure_does_not_retain_request_locals_or_grow_tracebacks(client):
    futures = []

    def fail_startup():
        futures.append(client._provider_startup[1])
        try:
            raise RuntimeError("driver unavailable")
        except RuntimeError as cause:
            raise UpstreamUnavailable("startup failed") from cause

    class RequestBody:
        def __init__(self):
            self.data = bytearray(256 * 1024)

    def request():
        body = RequestBody()
        reference = weakref.ref(body)
        try:
            _ = client.provider
        except UpstreamUnavailable as exc:
            depth, trace = 0, exc.__traceback__
            while trace is not None:
                depth += 1
                trace = trace.tb_next
            assert exc.__cause__ is None and exc.__context__ is None
            return reference, depth
        raise AssertionError("provider unexpectedly started")

    client._make_provider = fail_startup
    results = [request() for _ in range(100)]
    gc.collect()
    assert all(reference() is None for reference, _ in results)
    assert max(depth for _, depth in results) < 5
    assert len(futures) == 1 and futures[0].exception() is None


@pytest.mark.parametrize("failure", [
    RuntimeError("https://private.example?token=private-secret"),
    UpstreamUnavailable("cookie=private-secret"),
    UpstreamError(401, "/myself?token=private-secret", "cookie=private-secret"),
])
def test_shared_startup_failure_does_not_publish_private_driver_details(client, failure):
    def make():
        raise failure

    client._make_provider = make
    for _ in range(2):
        with pytest.raises((UpstreamUnavailable, UpstreamError)) as error:
            _ = client.provider
        assert "private-secret" not in str(error.value)
        assert "private-secret" not in getattr(error.value, "body", "")
    assert "private-secret" not in repr(client._provider_startup_failure)
