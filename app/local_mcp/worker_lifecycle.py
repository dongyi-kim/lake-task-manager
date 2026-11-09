"""Supervise one MCP SDK generation from the independent application event loop."""
import asyncio
from collections import deque
from concurrent.futures import Future
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
import time
import traceback


request_lease = ContextVar("ltm_mcp_request_lease", default=None)


@dataclass(eq=False)
class RequestLease:
    generation: object
    disconnected: threading.Event = field(default_factory=threading.Event)
    response_complete: bool = False
    restart_requested: bool = False


@dataclass
class Generation:
    number: int
    ready: Future = field(default_factory=Future)
    finished: Future = field(default_factory=Future)
    loop: object = None
    stop: threading.Event = field(default_factory=threading.Event)
    thread: object = None
    requests: set = field(default_factory=set)
    leases: set = field(default_factory=set)
    accepting: bool = False
    last_heartbeat: float = 0
    failure_type: str | None = None
    failure_frames: list = field(default_factory=list)


class WorkerLifecycle:
    def __init__(self, applications, *, factory=None, quiescent=lambda: True):
        self.applications, self.factory, self.quiescent = applications, factory, quiescent
        self.current = None
        self.monitor_interval, self.probe_timeout = 1.0, .5
        self.misses_allowed, self.restart_limit, self.restart_window = 3, 3, 600
        self.shutdown_timeout, self.startup_timeout = 10.0, 10.0
        self.state, self.failures, self.restart_count = "stopped", 0, 0
        self.last_reason = None
        self._restarts = deque()
        self._pending = set()
        self._events = deque(maxlen=20)
        self._restart_generation = None
        self._lock = threading.Lock()
        self._closing = True

    def snapshot(self):
        generation = self.current
        alive = bool(generation and generation.thread and generation.thread.is_alive())
        state = self.state
        if state == "healthy" and (not alive or not self.can_admit(generation)):
            state = "unresponsive"
        return {"state": state, "generation": generation.number if generation else 0,
            "workerAlive": alive, "automaticRecovery": not self._closing,
            "consecutiveFailures": self.failures, "restartCount": self.restart_count,
            "restartLimit": self.restart_limit, "lastRecoveryReason": self.last_reason,
            "lastHeartbeatAgeSeconds": round(max(0, time.monotonic() - generation.last_heartbeat), 3)
                if generation and generation.last_heartbeat else None}

    def can_admit(self, generation):
        return (generation is not None and generation is self.current and self.state == "healthy"
            and generation.accepting and not generation.finished.done()
            and time.monotonic() - generation.last_heartbeat <= max(self.probe_timeout, 2 * self.monitor_interval))

    def _record(self, event, **fields):
        with self._lock:
            self._events.append({"event": event, **fields})

    def drain_events(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def request_restart(self, expected_generation):
        lease = request_lease.get()
        with self._lock:
            generation = self.current
            status = ("not_running" if self._closing or not generation else
                "generation_changed" if generation.number != expected_generation else
                "recovery_failed" if self.state == "recovery_failed" else
                "not_running" if lease is None or lease.generation is not generation else
                "already_queued" if self._pending or self._restart_generation == expected_generation else "queued")
            accepted = status in {"queued", "already_queued"}
            if accepted:
                lease.restart_requested = True
                self._pending.add(lease)
            return {"accepted": accepted, "status": status,
                    "generation": generation.number if generation else 0}

    def finish_request(self, lease):
        with self._lock:
            self._pending.discard(lease)
            if (lease.restart_requested and lease.response_complete and not lease.disconnected.is_set()
                    and self.current is lease.generation and not self._closing):
                self._restart_generation = lease.generation.number

    async def _start(self):
        previous = self.current
        generation = Generation(previous.number + 1 if previous else 1)
        self.current = generation

        async def serve():
            generation.loop = asyncio.get_running_loop()
            applications = self.factory() if previous and self.factory else self.applications
            async with AsyncExitStack() as stack:
                for app in applications:
                    await stack.enter_async_context(app.router.lifespan_context(app))
                generation.accepting = True
                generation.last_heartbeat = time.monotonic()
                generation.ready.set_result(None)
                try:
                    while not generation.stop.is_set():
                        generation.last_heartbeat = time.monotonic()
                        await asyncio.sleep(self.monitor_interval)
                finally:
                    generation.accepting = False
                    for lease in tuple(generation.leases):
                        lease.disconnected.set()
                    for task in tuple(generation.requests):
                        task.cancel()
                    if generation.requests:
                        await asyncio.gather(*generation.requests, return_exceptions=True)

        def owner():
            try:
                asyncio.run(serve())
            except BaseException as exc:
                generation.failure_type = type(exc).__name__
                generation.failure_frames = [{"file": frame.filename, "line": frame.lineno,
                    "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)[-12:]]
                if not generation.ready.done():
                    generation.ready.set_exception(exc)
                generation.finished.set_exception(exc)
            else:
                generation.finished.set_result(None)
            finally:
                generation.accepting = False

        generation.thread = threading.Thread(target=owner, name="ltm-mcp-transport", daemon=True)
        generation.thread.start()
        deadline = time.monotonic() + self.startup_timeout
        while not generation.ready.done():
            if time.monotonic() >= deadline:
                raise TimeoutError("MCP worker startup timed out")
            await asyncio.sleep(.01)
        generation.ready.result()
        self.state, self.failures = "healthy", 0
        self._record("mcp_worker_started", generation=generation.number)

    async def _stop(self, generation):
        generation.accepting = False
        for lease in tuple(generation.leases):
            lease.disconnected.set()
        if generation.stop is not None:
            generation.stop.set()  # Never wake a possibly hung loop from the UI thread.
        deadline = time.monotonic() + self.shutdown_timeout
        while (not generation.finished.done() or generation.thread.is_alive() or not self.quiescent()):
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(min(.05, self.monitor_interval))
        generation.finished.exception()  # Consume a terminated owner's error, if any.
        return True

    async def _probe(self, generation):
        # The worker advances this pulse only while its own loop services tasks.
        # Sampling never enqueues an RPC or cross-loop wakeup from the UI loop.
        return (not generation.finished.done() and generation.accepting
            and time.monotonic() - generation.last_heartbeat <= max(self.probe_timeout, 2 * self.monitor_interval))

    async def _recover(self, reason):
        now = time.monotonic()
        while self._restarts and now - self._restarts[0] >= self.restart_window:
            self._restarts.popleft()
        self.last_reason = reason
        generation = self.current
        if len(self._restarts) >= self.restart_limit:
            self.state, self.last_reason = "recovery_failed", "recovery_limit"
            self._record("mcp_recovery_failed", generation=generation.number, reason=self.last_reason)
            return
        self._restarts.append(now)
        self.restart_count += 1
        self.state = "recovering"
        self._record("mcp_recovery_started", generation=generation.number, reason=reason,
            errorType=generation.failure_type, frames=generation.failure_frames)
        if not await self._stop(generation):
            self.state, self.last_reason = "recovery_failed", "shutdown_timeout"
            self._record("mcp_recovery_failed", generation=generation.number, reason=self.last_reason)
            return
        if self._closing:
            return
        with self._lock:
            self._pending.clear()
            self._restart_generation = None
        try:
            await self._start()
            self._record("mcp_recovery_completed", generation=self.current.number,
                previousGeneration=generation.number, reason=reason)
        except Exception:
            self.state, self.last_reason = "unresponsive", "startup_failed"
            self._record("mcp_recovery_failed", generation=self.current.number, reason=self.last_reason)

    async def _monitor(self):
        while True:
            await asyncio.sleep(self.monitor_interval)
            if self.state == "recovery_failed":
                continue
            generation = self.current
            if self._restart_generation == generation.number:
                await self._recover("manual_request")
            elif generation.finished.done():
                await self._recover("worker_exited")
            elif await self._probe(generation):
                self.state, self.failures = "healthy", 0
            else:
                self.state = "unresponsive"
                self.failures += 1
                if self.failures >= self.misses_allowed:
                    await self._recover("heartbeat_timeout")

    @asynccontextmanager
    async def lifespan(self):
        self._closing, self.state = False, "starting"
        monitor = None
        try:
            await self._start()
            monitor = asyncio.create_task(self._monitor())
            yield
        finally:
            self._closing = True
            if monitor is not None:
                monitor.cancel()
                try:
                    await monitor
                except asyncio.CancelledError:
                    pass
            if self.current is not None:
                await self._stop(self.current)
            self.state = "stopped"
