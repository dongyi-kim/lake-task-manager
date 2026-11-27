"""Desktop owner deadlines and a bounded probe of our own Windows tray message loop."""

import sys
import time


_window_runtime = ("closed", 0.0)


def window_phase(phase, timeout=0):
    global _window_runtime
    # Replace the snapshot atomically; the liveness endpoint never waits for a browser lock.
    _window_runtime = (phase, time.monotonic() + timeout)


def window_healthy():
    phase, deadline = _window_runtime
    # A clean browser owner can recreate its session while the server/tray remain
    # live. Failed cleanup or an overdue owner still requires process recovery.
    return phase == "closed" or (phase != "failed" and time.monotonic() <= deadline)


def runtime_status(tray_probe=None):
    phase, deadline = _window_runtime
    now = time.monotonic()
    tray_age = max(0, now - tray_probe["at"]) if tray_probe is not None else 0
    window_ok = window_healthy()
    tray_ok = tray_probe is None or tray_probe["healthy"] and tray_age < 15
    return {"healthy": window_ok and tray_ok, "windowPhase": phase,
            "windowHealthy": window_ok, "windowOverdueSeconds": round(max(0, now - deadline), 2)
            if phase != "closed" else 0, "windowRecovering": phase == "recovering",
            **({"trayHealthy": tray_ok, "trayProbeAgeSeconds": round(tray_age, 2)} if tray_probe is not None else {})}


def probe_tray_pump(icon):
    """Call only from a probe worker. None means native probing is unsupported."""
    if not sys.platform.startswith("win") or not getattr(icon, "_hwnd", None):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_ssize_t
        result = ctypes.c_size_t()
        # WM_NULL, SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT. No other process's HWND is probed.
        return bool(send(icon._hwnd, 0, 0, 0, 0x0022, 500, ctypes.byref(result)))
    except Exception:
        return None
