"""Bounded, payload-free evidence which survives worker recovery and a hidden console."""

import asyncio
import atexit
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback


MAX_RECORD_BYTES = 128 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. -]{0,119}$")
_FILE_LOCKS = {}
_FILE_LOCKS_GUARD = threading.Lock()
_exception_cleanup = None


def safe_filename(value):
    """Keep useful source locations without user/home/install directory names."""
    value = str(value or "").replace("\\", "/")
    for marker in ("app/", "tests/", "site-packages/"):
        position = value.rfind("/" + marker)
        if position >= 0:
            return value[position + 1:][:180]
        if value.startswith(marker):
            return value[:180]
    return value.rsplit("/", 1)[-1][:120]


def safe_frames(frames):
    if not isinstance(frames, list):
        return []
    return [{"file": safe_filename(frame.get("file")),
             "line": frame.get("line") if type(frame.get("line")) is int else 0,
             "function": str(frame.get("function") or "")[:80]}
            for frame in frames[-40:] if isinstance(frame, dict)]


def _numbers(value):
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in list(value.items())[:32]
            if isinstance(key, str) and _IDENTIFIER.fullmatch(key)
            and type(item) in (int, float, bool) and (type(item) is not float or abs(item) < 1e30)}


def safe_record(value):
    """The same allowlist applies on write, browsing, and download of older logs."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("event", "component", "errorType", "revision", "thread"):
        item = value.get(key)
        if isinstance(item, str) and _IDENTIFIER.fullmatch(item):
            result[key] = item
    at = value.get("at")
    if isinstance(at, str) and re.fullmatch(r"[0-9T:+.Z-]{1,40}", at):
        result["at"] = at
    for key in ("pid", "workerPid", "exitCode", "misses", "uptimeSeconds", "delaySeconds", "generation",
                "restartCount", "sequence", "truncated"):
        if type(value.get(key)) in (int, float, bool) and abs(value[key]) < 1e30:
            result[key] = value[key]
    reason = value.get("reason")
    if isinstance(reason, str) and (re.fullmatch(r"[a-z_]{1,64}", reason)
            or re.fullmatch(r"worker exited with code -?[0-9]+", reason)
            or reason in {"worker stopped responding", "worker could not start", "supervisor interrupted"}):
        result["reason"] = reason
    for key in ("resources", "loopLagSeconds"):
        if isinstance(value.get(key), dict):
            result[key] = _numbers(value[key])
    if isinstance(value.get("components"), dict):
        result["components"] = _numbers(value["components"])
        phase = value["components"].get("windowPhase")
        if isinstance(phase, str) and re.fullmatch(r"[a-z-]{1,40}", phase):
            result["components"]["windowPhase"] = phase
    if "frames" in value:
        result["frames"] = safe_frames(value["frames"])
    if isinstance(value.get("stacks"), list):
        result["stacks"] = [{"thread": str(stack.get("thread") or "unknown")[:80],
                              "frames": safe_frames(stack.get("frames"))}
                             for stack in value["stacks"][:16] if isinstance(stack, dict)]
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RECORD_BYTES - 256:
        result.pop("stacks", None)
        result["truncated"] = True
    return result


def resource_context():
    try:
        from app.infra.resource_pressure import resource_pressure
        return _numbers(resource_pressure.snapshot())
    except Exception:
        return {}  # Crash handling must not depend on memory counters or their locks.


class RuntimeLog:
    def __init__(self, directory, name):
        self.path = directory / (name + ".jsonl")
        self._handler = None
        with _FILE_LOCKS_GUARD:
            self._lock = _FILE_LOCKS.setdefault(str(self.path.absolute()), threading.Lock())

    def record(self, event, **fields):
        # Called by a supervisor/monitor thread, never by the UI event loop.
        try:
            with self._lock:
                if self._handler is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._handler = RotatingFileHandler(self.path, maxBytes=2 * 1024 * 1024,
                                                       backupCount=2, encoding="utf-8")
                value = safe_record({"at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
                                     "event": event, **fields})
                message = json.dumps(value, ensure_ascii=False)
                self._handler.emit(logging.LogRecord("ltm.runtime", logging.INFO, "", 0, message, (), None))
        except (OSError, ValueError, TypeError, RuntimeError):
            pass  # Diagnostics must not turn a disk problem into a server failure.

    def close(self):
        with self._lock:
            if self._handler is not None:
                try:
                    self._handler.close()
                except OSError:
                    pass
                finally:
                    self._handler = None


def thread_stacks():
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    stacks = sys._current_frames()
    priority = {"MainThread", "app-server", "app-window", "ltm-runtime-monitor"}
    identifiers = sorted(stacks, key=lambda ident: (names.get(ident) not in priority,
                                                   "mcp" not in names.get(ident, "")))[:16]
    return [{"thread": names.get(ident, "unknown"), "frames": [
        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_stack(stacks[ident], limit=40)]}
        for ident in identifiers]


def record_failure(component, error, *, directory=None, traceback_value=None, name="failures"):
    if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)) or (
            isinstance(error, SystemExit) and error.code in (None, 0)):
        return
    if directory is None:
        from app.infra.settings import CACHE_DIR
        directory = CACHE_DIR / "logs" / "runtime"
    log = RuntimeLog(directory, name)
    try:
        log.record("component_failed", component=component, errorType=type(error).__name__,
            frames=[{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(traceback_value or error.__traceback__, limit=-40)],
            resources=resource_context())
    finally:
        log.close()


def record_browser_recovery(event, **fields):
    from app.infra.settings import CACHE_DIR
    log = RuntimeLog(CACHE_DIR / "logs" / "runtime", "browser")
    try:
        log.record(event, **fields, resources=resource_context())
    finally:
        log.close()


def install_exception_hooks(directory, *, fatal=True, role="worker"):
    """Install once per process; chain original behavior and retain no exceptions."""
    global _exception_cleanup
    if _exception_cleanup is not None:
        return lambda: None
    original_main, original_thread = sys.excepthook, threading.excepthook
    original_unraisable = sys.unraisablehook

    def record(component, error, tb):
        try:
            if error is not None:
                record_failure(component, error, directory=directory, traceback_value=tb,
                               name="launcher-failures" if role == "launcher" else "failures")
        except Exception:
            pass

    def main_hook(kind, error, tb):
        record("main", error, tb)
        original_main(kind, error, tb)

    def thread_hook(args):
        record("thread", args.exc_value, args.exc_traceback)
        original_thread(args)

    def unraisable_hook(args):
        record("unraisable", args.exc_value, args.exc_traceback)
        original_unraisable(args)

    sys.excepthook, threading.excepthook, sys.unraisablehook = main_hook, thread_hook, unraisable_hook
    fatal_file = None
    fatal_previously_enabled = False
    if fatal:
        try:
            import faulthandler
            fatal_previously_enabled = faulthandler.is_enabled()
            # A live faulthandler descriptor cannot follow a rotating logging handler.
            # Redirect even PYTHONFAULTHANDLER/-X faulthandler so hidden stderr is
            # never the only native crash evidence. Rotate before opening the file.
            path = Path(directory) / ("fatal-launcher.log" if role == "launcher" else "fatal-worker.log")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size:
                path.replace(path.with_name(path.name + ".1"))
            fatal_file = path.open("w", encoding="utf-8")
            faulthandler.enable(file=fatal_file, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            if fatal_file is not None:
                fatal_file.close()
                fatal_file = None

    def cleanup():
        global _exception_cleanup
        for owner, name, installed, previous in ((sys, "excepthook", main_hook, original_main),
                (threading, "excepthook", thread_hook, original_thread),
                (sys, "unraisablehook", unraisable_hook, original_unraisable)):
            if getattr(owner, name) is installed:
                setattr(owner, name, previous)
        if fatal_file is not None and not fatal_file.closed:
            import faulthandler
            try:
                # CPython exposes enabled state, but not the prior descriptor.
                # Restore an existing handler to stderr before closing our file.
                if fatal_previously_enabled:
                    faulthandler.enable(all_threads=True)
                else:
                    faulthandler.disable()
            except (OSError, RuntimeError, ValueError):
                # Some embedded runtimes have no usable stderr descriptor. Never
                # leave faulthandler pointing at the file we are about to close.
                faulthandler.disable()
            finally:
                fatal_file.close()
        _exception_cleanup = None
        atexit.unregister(cleanup)

    _exception_cleanup = cleanup
    atexit.register(cleanup)
    return cleanup


def install_async_exception_handler(loop, *, directory):
    previous = loop.get_exception_handler()

    def handler(current, context):
        error = context.get("exception")
        if isinstance(error, BaseException):
            try:
                record_failure("asyncio", error, directory=directory)
            except Exception:
                pass
        if previous is not None:
            previous(current, context)
        else:
            current.default_exception_handler(context)

    loop.set_exception_handler(handler)

    def cleanup():
        if loop.get_exception_handler() is handler:
            loop.set_exception_handler(previous)
    return cleanup


class RuntimeMonitor:
    def __init__(self, log, loops, components=lambda: None, *, clock=time.monotonic, stall_after=3):
        self.log, self.loops, self.components = log, loops, components
        self.clock, self.stall_after = clock, stall_after
        self.last = {name: clock() for name in loops}
        self._stalled = False

    def sample(self):
        now = self.clock()
        lag = {name: round(max(0, now - at), 3) for name, at in self.last.items()}
        try:
            components = self.components()
        except Exception:
            components = {"healthy": False, "reason": "health_hook_error"}
        stalled = any(seconds >= self.stall_after for seconds in lag.values())
        if isinstance(components, dict):
            stalled = stalled or not components.get("healthy", True)
        if stalled and not self._stalled:
            self.log.record("runtime_stalled", loopLagSeconds=lag, components=components,
                            stacks=thread_stacks(), resources=resource_context())
        elif not stalled and self._stalled:
            self.log.record("runtime_responsive", loopLagSeconds=lag)
        self._stalled = stalled
        for name, loop in self.loops.items():
            loop = loop() if callable(loop) else loop
            if loop is not None and not loop.is_closed():
                # Pulse execution proves the loop is servicing work. No API/auth request.
                loop.call_soon_threadsafe(self._pulse, name)

    def _pulse(self, name):
        self.last[name] = self.clock()


def install_runtime_monitor(app, *, directory, mcp_host, components, revision=""):
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        restore_hooks = install_exception_hooks(directory, role="worker")
        try:
            async with previous(application):
                restore_async = install_async_exception_handler(asyncio.get_running_loop(), directory=directory)
                log = RuntimeLog(directory, "worker")
                monitor = RuntimeMonitor(log, {"ui": asyncio.get_running_loop(), "mcp": lambda: mcp_host._loop}, components)
                stop = threading.Event()

                def observe():
                    log.record("worker_started", revision=revision)
                    try:
                        while not stop.wait(1):
                            try:
                                for event in mcp_host.drain_events():
                                    log.record(event["event"], **{name: value for name, value in event.items() if name != "event"})
                                monitor.sample()
                            except Exception:
                                log.record("monitor_sample_failed")
                    finally:
                        log.record("worker_stopped")
                        log.close()

                thread = threading.Thread(target=observe, name="ltm-runtime-monitor", daemon=True)
                thread.start()
                try:
                    yield
                finally:
                    stop.set()
                    restore_async()
                    await asyncio.to_thread(thread.join, 2)
        finally:
            restore_hooks()
    app.router.lifespan_context = lifespan
