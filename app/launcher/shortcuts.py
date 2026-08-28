"""Windows Start-menu and autostart shortcut policy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.infra.settings import APP_ROOT, STATIC_DIR


SHORTCUT_NAME = "Lake Task Manager.lnk"


def launcher_bat():
    return APP_ROOT / "run.bat"


def start_menu_dir():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def startup_dir():
    return start_menu_dir() / "Startup"


def make_shortcut(lnk_path, target, icon):
    """Create a minimized Windows shortcut on a best-effort basis."""
    if not sys.platform.startswith("win"):
        return False
    try:
        lnk_path.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "$w=New-Object -ComObject WScript.Shell;"
            f"$s=$w.CreateShortcut('{lnk_path}');"
            f"$s.TargetPath='{target}';"
            f"$s.WorkingDirectory='{target.parent}';"
            "$s.WindowStyle=7;"
            + (f"$s.IconLocation='{icon}';" if icon else "")
            + "$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=15,
        )
        return lnk_path.exists()
    except Exception:
        return False


def icon_path():
    icon = STATIC_DIR / "favicon.ico"
    return str(icon) if icon.exists() else ""


def ensure_start_menu_shortcut():
    try:
        shortcut = start_menu_dir() / SHORTCUT_NAME
        target = launcher_bat()
        if not shortcut.exists() and target.exists():
            make_shortcut(shortcut, target, icon_path())
    except Exception:
        pass


def autostart_enabled():
    try:
        return (startup_dir() / SHORTCUT_NAME).exists()
    except Exception:
        return False


def set_autostart(enable):
    try:
        shortcut = startup_dir() / SHORTCUT_NAME
        if enable:
            make_shortcut(shortcut, launcher_bat(), icon_path())
        elif shortcut.exists():
            shortcut.unlink()
    except Exception:
        pass
