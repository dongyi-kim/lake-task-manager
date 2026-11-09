"""Keep SDK parsing/catalog/serialization off the UI event loop, in the same process."""

import asyncio
from contextlib import asynccontextmanager
import threading

from starlette.responses import JSONResponse
from app.local_mcp.capacity import request_disconnected
from app.local_mcp.worker_lifecycle import WorkerLifecycle, RequestLease, request_lease


class TransportHost:
    def __init__(self, applications, capacity=32, *, factory=None, quiescent=lambda: True):
        self.applications = applications
        self._permits = threading.BoundedSemaphore(capacity)
        self._worker = WorkerLifecycle(applications, factory=factory, quiescent=quiescent)
        self.monitor_interval, self.probe_timeout = 1.0, .5

    @property
    def _loop(self):
        generation = self._worker.current
        return generation.loop if self._worker.can_admit(generation) else None

    @property
    def _stop(self):
        return self._worker.current.stop

    def health_snapshot(self):
        return self._worker.snapshot()

    def request_restart(self, expected_generation):
        return self._worker.request_restart(expected_generation)

    def drain_events(self):
        return self._worker.drain_events()

    @asynccontextmanager
    async def lifespan(self):
        self._worker.monitor_interval, self._worker.probe_timeout = self.monitor_interval, self.probe_timeout
        async with self._worker.lifespan():
            yield

    def wrap(self, app, *, dynamic=False):
        async def bridge(scope, receive, send):
            generation = self._worker.current
            loop = self._loop
            if loop is None:
                return await JSONResponse({"error": "MCP is not running", "executed": False}, status_code=503)(scope, receive, send)
            if not self._permits.acquire(blocking=False):
                return await JSONResponse({"error": "MCP request capacity exceeded", "executed": False},
                    status_code=429, headers={"Retry-After": "1"})(scope, receive, send)
            caller = asyncio.get_running_loop()
            lock = threading.Lock()
            started = cancelled = False
            lease = RequestLease(generation)
            disconnected = lease.disconnected
            relays = set()

            async def relay(fn, *args, receiving=False, sending=False):
                # ASGI receive/send belong to Uvicorn's loop. Only SDK work moves;
                # no socket, queue, cache, client or authentication session is copied.
                fallback = {"type": "http.disconnect"} if receiving else None
                if disconnected.is_set():
                    return fallback
                future = asyncio.run_coroutine_threadsafe(fn(*args), caller)
                with lock:
                    relays.add(future)
                    abandoned = disconnected.is_set()
                if abandoned:
                    future.cancel()
                try:
                    result = await asyncio.wrap_future(future)
                    if (sending and args[0].get("type") == "http.response.body"
                            and not args[0].get("more_body", False) and not disconnected.is_set()):
                        lease.response_complete = True
                    return result
                except OSError:
                    # ASGI servers report closed client sockets from send().
                    # Finish SDK cleanup instead of abandoning its session task.
                    disconnected.set()
                    return fallback
                except asyncio.CancelledError:
                    if disconnected.is_set():
                        return fallback
                    raise
                finally:
                    with lock:
                        relays.discard(future)

            async def execute():
                nonlocal started
                with lock:
                    if cancelled or not generation.accepting or generation is not self._worker.current:
                        return False
                    started = True
                task = asyncio.current_task()
                generation.requests.add(task)
                generation.leases.add(lease)
                token = request_disconnected.set(disconnected)
                lease_token = request_lease.set(lease)
                try:
                    application = app() if dynamic else app
                    await application(scope, lambda: relay(receive, receiving=True),
                                      lambda message: relay(send, message, sending=True))
                finally:
                    request_lease.reset(lease_token)
                    request_disconnected.reset(token)
                    generation.requests.discard(task)
                    generation.leases.discard(lease)
                    self._worker.finish_request(lease)
                    self._permits.release()

            try:
                operation = execute()
                try:
                    future = asyncio.run_coroutine_threadsafe(operation, loop)
                except RuntimeError:
                    operation.close()
                    return await JSONResponse({"error": "MCP is stopping"}, status_code=503)(scope, receive, send)
                forwarded = asyncio.wrap_future(future)
                forwarded.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                try:
                    # Let the SDK finish its stateless-session teardown even if
                    # the HTTP caller leaves. Raw cancellation skips that cleanup.
                    accepted = await asyncio.shield(forwarded)
                    if accepted is False:
                        return await JSONResponse({"error": "MCP is recovering", "executed": False},
                            status_code=503)(scope, receive, send)
                except asyncio.CancelledError:
                    disconnected.set()
                    with lock:
                        abandoned_relays = tuple(relays)
                        if not started:
                            cancelled = True
                    for pending in abandoned_relays:
                        pending.cancel()
                    if not started:
                        future.cancel()
                    raise
            finally:
                with lock:
                    if not started:
                        # A disconnected client must not queue a write for later.
                        cancelled = True
                        self._permits.release()
        return _Endpoint(bridge)


class _Endpoint:
    # Starlette treats function endpoints as Request -> Response, and objects as ASGI.
    def __init__(self, handler):
        self.handler = handler

    async def __call__(self, scope, receive, send):
        await self.handler(scope, receive, send)
