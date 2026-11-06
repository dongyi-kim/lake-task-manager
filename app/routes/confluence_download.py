"""Local binary downloads: avoid putting full attachments into MCP model context."""

import ipaddress
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from app.local_mcp.confluence_errors import confluence_error
from app.services.confluence_commands import ConfluenceCommands


def build_confluence_download_router(*, get_client):
    router = APIRouter(prefix="/api/confluence")

    @router.get("/pages/{page_id}/attachments/{attachment_id}/download")
    def download(request: Request, page_id: str, attachment_id: str, version: int = Query(..., ge=1)):
        client = get_client()
        port = int(getattr(client.s, "app_port", 4457))
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        try:
            local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:
            local = False
        origin = request.headers.get("origin")
        if (not local or request.headers.get("host", "").lower() not in hosts
                or origin and origin not in {"http://" + host for host in hosts}):
            return JSONResponse({"error": "로컬 LTM에서만 첨부를 다운로드할 수 있습니다."}, status_code=403)
        try:
            commands = ConfluenceCommands(get_client, client.s)
            metadata, data = commands.attachment_bytes(page_id=page_id, attachment_id=attachment_id, version=version)
            # attachment prevents active HTML/SVG content from executing on the LTM origin.
            filename = quote(metadata["filename"] or "attachment", safe="")
            return Response(data, media_type="application/octet-stream", headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        except Exception as exc:
            error = confluence_error(exc)
            return JSONResponse({"error": error}, status_code=error.get("status", 500),
                                headers={"Cache-Control": "no-store"})

    return router
