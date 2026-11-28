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
    def __init__(self, applications, *, factory=None, quiescent=lambda: True, diagnostics_directory=None):
        self.applications, self.factory, self.quiescent = applications, factory, quiescent
        self.diagnostics_directory = diagnostics_directory
        self.current = None
        self.monitor_interval, self.probe_timeout = 1.0, .5
        self.misses_allowed, self.restart_limit, self.restart_window = 3, 3, 600
        self.recovery_grace = 60.0
        self.shutdown_timeout, self.startup_timeout = 10.0, 10.0
        # SSO attachment jobs allow up to 15 minutes plus 30 seconds each
        # for queueing and execution. Recovery must retain their actual leases
        # without treating that legitimate wait as a dead transport in ten seconds.
        self.drain_timeout = 2 * (15 * 60 + 30) + self.shutdown_timeout
        self.state, self.failures, self.restart_count = "stopped", 0, 0
        self.last_reason = None
        self._restarts = deque()
        self._pending = set()
        self._events = deque(maxlen=20)
        self._restart_generation = None
        self._suspect_since = None
        self._waiting_reason = None
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
            "drainingWork": self.state in {"recovering", "recovery_failed"} and generation is not None
                and not self._work_drained(generation),
            "admissionPaused": bool(generation and not generation.accepting
                                    and self.state not in {"starting", "stopped"}),
            "recoveryGraceRemainingSeconds": round(max(0, self.recovery_grace -
                (time.monotonic() - self._suspect_since)), 3) if self._suspect_since is not None else 0,
            "consecutiveFailures": self.failures, "restartCount": self.restart_count,
            "restartLimit": self.restart_limit, "lastRecoveryReason": self.last_reason,
            "lastHeartbeatAgeSeconds": round(max(0, time.monotonic() - generation.last_heartbeat), 3)
                if generation and generation.last_heartbeat else None}

    def can_admit(self, generation):
        return (self._current_running(generation) and self.state == "healthy"
            and time.monotonic() - generation.last_heartbeat <= max(self.probe_timeout, 2 * self.monitor_interval))

    def _current_running(self, generation):
        return (generation is not None and generation is self.current and not self._closing
            and generation.thread is not None and generation.thread.is_alive()
            and generation.accepting and not generation.finished.done() and not generation.stop.is_set())

    def can_dispatch(self, generation):
        # One delayed timer is suspicion, not the configured consecutive-miss verdict.
        # Keep strict health/observer sampling separate from bounded business admission.
        grace = max(self.probe_timeout, 2 * self.monitor_interval) + self.misses_allowed * self.monitor_interval
        return (self._current_running(generation) and self.state in {"healthy", "unresponsive"}
            and time.monotonic() - generation.last_heartbeat <= grace)

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

        async def serve(diagnostics_directory):
            generation.loop = asyncio.get_running_loop()
            applications = self.factory() if previous and self.factory else self.applications
            async with AsyncExitStack() as stack:
                from app.infra.runtime_diagnostics import install_async_exception_handler
                # Restored after application teardown, including startup failures.
                # Installation itself does not read settings or touch the disk.
                stack.callback(install_async_exception_handler(generation.loop, directory=diagnostics_directory))
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
                        draining = asyncio.gather(*generation.requests, return_exceptions=True)
                        while not draining.done():
                            # Preserve proof of event-loop progress while normal
                            # cancellation cleanup waits for admitted sync work.
                            generation.last_heartbeat = time.monotonic()
                            await asyncio.wait((draining,), timeout=self.monitor_interval)
                        await draining

        def owner():
            try:
                directory = self.diagnostics_directory
                if directory is None:
                    # Resolve the fallback on this owner thread before its loop
                    # starts; explicit test/runtime directories need no settings I/O.
                    from app.infra.settings import CACHE_DIR
                    directory = CACHE_DIR / "logs" / "runtime"
                asyncio.run(serve(directory))
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
        self._suspect_since = self._waiting_reason = None
        self._record("mcp_worker_started", generation=generation.number)

    async def _stop(self, generation):
        generation.accepting = False
        for lease in tuple(generation.leases):
            lease.disconnected.set()
        if generation.stop is not None:
            generation.stop.set()  # Never wake a possibly hung loop from the UI thread.
        started = time.monotonic()
        deadline = started + self.shutdown_timeout
        while (not generation.finished.done() or generation.thread.is_alive() or not self.quiescent()):
            # The larger allowance applies only to admitted synchronous work,
            # never to an idle hung SDK loop. Explicit app shutdown stays bounded
            # by shutdown_timeout; the process supervisor owns final teardown.
            responsive = (generation.finished.done() or
                time.monotonic() - generation.last_heartbeat <= max(self.probe_timeout, 2 * self.monitor_interval))
            if not self._closing and not self.quiescent() and responsive:
                deadline = started + self.drain_timeout
            elif not self._closing:
                deadline = min(deadline, time.monotonic() + self.shutdown_timeout)
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(min(.05, self.monitor_interval))
        generation.finished.exception()  # Consume a terminated owner's error, if any.
        return True

    async def _probe(self, generation):
        # The worker advances this pulse only while its own loop services tasks.
        # Sampling never enqueues an RPC or cross-loop wakeup from the UI loop.
        # A paused admission gate is not evidence that the owner loop is dead.
        # Its real pulse must be able to reopen the same generation after a delay.
        return (generation is self.current and not self._closing
            and generation.thread is not None and generation.thread.is_alive()
            and not generation.finished.done() and not generation.stop.is_set()
            and time.monotonic() - generation.last_heartbeat <= max(self.probe_timeout, 2 * self.monitor_interval))

    def _work_drained(self, generation):
        if not self.quiescent():
            return False
        # An exited loop cannot send more responses. Its actual synchronous tools
        # still own their lanes, but obsolete asyncio lease sets need not block a replacement.
        exited = generation.finished.done() and (generation.thread is None or not generation.thread.is_alive())
        return exited or not (generation.requests or generation.leases)

    def _resume(self, generation):
        generation.accepting = True
        self.state, self.failures = "healthy", 0
        self._suspect_since = self._waiting_reason = None
        self._record("mcp_admission_resumed", generation=generation.number, reason="heartbeat_progress")

    async def _drain_before_recovery(self, generation, reason):
        if reason == "heartbeat_timeout" and await self._probe(generation):
            self._resume(generation)
            return False
        if self._work_drained(generation):
            return True
        self._record("mcp_recovery_waiting", generation=generation.number, reason=reason)
        deadline = time.monotonic() + self.drain_timeout
        while not self._work_drained(generation):
            if self._closing:
                return False
            if reason == "heartbeat_timeout" and await self._probe(generation):
                self._resume(generation)
                return False
            if time.monotonic() >= deadline:
                self.state, self.last_reason = "recovery_failed", "drain_timeout"
                self._record("mcp_recovery_failed", generation=generation.number, reason=self.last_reason)
                return False
            await asyncio.sleep(min(.05, self.monitor_interval))
        # The pulse may have returned just as the last response completed.
        if reason == "heartbeat_timeout" and await self._probe(generation):
            self._resume(generation)
            return False
        return not self._closing

    async def _recover(self, reason):
        now = time.monotonic()
        while self._restarts and now - self._restarts[0] >= self.restart_window:
            self._restarts.popleft()
        self.last_reason = reason
        generation = self.current
        generation.accepting = False
        self._waiting_reason = reason
        if len(self._restarts) >= self.restart_limit:
            self.state, self.last_reason = "recovery_failed", "recovery_limit"
            self._record("mcp_recovery_failed", generation=generation.number, reason=self.last_reason)
            return
        self.state = "recovering"
        # Preserve accepted writes and their responses before signalling stop or
        # disconnecting any lease. Long work is not a reason to tear down the SDK.
        if not await self._drain_before_recovery(generation, reason):
            return
        self._waiting_reason = None
        self._restarts.append(time.monotonic())
        self.restart_count += 1
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

    async def _monitor_once(self):
        generation = self.current
        if self.state == "recovery_failed":
            if self._waiting_reason is not None:
                # A drain timeout never relinquishes the owner or cancels work.
                # Real loop progress can still recover a suspected heartbeat gap.
                if self._waiting_reason == "heartbeat_timeout" and await self._probe(generation):
                    self._resume(generation)
                    return
                if not self._work_drained(generation):
                    return
            else:
                # Never overlap a hung generation or its admitted writes. Once
                # both really finish, recovery is safe again without replacing
                # the usable UI process. Repeated startup failures still observe
                # the existing rolling recovery limit/cooldown.
                if (not self._work_drained(generation) or not generation.finished.done()
                        or generation.thread.is_alive()):
                    return
            if (len(self._restarts) >= self.restart_limit and self._restarts
                    and time.monotonic() - self._restarts[0] < self.restart_window):
                return
            await self._recover(self._waiting_reason or "work_drained")
            return
        if self._restart_generation == generation.number:
            await self._recover("manual_request")
        elif generation.finished.done() or not generation.thread.is_alive():
            await self._recover("worker_exited")
        elif await self._probe(generation):
            if self._suspect_since is not None:
                self._resume(generation)
            else:
                self.state, self.failures = "healthy", 0
        else:
            self.state = "unresponsive"
            self.failures += 1
            if self.failures >= self.misses_allowed:
                if self._suspect_since is None:
                    self._suspect_since = time.monotonic()
                    generation.accepting = False
                    self._record("mcp_admission_paused", generation=generation.number, reason="heartbeat_timeout")
                if time.monotonic() - self._suspect_since >= self.recovery_grace:
                    await self._recover("heartbeat_timeout")

    async def _monitor(self):
        while True:
            await asyncio.sleep(self.monitor_interval)
            await self._monitor_once()

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
