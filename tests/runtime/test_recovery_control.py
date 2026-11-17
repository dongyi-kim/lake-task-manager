"""Parent controls remain independent of the API worker and native UI startup."""

import ast
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from support.paths import REPO_ROOT


class Icon:
    def __init__(self):
        self.shown, self.hidden, self.stopped = threading.Event(), threading.Event(), threading.Event()
        self._visible = False

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        self._visible = value
        (self.shown if value else self.hidden).set()

    def run(self, *, setup):
        setup(self)

    def stop(self):
        self.stopped.set()


def test_recovery_control_reuses_native_surface_and_callbacks_only_signal_events():
    from app.launcher.recovery_control import RecoveryControl
    icon, calls, commands = Icon(), [], {}

    def factory(retry, quit_app):
        calls.append(True)
        commands.update(retry=retry, quit=quit_app)
        return icon

    control = RecoveryControl(icon_factory=factory)
    try:
        control.show_cooldown(30, "fixture worker fault")
        assert icon.shown.wait(1)
        commands["retry"]()
        assert control.retry_requested.is_set() and not control.quit_requested.is_set()
        control.hide()
        assert icon.hidden.wait(1)
        icon.shown.clear()
        control.show_cooldown(60, "fixture worker fault")
        assert icon.shown.wait(1) and calls == [True]
        commands["quit"]()
        assert control.quit_requested.is_set()
    finally:
        control.close()
    assert icon.stopped.wait(1)


def test_hide_and_close_never_wait_for_slow_native_initialization():
    from app.launcher.recovery_control import RecoveryControl
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    icon = Icon()

    def factory(*_):
        entered.set()
        assert release.wait(2)
        return icon

    control = RecoveryControl(icon_factory=factory)
    control.show_cooldown(30, "fixture")
    try:
        assert entered.wait(1)
        closer = threading.Thread(target=lambda: (control.hide(), control.close(), returned.set()))
        closer.start()
        assert returned.wait(.2)
    finally:
        release.set()
        control.close()
    assert icon.stopped.wait(1)
    assert not icon.shown.is_set()


def test_production_launcher_opts_into_persistent_recovery_and_closes_parent_control(monkeypatch, tmp_path):
    from app.launcher import supervisor, recovery_control
    from app.infra import runtime_diagnostics, settings

    tree = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_supervise_runtime")
    hidden, shown = lambda: None, lambda: None
    namespace = {"os": os, "sys": sys, "Path": Path, "__file__": str(REPO_ROOT / "run.py"),
                 "_hide_console_win": hidden, "_show_console_win": shown}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "run.py", "exec"), namespace)
    calls, closed = [], []
    control = SimpleNamespace(close=lambda: closed.append("control"))
    monkeypatch.delenv(supervisor.WORKER_ENV, raising=False)
    monkeypatch.setattr(recovery_control, "RecoveryControl", lambda: control)
    monkeypatch.setattr(supervisor, "run_supervisor", lambda *args, **kwargs: calls.append(kwargs) or 0)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(runtime_diagnostics, "RuntimeLog", lambda *_: SimpleNamespace(
        record=lambda *args, **kwargs: None, close=lambda: closed.append("log")))
    with pytest.raises(SystemExit) as stopped:
        namespace["_supervise_runtime"](SimpleNamespace(app_port=4457), hide_console=True)
    assert stopped.value.code == 0
    assert calls[0]["persistent_recovery"] is True and calls[0]["recovery_control"] is control
    assert calls[0]["on_ready"] is hidden and calls[0]["on_failure"] is shown
    assert closed == ["control", "log"]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows recovery tray")
def test_native_windows_recovery_tray_pump_and_actions():
    pytest.importorskip("pystray")
    pytest.importorskip("PIL")
    from app.launcher.recovery_control import RecoveryControl, _native_icon
    from app.launcher.runtime_health import probe_tray_pump

    icons, created = [], threading.Event()

    def factory(retry, quit_app):
        icon = _native_icon(retry, quit_app)
        icons.append(icon)
        created.set()
        return icon

    control = RecoveryControl(icon_factory=factory)
    try:
        control.show_cooldown(30, "native fixture")
        assert created.wait(2)
        icon = icons[0]
        deadline = time.monotonic() + 2
        while not icon.visible and time.monotonic() < deadline:
            time.sleep(.01)
        assert icon.visible and probe_tray_pump(icon) is True
        menu = list(icon.menu)
        menu[1](icon)
        assert control.retry_requested.is_set()
        menu[2](icon)
        assert control.quit_requested.is_set()
        control.hide()
        deadline = time.monotonic() + 2
        while icon.visible and time.monotonic() < deadline:
            time.sleep(.01)
        assert not icon.visible
    finally:
        control.close()
    control._thread.join(timeout=2)
    assert not control._thread.is_alive()
