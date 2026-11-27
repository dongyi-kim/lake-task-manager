"""Retry only browser sessions whose owner confirmed cleanup, on that same thread."""
import os
import threading
import time


_OWNER_LOCK = threading.Lock()


class WindowSessionFailure(RuntimeError):
    def __init__(self, reason, *, cleanup_confirmed=False):
        super().__init__(reason)
        self.reason = reason
        self.cleanup_confirmed = cleanup_confirmed


class BrowserOwnerProbe:
    """Observe only processes launched by this session; never infer user intent from EOF."""
    def __init__(self, playwright, context):
        connection = getattr(getattr(playwright, "_impl_obj", None), "_connection", None)
        # Playwright has no public driver process API. This read-only adapter is
        # isolated for our pinned version; absence means unknown, never safe cleanup.
        transport = getattr(connection, "_transport", None)
        self._driver = getattr(transport, "_proc", None)
        self._driver_error = getattr(transport, "on_error_future", None)
        self._browser = getattr(context, "browser", None)
        self._handle = None
        session = None
        try:
            if os.name == "nt" and self._browser is not None:
                import win32api
                session = self._browser.new_browser_cdp_session()
                processes = session.send("SystemInfo.getProcessInfo").get("processInfo", [])
                browser = next(item for item in processes if item.get("type") == "browser")
                # The PID comes from this launched browser, not a process-name scan.
                self._handle = win32api.OpenProcess(0x00100000 | 0x1000, False, int(browser["id"]))
        except Exception:
            pass
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass

    def _browser_exit(self):
        """Return an exit code only after the kernel confirms process completion."""
        if self._handle is None:
            return None
        try:
            import win32event
            import win32process
            # Chromium can close its context/pipe before Windows signals process
            # completion, while Playwright's is_connected() remains stale True.
            # These checks only run on close/failure paths, never the UI heartbeat.
            if win32event.WaitForSingleObject(self._handle, 1000) == win32event.WAIT_TIMEOUT:
                return None
            return win32process.GetExitCodeProcess(self._handle)
        except Exception:
            return None

    def _browser_termination_status(self):
        """Published failure status can precede completion; it never releases ownership."""
        if self._handle is None:
            return None
        try:
            import win32process
            code = win32process.GetExitCodeProcess(self._handle)
            return None if code == 259 else code  # STILL_ACTIVE is not termination evidence.
        except Exception:
            return None

    def failure_reason(self):
        if self._driver is not None and self._driver.returncode is not None:
            return "driver_disconnected"
        error = self._driver_error
        if error is not None and error.done() and not error.cancelled() and error.exception() is not None:
            return "driver_disconnected"
        code = self._browser_exit()
        if code is None:
            code = self._browser_termination_status()
        return "browser_crash" if code is not None and code != 0 else None

    def cleanup_confirmed(self):
        return (self._driver is not None and self._driver.returncode is not None
                and self._browser_exit() is not None)

    def close(self):
        if self._handle is not None:
            try:
                import win32api
                win32api.CloseHandle(self._handle)
            finally:
                self._handle = None


def run_window_recovery(session, *, should_stop, stop_event=None, on_ready=None,
                        phase=lambda *args: None, record=lambda *args, **kwargs: None,
                        clock=time.monotonic, wait=None, owner_lock=None):
    # The lease includes backoff: another tray/Open request must not race the
    # original owner's next attempt even though its previous context is closed.
    owner_lock = owner_lock if owner_lock is not None else _OWNER_LOCK
    if not owner_lock.acquire(blocking=False):
        return False
    release_owner = True
    stop_event = stop_event if stop_event is not None else threading.Event()
    wait = wait if wait is not None else stop_event.wait
    attempts, delay, ready_at = 0, 1, None

    def stopping():
        return stop_event.is_set() or should_stop()

    def emit(event, **fields):
        try:
            record(event, **fields)
        except Exception:
            pass  # Diagnostic observers never own browser recovery.

    def ready():
        nonlocal ready_at
        ready_at = clock()
        if attempts:
            emit("browser_recovery_completed", restartCount=attempts)
        if on_ready is not None:
            try:
                on_ready()
            except Exception:
                pass

    try:
        while not stopping():
            try:
                result = session(on_ready=ready)
            except WindowSessionFailure as error:
                if not error.cleanup_confirmed:
                    release_owner = False
                    phase("failed")
                    emit("browser_recovery_failed", reason=error.reason, restartCount=attempts,
                         errorType=type(error.__cause__ or error).__name__)
                    raise  # The supervisor alone may replace an uncertain owner.
                if stopping():
                    break
                if ready_at is not None and clock() - ready_at >= 60:
                    delay = 1
                ready_at = None
                attempts += 1
                phase("recovering", delay + 60)
                emit("browser_recovery_started", reason=error.reason, restartCount=attempts,
                     delaySeconds=delay, errorType=type(error.__cause__ or error).__name__)
                deadline = clock() + delay
                while not stopping() and clock() < deadline:
                    wait(min(.25, max(0, deadline - clock())))
                delay = min(30, delay * 2)
                continue
            phase("closed")
            return result  # Intentional closure (including during navigation) is final.
        phase("closed")
        if attempts:
            emit("browser_recovery_cancelled", reason="stop_requested", restartCount=attempts)
        return False
    finally:
        if release_owner:
            owner_lock.release()
