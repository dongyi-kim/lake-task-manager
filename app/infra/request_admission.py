"""Bound HTTP work and body parsing before FastAPI allocates or queues handlers.

There is no waiting queue: callers receive an explicit unexecuted 429. JSON/API
bodies are limited to 16 MiB; attachment requests allow 64 MiB plus 1 MiB of
multipart overhead, with only one upload in flight. Existing domain limits
(including Confluence's 10 MiB attachment limit) still apply.
"""
from __future__ import annotations

import asyncio
from functools import partial
import re
import tempfile
import threading

import anyio
from starlette.responses import JSONResponse

from app.auth.base import UpstreamBusy


_UPLOAD = re.compile(r"(?:/api/ticket/[^/]+/attachment|/api/confluence/pages/[^/]+/attachments)/?$")
_CANCEL = re.compile(r"/api/tracking/runs/[^/]+/cancel/?$")
_DIAGNOSTICS = re.compile(r"/api/app/diagnostics(?:/[A-Za-z0-9.-]+/download)?/?$")
_LIGHTWEIGHT = {("GET", "/api/health/live"), ("POST", "/api/app/open"), ("POST", "/api/app/quit")}


class RequestAdmissionMiddleware:
    def __init__(self, app, *, capacity=8, control_capacity=2, max_body_bytes=16 * 1024 * 1024,
                 max_upload_bytes=65 * 1024 * 1024, body_timeout=30, pressure_check=None, activity=None):
        self.app = app
        self.capacity, self.control_capacity = capacity, control_capacity
        self.max_body_bytes, self.max_upload_bytes = max_body_bytes, max_upload_bytes
        self.body_timeout, self.pressure_check = body_timeout, pressure_check
        self._lock = threading.Lock()
        self._business = self._control = self._uploads = 0
        self._file_limiter = anyio.CapacityLimiter(2)
        self._requests = set()
        self.activity = activity if activity is not None else {}

    def _acquire(self, control, upload):
        with self._lock:
            if control:
                if self._control >= self.control_capacity:
                    return False
                self._control += 1
            else:
                if self._business >= self.capacity or (upload and self._uploads):
                    return False
                self._business += 1
                self._uploads += int(upload)
            self.activity["http"] = self._business + self._control
            return True

    def _release(self, control, upload):
        with self._lock:
            if control:
                self._control -= 1
            else:
                self._business -= 1
                self._uploads -= int(upload)
            self.activity["http"] = self._business + self._control

    @staticmethod
    async def _reject(scope, receive, send, status, code, message):
        headers = {"Cache-Control": "no-store", "Connection": "close"}
        if status == 429:
            headers["Retry-After"] = "1"
        await JSONResponse({"error": message, "code": code, "executed": False,
                            "retryable": status == 429}, status_code=status, headers=headers)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope.get("method", "GET")
        # These async handlers use memory only and do not inspect the body. They
        # must remain reachable even when both cancellation workers are stuck.
        if (method, path.rstrip("/")) in _LIGHTWEIGHT:
            return await self.app(scope, receive, send)
        control = (method == "POST" and bool(_CANCEL.fullmatch(path))) or (
            method == "GET" and bool(_DIAGNOSTICS.fullmatch(path)))
        upload = method == "POST" and bool(_UPLOAD.fullmatch(path))
        if not self._acquire(control, upload):
            return await self._reject(scope, receive, send, 429, "request_capacity",
                                      "요청이 많아 실행하지 않았습니다. 잠시 후 다시 시도해 주세요.")
        disconnected = asyncio.Event()

        async def relay(fn, *args, receiving=False):
            def gone():
                if receiving:
                    return {"type": "http.disconnect"}
                # ASGI streaming responses use a failed send to stop their
                # iterator; silently swallowing it can retain a lease forever.
                raise OSError("HTTP client disconnected")

            if disconnected.is_set():
                return gone()
            operation = asyncio.create_task(fn(*args))
            departed = asyncio.create_task(disconnected.wait())
            try:
                done, _ = await asyncio.wait((operation, departed), return_when=asyncio.FIRST_COMPLETED)
                if departed in done:
                    return gone()
                try:
                    result = await operation
                except OSError:
                    disconnected.set()
                    return gone()
                if receiving and result.get("type") == "http.disconnect":
                    disconnected.set()
                return result
            finally:
                operation.cancel()
                departed.cancel()
                await asyncio.gather(operation, departed, return_exceptions=True)

        # The child, rather than the HTTP waiter, owns file operations and the
        # downstream handler lease. Raw Task.cancel otherwise abandons a running
        # AnyIO sync worker and releases admission before its write has finished.
        task = asyncio.create_task(self._serve(scope, lambda: relay(receive, receiving=True),
            lambda message: relay(send, message), control, upload, disconnected))
        with self._lock:
            self._requests.add(task)

        def finished(done):
            with self._lock:
                self._requests.discard(done)
            if not done.cancelled():
                done.exception()  # Observe orphaned failures after the HTTP caller leaves.

        task.add_done_callback(finished)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            disconnected.set()
            raise

    async def _serve(self, scope, receive, send, control, upload, disconnected):
        spool = None
        try:
            if disconnected.is_set():
                return
            if not control and self.pressure_check is not None:
                try:
                    self.pressure_check()
                except UpstreamBusy:
                    return await self._reject(scope, receive, send, 429, "resource_pressure",
                                              "앱이 처리 중이어서 실행하지 않았습니다. 잠시 후 다시 시도해 주세요.")
            limit = self.max_upload_bytes if upload else self.max_body_bytes
            lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
            try:
                if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
                    raise ValueError("invalid content length")
                length = int(lengths[0]) if lengths else None
            except ValueError:
                return await self._reject(scope, receive, send, 400, "invalid_content_length",
                                          "요청 본문 크기가 올바르지 않습니다.")
            if length is not None and length > limit:
                return await self._reject(scope, receive, send, 413, "request_body_too_large",
                                          f"요청 본문은 {limit // (1024 * 1024)} MiB 이하여야 합니다.")
            spool = tempfile.SpooledTemporaryFile(max_size=256 * 1024, mode="w+b")
            size = 0
            try:
                with anyio.fail_after(self.body_timeout):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > limit:
                            return await self._reject(scope, receive, send, 413, "request_body_too_large",
                                                      f"요청 본문은 {limit // (1024 * 1024)} MiB 이하여야 합니다.")
                        if chunk:
                            await anyio.to_thread.run_sync(spool.write, chunk, limiter=self._file_limiter)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await self._reject(scope, receive, send, 408, "request_body_timeout",
                                          "요청 본문을 받는 시간이 초과되어 실행하지 않았습니다.")
            await anyio.to_thread.run_sync(spool.seek, 0, limiter=self._file_limiter)
            remaining = size
            body_delivered = False

            async def replay_receive():
                nonlocal remaining, body_delivered
                if body_delivered:
                    return await receive()
                if remaining:
                    chunk = await anyio.to_thread.run_sync(partial(spool.read, min(64 * 1024, remaining)),
                                                          limiter=self._file_limiter)
                    remaining -= len(chunk)
                    body_delivered = remaining == 0
                    return {"type": "http.request", "body": chunk, "more_body": remaining > 0}
                body_delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}

            if not disconnected.is_set():
                return await self.app(scope, replay_receive, send)
        finally:
            try:
                if spool is not None:
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(spool.close, limiter=self._file_limiter)
            finally:
                self._release(control, upload)
