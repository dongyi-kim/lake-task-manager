"""Authentication maintenance behavior without real Jira or user credentials."""
import itertools
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.auth.base import PRIO_BACKGROUND, SessionExpired, UpstreamUnavailable, background_upstream
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
