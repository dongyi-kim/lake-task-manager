"""Tiny parent-owned recovery controls, independent of HTTP, auth and the worker.

Native imports, image loading and message-pump operations stay on a daemon owner.
Supervisor calls only change events/snapshots, so a slow native startup cannot block
process recovery or explicit quit. A missing tray backend leaves the shown console
and Ctrl+C available.
"""

import os
from pathlib import Path
import threading


def _native_icon(on_retry, on_quit):
    if os.name != "nt":
        raise RuntimeError("Recovery tray is Windows-only")
    import pystray
    from PIL import Image

    image = None
    static = Path(__file__).resolve().parents[1] / "static"
    for name in ("icon.png", "favicon.ico"):
        try:
            with Image.open(static / name) as source:
                image = source.copy()
            break
        except OSError:
            continue
    if image is None:
        raise OSError("Recovery tray image unavailable")
    menu = pystray.Menu(
        pystray.MenuItem("앱을 복구하고 있습니다", None, enabled=False),
        pystray.MenuItem("지금 다시 시도", lambda icon, item: on_retry(), default=True),
        pystray.MenuItem("종료", lambda icon, item: on_quit()),
    )
    return pystray.Icon("lake-task-manager-recovery", image, "Lake Task Manager - 복구 중", menu)


class RecoveryControl:
    def __init__(self, *, icon_factory=_native_icon):
        self.quit_requested = threading.Event()
        self.retry_requested = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._visible = False
        self._thread = None
        self._icon_factory = icon_factory

    def request_retry(self):
        if not self._closed.is_set():
            self.retry_requested.set()

    def request_quit(self):
        self.quit_requested.set()

    def show_cooldown(self, seconds, reason):
        # Details are recorded by the supervisor. The native menu never needs to
        # parse an exception, query an unhealthy worker, or access its shared state.
        with self._lock:
            if self._closed.is_set():
                return
            self._visible = True
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run_icon, name="ltm-recovery-tray", daemon=True)
                try:
                    self._thread.start()
                except RuntimeError:
                    pass  # The already-shown console remains the fallback.

    def hide(self):
        with self._lock:
            self._visible = False

    def close(self):
        self.hide()
        self._closed.set()

    def _run_icon(self):
        try:
            icon = self._icon_factory(self.request_retry, self.request_quit)

            def setup(icon):
                visible = None
                try:
                    while not self._closed.wait(.1):
                        with self._lock:
                            desired = self._visible
                        if desired != visible:
                            icon.visible = visible = desired
                finally:
                    try:
                        icon.visible = False
                    finally:
                        icon.stop()

            icon.run(setup=setup)
        except Exception:
            pass  # Optional native UI must never take down the protected parent.
