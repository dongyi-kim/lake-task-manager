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
from app.launcher.window_recovery import BrowserOwnerProbe, WindowSessionFailure, run_window_recovery


def launcher_functions(*names):
    tree = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"threading": threading, "time": time, "sys": sys,
                 "_APP_WINDOW_LOCK": threading.Lock(), "_WINDOW_ICONS": {}, "_BOOT_DATA_URL": "data:text/html,boot",
                 "WindowSessionFailure": WindowSessionFailure}
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


def window_harness(monkeypatch, *, driver_error=False, launch_error=False, closing=None, browser_crash=False,
                   cleanup_error=False):
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
    closed, handlers = [False], {}
    page = SimpleNamespace(is_closed=lambda: closed[0], on=lambda event, callback: handlers.update({event: callback}))

    class Context:
        pages = [page]

        def grant_permissions(self, permissions, *, origin):
            ns.setdefault("clipboard_grants", []).append((permissions, origin))

        def wait_for_event(self, name, timeout):
            if browser_crash and (not callable(browser_crash) or browser_crash()):
                handlers["crash"]()
                closed[0] = True
                raise RuntimeError("closed after renderer crash")
            if driver_error:
                raise RuntimeError("injected driver pipe failure")

        def close(self):
            events.append(("context.close", threading.get_ident()))
            if cleanup_error:
                raise RuntimeError("injected cleanup failure")
            if closing:
                closing[0].set()
                assert closing[1].wait(2)

    def launch(**kwargs):
        profiles.append(kwargs["user_data_dir"])
        closed[0] = False
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


def test_renderer_crash_followed_by_page_close_is_saved_and_allows_local_recovery(monkeypatch):
    ns, _events, _profiles = window_harness(monkeypatch, browser_crash=True)
    phases = []
    ns["_window_phase"] = lambda phase, *args: phases.append(phase)
    with pytest.raises(WindowSessionFailure) as caught:
        ns["_window_session"](SimpleNamespace(app_port=4457, jira_env="mock"))
    assert ns["runtime_failures"] == [("browser", "RuntimeError")]
    assert caught.value.cleanup_confirmed and phases[-1] == "recovering"


def test_real_window_session_recreates_only_after_owner_cleanup_and_keeps_server_running(monkeypatch):
    crashes = [True, False]
    ns, events, profiles = window_harness(monkeypatch, browser_crash=lambda: crashes.pop(0))
    clock, recovery = [0.0], []
    settings = SimpleNamespace(app_port=4457, jira_env="mock")
    server = SimpleNamespace(should_exit=False, _ltm_stopped=threading.Event())

    def session(*, on_ready):
        return ns["_window_session"](settings, server=server, on_ready=on_ready)

    assert run_window_recovery(session, should_stop=lambda: server.should_exit,
        clock=lambda: clock[0], wait=lambda delay: clock.__setitem__(0, clock[0] + delay),
        record=lambda event, **fields: recovery.append(event))
    assert profiles == [str(Path("persistent-profile"))] * 2
    assert [name for name, _thread in events] == ["opened", "closed", "context.close", "playwright.stop"] * 2
    assert all(thread_id == threading.get_ident() for _name, thread_id in events)
    assert not server.should_exit and not server._ltm_stopped.is_set()
    assert recovery == ["browser_recovery_started", "browser_recovery_completed"]


def test_failed_context_cleanup_retains_profile_lease_and_requires_process_recovery(monkeypatch):
    ns, _events, profiles = window_harness(monkeypatch, cleanup_error=True)
    phases = []
    ns["_window_phase"] = lambda phase, *args: phases.append(phase)
    settings = SimpleNamespace(app_port=4457, jira_env="mock")
    with pytest.raises(WindowSessionFailure) as caught:
        ns["_window_session"](settings)
    assert not caught.value.cleanup_confirmed and phases[-1] == "failed"
    assert ns["_APP_WINDOW_LOCK"].locked()
    assert ns["_window_session"](settings) is False
    assert len(profiles) == 1


@pytest.mark.parametrize("driver_exit,browser_exit,termination_status,stop_raises,reason,confirmed", [
    (None, 0, 0, False, None, True),
    (None, 17, 17, False, "browser_crash", True),
    (None, None, 17, True, "cleanup_failed", False),
    (17, 0, 0, True, "driver_disconnected", True),
    (17, None, None, True, "cleanup_failed", False),
])
def test_closed_page_uses_owned_process_evidence_instead_of_assuming_user_intent(
        monkeypatch, driver_exit, browser_exit, termination_status, stop_raises, reason, confirmed):
    from app.launcher import window_recovery
    ns, _events, _profiles = window_harness(monkeypatch)
    probe = object.__new__(BrowserOwnerProbe)
    probe._driver = SimpleNamespace(returncode=driver_exit)
    probe._driver_error, probe._handle = None, None
    probe._browser_exit = lambda: browser_exit
    probe._browser_termination_status = lambda: termination_status
    if driver_exit is None and termination_status == 17:
        assert probe.failure_reason() == "browser_crash"
    monkeypatch.setattr(window_recovery, "BrowserOwnerProbe", lambda *args: probe)
    ns["_wire_external_links"] = lambda context, page: setattr(page, "is_closed", lambda: True)
    if stop_raises:
        def broken_stop():
            raise RuntimeError("driver already exited")
        p = sys.modules["playwright.sync_api"].sync_playwright().start()
        p.stop = broken_stop
    settings = SimpleNamespace(app_port=4457, jira_env="mock")
    if reason is None:
        assert ns["_window_session"](settings) is True
        assert ns["runtime_failures"] == []
    else:
        with pytest.raises(WindowSessionFailure) as caught:
            ns["_window_session"](settings)
        assert caught.value.reason == reason and caught.value.cleanup_confirmed is confirmed
    assert ns["_APP_WINDOW_LOCK"].locked() is not confirmed


def test_user_page_close_during_navigation_never_respawns(monkeypatch):
    ns, _events, profiles = window_harness(monkeypatch)
    ns["_wire_external_links"] = lambda context, page: setattr(page, "is_closed", lambda: True)

    def closed_navigation(*args):
        raise RuntimeError("page closed during pending navigation")

    ns["_navigate_app"] = closed_navigation
    settings = SimpleNamespace(app_port=4457, jira_env="mock")
    assert run_window_recovery(lambda **kwargs: ns["_window_session"](settings, **kwargs),
                               should_stop=lambda: False) is False
    assert len(profiles) == 1 and ns["runtime_failures"] == []


@pytest.mark.parametrize("mode", ["tray", "nontray"])
def test_production_window_wrapper_quit_and_server_stop_cancel_recovery(monkeypatch, mode):
    from app.launcher import window_recovery
    ns, _events, profiles = window_harness(monkeypatch, browser_crash=True)
    tree = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    wrapper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_window_with_recovery")
    nontray = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_app_window")
    tray = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_tray")
    open_callback = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "open_window")
    quit_callback = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "on_quit")
    exec(compile(ast.Module(body=[wrapper, nontray, open_callback, quit_callback], type_ignores=[]), "run.py", "exec"), ns)
    server = SimpleNamespace(should_exit=False, _ltm_stopped=threading.Event())
    window_stop, stopped, callbacks, recovery, hooks = threading.Event(), [], [], [], []
    icon = SimpleNamespace(stop=lambda: stopped.append(1))
    ns.update(server=server, window_stop=window_stop, _runtime_status=runtime_health.runtime_status,
        _serve_bg=lambda *args, **kwargs: server, _warm_session_bg=lambda settings: None,
        _hide_console_win=lambda: callbacks.append(1),
        _record_browser_recovery=lambda event, **fields: recovery.append(event))
    sys.modules["app.main"].set_runtime_health_hook = lambda hook: hooks.append(hook)
    original = window_recovery.run_window_recovery
    forwarded, clock = [], [0.0]

    def wait(delay):
        clock[0] += delay
        if mode == "tray":
            ns["on_quit"](icon, None)
        else:
            server.should_exit = True
        return False

    def controlled(session, **kwargs):
        forwarded.append(kwargs)
        return original(session, clock=lambda: clock[0], wait=wait, **kwargs)

    monkeypatch.setattr(window_recovery, "run_window_recovery", controlled)
    settings = SimpleNamespace(app_port=4457, jira_env="mock")
    if mode == "tray":
        ns.update(s=settings, _open={"t": 0.0, "ready": True}, _OPEN_MIN=1.5, _OPEN_WAIT=15.0,
            threading=SimpleNamespace(Thread=lambda **kwargs: SimpleNamespace(start=kwargs["target"])))
        ns["open_window"](initial=True)
        assert forwarded[0]["stop_event"] is window_stop and window_stop.is_set() and stopped == [1]
    else:
        ns["_run_app_window"](settings)
        assert hooks == [runtime_health.runtime_status]
    assert server.should_exit and len(profiles) == 1 and callbacks == [1]
    assert recovery == ["browser_recovery_started", "browser_recovery_cancelled"]


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
                                          ("login", 360), ("download", 900), ("recovering", 90)])
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


def test_recovering_browser_preserves_liveness_only_while_tray_and_owner_are_responsive(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(runtime_health, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(runtime_health, "_window_runtime", ("closed", 0))
    runtime_health.window_phase("recovering", 90)
    state = runtime_health.runtime_status({"healthy": True, "at": clock[0]})
    assert state["healthy"] and state["windowHealthy"] and state["windowRecovering"]
    assert not runtime_health.runtime_status({"healthy": False, "at": clock[0]})["healthy"]
    clock[0] += 91
    assert not runtime_health.runtime_status({"healthy": True, "at": clock[0]})["healthy"]


def test_nontray_window_owner_deadlines_are_reported_to_process_liveness(monkeypatch):
    monkeypatch.setattr(runtime_health, "_window_runtime", ("closed", 0))
    runtime_health.window_phase("recovering", 60)
    assert runtime_health.runtime_status()["healthy"]
    assert "trayHealthy" not in runtime_health.runtime_status()
    runtime_health.window_phase("failed")
    assert not runtime_health.runtime_status()["healthy"]


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


@pytest.mark.parametrize("hide_console", [False, True])
def test_normal_runtime_supervision_preserves_console_mode_and_skips_owned_child(monkeypatch, tmp_path, hide_console):
    from app.launcher import supervisor
    from app.infra import runtime_diagnostics, settings
    ns = launcher_functions("_supervise_runtime")
    called, closed = [], []
    monkeypatch.delenv(supervisor.WORKER_ENV, raising=False)
    monkeypatch.setattr(supervisor, "run_supervisor", lambda *args, **kwargs: called.append((args, kwargs)) or 0)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(runtime_diagnostics, "RuntimeLog", lambda *args: SimpleNamespace(
        record=lambda *args, **kwargs: None, close=lambda: closed.append(True)))
    hidden, shown = lambda: None, lambda: None
    ns.update(os=os, Path=Path, __file__=str(REPO_ROOT / "run.py"),
        _hide_console_win=hidden, _show_console_win=shown)
    with pytest.raises(SystemExit) as stopped:
        ns["_supervise_runtime"](SimpleNamespace(app_port=4457), hide_console=hide_console)
    assert stopped.value.code == 0 and closed == [True]
    assert called[0][1]["on_ready"] is (hidden if hide_console else None)
    assert called[0][1]["on_failure"] is shown
    monkeypatch.setenv(supervisor.WORKER_ENV, "already-owned-generation")
    ns["_supervise_runtime"](SimpleNamespace(app_port=4457), hide_console=hide_console)
    assert len(called) == 1


def test_update_launch_starts_its_own_supervisor(monkeypatch):
    tree = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    tray = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_tray")
    restart = next(node for node in tray.body if isinstance(node, ast.FunctionDef) and node.name == "restart")
    server = SimpleNamespace(should_exit=False)
    window_stop = threading.Event()
    ns = {"os": os, "sys": sys, "server": server, "window_stop": window_stop,
          "_launcher_bat": lambda: REPO_ROOT / "run.py"}
    exec(compile(ast.Module(body=[restart], type_ignores=[]), "run.py", "exec"), ns)
    seen = []
    monkeypatch.setenv("LAKE_SUPERVISED_WORKER", "previous-worker-nonce")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: seen.append(kwargs))
    stopped = threading.Event()
    ns["restart"](SimpleNamespace(stop=stopped.set))
    assert "LAKE_SUPERVISED_WORKER" not in seen[0]["env"]
    assert seen[0]["env"]["LAKE_RESTART"] == "1"
    assert server.should_exit and stopped.is_set() and window_stop.is_set()


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
