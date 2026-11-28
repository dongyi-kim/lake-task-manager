"""Desktop owner deadlines and a bounded probe of our own Windows tray message loop."""

import sys
import time


_window_runtime = ("closed", 0.0)
NATIVE_RECOVERY_GRACE_SECONDS = 120


def window_phase(phase, timeout=0):
    global _window_runtime
    # Replace the snapshot atomically; the liveness endpoint never waits for a browser lock.
    if phase == "failed" and _window_runtime[0] == "failed":
        return  # Repeated failure reports are not owner progress.
    _window_runtime = (phase, time.monotonic() + timeout)


def window_healthy():
    phase, deadline = _window_runtime
    # A clean browser owner can recreate its session while the server/tray remain
    # live. Failed cleanup or an overdue owner still requires process recovery.
    return phase == "closed" or (phase != "failed" and time.monotonic() <= deadline)


def runtime_status(tray_probe=None):
    phase, deadline = _window_runtime
    now = time.monotonic()
    tray_probe = dict(tray_probe) if tray_probe is not None else None
    tray_age = max(0, now - tray_probe["at"]) if tray_probe is not None else 0
    window_ok = phase == "closed" or (phase != "failed" and now <= deadline)
    tray_ok = tray_probe is None or tray_probe["healthy"] and tray_age < 15
    window_overdue = max(0, now - deadline) if phase != "closed" else 0
    tray_since = None
    if not tray_ok:
        tray_since = tray_probe.get("unhealthySince")
        if tray_since is None:
            tray_since = tray_probe["at"] + (15 if tray_probe["healthy"] else 0)
    tray_overdue = max(0, now - tray_since) if tray_since is not None else 0
    restart = ((not window_ok and window_overdue >= NATIVE_RECOVERY_GRACE_SECONDS)
               or (not tray_ok and tray_overdue >= NATIVE_RECOVERY_GRACE_SECONDS))
    return {"healthy": window_ok and tray_ok, "windowPhase": phase,
            "windowHealthy": window_ok, "windowOverdueSeconds": round(window_overdue, 2),
            "windowRecovering": phase == "recovering", "restartRecommended": restart,
            **({"trayHealthy": tray_ok, "trayProbeAgeSeconds": round(tray_age, 2),
                "trayUnhealthySeconds": round(tray_overdue, 2)} if tray_probe is not None else {})}


def update_tray_probe(probe, healthy):
    """Keep the first unhealthy observation until the native owner makes progress."""
    now = time.monotonic()
    since = None if healthy else probe.get("unhealthySince")
    if not healthy and since is None:
        since = now
    probe.update(healthy=healthy, at=now, unhealthySince=since)


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
