"""Bounded, payload-free evidence which survives worker recovery and a hidden console."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time
import traceback


class RuntimeLog:
    def __init__(self, directory, name):
        self.path = directory / (name + ".jsonl")
        self._handler = None
        self._lock = threading.Lock()

    def record(self, event, **fields):
        # Called by a supervisor/monitor thread, never by the UI event loop.
        try:
            with self._lock:
                if self._handler is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._handler = RotatingFileHandler(self.path, maxBytes=2 * 1024 * 1024,
                                                       backupCount=2, encoding="utf-8")
                value = {"at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
                         "event": event, **fields}
                message = json.dumps(value, ensure_ascii=False)
                self._handler.emit(logging.LogRecord("ltm.runtime", logging.INFO, "", 0, message, (), None))
        except (OSError, ValueError):
            pass  # Diagnostics must not turn a disk problem into a server failure.

    def close(self):
        with self._lock:
            if self._handler is not None:
                self._handler.close()
                self._handler = None


def thread_stacks():
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    return [{"thread": names.get(ident, "unknown"), "frames": [
        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_stack(stack)[-40:]]}
        for ident, stack in sys._current_frames().items()]


def record_failure(component, error):
    from app.infra.settings import CACHE_DIR
    log = RuntimeLog(CACHE_DIR / "logs" / "runtime", "failures")
    try:
        log.record("component_failed", component=component, errorType=type(error).__name__,
            frames=[{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(error.__traceback__)[-40:]])
    finally:
        log.close()


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
            self.log.record("runtime_stalled", loopLagSeconds=lag, components=components, stacks=thread_stacks())
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
        async with previous(application):
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
                await asyncio.to_thread(thread.join, 2)
    app.router.lifespan_context = lifespan
