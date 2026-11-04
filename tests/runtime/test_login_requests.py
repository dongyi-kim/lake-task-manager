"""Interactive login orchestration; no credentials, actual browser, delay or real SSO."""

import threading
import json
import sys
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

import pytest

from app.auth.login_requests import LoginRequests


def test_simultaneous_requests_share_one_attempt_and_claim_per_service():
    requests, dispatched = LoginRequests(), []
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: requests.request("jira", dispatched.append), range(24)))
    assert sum(row["started"] for row in rows) == 1
    assert dispatched == ["jira"] and all(row["pending"] for row in rows)
    requests.request("confluence", dispatched.append)
    assert requests.claim() == "jira"
    assert requests.requested.is_set()
    assert requests.claim("jira") is None
    assert requests.claim() == "confluence"
    assert not requests.requested.is_set()
    requests.complete("jira", True)
    assert requests.status("jira")["succeeded"]
    assert "authenticated" not in requests.status("jira")
    assert requests.status("confluence")["pending"]


def test_old_completion_cannot_finish_a_new_attempt():
    requests = LoginRequests()
    first = requests.request("jira", lambda _: None)
    requests.claim("jira")
    requests.complete("jira", False, first["attemptId"])
    second = requests.request("jira", lambda _: None)
    requests.claim("jira")
    requests.complete("jira", False, first["attemptId"])
    assert second["attemptId"] > first["attemptId"]
    assert requests.status("jira")["state"] == "running"


def test_dispatch_failure_releases_attempt_without_claiming_login_success():
    requests = LoginRequests()
    def failed(_service):
        raise RuntimeError("fixture failure")
    with pytest.raises(RuntimeError):
        requests.request("jira", failed)
    assert requests.status("jira")["state"] == "completed"
    assert not requests.status("jira")["succeeded"]
    assert not requests.requested.is_set()
    assert requests.request("jira", lambda _: None)["started"]


@pytest.fixture
def bridge(monkeypatch):
    import app.main as main
    requests = LoginRequests()
    monkeypatch.setattr(main, "_login_requests", requests)
    monkeypatch.setattr(main, "_login_requested", requests.requested)
    monkeypatch.setattr(main, "_settings", SimpleNamespace(jira_env="prod", auth_targets=[
        ("Jira", "https://jira.example", ["/rest/api/2/myself"]),
        ("Confluence", "https://confluence.example", ["/rest/api/user/current"]),
    ]))
    monkeypatch.setattr(main, "_app_ctrl", {**main._app_ctrl, "live": 0, "open_hook": None,
                                          "focus": threading.Event(), "lock": threading.Lock()})
    reset = []
    monkeypatch.setattr(main, "_client", SimpleNamespace(
        sso_store=lambda: "fixture-store", reset_provider=lambda: reset.append(True)))
    return SimpleNamespace(main=main, requests=requests, reset=reset)


def test_ui_and_mcp_requests_join_same_jira_attempt_and_validate_service(bridge, monkeypatch):
    dispatched = []
    monkeypatch.setattr(bridge.main, "_dispatch_login", dispatched.append)
    assert bridge.main.api_login()["started"]
    assert not bridge.main.request_service_login("jira")["started"]
    assert bridge.main.request_service_login("confluence")["started"]
    assert dispatched == ["jira", "confluence"]
    with pytest.raises(ValueError):
        bridge.main.request_service_login("unconfigured")


def test_hidden_tray_window_is_reopened_and_service_is_not_replaced_by_jira(bridge, monkeypatch):
    import run as launcher
    opened, timers, calls = [], [], []
    bridge.main._app_ctrl["open_hook"] = lambda: opened.append(True)
    class Timer:
        def __init__(self, delay, target, args):
            self.delay, self.target, self.args = delay, target, args
        def start(self):
            timers.append(self)
    monkeypatch.setattr(bridge.main.threading, "Timer", Timer)
    monkeypatch.setattr(launcher, "_do_login_in_window",
                        lambda *_args, **kwargs: calls.append(kwargs["service"]) or True)
    first = bridge.main.request_service_login("confluence")
    assert first["pending"] and opened == [True] and len(timers) == 1
    launcher._run_pending_login(None, None, None, bridge.main)
    assert calls == ["confluence"]
    assert bridge.requests.status("confluence")["succeeded"]
    # Late fallback cannot repeat an attempt already consumed by the managed window.
    timers[0].target(*timers[0].args)
    assert bridge.reset == []


def test_browser_only_server_returns_before_login_and_coalesces_until_completion(bridge, monkeypatch):
    import app.auth.sso_session as sso
    workers, calls = [], []
    class Worker:
        def __init__(self, target, args, **_kwargs):
            self.target, self.args = target, args
        def start(self):
            workers.append(self)
    monkeypatch.setattr(bridge.main.threading, "Thread", Worker)
    monkeypatch.setattr(sso, "login_wait", lambda base, store, **kwargs:
                        calls.append((base, store, kwargs)) or True)
    result = bridge.main.request_service_login("confluence")
    assert result["pending"] and result["started"] and not calls
    assert not bridge.main.request_service_login("confluence")["started"]
    assert len(workers) == 1
    workers[0].target(*workers[0].args)
    assert calls == [("https://confluence.example", "fixture-store", {
        "service": "confluence", "timeout": 300, "paths": ["/rest/api/user/current"]})]
    assert bridge.reset == [True]
    assert not bridge.requests.status("confluence")["pending"]


def test_queued_request_falls_back_when_managed_window_never_opens(bridge, monkeypatch):
    import app.auth.sso_session as sso
    bridge.main._app_ctrl["open_hook"] = lambda: None
    timers, calls = [], []
    class Timer:
        def __init__(self, delay, target, args):
            self.target, self.args = target, args
        def start(self):
            timers.append(self)
    monkeypatch.setattr(bridge.main.threading, "Timer", Timer)
    monkeypatch.setattr(sso, "login_wait", lambda *args, **kwargs: calls.append(kwargs["service"]) or False)
    bridge.main.request_service_login("jira")
    timers[0].target(*timers[0].args)
    assert calls == ["jira"] and not bridge.requests.status("jira")["pending"]
    assert not bridge.requests.status("jira")["succeeded"]
    assert bridge.reset == []


def test_window_login_failure_always_releases_the_request(bridge, monkeypatch):
    import run as launcher
    monkeypatch.setattr(bridge.main, "_dispatch_login", lambda _: None)
    bridge.main.request_service_login("jira")
    def crashed(*_args, **_kwargs):
        raise RuntimeError("closed fixture window")
    monkeypatch.setattr(launcher, "_do_login_in_window", crashed)
    with pytest.raises(RuntimeError):
        launcher._run_pending_login(None, None, None, bridge.main)
    assert not bridge.requests.status("jira")["pending"]
    assert not bridge.requests.status("jira")["succeeded"]


@pytest.mark.parametrize("save_fails", [False, True])
def test_managed_login_selects_confluence_and_keeps_the_app_page(monkeypatch, save_fails):
    import run as launcher
    import app.auth.sso_session as sso
    import app.auth.sso_navigation as navigation
    probes, saves, resets = [], [], []
    page = SimpleNamespace()
    context = SimpleNamespace(pages=[page])
    settings = SimpleNamespace(jira_base="https://jira.example", auth_targets=[
        ("Jira", "https://jira.example", ["/myself"]),
        ("Confluence", "https://confluence.example", ["/current"]),
    ])
    def save(*args):
        saves.append(args)
        if save_fails:
            raise OSError("fixture disk failure")
    client = SimpleNamespace(sso_store=lambda: SimpleNamespace(save=save),
                             reset_provider=lambda: resets.append(True))
    monkeypatch.setattr(sso, "service_probe", lambda _ctx, base, paths: probes.append((base, paths)) or (True, "ready"))
    monkeypatch.setattr(navigation, "login_state", lambda _: {"cookies": [], "origins": []})
    monkeypatch.setattr(launcher, "_poke_auth_changed", lambda _: None)
    assert launcher._do_login_in_window(settings, page, context, SimpleNamespace(_client=client),
                                       service="confluence") is not save_fails
    assert probes == [("https://confluence.example", ["/current"])]
    assert saves[0][0] == "confluence" and resets == ([] if save_fails else [True])


def test_status_exposes_only_latest_jira_login_attempt(bridge, monkeypatch):
    main = bridge.main
    monkeypatch.setattr(main, "_dispatch_login", lambda _: None)
    monkeypatch.setattr(main, "_client", SimpleNamespace(
        session_recheck_async=lambda: None, upstream_state=lambda: {"down": False}, needs_login=lambda: True))
    monkeypatch.setattr(main, "_probe_online", lambda: True)
    monkeypatch.setattr(main._local_mcp.workspace_config, "revision", lambda: "fixture")
    result = main.api_login()
    main.complete_login_request("jira", False, result["attemptId"])
    status = json.loads(main.api_status().body)
    assert status["needLogin"] is True
    assert status["loginAttempt"]["attemptId"] == result["attemptId"]
    assert status["loginAttempt"]["state"] == "completed"
    assert status["loginAttempt"]["succeeded"] is False


@pytest.mark.parametrize("service,paths,endpoint,user", [
    ("confluence", ["/rest/api/user/current"], "/rest/api/user/current", {"username": "fixture-user"}),
    ("jira", None, "/rest/api/2/myself", {"name": "fixture-user"}),
])
def test_login_wait_probes_configured_service_before_saving(monkeypatch, service, paths, endpoint, user):
    import app.auth.sso_session as sso
    # Exercise the real login loop and service probe without the optional browser package.
    playwright = ModuleType("playwright")
    playwright.__path__ = []
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: nullcontext(object())
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    calls, saves, closed = [], [], []
    def get(url, **_kwargs):
        calls.append(url)
        matched = url == "https://service.example" + endpoint
        return SimpleNamespace(status=200 if matched else 404, json=lambda: user if matched else {})
    page = SimpleNamespace(goto=lambda *_args, **_kwargs: None)
    context = SimpleNamespace(request=SimpleNamespace(get=get), new_page=lambda: page)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: closed.append(True))
    monkeypatch.setattr(sso, "_launch", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr(sso, "observe_login_page", lambda _: None)
    monkeypatch.setattr(sso, "login_state", lambda _: {"cookies": [], "origins": []})
    store = SimpleNamespace(save=lambda *args: saves.append(args))
    assert sso.login_wait("https://service.example", store, service=service, timeout=1, paths=paths)
    assert calls == ["https://service.example" + endpoint]
    assert saves == [(service, {"cookies": [], "origins": []})]
    assert closed == [True]
