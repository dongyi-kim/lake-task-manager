"""Local UI management; secret export happens only after an explicit same-origin action."""

import json
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.local_mcp.security import local_request_error


class EnableBody(BaseModel):
    enabled: bool


class ExportBody(BaseModel):
    format: Literal["token", "http", "stdio"]


def build_settings_router(*, runtime):
    router = APIRouter(prefix="/api/mcp")

    def check(request):
        error = local_request_error(request.scope, runtime.port)
        if error:
            raise HTTPException(403, error)
        if request.method != "GET" and (
            request.headers.get("x-ltm-local") != "1"
            or request.headers.get("content-type", "").split(";", 1)[0] != "application/json"
        ):
            raise HTTPException(403, "LTM 설정 화면에서 요청해 주세요.")

    @router.get("/settings")
    def status(request: Request):
        check(request)
        return runtime.status()

    @router.put("/settings")
    def configure(request: Request, body: EnableBody):
        check(request)
        runtime.credentials.configure(body.enabled)
        return runtime.status()

    @router.post("/rotate")
    def rotate(request: Request):
        check(request)
        runtime.credentials.rotate()
        return runtime.status()

    @router.post("/credentials")
    def export(request: Request, body: ExportBody):
        check(request)
        token = runtime.credentials.token()
        if body.format == "token":
            text = token
        elif body.format == "http":
            text = json.dumps({"mcpServers": {"lake-task-manager": {
                "url": runtime.url, "headers": {"Authorization": "Bearer " + token},
            }}}, ensure_ascii=False, indent=2)
        else:
            text = json.dumps({"mcpServers": {"lake-task-manager": {
                "command": sys.executable,
                "args": ["--mcp-stdio"] if getattr(sys, "frozen", False)
                        else [str(Path(__file__).with_name("stdio.py").resolve())],
                "env": {"LTM_MCP_URL": runtime.url, "LTM_MCP_TOKEN": token},
            }}}, ensure_ascii=False, indent=2)
        from fastapi.responses import JSONResponse
        return JSONResponse({"text": text}, headers={"Cache-Control": "no-store"})

    return router
