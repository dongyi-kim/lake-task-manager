"""Keep SDK parsing/catalog/serialization off the UI event loop, in the same process."""

import asyncio
from concurrent.futures import Future
from contextlib import AsyncExitStack, asynccontextmanager
import threading

from starlette.responses import JSONResponse


class TransportHost:
    def __init__(self, applications, capacity=32):
        self.applications = applications
        self._permits = threading.BoundedSemaphore(capacity)
        self._loop = None
        self._requests = set()

    @asynccontextmanager
    async def lifespan(self):
        ready, finished = Future(), Future()

        async def serve():
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            async with AsyncExitStack() as stack:
                for app in self.applications:
                    await stack.enter_async_context(app.router.lifespan_context(app))
                ready.set_result(None)
                await self._stop.wait()
                for task in tuple(self._requests):
                    task.cancel()
                if self._requests:
                    await asyncio.gather(*self._requests, return_exceptions=True)

        def owner():
            try:
                asyncio.run(serve())
            except BaseException as exc:
                self._loop = None
                if not ready.done():
                    ready.set_exception(exc)
                finished.set_exception(exc)
            else:
                self._loop = None
                finished.set_result(None)

        thread = threading.Thread(target=owner, name="ltm-mcp-transport", daemon=True)
        thread.start()
        try:
            await asyncio.shield(asyncio.wrap_future(ready))
            yield
        finally:
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._stop.set)
            await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(finished)), timeout=10)

    def wrap(self, app):
        async def bridge(scope, receive, send):
            loop = self._loop
            if loop is None:
                return await JSONResponse({"error": "MCP is not running"}, status_code=503)(scope, receive, send)
            if not self._permits.acquire(blocking=False):
                return await JSONResponse({"error": "MCP request capacity exceeded", "executed": False},
                    status_code=429, headers={"Retry-After": "1"})(scope, receive, send)
            caller = asyncio.get_running_loop()
            lock = threading.Lock()
            started = cancelled = False

            async def relay(fn, *args):
                # ASGI receive/send belong to Uvicorn's loop. Only SDK work moves;
                # no socket, queue, cache, client or authentication session is copied.
                future = asyncio.run_coroutine_threadsafe(fn(*args), caller)
                return await asyncio.wrap_future(future)

            async def execute():
                nonlocal started
                with lock:
                    if cancelled:
                        return
                    started = True
                task = asyncio.current_task()
                self._requests.add(task)
                try:
                    await app(scope, lambda: relay(receive), lambda message: relay(send, message))
                finally:
                    self._requests.discard(task)
                    self._permits.release()

            try:
                operation = execute()
                try:
                    future = asyncio.run_coroutine_threadsafe(operation, loop)
                except RuntimeError:
                    operation.close()
                    return await JSONResponse({"error": "MCP is stopping"}, status_code=503)(scope, receive, send)
                await asyncio.wrap_future(future)
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
