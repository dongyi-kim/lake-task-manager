"""Bound MCP workers without borrowing the UI's default AnyIO worker capacity."""

import threading
from contextvars import ContextVar

import anyio


class ToolCapacityExceeded(Exception):
    """The operation was rejected before its callable could execute."""


class ToolRequestCancelled(Exception):
    """The HTTP caller disconnected before this callable started."""


request_disconnected = ContextVar("ltm_mcp_disconnected", default=None)


class ToolLane:
    def __init__(self, capacity):
        self._permits = threading.BoundedSemaphore(capacity)
        self.limiter = anyio.CapacityLimiter(capacity)

    async def run(self, fn):
        disconnected = request_disconnected.get()
        # No waiting backlog: especially writes must not start much later after
        # their caller has already given up. Other lanes and the UI stay usable.
        if not self._permits.acquire(blocking=False):
            raise ToolCapacityExceeded
        lock = threading.Lock()
        started = cancelled = False

        def execute():
            nonlocal started
            with lock:
                if cancelled:
                    return None
                started = True
            try:
                if disconnected is not None and disconnected.is_set():
                    raise ToolRequestCancelled
                return fn()
            finally:
                # The worker owns admission until actual completion. A transport's
                # raw asyncio cancellation can bypass AnyIO's cancellation shield,
                # but must not free capacity while a Jira write is still running.
                self._permits.release()

        try:
            return await anyio.to_thread.run_sync(execute, limiter=self.limiter)
        finally:
            with lock:
                if not started:
                    # Covers cancellation at dispatch/limiter checkpoints and a
                    # worker dequeuing this job after its caller was cancelled.
                    cancelled = True
                    self._permits.release()
