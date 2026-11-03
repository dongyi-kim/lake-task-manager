"""Manager-only local configuration API, using the same commands as MCP."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from app.auth.base import SessionExpired
from app.local_mcp.security import local_request_error
from app.services.ticket_commands import CommandError
from app.services.workspace_config import WbsTask


class WbsBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str
    tasks: list[WbsTask]


class PeopleBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str
    people: dict[str, list[str]]


class ConfigRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                details = [".".join(str(p) for p in error["loc"][1:]) + ": " + error["msg"]
                           for error in exc.errors()[:5]]
                return JSONResponse({"error": "입력값을 확인해 주세요. " + " / ".join(details)}, status_code=422)
        return validated


def build_workspace_config_router(commands, *, port):
    router = APIRouter(prefix="/api/workspace/config", route_class=ConfigRoute)

    def call(request, action):
        error = local_request_error(request.scope, port)
        if error or request.method != "GET" and (
            request.headers.get("x-ltm-local") != "1" or
            request.headers.get("content-type", "").split(";", 1)[0] != "application/json"
        ):
            raise HTTPException(403, error or "LTM 설정 화면에서 요청해 주세요.")
        try:
            return JSONResponse(action(), headers={"Cache-Control": "no-store"})
        except SessionExpired as exc:
            return JSONResponse({"error": str(exc), "needLogin": True}, status_code=401)
        except (ValueError, CommandError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=getattr(exc, "status", 400))

    @router.get("")
    def read(request: Request):
        return call(request, commands.read)

    @router.put("/wbs")
    def wbs(request: Request, body: WbsBody):
        return call(request, lambda: commands.save("wbs", body.revision, [task.model_dump() for task in body.tasks]))

    @router.put("/people")
    def people(request: Request, body: PeopleBody):
        return call(request, lambda: commands.save("people", body.revision, body.people))

    return router
