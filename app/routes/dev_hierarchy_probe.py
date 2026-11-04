"""Manager-only local DevTool entry points; a click starts real Jira writes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from contextlib import asynccontextmanager
import anyio

from app.local_mcp.security import local_request_error
from app.local_mcp.write_tools import MutationId
from app.services.dev_hierarchy_probe import HierarchyProbe


class ProbeStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runId: MutationId
    taskType: str = Field(min_length=1, max_length=120)
    subtaskType: str = Field(min_length=1, max_length=120)
    expectedScope: str = Field(pattern=r"^[0-9a-f]{64}$")


def build_hierarchy_probe_router(*, runtime, require_manager, state_dir):
    probe = HierarchyProbe(runtime, state_dir)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await probe.close()

    router = APIRouter(lifespan=lifespan)

    async def invoke(request, action, status=200):
        error = local_request_error(request.scope, runtime.port)
        if error:
            return JSONResponse({"error": error}, status_code=403)
        await anyio.to_thread.run_sync(require_manager)
        try:
            return JSONResponse(await action(), status_code=status, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            problem = probe.error(exc)
            return JSONResponse({"error": str(problem), "code": problem.code}, status_code=problem.status)

    @router.get("/api/dev/hierarchy-test/options")
    async def options(request: Request):
        return await invoke(request, probe.options)

    @router.post("/api/dev/hierarchy-test", status_code=202)
    async def start(request: Request, body: ProbeStart):
        return await invoke(request, lambda: probe.start(body.runId, body.taskType, body.subtaskType, body.expectedScope), 202)

    @router.get("/api/dev/hierarchy-test/{run_id}")
    async def status(request: Request, run_id: str):
        return await invoke(request, lambda: probe.get(run_id))

    router.probe = probe  # Lifecycle and deterministic local tests can wait for this owned runner.
    return router
