"""Ticket detail, relationship, timeline, and comment routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.auth.base import (PermissionDenied, SessionExpired, UpstreamUnavailable,
                           background_upstream)


class CommentBody(BaseModel):
    html: str = ""
    clientMutationId: str | None = None


class CheckboxBody(BaseModel):
    target: str = "description"
    commentId: str | None = None
    id: str | None = None
    index: int | None = None
    checked: bool


def build_ticket_router(*, get_client) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ticket/{key}")
    def api_ticket(key: str, fresh: int = 0):
        view = get_client().ticket_view(key, fresh=bool(fresh))
        if view is None:
            return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
        if fresh:
            return JSONResponse(view, headers={"Cache-Control": "no-store"})
        return JSONResponse(view)

    @router.get("/api/ticket/{key}/badge")
    def api_ticket_badge(key: str):
        badge = get_client().ticket_badge(key)
        if badge is None:
            return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
        return JSONResponse(badge)

    @router.get("/api/avatar/{user}")
    def api_avatar(user: str):
        data, content_type = get_client().user_avatar(user)
        if data is None:
            return JSONResponse({"error": "no avatar", "user": user}, status_code=404)
        return Response(
            content=data,
            media_type=(content_type or "image/png").split(";")[0].strip(),
            headers={"Cache-Control": "private, max-age=2592000, immutable"},
        )

    @router.get("/api/ticket/{key}/ancestors")
    def api_ticket_ancestors(key: str):
        return JSONResponse(get_client().ticket_ancestors(key))

    @router.get("/api/ticket/{key}/timeline")
    def api_ticket_timeline(key: str, deferred: bool = False, children: bool = True):
        with background_upstream():
            timeline = get_client().ticket_timeline(key, defer=deferred, include_children=children)
        if deferred and timeline is None:
            return JSONResponse({"pending": True}, status_code=202)
        return JSONResponse(timeline)

    @router.get("/api/ticket/{key}/attachments")
    def api_ticket_attachments(key: str):
        return JSONResponse(get_client().ticket_attachments(key))

    @router.get("/api/ticket/{key}/documents")
    def api_ticket_documents(key: str):
        return JSONResponse(get_client().ticket_documents(key))

    @router.get("/api/ticket/{key}/children")
    def api_ticket_children(key: str):
        return JSONResponse(get_client().ticket_children(key))

    @router.get("/api/ticket/{key}/related")
    def api_ticket_related(key: str):
        return JSONResponse(get_client().ticket_related(key, include_mentions=False))

    @router.get("/api/ticket/{key}/siblings")
    def api_ticket_siblings(key: str):
        return JSONResponse(get_client().ticket_siblings(key))

    @router.post("/api/ticket/{key}/comment")
    def api_comment_create(key: str, body: CommentBody):
        value = get_client().comment_field_value(body.html or "")
        if not (value or "").strip():
            return JSONResponse({"error": "빈 코멘트"}, status_code=400)
        return JSONResponse(
            get_client().add_comment(key, value, mutation_id=body.clientMutationId),
            status_code=201,
        )

    @router.put("/api/ticket/{key}/comment/{cid}")
    def api_comment_update(key: str, cid: str, body: CommentBody):
        value = get_client().comment_field_value(body.html or "")
        if not (value or "").strip():
            return JSONResponse({"error": "빈 코멘트"}, status_code=400)
        return JSONResponse(get_client().update_comment(key, cid, value))

    @router.delete("/api/ticket/{key}/comment/{cid}")
    def api_comment_delete(key: str, cid: str):
        return JSONResponse(get_client().delete_comment(key, cid))

    @router.post("/api/ticket/{key}/refresh")
    def api_ticket_refresh(key: str):
        try:
            return JSONResponse(get_client().invalidate_ticket_all(key))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.post("/api/ticket/{key}/checkbox")
    def api_ticket_checkbox(key: str, body: CheckboxBody):
        client = get_client()
        try:
            if body.target == "comment":
                if not body.commentId:
                    return JSONResponse({"ok": False, "error": "commentId 가 필요합니다."}, status_code=400)
                result = client.toggle_comment_checkbox(
                    key, body.commentId, body.index, bool(body.checked), cbid=body.id,
                )
            else:
                result = client.toggle_description_checkbox(
                    key, body.index, bool(body.checked), cbid=body.id,
                )
            return JSONResponse(result)
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.get("/api/ticket/{key}/comment/{cid}/source")
    def api_comment_source(key: str, cid: str):
        source = get_client().comment_source(key, cid)
        if source is None:
            return JSONResponse({"error": "코멘트 없음", "key": key, "cid": cid}, status_code=404)
        return JSONResponse(source)

    return router
