"""Local document composer, using the same commands/receipts as MCP."""
import ipaddress

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.jira import confluence_service as reads
from app.local_mcp.confluence_errors import confluence_error
from app.services.confluence_commands import ConfluenceCommands, MAX_ATTACHMENT_BYTES
from app.services.ticket_commands import CommandError


class CreateDocument(BaseModel):
    space: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(max_length=2_000_000)
    parent_id: str | None = None
    mutation_id: str = Field(min_length=8, max_length=128)


class MentionUsers(BaseModel):
    usernames: list[str] = Field(max_length=100)


class UpdateDocument(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(max_length=2_000_000)
    expected_version: int = Field(ge=1)
    mutation_id: str = Field(min_length=8, max_length=128)


def build_confluence_authoring_router(*, get_client):
    router = APIRouter(prefix="/api/confluence")

    def guard(request):
        port = int(getattr(get_client().s, "app_port", 4457))
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        try:
            local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:
            local = False
        if (not local or request.headers.get("host", "").lower() not in hosts
                or request.headers.get("origin") not in {"http://" + host for host in hosts}
                or request.headers.get("x-ltm-confluence") != "1"):
            raise CommandError("로컬 LTM 문서 작성 화면에서만 변경할 수 있습니다.", 403)

    def reply(request, callback):
        try:
            guard(request)
            result = callback(ConfluenceCommands(get_client, get_client().s))
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            error = confluence_error(exc)
            return JSONResponse({**error, "error": error["message"], "uncertain": error["code"] == "outcome_unknown"},
                                status_code=error.get("status") or 500, headers={"Cache-Control": "no-store"})

    @router.post("/pages")
    def create(request: Request, payload: CreateDocument):
        def write(commands):
            # Root pages have no parent preflight. This read performs silent Confluence renewal
            # before the first POST; a write itself is never blindly repeated after a 401/timeout.
            commands.check_space(payload.space)
            reads.raw_read(commands.client, "/rest/api/space", {"spaceKey": payload.space, "limit": 1})
            return commands.create_page(**payload.model_dump())
        return reply(request, write)

    @router.post("/pages/{page_id}/attachments")
    def upload(request: Request, page_id: str, mutation_id: str = Form(...), file: UploadFile = File(...)):
        def write(commands):
            data = file.file.read(MAX_ATTACHMENT_BYTES + 1)
            return commands.upload_attachment(page_id=page_id, filename=file.filename, data=data,
                mutation_id=mutation_id, content_type=file.content_type or "application/octet-stream")
        return reply(request, write)

    @router.put("/pages/{page_id}")
    def update(request: Request, page_id: str, payload: UpdateDocument):
        return reply(request, lambda commands: commands.update_page(page_id=page_id, **payload.model_dump()))

    @router.post("/mention-users")
    def users(request: Request, payload: MentionUsers):
        def resolve(commands):
            result = {}
            for username in dict.fromkeys(payload.usernames):
                if not username or len(username) > 255:
                    raise CommandError("유효한 사용자 이름이 필요합니다.")
                item = commands.request("GET", "/rest/api/user", params={"username": username})
                # Jira usernames are lookup inputs, never assumed to be Confluence user keys.
                if (not isinstance(item, dict) or item.get("username") != username or not item.get("userKey")):
                    raise CommandError("Confluence 멘션 사용자를 확인하지 못했습니다: " + username)
                result[username] = item["userKey"]
            return result
        return reply(request, resolve)

    return router
