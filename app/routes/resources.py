"""Shared issue, search, mention, and media HTTP resources."""

from __future__ import annotations

import urllib.parse
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.domain import search
from app.domain.person_context import record_person_event
from app.infra.cache_policy import note_read, read_scope, revalidating
from app.local_mcp.security import local_request_error
from app.services.ticket_activity import mark_ticket_viewed, ticket_activity


class RecentBody(BaseModel):
    url: str
    kind: str = "web"
    title: str = ""
    meta: str = ""
    type: str = ""
    data: dict = Field(default_factory=dict)
    openedAt: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class PersonRecentBody(BaseModel):
    userId: str = Field(min_length=1, max_length=512)
    event: Literal["selection", "mention"]
    actorHint: str | None = Field(default=None, max_length=512)  # Older clients may still send it.


TicketKey = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*-[0-9]+$", max_length=128)]


class TicketActivityBody(BaseModel):
    keys: list[TicketKey] = Field(max_length=500)


class TicketViewedBody(BaseModel):
    key: TicketKey
    viewedThrough: int = Field(strict=True, gt=0)


def build_resource_router(*, get_client, cache, settings) -> APIRouter:
    """Build endpoints shared by search, badges, and editor content."""
    router = APIRouter()

    def local_json_error(request):
        error = local_request_error(request.scope, settings.app_port)
        if error or request.headers.get("x-ltm-local") != "1" or (
                request.headers.get("content-type", "").split(";", 1)[0] != "application/json"):
            return JSONResponse({"error": error or "LTM 화면에서 요청해 주세요."}, status_code=403)
        return None

    @router.post("/api/tickets/activity")
    def api_ticket_activity(request: Request, body: TicketActivityBody):
        if (error := local_json_error(request)) is not None:
            return error
        return JSONResponse(ticket_activity(get_client(), settings, cache, body.keys),
                            headers={"Cache-Control": "no-store"})

    @router.post("/api/tickets/viewed")
    def api_ticket_viewed(request: Request, body: TicketViewedBody):
        if (error := local_json_error(request)) is not None:
            return error
        try:
            result = mark_ticket_viewed(get_client(), settings, cache, body.key, body.viewedThrough)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/api/people/recent")
    def api_person_recent(request: Request, body: PersonRecentBody):
        error = local_request_error(request.scope, settings.app_port)
        if error or request.headers.get("x-ltm-local") != "1" or (
                request.headers.get("content-type", "").split(";", 1)[0] != "application/json"):
            return JSONResponse({"error": error or "LTM 화면에서 요청해 주세요."}, status_code=403)
        recorded = record_person_event(get_client(), settings, body.userId, body.event, body.actorHint)
        return JSONResponse({"recorded": recorded}, headers={"Cache-Control": "no-store"})

    @router.get("/api/issue/{key}")
    def api_issue(key: str):
        client = get_client()
        node = client.issue_detail(key)
        if node is None:
            return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
        return JSONResponse(node)

    @router.get("/api/issue/{key}/comments")
    def api_issue_comments(key: str, limit: int = 100, freshAfter: int | None = Query(default=None, gt=0)):
        if freshAfter is not None and freshAfter > int(time.time() * 1000):
            return JSONResponse({"error": "댓글 확인 기준 시각은 현재보다 미래일 수 없습니다."}, status_code=422)
        with read_scope(strict=revalidating()) as sources:
            client = get_client()
            rows = (client.issue_comments(key, limit, fresh_after=freshAfter) if freshAfter is not None
                    else client.issue_comments(key, limit))
        for source in sources:
            note_read(source)  # Preserve the existing aggregate cache headers as well.
        comments = [source["fetchedAt"] for source in sources if source.get("kind") == "comments"]
        headers = {"X-LTM-Comments-Fetched-At": str(min(comments))} if comments else {}
        coverage = client.comment_coverage(key, rows)
        headers["X-LTM-Comments-Complete"] = "1" if coverage["complete"] else "0"
        if coverage["total"] is not None:
            headers["X-LTM-Comments-Total"] = str(coverage["total"])
        return JSONResponse(rows, headers=headers)

    @router.get("/api/issue/{key}/comments/complete")
    def api_issue_comments_complete(key: str, freshAfter: int | None = Query(default=None, gt=0)):
        if freshAfter is not None and freshAfter > int(time.time() * 1000):
            return JSONResponse({"error": "댓글 확인 기준 시각은 현재보다 미래일 수 없습니다."}, status_code=422)
        result = get_client().issue_comments_complete(key, fresh_after=freshAfter)
        source = result.get("fetchedAt")
        headers = {"X-LTM-Comments-Fetched-At": str(source)} if source is not None else {}
        return JSONResponse(result, headers=headers)

    @router.get("/api/recent")
    def api_recent(limit: int = 20, kind: str = ""):
        return JSONResponse(cache.recent_items(max(1, min(limit, 100)), kind or None))

    @router.post("/api/recent")
    def api_recent_add(body: RecentBody):
        cache.touch_recent(body.url, body.kind, body.title, body.meta, body.type, body.data or {}, opened_at=body.openedAt)
        return JSONResponse({"ok": True})

    @router.delete("/api/recent")
    def api_recent_clear(url: str = ""):
        cache.forget_recent(url or None)
        return JSONResponse({"ok": True})

    @router.get("/api/search")
    def api_search(q: str = "", scope: str = "scoped", limit: int = 8, only: str = ""):
        picks = [value for value in (only or "").split(",") if value.strip()] or None
        return JSONResponse(search.search_all(get_client(), settings, q, scope, limit, picks))

    @router.get("/api/linktitle")
    def api_link_title(u: str):
        client = get_client()
        return JSONResponse({"url": u, "title": client.conf_title_by_id(u) or client.link_title(u) or ""})

    @router.get("/api/favicon")
    def api_favicon(u: str):
        data, content_type = get_client().favicon(u)
        if data is None:
            return JSONResponse({"error": "no favicon", "u": u}, status_code=404)
        return Response(
            content=data,
            media_type=content_type or "image/x-icon",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @router.get("/api/mention/users")
    def api_mention_users(q: str = "", key: str = "", limit: int = 8):
        return JSONResponse(search.mention_suggestions(get_client(), settings, q, key, limit))

    @router.get("/api/mention/user/{user_id}")
    def api_mention_user(user_id: str):
        user_id = (user_id or "").strip()
        row = get_client().user_badge(user_id)
        if not row:
            return JSONResponse({"error": "User Does Not Exist", "id": user_id}, status_code=404)
        return JSONResponse(row)

    @router.get("/api/img")
    def api_img(u: str):
        data, content_type = get_client().fetch_media(u)
        if data is None:
            return JSONResponse({"error": "이미지 없음 또는 허용되지 않은 호스트", "u": u}, status_code=404)
        return Response(
            content=data,
            media_type=(content_type or "application/octet-stream").split(";")[0].strip(),
            headers={"Cache-Control": "private, max-age=2592000, immutable"},
        )

    @router.get("/api/file")
    def api_file(u: str, inline: int = 0):
        data, content_type = get_client().fetch_media(u)
        if data is None:
            return JSONResponse({"error": "첨부 없음 또는 허용되지 않은 호스트", "u": u}, status_code=404)
        name = urllib.parse.unquote((u or "").rstrip("/").split("/")[-1]) or "download"
        ascii_name = name.encode("ascii", "ignore").decode().strip()
        if not ascii_name or ascii_name.startswith("."):
            ascii_name = "download" + ascii_name
        disposition = "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
            "inline" if inline else "attachment",
            ascii_name.replace('"', ""),
            urllib.parse.quote(name),
        )
        return Response(
            content=data,
            media_type=(content_type or "application/octet-stream").split(";")[0].strip(),
            headers={"Content-Disposition": disposition, "Cache-Control": "private, max-age=86400"},
        )

    return router
