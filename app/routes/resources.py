"""Shared issue, search, mention, and media HTTP resources."""

from __future__ import annotations

import urllib.parse

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.domain import search


class RecentBody(BaseModel):
    url: str
    kind: str = "web"
    title: str = ""
    meta: str = ""
    type: str = ""
    data: dict = Field(default_factory=dict)


def build_resource_router(*, get_client, cache, settings) -> APIRouter:
    """Build endpoints shared by search, badges, and editor content."""
    router = APIRouter()

    @router.get("/api/issue/{key}")
    def api_issue(key: str):
        client = get_client()
        node = client.issue_detail(key)
        if node is None:
            return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
        return JSONResponse(node)

    @router.get("/api/issue/{key}/comments")
    def api_issue_comments(key: str, limit: int = 100):
        return JSONResponse(get_client().issue_comments(key, limit))

    @router.get("/api/recent")
    def api_recent(limit: int = 20, kind: str = ""):
        return JSONResponse(cache.recent_items(max(1, min(limit, 100)), kind or None))

    @router.post("/api/recent")
    def api_recent_add(body: RecentBody):
        cache.touch_recent(body.url, body.kind, body.title, body.meta, body.type, body.data or {})
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
