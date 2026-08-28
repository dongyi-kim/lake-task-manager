from types import SimpleNamespace

import run as launcher
from app.launcher import hotkey, instance, shortcuts


def test_run_keeps_launcher_compatibility_exports():
    assert launcher._parse_hotkey is hotkey.parse_hotkey
    assert launcher._wait_port_free is instance.wait_port_free
    assert launcher._port_is_listening is instance.port_is_listening
    assert launcher._signal_existing_instance is instance.signal_existing_instance
    assert launcher._autostart_enabled is shortcuts.autostart_enabled
    assert launcher._launcher_bat is shortcuts.launcher_bat
    assert launcher._set_autostart is shortcuts.set_autostart


def test_hotkey_parser_contract():
    assert hotkey.parse_hotkey("ctrl + alt + space") == (0x3, 0x20, "Ctrl+Alt+Space")
    assert hotkey.parse_hotkey("shift+f12") == (0x4, 0x7B, "Shift+F12")
    assert hotkey.parse_hotkey("win+k") == (0x8, ord("K"), "Win+K")
    assert hotkey.parse_hotkey("space") is None
    assert hotkey.parse_hotkey("ctrl+mouse1") is None


def test_single_instance_returns_immediately_when_port_is_not_ltm(monkeypatch):
    monkeypatch.setattr(instance, "ltm_health", lambda base: None)
    assert instance.signal_existing_instance(SimpleNamespace(app_port=4457)) is False


def test_stale_single_instance_is_replaced_before_focus(monkeypatch):
    monkeypatch.setattr(instance, "ltm_health", lambda base: {"status": "ok"})
    monkeypatch.setattr(instance, "disk_rev", lambda: "new")
    monkeypatch.setattr(instance, "running_rev", lambda base: "old")
    monkeypatch.setattr(instance, "quit_existing", lambda base, port: True)
    assert instance.signal_existing_instance(SimpleNamespace(app_port=4457)) is False


def test_current_single_instance_receives_open_request(monkeypatch, capsys):
    class Response:
        def read(self):
            return b'{"action":"focus"}'

    monkeypatch.setattr(instance, "ltm_health", lambda base: {"status": "ok"})
    monkeypatch.setattr(instance, "disk_rev", lambda: "same")
    monkeypatch.setattr(instance, "running_rev", lambda base: "same")
    monkeypatch.setattr(instance.urllib.request, "urlopen", lambda request, timeout: Response())
    assert instance.signal_existing_instance(SimpleNamespace(app_port=4457)) is True
    assert "기존 창을 앞으로" in capsys.readouterr().out


def test_shortcut_locations_derive_from_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    programs = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    assert shortcuts.start_menu_dir() == programs
    assert shortcuts.startup_dir() == programs / "Startup"
