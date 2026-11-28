"""HTTP overload is refused before parsing or executing application work."""
import asyncio
import json
import tempfile
import threading

import anyio
from fastapi import FastAPI, File, Request, UploadFile
import httpx
import pytest

from app.infra.request_admission import RequestAdmissionMiddleware


def test_business_flood_is_not_queued_and_lightweight_controls_remain_available():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def app(scope, receive, send):
            calls.append(scope["path"])
            if scope["path"] == "/api/work":
                entered.set()
                await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        activity = {}
        gate = RequestAdmissionMiddleware(app, capacity=1, activity=activity)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            active = asyncio.create_task(client.get("/api/work"))
            await entered.wait()
            assert activity == {"http": 1}
            try:
                with anyio.fail_after(2):
                    rejected = await asyncio.gather(*(client.post("/api/write", json={"i": i}) for i in range(100)))
                    assert all(r.status_code == 429 for r in rejected)
                    assert all(r.json()["executed"] is False and r.headers["Retry-After"] == "1" for r in rejected)
                    assert (await client.get("/api/health/live")).status_code == 200
                    assert (await client.post("/api/app/open")).status_code == 200
                    assert (await client.post("/api/app/quit")).status_code == 200
                assert calls == ["/api/work", "/api/health/live", "/api/app/open", "/api/app/quit"]
                assert activity == {"http": 1}
            finally:
                release.set()
                await active
            assert activity == {"http": 0}
            assert (await client.post("/api/write")).status_code == 200

    asyncio.run(scenario())


@pytest.mark.parametrize("chunked", [False, True])
def test_oversized_bodies_never_reach_handler_or_parser(chunked, monkeypatch):
    async def scenario():
        calls, files = [], []
        original = tempfile.SpooledTemporaryFile

        def record_file(*args, **kwargs):
            result = original(*args, **kwargs)
            files.append(result)
            return result

        monkeypatch.setattr(tempfile, "SpooledTemporaryFile", record_file)

        async def app(scope, receive, send):
            calls.append(scope)
            raise AssertionError("oversized request must not execute")

        gate = RequestAdmissionMiddleware(app, max_body_bytes=8)
        async def chunks():
            yield b"1234"
            yield b"56789"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            response = await client.post("/api/write", content=chunks() if chunked else b"123456789")
        assert response.status_code == 413
        assert response.json()["executed"] is False
        assert response.json()["retryable"] is False
        assert response.headers["connection"] == "close"
        assert calls == []
        assert all(file.closed for file in files)
        if not chunked:
            assert files == []

    asyncio.run(scenario())


def test_spooled_body_is_replayed_exactly_and_file_closed_on_handler_failure(monkeypatch):
    async def scenario():
        files, bodies = [], []
        original = tempfile.SpooledTemporaryFile

        def record_file(*args, **kwargs):
            assert kwargs["max_size"] == 256 * 1024
            result = original(*args, **kwargs)
            files.append(result)
            return result

        monkeypatch.setattr(tempfile, "SpooledTemporaryFile", record_file)
        async def app(scope, receive, send):
            data = bytearray()
            while True:
                message = await receive()
                data.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            bodies.append(bytes(data))
            raise ValueError("handler failed")

        gate = RequestAdmissionMiddleware(app, capacity=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            for _ in range(2):
                with pytest.raises(ValueError, match="handler failed"):
                    await client.post("/api/write", content=b"x" * 300_000)
        assert bodies == [b"x" * 300_000] * 2
        assert len(files) == 2 and all(file.closed for file in files)

    asyncio.run(scenario())


def test_upload_has_its_own_single_slot_and_cannot_bypass_json_limit():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            if scope["path"].endswith("/attachment"):
                entered.set()
                await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        gate = RequestAdmissionMiddleware(app, max_body_bytes=8, max_upload_bytes=32)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            active = asyncio.create_task(client.post("/api/ticket/DL-1/attachment", content=b"x" * 20))
            await entered.wait()
            try:
                assert (await client.post("/api/ticket/DL-2/attachment")).status_code == 429
                assert (await client.post("/api/write", content=b"x" * 9,
                                          headers={"content-type": "multipart/form-data"})).status_code == 413
                assert (await client.get("/api/data")).status_code == 200
            finally:
                release.set()
                assert (await active).status_code == 200
    asyncio.run(scenario())


def test_tracking_cancel_uses_reserved_workers_when_default_pool_is_full():
    from app.routes.tracking import build_tracking_router

    class Manager:
        def cancel(self, run_id):
            return {"id": run_id, "thread": threading.current_thread().name}

    async def scenario():
        app = FastAPI()
        app.include_router(build_tracking_router(get_client=lambda: None, manager=Manager()))
        app.add_middleware(RequestAdmissionMiddleware)
        limiter = anyio.to_thread.current_default_thread_limiter()
        borrowers = [object() for _ in range(limiter.total_tokens)]
        for borrower in borrowers:
            limiter.acquire_on_behalf_of_nowait(borrower)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                with anyio.fail_after(2):
                    response = await client.post("/api/tracking/runs/run-1/cancel")
                assert response.status_code == 200
                assert response.json()["id"] == "run-1"
                assert response.json()["thread"] != threading.current_thread().name
        finally:
            for borrower in borrowers:
                limiter.release_on_behalf_of(borrower)

    asyncio.run(scenario())


def test_header_rejection_does_not_read_body_and_pressure_rejection_is_unexecuted():
    from app.auth.base import UpstreamBusy

    async def scenario():
        messages = []
        async def forbidden(*args):
            raise AssertionError("request must not be read or executed")
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "path": "/api/write", "method": "POST",
                 "headers": [(b"content-length", b"9")]}
        gate = RequestAdmissionMiddleware(forbidden, max_body_bytes=8)
        await gate(scope, forbidden, send)
        assert messages[0]["status"] == 413

        def pressure():
            raise UpstreamBusy("memory budget exhausted")
        gate = RequestAdmissionMiddleware(forbidden, pressure_check=pressure)
        messages.clear()
        await gate(scope, forbidden, send)
        assert messages[0]["status"] == 429
        assert json.loads(messages[1]["body"])["executed"] is False

    asyncio.run(scenario())


def test_slow_or_disconnected_body_is_not_executed_and_releases_capacity():
    async def scenario():
        calls, messages = [], []
        async def app(scope, receive, send):
            calls.append(scope)
        async def slow():
            await asyncio.Event().wait()
        async def disconnected():
            return {"type": "http.disconnect"}
        async def complete():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "path": "/api/write", "method": "POST", "headers": []}
        gate = RequestAdmissionMiddleware(app, capacity=1, body_timeout=0.01)
        await gate(scope, slow, send)
        assert messages[0]["status"] == 408
        assert calls == []
        await gate(scope, disconnected, send)
        assert calls == []
        await gate(scope, complete, send)
        assert len(calls) == 1

    asyncio.run(scenario())


def test_request_cancellation_closes_spool_and_releases_capacity(monkeypatch):
    async def scenario():
        files, calls = [], []
        started = asyncio.Event()
        original = tempfile.SpooledTemporaryFile
        def record_file(*args, **kwargs):
            result = original(*args, **kwargs)
            files.append(result)
            return result
        monkeypatch.setattr(tempfile, "SpooledTemporaryFile", record_file)
        async def app(scope, receive, send):
            calls.append(scope)
        async def receive():
            started.set()
            await asyncio.Event().wait()
        async def complete():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            pass
        scope = {"type": "http", "path": "/api/write", "method": "POST", "headers": []}
        gate = RequestAdmissionMiddleware(app, capacity=1)
        task = asyncio.create_task(gate(scope, receive, send))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with anyio.fail_after(2):
            while gate._business:
                await anyio.sleep(.001)
        assert files[0].closed
        await gate(scope, complete, send)
        assert len(calls) == 1 and all(file.closed for file in files)

    asyncio.run(scenario())


def test_stalled_cancellations_have_separate_capacity_from_liveness_and_data():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        cancelled = []
        async def app(scope, receive, send):
            if scope["path"].endswith("/cancel"):
                cancelled.append(scope["path"])
                if len(cancelled) == 2:
                    entered.set()
                await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        gate = RequestAdmissionMiddleware(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            active = [asyncio.create_task(client.post(f"/api/tracking/runs/{i}/cancel")) for i in range(2)]
            await entered.wait()
            try:
                assert (await client.post("/api/tracking/runs/other/cancel")).status_code == 429
                assert (await client.get("/api/health/live")).status_code == 200
                assert (await client.get("/api/data")).status_code == 200
            finally:
                release.set()
                await asyncio.gather(*active)
    asyncio.run(scenario())


def test_large_utf8_editor_json_and_multipart_reach_fastapi_parsers():
    async def scenario():
        app = FastAPI()
        @app.post("/api/editor")
        async def editor(request: Request):
            return {"length": len((await request.json())["html"])}
        @app.post("/api/ticket/{key}/attachment")
        async def upload(key: str, file: UploadFile = File(...)):
            return {"body": (await file.read()).decode(), "filename": file.filename}
        app.add_middleware(RequestAdmissionMiddleware)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/editor", json={"html": "가" * 2_000_000})).json() == {"length": 2_000_000}
            result = await client.post("/api/ticket/DL-1/attachment", files={"file": ("example.txt", b"contents")})
            assert result.json() == {"body": "contents", "filename": "example.txt"}
    asyncio.run(scenario())


def test_live_health_reports_cached_resource_state_without_sampling(monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient
    state = {"busy": True, "supported": True, "processBytes": 1024}
    monkeypatch.setattr(main.resource_pressure, "snapshot", lambda: dict(state))
    def forbidden():
        raise AssertionError("liveness must not sample OS memory")
    monkeypatch.setattr(main.resource_pressure, "sampler", forbidden)
    monkeypatch.setattr(main, "_runtime_health_hook", lambda: True)
    response = TestClient(main.app).get("/api/health/live")
    assert response.status_code == 200
    assert response.json()["resources"] == state


def test_quit_flood_coalesces_one_pending_thread_and_allows_retry_after_hook_failure(monkeypatch):
    import app.main as main
    import time
    workers, hooks = [], []
    class Thread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            workers.append(self)
    def hook():
        hooks.append("quit")
        raise OSError("launcher quit failed")
    monkeypatch.setattr(main.threading, "Thread", Thread)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setitem(main._app_ctrl, "quit_hook", hook)
    monkeypatch.setitem(main._app_ctrl, "quit_pending", False)
    assert all(main.request_quit() == {"action": "quit"} for _ in range(100))
    assert len(workers) == 1
    workers[0].target()
    assert hooks == ["quit"]
    assert main.request_quit() == {"action": "quit"}
    assert len(workers) == 2
    workers[1].target()


def test_temporary_file_close_failure_still_releases_capacity(monkeypatch):
    async def scenario():
        calls = []
        original = tempfile.SpooledTemporaryFile
        class FailingClose:
            def __init__(self, *args, **kwargs):
                self.file = original(*args, **kwargs)
            def __getattr__(self, name):
                return getattr(self.file, name)
            def close(self):
                self.file.close()
                raise OSError("temporary disk close failed")
        monkeypatch.setattr(tempfile, "SpooledTemporaryFile", FailingClose)
        async def app(scope, receive, send):
            calls.append(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        gate = RequestAdmissionMiddleware(app, capacity=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            for _ in range(2):
                with pytest.raises(OSError, match="temporary disk close failed"):
                    await client.post("/api/write")
        assert len(calls) == 2
    asyncio.run(scenario())


def test_raw_caller_cancellation_keeps_permit_until_sync_handler_really_finishes():
    async def scenario():
        app = FastAPI()
        entered, release = threading.Event(), threading.Event()
        calls = []
        activity = {"http": 0}

        @app.post("/api/write")
        def write():
            calls.append("started")
            entered.set()
            assert release.wait(5)
            return {"ok": True}

        gate = RequestAdmissionMiddleware(app, capacity=1, activity=activity)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/write"))
            with anyio.fail_after(2):
                while not entered.is_set():
                    await anyio.sleep(.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert activity == {"http": 1}
            try:
                refused = await client.post("/api/write")
                assert refused.status_code == 429
                assert refused.json()["executed"] is False
                assert calls == ["started"]
            finally:
                release.set()
                with anyio.fail_after(2):
                    while gate._business:
                        await anyio.sleep(.001)
            assert activity == {"http": 0}
            assert (await client.post("/api/write")).status_code == 200
            assert calls == ["started", "started"]
            assert activity == {"http": 0}

    asyncio.run(scenario())


def test_raw_caller_cancellation_cannot_close_spool_while_write_is_running(monkeypatch):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        files, calls, closed_early = [], [], []
        original = tempfile.SpooledTemporaryFile

        class HeldWrite:
            def __init__(self, *args, **kwargs):
                self.file = original(*args, **kwargs)
                self.writing = False
                files.append(self.file)

            def __getattr__(self, name):
                return getattr(self.file, name)

            def write(self, data):
                self.writing = True
                entered.set()
                assert release.wait(5)
                try:
                    return self.file.write(data)
                finally:
                    self.writing = False

            def close(self):
                if self.writing:
                    closed_early.append(True)
                return self.file.close()

        monkeypatch.setattr(tempfile, "SpooledTemporaryFile", HeldWrite)

        async def app(scope, receive, send):
            calls.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        gate = RequestAdmissionMiddleware(app, capacity=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/write", content=b"body"))
            with anyio.fail_after(2):
                while not entered.is_set():
                    await anyio.sleep(.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            try:
                assert (await client.post("/api/write")).status_code == 429
                assert not files[0].closed
            finally:
                release.set()
                with anyio.fail_after(2):
                    while gate._business:
                        await anyio.sleep(.001)
            assert closed_early == []
            assert files[0].closed
            assert calls == []

    asyncio.run(scenario())


def test_spooled_json_and_streaming_response_survive_base_http_middleware():
    from starlette.responses import StreamingResponse

    async def scenario():
        app = FastAPI()

        @app.middleware("http")
        async def inspect_body(request, call_next):
            body = await request.body()
            response = await call_next(request)
            response.headers["X-Body-Bytes"] = str(len(body))
            return response

        @app.post("/api/stream")
        async def stream(request: Request):
            text = (await request.json())["text"]

            async def chunks():
                yield "first:"
                await anyio.sleep(0)
                yield text
                yield ":last"

            return StreamingResponse(chunks())

        gate = RequestAdmissionMiddleware(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://test") as client:
            response = await client.post("/api/stream", json={"text": "가" * 100_000})
            assert response.status_code == 200
            assert response.text == "first:" + "가" * 100_000 + ":last"
            assert int(response.headers["X-Body-Bytes"]) > 256 * 1024

    asyncio.run(scenario())


def test_raw_streaming_caller_cancellation_stops_iterator_and_releases_permit():
    from starlette.responses import StreamingResponse

    async def scenario():
        app = FastAPI()
        first_chunk, stopped = asyncio.Event(), asyncio.Event()

        @app.get("/api/stream")
        async def stream():
            async def chunks():
                try:
                    while True:
                        yield b"chunk"
                        await anyio.sleep(.001)
                finally:
                    stopped.set()

            return StreamingResponse(chunks())

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                first_chunk.set()

        scope = {"type": "http", "method": "GET", "path": "/api/stream", "query_string": b"",
                 "headers": [], "scheme": "http", "server": ("test", 80), "client": ("test", 1),
                 "http_version": "1.1", "asgi": {"version": "3.0", "spec_version": "2.4"}}
        gate = RequestAdmissionMiddleware(app, capacity=1)
        task = asyncio.create_task(gate(scope, receive, send))
        with anyio.fail_after(2):
            await first_chunk.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with anyio.fail_after(2):
            while gate._business:
                await anyio.sleep(.001)
            await stopped.wait()

    asyncio.run(scenario())
