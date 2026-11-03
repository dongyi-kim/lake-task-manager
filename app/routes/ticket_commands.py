"""Ticket creation and mutation HTTP routes.

The Jira client owns mutation/cache semantics. This module only validates HTTP input,
checks access, and translates UI-shaped payloads into that client contract.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from app.services.ticket_models import (
    AssigneeBody, TransitionBody, FieldsBody, ChildBody, TaskBody, BulkBody, BulkUpdateBody,
    BulkCommentBody, EpicBody, EpicLinkBody, LinkBody, DocumentBody,
)
from app.services.ticket_commands import CommandError, TicketCommands

from app.auth.base import PermissionDenied, SessionExpired, UpstreamUnavailable


def _bulk_check(client, mode: str, items: list[dict]):
    # Creating a child does not edit its parent. Jira enforces CREATE_ISSUES on POST.
    return TicketCommands(lambda: client, None).bulk_validate(mode, items)


def fields_for_update(client, key: str, changes: dict):
    """Translate agent/UI change names to fields allowed by this issue's editmeta."""
    from app.domain.ticket_actions import field_update_error

    if field_update_error(client.ticket_badge(key), (changes or {}).keys()):
        return {}
    meta = client.editmeta(key) or {}
    result = {}

    def put(field_id, value):
        if field_id in meta:
            result[field_id] = value

    for name, value in (changes or {}).items():
        if name == "priority":
            put("priority", {"name": value})
        elif name == "assignee":
            put("assignee", {"name": value} if value else None)
        elif name == "duedate":
            put("duedate", value or None)
        elif name == "labels":
            put("labels", list(value or []))
        elif name == "components":
            put("components", [{"name": item} for item in (value or [])])
        elif name == "summary":
            put("summary", value)
        elif name == "description":
            put("description", client.desc_field_value(value))
    return result


def update_fields_response(client, settings, key: str, body: FieldsBody):
    """HTTP response adapter around the shared application command."""
    try:
        return JSONResponse(TicketCommands(lambda: client, settings).update_fields(key, body))
    except CommandError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)
    except (PermissionDenied, SessionExpired, UpstreamUnavailable):
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)


def build_ticket_command_router(*, get_client, settings, may_edit, require_edit,
                                session_user) -> APIRouter:
    router = APIRouter()
    commands = TicketCommands(get_client, settings, require_edit)

    @router.put("/api/ticket/{key}/assignee")
    def api_set_assignee(key: str, body: AssigneeBody):
        require_edit(key)
        return JSONResponse(get_client().set_assignee(key, body.assignee.strip()))

    @router.delete("/api/ticket/{key}")
    def api_delete_ticket(key: str, clientMutationId: str = ""):
        try:
            return JSONResponse(commands.delete_ticket(key, clientMutationId or None))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)

    @router.get("/api/timetracking")
    def api_timetracking():
        return JSONResponse(get_client().timetracking())

    @router.get("/api/ticket/{key}/editmeta")
    def api_editmeta(key: str):
        from app.domain.ticket_actions import is_done
        client = get_client()
        return JSONResponse({} if is_done(client.ticket_badge(key)) else client.editmeta(key))

    @router.get("/api/options/{kind}")
    def api_options(kind: str, q: str = ""):
        client = get_client()
        if kind == "priorities":
            return JSONResponse(client.priorities())
        if kind == "components":
            return JSONResponse(client.components())
        if kind == "labels":
            return JSONResponse(client.label_suggestions(q))
        if kind == "epics":
            return JSONResponse(client.epic_options(q))
        if kind == "childtypes":
            return JSONResponse(client.child_types(q))
        if kind == "tasktypes":
            return JSONResponse(client.task_types())
        return JSONResponse({"error": "unknown kind"}, status_code=404)

    @router.post("/api/ticket/{key}/child")
    def api_create_child(key: str, body: ChildBody):
        try:
            return JSONResponse(commands.create(body, parent=key))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.post("/api/task")
    def api_create_task(body: TaskBody):
        try:
            return JSONResponse(commands.create(body))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @router.post("/api/bulk/validate")
    def api_bulk_validate(body: BulkBody):
        return JSONResponse(commands.bulk_validate(body.mode, body.items))

    @router.post("/api/bulk/create")
    def api_bulk_create(body: BulkBody):
        result = commands.bulk_create(body)
        return JSONResponse(result, status_code=400 if result.get("errors") else 200)

    @router.post("/api/bulk/update/validate")
    def api_bulk_update_validate(body: BulkUpdateBody):
        from app.domain import bulk
        client = get_client()
        return JSONResponse(bulk.validate_bulk_update(body.items, client.bulk_lookup(may_edit=may_edit)))

    @router.post("/api/bulk/update")
    def api_bulk_update(body: BulkUpdateBody):
        from app.domain import bulk
        client = get_client()
        check = bulk.validate_bulk_update(body.items, client.bulk_lookup(may_edit=may_edit))
        if not check.get("ok"):
            return JSONResponse({"ok": False, "errors": check.get("errors", [])}, status_code=400)
        return JSONResponse(client.bulk_update(
            check.get("items") or body.items,
            lambda key, changes: fields_for_update(client, key, changes),
        ))

    @router.post("/api/bulk/comment")
    def api_bulk_comment(body: BulkCommentBody):
        from app.domain import bulk
        client = get_client()
        check = bulk.validate_bulk_comment(body.items, client.bulk_lookup(may_edit=may_edit))
        if not check.get("ok"):
            return JSONResponse({"ok": False, "errors": check.get("errors", [])}, status_code=400)
        return JSONResponse(client.bulk_comment(body.items, to_body=client.desc_field_value))

    @router.post("/api/epic")
    def api_create_epic(body: EpicBody):
        try:
            return JSONResponse(commands.create_epic(body))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)

    @router.post("/api/epic/{key}/link")
    def api_epic_link(key: str, body: EpicLinkBody):
        try:
            return JSONResponse(get_client().set_epic_link(key, body.taskKeys))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)

    @router.get("/api/parent-task-candidates")
    def api_parent_task_candidates(q: str = "", limit: int = 20, excludeLinked: int = 0):
        try:
            return JSONResponse(get_client().parent_task_candidates(q, limit, exclude_linked=bool(excludeLinked)))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"error": str(exc)[:200], "items": []})

    @router.put("/api/ticket/{key}/fields")
    def api_update_fields(key: str, body: FieldsBody):
        return update_fields_response(get_client(), settings, key, body)

    @router.get("/api/ticket/{key}/menu")
    def api_ticket_menu(key: str):
        client = get_client()
        badge, me, editable = client.ticket_badge(key) or {}, session_user(), may_edit(key)
        return JSONResponse({
            "key": key, "summary": badge.get("summary") or "",
            "assignee": badge.get("assignee"), "assigneeId": badge.get("assigneeId"),
            "status": badge.get("status") or "", "statusCategory": badge.get("statusCategory") or "",
            "type": badge.get("type") or "", "due": badge.get("due") or "",
            "me": {"id": me.get("id") or "", "name": me.get("name") or ""},
            "mayEdit": editable, "jiraBase": (settings.jira_base or "").rstrip("/"),
            "transitions": client.transitions(key) if editable else [],
            "childTypes": client.child_types(key) or [],
        })

    @router.get("/api/ticket/{key}/transitions")
    def api_transitions(key: str):
        return JSONResponse(get_client().transitions(key))

    @router.post("/api/ticket/{key}/transition")
    def api_do_transition(key: str, body: TransitionBody):
        try:
            return JSONResponse(commands.transition(key, body))
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)

    @router.get("/api/linktypes")
    def api_link_types():
        return JSONResponse(get_client().link_types())

    @router.post("/api/ticket/{key}/link")
    def api_link_add(key: str, body: LinkBody):
        try:
            return JSONResponse(commands.link(key, body.key, body.type, body.direction, body.clientMutationId))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.delete("/api/ticket/{key}/link/{link_id}")
    def api_link_delete(key: str, link_id: str, other: str = "", clientMutationId: str = ""):
        try:
            return JSONResponse(commands.delete_link(key, link_id, other or None, clientMutationId or None))
        except CommandError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @router.post("/api/ticket/{key}/document")
    def api_document_add(key: str, body: DocumentBody):
        try:
            commands.document(key, body.url, body.title, body.clientMutationId)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    @router.post("/api/ticket/{key}/attachment")
    async def api_attachment_upload(key: str, file: UploadFile = File(...),
                                    clientMutationId: str = Form("")):
        data = await file.read()
        return JSONResponse(commands.attachment(
            key, file.filename or "paste.png", data, file.content_type, clientMutationId or None))

    @router.delete("/api/ticket/{key}/attachment/{aid}")
    def api_attachment_delete(key: str, aid: str, clientMutationId: str = ""):
        try:
            return JSONResponse(commands.delete_attachment(key, aid, clientMutationId or None))
        except CommandError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @router.delete("/api/ticket/{key}/document/{lid}")
    def api_document_delete(key: str, lid: str, clientMutationId: str = ""):
        return JSONResponse(commands.delete_document(key, lid, clientMutationId or None))

    return router
