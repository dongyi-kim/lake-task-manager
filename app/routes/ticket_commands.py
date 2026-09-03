"""Ticket creation and mutation HTTP routes.

The Jira client owns mutation/cache semantics. This module only validates HTTP input,
checks access, and translates UI-shaped payloads into that client contract.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.base import PermissionDenied, SessionExpired, UpstreamUnavailable


class AssigneeBody(BaseModel):
    assignee: str = ""


class TransitionBody(BaseModel):
    id: str = ""
    targetStatusId: str = ""
    targetStatusName: str = ""
    targetStatusCategory: str = ""
    days: int = 0
    hours: int = 0
    minutes: int = 0
    assignee: str = ""
    resolution: str = ""
    commentHtml: str = ""
    clientMutationId: str | None = None


class FieldsBody(BaseModel):
    priority: str | None = None
    assignee: str | None = None
    reporter: str | None = None
    duedate: str | None = None
    labels: list[str] | None = None
    components: list[str] | None = None
    summary: str | None = None
    descriptionHtml: str | None = None
    epic: str | None = None


class ChildBody(BaseModel):
    type: str
    summary: str
    priority: str | None = None
    duedate: str | None = None
    assignee: str | None = None
    components: list[str] | None = None
    labels: list[str] | None = None
    descriptionHtml: str | None = None
    clientMutationId: str | None = None
    parentIsEpic: bool | None = None


class TaskBody(ChildBody):
    pass


class BulkBody(BaseModel):
    mode: str
    items: list[dict]


class BulkUpdateBody(BaseModel):
    items: list[dict]


class BulkCommentBody(BaseModel):
    items: list[dict]


class EpicBody(BaseModel):
    summary: str
    epicName: str | None = None
    priority: str | None = None
    duedate: str | None = None
    assignee: str | None = None
    components: list[str] | None = None
    descriptionHtml: str | None = None
    taskKeys: list[str] | None = None
    clientMutationId: str | None = None


class EpicLinkBody(BaseModel):
    taskKeys: list[str]


class LinkBody(BaseModel):
    key: str
    type: str = "Relates"
    direction: str = "outward"


class DocumentBody(BaseModel):
    url: str
    title: str = ""


def _bulk_check(client, may_edit: Callable[[str], bool], mode: str, items: list[dict]):
    from app.domain import bulk
    return bulk.validate_bulk(mode, items, client.bulk_lookup(may_edit=may_edit))


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
    """Shared implementation kept callable by legacy direct unit tests."""
    from app.domain.ticket_actions import field_update_error, is_done

    requested = [name for name, value in {
        "priority": body.priority, "assignee": body.assignee, "reporter": body.reporter,
        "duedate": body.duedate, "labels": body.labels, "components": body.components,
        "summary": body.summary, "description": body.descriptionHtml, "epic": body.epic,
    }.items() if value is not None]
    current = client.ticket_badge(key)
    state_error = field_update_error(current, [field for field in requested if field not in ("reporter", "epic")])
    if is_done(current):
        return JSONResponse({"ok": False, "error": state_error}, status_code=409)

    meta = client.editmeta(key)
    fields, denied = {}, []

    def put(field_id, value):
        if field_id not in meta:
            denied.append(field_id)
        else:
            fields[field_id] = value

    if body.priority is not None:
        put("priority", {"name": body.priority})
    if body.assignee is not None:
        put("assignee", {"name": body.assignee} if body.assignee else None)
    if body.reporter is not None:
        put("reporter", {"name": body.reporter} if body.reporter else None)
    if body.duedate is not None:
        put("duedate", body.duedate or None)
    if body.labels is not None:
        put("labels", list(body.labels))
    if body.components is not None:
        put("components", [{"name": item} for item in body.components])
    if body.summary is not None:
        put("summary", body.summary)
    if body.descriptionHtml is not None:
        put("description", client.desc_field_value(body.descriptionHtml))
    if body.epic is not None:
        put(settings.epic_link_field_id, body.epic or None)

    if denied:
        return JSONResponse(
            {"ok": False, "error": "편집 권한이 없는 항목입니다: " + ", ".join(denied)},
            status_code=403,
        )
    if not fields:
        return JSONResponse({"ok": True, "note": "변경 없음"})
    try:
        return JSONResponse(client.update_fields(key, fields))
    except (PermissionDenied, SessionExpired, UpstreamUnavailable):
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)


def build_ticket_command_router(*, get_client, settings, may_edit, require_edit,
                                session_user) -> APIRouter:
    router = APIRouter()

    @router.put("/api/ticket/{key}/assignee")
    def api_set_assignee(key: str, body: AssigneeBody):
        require_edit(key)
        return JSONResponse(get_client().set_assignee(key, body.assignee.strip()))

    @router.delete("/api/ticket/{key}")
    def api_delete_ticket(key: str):
        require_edit(key)
        try:
            return JSONResponse(get_client().delete_issue(key))
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
        client = get_client()
        issue_type, summary = (body.type or "").strip(), (body.summary or "").strip()
        if not summary:
            return JSONResponse({"ok": False, "error": "제목을 입력하세요."}, status_code=400)
        def validate_create():
            require_edit(key)
            if issue_type not in client.child_types(key):
                raise ValueError(f"이 티켓 밑에는 {issue_type} 을(를) 만들 수 없습니다.")
        try:
            result = client.create_child(
                key, issue_type, summary, priority=body.priority or None,
                duedate=body.duedate or None, assignee=body.assignee or None,
                components=body.components or None,
                description=client.desc_field_value(body.descriptionHtml) or None,
                labels=body.labels or None,
                mutation_id=body.clientMutationId,
                parent_is_epic=body.parentIsEpic,
                before_write=validate_create,
            )
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        new_key = (result or {}).get("key")
        try:
            cascade = client.cascade_suggestion(new_key, created=True) if new_key else None
        except Exception:
            cascade = None
        return JSONResponse({"ok": True, "key": new_key, "cascade": cascade})

    @router.post("/api/task")
    def api_create_task(body: TaskBody):
        client = get_client()
        issue_type, summary = (body.type or "").strip(), (body.summary or "").strip()
        if not summary:
            return JSONResponse({"ok": False, "error": "제목을 입력하세요."}, status_code=400)
        def validate_create():
            if issue_type not in client.task_types():
                raise ValueError(f"{issue_type} 타입은 만들 수 없습니다.")
        try:
            result = client.create_child(
                None, issue_type, summary, priority=body.priority or None,
                duedate=body.duedate or None, assignee=body.assignee or None,
                components=body.components or None,
                description=client.desc_field_value(body.descriptionHtml) or None,
                labels=body.labels or None,
                mutation_id=body.clientMutationId,
                before_write=validate_create,
            )
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "key": (result or {}).get("key")})

    @router.post("/api/bulk/validate")
    def api_bulk_validate(body: BulkBody):
        return JSONResponse(_bulk_check(get_client(), may_edit, body.mode, body.items))

    @router.post("/api/bulk/create")
    def api_bulk_create(body: BulkBody):
        from app.content.mdhtml import markdown_to_html
        client = get_client()
        check = _bulk_check(client, may_edit, body.mode, body.items)
        if not check.get("ok"):
            return JSONResponse({"ok": False, "errors": check.get("errors", []), "warnings": check.get("warnings", [])}, status_code=400)
        result = client.bulk_create(
            body.mode, check.get("items") or body.items,
            desc_to_field=lambda markdown: client.desc_field_value(markdown_to_html(markdown)),
        )
        result["warnings"] = check.get("warnings", [])
        return JSONResponse(result)

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
        client = get_client()
        summary = (body.summary or "").strip()
        if not summary:
            return JSONResponse({"ok": False, "error": "제목을 입력하세요."}, status_code=400)
        try:
            result = client.create_epic(
                summary, priority=body.priority or None, duedate=body.duedate or None,
                assignee=body.assignee or None, components=body.components or None,
                description=client.desc_field_value(body.descriptionHtml) or None,
                epic_name=(body.epicName or "").strip() or None,
                mutation_id=body.clientMutationId,
            )
            key = (result or {}).get("key")
            linked = client.set_epic_link(key, body.taskKeys) if key and body.taskKeys else None
            return JSONResponse({"ok": True, "key": key, "link": linked})
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
            "childTypes": (client.child_types(key) or []) if editable else [],
        })

    @router.get("/api/ticket/{key}/transitions")
    def api_transitions(key: str):
        return JSONResponse(get_client().transitions(key))

    @router.post("/api/ticket/{key}/transition")
    def api_do_transition(key: str, body: TransitionBody):
        client = get_client()
        duration = " ".join(
            value for value in (
                f"{int(body.days)}d" if body.days else "",
                f"{int(body.hours)}h" if body.hours else "",
                f"{int(body.minutes)}m" if body.minutes else "",
            ) if value
        )
        comment = client.comment_field_value(body.commentHtml) if body.commentHtml else ""
        try:
            client.do_transition(
                key, body.id, time_spent=duration or None, assignee=body.assignee or None,
                resolution=body.resolution or None, comment=comment or None,
                target_status_id=body.targetStatusId or None,
                target_status_name=body.targetStatusName or None,
                target_status_category=body.targetStatusCategory or None,
                mutation_id=body.clientMutationId or None,
            )
        except (PermissionDenied, SessionExpired, UpstreamUnavailable):
            raise
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)
        try:
            cascade = client.cascade_suggestion(key)
        except Exception:
            cascade = None
        return JSONResponse({"ok": True, "cascade": cascade})

    @router.get("/api/linktypes")
    def api_link_types():
        return JSONResponse(get_client().link_types())

    @router.post("/api/ticket/{key}/link")
    def api_link_add(key: str, body: LinkBody):
        try:
            return JSONResponse(get_client().add_issue_link(key, body.key, body.type, body.direction))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.delete("/api/ticket/{key}/link/{link_id}")
    def api_link_delete(key: str, link_id: str, other: str = ""):
        return JSONResponse(get_client().delete_issue_link(link_id, key, other or None))

    @router.post("/api/ticket/{key}/document")
    def api_document_add(key: str, body: DocumentBody):
        try:
            get_client().add_remote_link(key, body.url, body.title)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    @router.post("/api/ticket/{key}/attachment")
    async def api_attachment_upload(key: str, file: UploadFile = File(...),
                                    clientMutationId: str = Form("")):
        client = get_client()
        data = await file.read()
        try:
            result = client.upload_attachment(
                key, file.filename or "paste.png", data, file.content_type,
                mutation_id=clientMutationId or None,
            )
        except SessionExpired:
            # Do not consult current_user(): it is cached for hours and can claim a dead idle
            # session is alive. The global handler performs a direct session_alive probe and
            # emits the need-login contract when appropriate.
            raise
        attachment = result[0] if isinstance(result, list) and result else (result or {})
        attachment_id = str(attachment.get("id") or "")
        filename = attachment.get("filename") or (file.filename or "")
        path = f"/secure/attachment/{attachment_id}/{filename}" if attachment_id else filename
        return JSONResponse({"id": attachment_id, "filename": filename, "path": path})

    @router.delete("/api/ticket/{key}/attachment/{aid}")
    def api_attachment_delete(key: str, aid: str):
        require_edit(key)
        return JSONResponse(get_client().delete_attachment(aid, key=key))

    @router.delete("/api/ticket/{key}/document/{lid}")
    def api_document_delete(key: str, lid: str):
        require_edit(key)
        return JSONResponse(get_client().delete_remote_link(key, lid))

    return router
