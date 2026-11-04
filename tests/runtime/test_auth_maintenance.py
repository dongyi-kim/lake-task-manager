"""Authentication maintenance behavior without real Jira or user credentials."""
import itertools
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.auth.base import LoginRequired, PRIO_BACKGROUND, SessionExpired, UpstreamBusy, UpstreamUnavailable, background_upstream
from app.auth.sso_session import SsoSessionProvider
from app.auth.sso_store import SsoStore
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


def client(tmp_path):
    c = JiraClient(get_settings(), Cache(":memory:"))
    c.env = "prod"
    c.s.auth_targets = [("Jira", "https://jira.example", ["/rest/api/2/myself"])]
    c._sso_store = SsoStore(str(tmp_path / "state.json"), {"jira": "https://jira.example"})
    return c


def cookie(value, domain="jira.example", name="JSESSIONID"):
    return {"name": name, "value": value, "domain": domain, "path": "/"}


def test_timer_ticks_check_jira_and_persist_rotating_cookies_every_four_minutes(tmp_path, monkeypatch):
    import app.jira.identity_service as identity
    c = client(tmp_path)
    clock, calls, snapshots = [1000.0], [], []
    monkeypatch.setattr(identity.time, "monotonic", lambda: clock[0])
    c._provider = SimpleNamespace(
        get_json=lambda *a, **kw: calls.append(clock[0]) or {"name": "tester"},
        storage_state_snapshot=lambda: snapshots.append(clock[0]) or {
            "cookies": [cookie(str(clock[0]))], "origins": []},
    )
    c._provider_built = True
    for _ in range(8):
        c.keepalive_auth()
        c.proactive_auth_probe()  # Simultaneous active tab uses the same recently checked state.
        clock[0] += c.KEEPALIVE_EVERY
    assert len(calls) == len(snapshots) == 8
    assert c.sso_store().merged()["cookies"][0]["value"] == str(clock[0] - 240)
    diag = c.upstream_state()["authMaintenance"]
    assert diag["state"] == "alive" and diag["failure"] is None
    assert diag["nextCheckAt"] - diag["lastCheckedAt"] == pytest.approx(240, abs=0.1)


def test_focus_timer_and_status_join_one_inflight_probe(tmp_path, monkeypatch):
    c = client(tmp_path)
    started, release = threading.Event(), threading.Event()
    calls = []

    def probe(**_):
        calls.append(1)
        started.set()
        assert release.wait(2)
        return "alive"

    monkeypatch.setattr(c, "direct_session_state", probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(c.proactive_auth_probe)
        assert started.wait(1)
        pending = c.proactive_auth_probe()
        assert pending["pending"] and not pending["needLogin"]
        assert c.upstream_state()["authMaintenance"]["state"] == "checking"
        release.set()
        assert task.result(timeout=2)["ok"]
    assert calls == [1]


def test_network_loss_during_silent_renewal_does_not_require_login(tmp_path, monkeypatch):
    c = client(tmp_path)
    states = iter(["expired", "unknown"])
    monkeypatch.setattr(c, "direct_session_state", lambda **_: next(states))
    monkeypatch.setattr(c, "_renew_service", lambda _: False)
    result = c.proactive_auth_probe()
    assert result == {"ok": False, "needLogin": False, "mode": "degraded"}
    assert c.upstream_state()["authMaintenance"]["failure"] == "transport"


def test_expired_idp_requires_login_without_opening_a_window(tmp_path, monkeypatch):
    c = client(tmp_path)
    monkeypatch.setattr(c, "direct_session_state", lambda **_: "expired")
    monkeypatch.setattr(c, "_renew_service", lambda _: False)
    assert c.proactive_auth_probe()["needLogin"]
    assert c.upstream_state()["authMaintenance"]["state"] == "login_required"


def test_concurrent_renewals_reuse_success_and_cannot_overwrite_new_login(tmp_path):
    c = client(tmp_path)
    store = c.sso_store()
    store.save("jira", {"cookies": [cookie("initial")], "origins": []})
    calls = []

    def renew(_targets, save_cb):
        calls.append(1)
        store.save("jira", {"cookies": [cookie("new-login")], "origins": []})
        assert save_cb({"cookies": [cookie("old-provider")], "origins": []}) is False
        return True

    c._provider = SimpleNamespace(renew_silent=renew)
    c._provider_built = True
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(c._renew_service, ["Jira"] * 4))
    assert all(results) and calls == [1]
    assert store.merged()["cookies"][0]["value"] == "new-login"


def test_new_idp_login_fences_old_jira_snapshot(tmp_path):
    store = client(tmp_path).sso_store()
    store.save("jira", {"cookies": [cookie("initial")], "origins": []})
    revision = store.revision("jira")
    # A login to another service changes the shared IdP session but not Jira's own file.
    store.save("confluence", {"authOrigins": ["https://idp.example"],
        "cookies": [cookie("new-idp", "idp.example", "IDP")], "origins": []})
    assert not store.save_if_unchanged("jira", {"authOrigins": ["https://idp.example"],
        "cookies": [cookie("stale"), cookie("old-idp", "idp.example", "IDP")],
        "origins": []}, revision)
    assert {c["value"] for c in store.merged()["cookies"]} == {"initial", "new-idp"}


def test_queued_background_timeout_does_not_retire_busy_provider():
    p = object.__new__(SsoSessionProvider)
    p._broken, p._closed = threading.Event(), threading.Event()
    p._thread = SimpleNamespace(is_alive=lambda: True)
    p._jobs, p._seq = queue.PriorityQueue(), itertools.count()
    ran = []
    with pytest.raises(UpstreamUnavailable):
        p._submit(lambda: ran.append(1), PRIO_BACKGROUND, wait=0.001)
    assert not p.broken
    _priority, _seq, (job, _done, _box) = p._jobs.get_nowait()
    job()  # Eventually dequeued work must be cancelled, not re-run after the next timer.
    assert ran == []


def test_silent_renewal_respects_background_priority():
    p = object.__new__(SsoSessionProvider)
    captured = []
    p._submit = lambda fn, priority: captured.append(priority)
    with background_upstream():
        p.renew_silent([])
    assert captured == [PRIO_BACKGROUND]


@pytest.mark.parametrize("expired", [False, True])
def test_retired_provider_probe_cannot_change_new_login_state(tmp_path, expired):
    c = client(tmp_path)

    def old_probe(*_args, **_kwargs):
        c._provider = SimpleNamespace()
        c._provider_generation += 1
        c.mark_session_alive()
        if expired:
            raise SessionExpired("old session")
        return {"name": "old-user"}

    c._provider = SimpleNamespace(get_json=old_probe)
    c._provider_built = True
    assert c.direct_session_state(background=True) == "unknown"
    assert not c._session_dead


def test_missing_initial_session_is_login_required_not_transport_failure(tmp_path, monkeypatch):
    c = client(tmp_path)

    def missing_provider():
        raise LoginRequired("fixture: no saved session")

    monkeypatch.setattr(c, "_make_provider", missing_provider)
    assert c.direct_session_state() == "expired"
    assert c.proactive_auth_probe(force=True, wait=True)["needLogin"]
    assert c.upstream_state()["authMaintenance"]["failure"] == "authentication"
    assert not c._provider_built


def test_provider_creation_failure_cannot_override_a_new_login(tmp_path, monkeypatch):
    c = client(tmp_path)

    def replaced_during_construction():
        c._provider = SimpleNamespace()
        c._provider_generation += 1
        c.mark_session_alive()
        raise LoginRequired("fixture: old missing session")

    monkeypatch.setattr(c, "_make_provider", replaced_during_construction)
    with pytest.raises(UpstreamBusy):
        c.direct_session_state()
    assert not c._session_dead


@pytest.mark.parametrize("previous", [
    {"ok": True, "needLogin": False, "mode": "ok"},
    {"ok": False, "needLogin": True, "mode": "authenticating"},
    {"ok": False, "needLogin": False, "mode": "degraded"},
])
def test_forced_probe_bypasses_recent_result_after_auth_error(tmp_path, monkeypatch, previous):
    import app.jira.identity_service as identity
    c = client(tmp_path)
    monkeypatch.setattr(identity.time, "monotonic", lambda: 1000.0)
    c._auth_probe_at, c._auth_probe_result = 1000.0, previous
    calls = []
    monkeypatch.setattr(c, "direct_session_state", lambda **_: calls.append(1) or "alive")

    assert c.proactive_auth_probe() == {**previous, "cached": True}
    assert calls == []
    assert c.proactive_auth_probe(force=True, wait=True) == {"ok": True, "needLogin": False, "mode": "ok"}
    assert calls == [1]
    # A subsequent independent authentication error must not be hidden by that success.
    c.proactive_auth_probe(force=True, wait=True)
    assert calls == [1, 1]


def test_concurrent_forced_probes_wait_for_one_silent_recovery(tmp_path, monkeypatch):
    c = client(tmp_path)
    started, release, all_arrived = threading.Event(), threading.Event(), threading.Event()
    calls, renewals = [], []

    class ObservedLock:
        def __init__(self):
            self.lock, self.guard, self.arrived = threading.Lock(), threading.Lock(), 0

        def acquire(self, *, timeout):
            with self.guard:
                self.arrived += 1
                if self.arrived == 4:
                    all_arrived.set()
            return self.lock.acquire(timeout=timeout)

        def release(self):
            self.lock.release()

    c._auth_probe_lock = ObservedLock()

    def probe(**_):
        calls.append(1)
        return "expired" if len(calls) == 1 else "alive"

    def renew(_):
        renewals.append(1)
        started.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(c, "direct_session_state", probe)
    monkeypatch.setattr(c, "_renew_service", renew)
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(c.proactive_auth_probe, force=True, wait=True)
        assert started.wait(1)
        followers = [pool.submit(c.proactive_auth_probe, force=True, wait=True) for _ in range(3)]
        assert all_arrived.wait(1)
        release.set()
        assert first.result(timeout=2)["recovered"]
        results = [task.result(timeout=2) for task in followers]
    assert all(result["ok"] and result["recovered"] and result["shared"] for result in results)
    assert calls == [1, 1] and renewals == [1]


def test_waiting_probe_timeout_is_pending_not_failed_auth(tmp_path, monkeypatch):
    c = client(tmp_path)
    c.AUTH_PROBE_WAIT_SECONDS = 0.001
    monkeypatch.setattr(c, "direct_session_state", lambda **_: pytest.fail("must join existing probe"))
    with c._auth_probe_lock:
        result = c.proactive_auth_probe(force=True, wait=True)
    assert result == {"ok": False, "needLogin": False, "pending": True,
                      "mode": "checking", "timedOut": True}
    assert not getattr(c, "_session_dead", False)


def test_waiter_does_not_reuse_completed_alive_after_a_new_confirmed_failure(tmp_path, monkeypatch):
    c = client(tmp_path)
    started, release, follower_waiting = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class FailureAfterCompletionLock:
        def __init__(self):
            self.lock, self.guard, self.arrived = threading.Lock(), threading.Lock(), 0

        def acquire(self, *, timeout):
            with self.guard:
                self.arrived += 1
                follower = self.arrived == 2
                if follower:
                    follower_waiting.set()
            acquired = self.lock.acquire(timeout=timeout)
            if acquired and follower:
                # The first check completed alive, but another request has now confirmed a
                # newer authentication failure before this waiter can reuse its result.
                assert c._auth_probe_completed_result["ok"]
                c.mark_session_dead("fixture: later confirmed 401")
            return acquired

        def release(self):
            self.lock.release()

    c._auth_probe_lock = FailureAfterCompletionLock()

    def probe(**_):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert release.wait(2)
            return "alive"
        return "expired"

    monkeypatch.setattr(c, "direct_session_state", probe)
    monkeypatch.setattr(c, "_renew_service", lambda _: False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(c.proactive_auth_probe, force=True, wait=True)
        assert started.wait(1)
        follower = pool.submit(c.proactive_auth_probe, force=True, wait=True)
        assert follower_waiting.wait(1)
        release.set()
        assert first.result(timeout=2)["ok"]
        result = follower.result(timeout=2)
    assert result["needLogin"] and not result["ok"] and not result.get("shared")
    assert c._session_dead and calls == [1, 1, 1]
