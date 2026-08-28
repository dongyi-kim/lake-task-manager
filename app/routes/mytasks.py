"""My Tasks snapshot and progressive synchronization routes."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth.base import SessionExpired, background_upstream
from app.domain import mytasks


def _scope(value: str) -> str:
    if value in ("assignee", "reporter", "both", "mymodules"):
        return value
    if value.startswith(("module:", "assignee:", "reporter:", "epic:", "jql:")):
        return value
    return "assignee"


def _filters(open_filter: str, progress_filter: str, done_filter: str):
    return (
        open_filter if open_filter in ("all", "2w") else "all",
        progress_filter if progress_filter in ("all", "1m") else "all",
        done_filter if done_filter in ("1w", "1m") else "1w",
    )


def build_mytasks_router(*, get_client) -> APIRouter:
    router = APIRouter()

    @router.get("/api/mytasks")
    def api_mytasks(user: str = "", done: bool = False, scope: str = "assignee",
                    openFilter: str = "all", progFilter: str = "all", doneFilter: str = "1w",
                    deferred: bool = False):
        open_filter, progress_filter, done_filter = _filters(openFilter, progFilter, doneFilter)
        return JSONResponse(mytasks.build_my_tasks(
            get_client(), user or None, include_done=done, scope=_scope(scope),
            open_filter=open_filter, prog_filter=progress_filter, done_filter=done_filter,
            defer_children=deferred,
        ))

    @router.get("/api/mytasks/stream")
    def api_mytasks_stream(user: str = "", done: bool = False, scope: str = "assignee",
                           openFilter: str = "all", progFilter: str = "all",
                           doneFilter: str = "1w", requestToken: str = ""):
        token = re.sub(r"[^A-Za-z0-9._:-]", "", requestToken or "")[:128]
        open_filter, progress_filter, done_filter = _filters(openFilter, progFilter, doneFilter)

        def lines():
            try:
                for event in mytasks.iter_my_task_models(
                        get_client(), user or None, include_done=done, scope=_scope(scope),
                        open_filter=open_filter, prog_filter=progress_filter, done_filter=done_filter,
                        request_token=token or None):
                    yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            except Exception as exc:
                yield json.dumps({
                    "type": "error", "error": str(exc),
                    "contract": mytasks.TASK_STREAM_CONTRACT, "requestToken": token,
                    "needLogin": isinstance(exc, SessionExpired),
                }, ensure_ascii=False, separators=(",", ":")) + "\n"

        return StreamingResponse(
            lines(), media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/mytasks/sync/{sync_id}/group/{key}")
    def api_mytasks_group(sync_id: str, key: str):
        try:
            with background_upstream():
                snapshot = mytasks.hydrate_my_task_snapshot(get_client(), sync_id, key.upper())
            return JSONResponse(snapshot)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=410)

    @router.get("/api/mytasks/epics")
    def api_mytasks_epic_metadata(keys: str = ""):
        requested = [key.strip().upper() for key in keys.split(",") if key.strip()][:100]
        with background_upstream():
            epics = get_client().epic_metadata_many(requested)
        return JSONResponse({"epics": epics}, headers={"Cache-Control": "no-store"})

    @router.get("/api/mytasks/sync/{sync_id}/epics")
    def api_mytasks_epics(sync_id: str):
        try:
            with background_upstream():
                epics = mytasks.hydrate_my_task_epics(get_client(), sync_id)
            return JSONResponse({"epics": epics})
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=410)

    return router
