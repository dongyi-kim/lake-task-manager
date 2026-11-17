"""A slow synchronous Jira upload cannot block the API's liveness loop."""
import asyncio
import threading
from types import SimpleNamespace

import anyio
from fastapi import FastAPI
import httpx

from app.infra.request_admission import RequestAdmissionMiddleware
from app.routes.ticket_commands import build_ticket_command_router


def test_attachment_upload_keeps_health_available_and_preserves_one_write_and_response():
    entered, release, health_seen = threading.Event(), threading.Event(), threading.Event()
    calls, health_before_upload_finished = [], []
    payload = b"attachment" * 40_000

    def upload(key, filename, data, content_type, *, mutation_id):
        calls.append((key, filename, data, content_type, mutation_id))
        entered.set()
        # The timeout permits the pre-fix blocking-loop regression to finish and
        # fail on ordering, instead of leaving the test itself deadlocked.
        release.wait(2)
        health_before_upload_finished.append(health_seen.is_set())
        return [{"id": "42", "filename": "confirmed.bin"}]

    app = FastAPI()
    app.include_router(build_ticket_command_router(
        get_client=lambda: SimpleNamespace(upload_attachment=upload), settings=None,
        may_edit=lambda _key: True, require_edit=lambda _key: None, session_user=lambda: {"id": "owner"}))
    app.add_middleware(RequestAdmissionMiddleware)

    @app.get("/api/health/live")
    async def health():
        health_seen.set()
        return {"status": "ok"}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            uploading = asyncio.create_task(client.post("/api/ticket/P-1/attachment",
                files={"file": ("draft.bin", payload, "application/octet-stream")},
                data={"clientMutationId": "upload:responsiveness:0001"}))
            try:
                with anyio.fail_after(4):
                    while not entered.is_set():
                        await anyio.sleep(.001)
                    assert (await client.get("/api/health/live")).json() == {"status": "ok"}
            finally:
                release.set()
            response = await uploading
            assert response.status_code == 200
            assert response.json() == {"id": "42", "filename": "confirmed.bin",
                                       "path": "/secure/attachment/42/confirmed.bin"}

    asyncio.run(scenario())
    assert health_before_upload_finished == [True]
    assert calls == [("P-1", "draft.bin", payload, "application/octet-stream", "upload:responsiveness:0001")]
