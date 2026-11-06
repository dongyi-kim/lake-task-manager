"""Exercise real launcher functions without importing settings or touching user profiles."""

import ast
import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from support.paths import REPO_ROOT
from app.launcher import runtime_health


def launcher_functions(*names):
    tree = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"threading": threading, "time": time, "sys": sys,
                 "_APP_WINDOW_LOCK": threading.Lock(), "_WINDOW_ICONS": {}, "_BOOT_DATA_URL": "data:text/html,boot"}
    namespace["runtime_failures"] = []
    namespace["_record_runtime_failure"] = lambda component, error: namespace["runtime_failures"].append(
        (component, type(error).__name__))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "run.py", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("failure", [SystemExit(1), None])
def test_server_thread_exit_is_observed_without_waiting_for_startup_deadline(failure):
    ns = launcher_functions("_serve_bg", "_wait_server_ready", "_check_server")

    class Server:
        started = False

        def __init__(self, config):
            self.should_exit = False

        def run(self):
            if failure:
                raise failure

    ns["uvicorn"] = SimpleNamespace(Config=lambda *args, **kwargs: None, Server=Server)
    settings = SimpleNamespace(app_host="127.0.0.1", app_port=4457)
    server = ns["_serve_bg"](settings, wait=False)
    assert server._ltm_stopped.wait(1)
    started = time.monotonic()
    with pytest.raises(RuntimeError) as caught:
        ns["_wait_server_ready"](server, settings)
    assert time.monotonic() - started < .5
    assert caught.value.__cause__ is failure
    assert not server._ltm_thread.is_alive()
    assert ns["runtime_failures"] == ([("server", type(failure).__name__)] if failure else [])


def test_navigation_has_one_deadline_without_unbounded_evaluate():
    ns = launcher_functions("_navigate_app", "_check_server")
    clock = [0.0]
    budgets = []
    ns["time"] = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))

    class Page:
        def is_closed(self):
            return False

        def goto(self, url, *, wait_until, timeout):
            budgets.append(timeout)
            clock[0] += timeout / 1000
            raise TimeoutError("injected nonresponsive HTTP listener")

    with pytest.raises(RuntimeError):
        ns["_navigate_app"](Page(), "http://127.0.0.1:4457", timeout=3)
    assert clock[0] <= 3.001
    assert len(budgets) == 2
    assert max(budgets) <= 2000


def window_harness(monkeypatch, *, driver_error=False, launch_error=False, closing=None):
    ns = launcher_functions("_window_session", "_check_server")
    events, profiles = [], []
    main = ModuleType("app.main")
    main._login_requested = threading.Event()
    main.note_window_opened = lambda: events.append(("opened", threading.get_ident()))
    main.note_window_closed = lambda: events.append(("closed", threading.get_ident()))
    main.consume_focus_request = lambda: False
    main.request_service_login = lambda service: events.append(("login", service))
    app = ModuleType("app")
    app.main = main
    settings = ModuleType("app.infra.settings")
    settings.STATIC_DIR = Path("unused")
    page = SimpleNamespace(is_closed=lambda: False)

    class Context:
        pages = [page]

        def grant_permissions(self, permissions, *, origin):
            ns.setdefault("clipboard_grants", []).append((permissions, origin))

        def wait_for_event(self, name, timeout):
            if driver_error:
                raise RuntimeError("injected driver pipe failure")

        def close(self):
            events.append(("context.close", threading.get_ident()))
            if closing:
                closing[0].set()
                assert closing[1].wait(2)

    def launch(**kwargs):
        profiles.append(kwargs["user_data_dir"])
        if launch_error:
            raise RuntimeError("injected persistent profile lock")
        return Context()

    p = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch),
                        stop=lambda: events.append(("playwright.stop", threading.get_ident())))
    api = ModuleType("playwright.sync_api")
    api.TimeoutError = TimeoutError
    api.sync_playwright = lambda: SimpleNamespace(start=lambda: p)
    for name, module in {"app": app, "app.main": main, "app.infra.settings": settings,
                         "playwright.sync_api": api}.items():
        monkeypatch.setitem(sys.modules, name, module)
    ns.update(_appwin_profile=lambda: Path("persistent-profile"),
              _navigate_app=lambda *args: True, _wire_external_links=lambda *args: None,
              _wire_downloads=lambda *args: None, _drain_downloads=lambda *args: None,
              _set_window_icon_win=lambda *args: None, _window_phase=lambda *args: None)
    return ns, events, profiles


@pytest.mark.parametrize("driver_error,launch_error,port", [(False, False, 4457), (True, False, 4567), (False, True, 4457)])
def test_window_lifecycle_releases_live_count_and_cleans_up_on_owner(monkeypatch, driver_error, launch_error, port):
    ns, events, profiles = window_harness(monkeypatch, driver_error=driver_error, launch_error=launch_error)
    owner = threading.get_ident()
    if driver_error or launch_error:
        with pytest.raises(RuntimeError):
            ns["_window_session"](SimpleNamespace(app_port=port, jira_env="mock"))
    else:
        assert ns["_window_session"](SimpleNamespace(app_port=port, jira_env="prod"), auto_login=True)
        assert ("login", "jira") in events
    assert [name for name, _ in events].count("opened") == 1
    assert [name for name, _ in events].count("closed") == 1
    assert all(thread_id == owner for name, thread_id in events if name != "login")
    assert events[-1] == ("playwright.stop", owner)
    assert profiles == [str(Path("persistent-profile"))]  # Never retry with an empty temporary profile.
    assert ns.get("clipboard_grants", []) == ([] if launch_error else
        [(["clipboard-read"], f"http://127.0.0.1:{port}")])
    assert ns["_APP_WINDOW_LOCK"].acquire(blocking=False)
    ns["_APP_WINDOW_LOCK"].release()


def test_profile_has_one_owner_until_cleanup_finishes_then_reopens(monkeypatch):
    closing = (threading.Event(), threading.Event())
    ns, events, profiles = window_harness(monkeypatch, closing=closing)
    settings = SimpleNamespace(app_port=4457, jira_env="mock")
    worker = threading.Thread(target=ns["_window_session"], args=(settings,), daemon=True)
    worker.start()
    try:
        assert closing[0].wait(1)
        assert ns["_window_session"](settings) is False
        assert len(profiles) == 1
    finally:
        closing[1].set()
        worker.join(2)
    assert not worker.is_alive()
    assert ns["_window_session"](settings)
    assert profiles == [str(Path("persistent-profile"))] * 2


def test_native_window_icon_messages_have_a_timeout(monkeypatch):
    ns = launcher_functions("_set_window_icon_win")
    calls, loaded = [], []

    def send(*args):
        calls.append(args)
        return 0  # Hung foreign window is a normal bounded failure.

    def title(hwnd, buffer, size):
        buffer.value = "Lake Task Manager"

    def load(*args):
        loaded.append(args)
        return 0x100000123

    user32 = SimpleNamespace(EnumWindows=lambda callback, _: callback(123, 0),
                             GetWindowTextLengthW=lambda _: 17, IsWindowVisible=lambda _: True,
                             GetWindowTextW=title, LoadImageW=load,
                             SendMessageTimeoutW=send)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *args: lambda fn: fn, raising=False)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert ns["_set_window_icon_win"]("unused.ico")
    assert len(calls) == 2
    assert all(args[4:6] == (0x0002, 200) for args in calls)
    assert all(args[3] == 0x100000123 for args in calls)
    assert ns["_set_window_icon_win"]("unused.ico")
    assert len(loaded) == 2  # Reapplying the icon does not leak another pair of GDI handles.
    assert user32.LoadImageW.restype is ctypes.c_void_p


@pytest.mark.parametrize("phase,timeout", [("starting", 60), ("running", 15), ("closing", 60),
                                          ("login", 360), ("download", 900)])
def test_window_health_respects_operation_deadlines(monkeypatch, phase, timeout):
    clock = [0.0]
    monkeypatch.setattr(runtime_health, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(runtime_health, "_window_runtime", ("closed", 0))
    runtime_health.window_phase(phase, timeout)
    clock[0] = timeout - .01
    assert runtime_health.window_healthy()
    clock[0] = timeout + .01
    assert not runtime_health.window_healthy()
    runtime_health.window_phase("closed")
    clock[0] += 3600
    assert runtime_health.window_healthy()
    runtime_health.window_phase("failed")
    assert not runtime_health.window_healthy()


def test_native_tray_probe_only_targets_own_hwnd_and_skips_unsupported(monkeypatch):
    calls = []

    def send(*args):
        calls.append(args)
        return 0

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(SendMessageTimeoutW=send)), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime_health.probe_tray_pump(SimpleNamespace(_hwnd=123)) is False
    assert calls[0][:6] == (123, 0, 0, 0, 0x0022, 500)
    assert runtime_health.probe_tray_pump(SimpleNamespace()) is None
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(), raising=False)
    assert runtime_health.probe_tray_pump(SimpleNamespace(_hwnd=123)) is None


def test_blocking_tray_job_does_not_block_callbacks_and_coalesces_duplicates():
    tree = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    tray = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_tray")
    job = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "run_tray_job")
    callback = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "on_autostart")
    ns = {"threading": threading, "sys": sys, "_tray_jobs": set(), "_tray_jobs_lock": threading.Lock(),
          "autostart_state": {}, "_autostart_enabled": lambda: False}
    exec(compile(ast.Module(body=[job, callback], type_ignores=[]), "run.py", "exec"), ns)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_shortcut(enabled):
        calls.append(1)
        entered.set()
        assert release.wait(2)

    try:
        ns["_set_autostart"] = slow_shortcut
        icon = SimpleNamespace(update_menu=lambda: None)
        ns["on_autostart"](icon, None)
        assert entered.wait(1)
        ns["on_autostart"](icon, None)
        # Other tray work can still run while a filesystem/COM operation is blocked.
        opened = threading.Event()
        ns["run_tray_job"]("open", opened.set)
        assert opened.wait(1)
        assert calls == [1]
    finally:
        release.set()


def test_update_launch_starts_its_own_supervisor(monkeypatch):
    tree = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    tray = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_tray")
    restart = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "restart")
    server = SimpleNamespace(should_exit=False)
    ns = {"os": os, "sys": sys, "server": server, "_launcher_bat": lambda: REPO_ROOT / "run.py"}
    exec(compile(ast.Module(body=[restart], type_ignores=[]), "run.py", "exec"), ns)
    seen = []
    monkeypatch.setenv("LAKE_SUPERVISED_WORKER", "previous-worker-nonce")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: seen.append(kwargs))
    stopped = threading.Event()
    ns["restart"](SimpleNamespace(stop=stopped.set))
    assert "LAKE_SUPERVISED_WORKER" not in seen[0]["env"]
    assert seen[0]["env"]["LAKE_RESTART"] == "1"
    assert server.should_exit and stopped.is_set()


def test_slow_login_fallback_keeps_full_interactive_allowance_healthy(monkeypatch):
    ns = launcher_functions("_do_login_in_window", "_login_one_service")
    clock, phases = [0.0], []
    monkeypatch.setattr(runtime_health.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime_health, "_window_runtime", ("closed", 0))
    ns["time"] = SimpleNamespace(monotonic=lambda: clock[0])

    def phase(name, timeout):
        phases.append(name)
        runtime_health.window_phase(name, timeout)

    def elapsed(seconds):
        clock[0] += seconds
        assert runtime_health.window_healthy(), f"healthy login interrupted in {phases[-1]}"

    def probe(*_):
        elapsed(5)
        return clock[0] > 470, "fake identity"

    import app.auth.sso_session as sso
    import app.auth.sso_navigation as navigation
    monkeypatch.setattr(sso, "service_probe", probe)
    monkeypatch.setattr(navigation, "observe_login_page", lambda _: None)
    monkeypatch.setattr(navigation, "login_state", lambda _: {})
    ns.update(_window_phase=phase,
              _silent_renew_via_provider=lambda *_: elapsed(80) or False,
              _headless_cert_login=lambda *_: elapsed(80) or False,
              _poke_auth_changed=lambda _: None)
    login = SimpleNamespace(is_closed=lambda: False, url="https://jira.example",
        goto=lambda *a, **k: elapsed(30), wait_for_load_state=lambda *a, **k: elapsed(15),
        wait_for_timeout=lambda milliseconds: elapsed(milliseconds / 1000), close=lambda: None)
    page = object()
    context = SimpleNamespace(new_page=lambda: login, pages=[page, login])
    client = SimpleNamespace(sso_store=lambda: SimpleNamespace(save=lambda *_: None), reset_provider=lambda: None)
    settings = SimpleNamespace(jira_base="https://jira.example", auth_targets=None)
    assert ns["_do_login_in_window"](settings, page, context, SimpleNamespace(_client=client),
                                      p=object(), service="jira")
    assert clock[0] > 360
    assert "login-input" in phases and "login-save" in phases
