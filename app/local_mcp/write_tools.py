"""Explicit direct-write catalog; built-in Agent HITL remains a separate caller boundary."""

import base64
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.ticket_models import BulkBody, ChildBody, EpicBody, TransitionBody

HTML_DESCRIPTION = (
    "Editor HTML, not Markdown, Jira wiki or TipTap JSON. Read get_content_guide for supported "
    "elements and mention/link markup. Use verified user IDs and canonical URLs; upload images "
    "before embedding the returned attachment path."
)

MutationId = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
    description="Unique request ID (8..128 ASCII letters/digits/._:-). Reuse only for the same exact request retry.")]

_BULK_PROPERTIES = {
    "summary": {"type": "string", "description": "Non-empty ticket title; required when creating."},
    "type": {"type": "string", "description": "Actual issue type name from list_options; required when creating."},
    "epic": {"type": ["string", "null"], "description": "mode=task requires this key: existing Epic key or explicit null."},
    "parent": {"type": "string", "description": "mode=subtask requires an existing Task-tier parent key; never null."},
    "priority": {"type": ["string", "null"], "description": "Actual priority name from list_options(kind=priorities)."},
    "duedate": {"type": ["string", "null"], "description": "YYYY-MM-DD; empty or null means no due date."},
    "assignee": {"type": ["string", "null"], "description": "Verified Jira user ID, not display name; resolve with search_users."},
    "components": {"type": "array", "items": {"type": "string"}, "description": "Jira Component names from list_options(kind=components)."},
    "labels": {"type": "array", "items": {"type": "string"}},
    "description": {"type": ["string", "null"], "description": "Markdown, not editor HTML; images need follow-up attachment/body writes."},
}
# Draft validation must still accept missing/invalid values and report indexed errors. The
# examples below document known shapes without replacing the shared bulk validator's result.
BulkDraftItems = Annotated[list[dict], Field(json_schema_extra={"items": {
    "type": "object", "properties": {key: {"description": value.get("description", "Jira labels as a string array.")}
                                     for key, value in _BULK_PROPERTIES.items()}, "additionalProperties": True}},
    description="Draft rows, including incomplete/invalid values; returns indexed errors and warnings without writing.")]
BulkCreateItems = Annotated[list[dict], Field(json_schema_extra={"minItems": 1, "maxItems": 100,
    "items": {"type": "object", "properties": _BULK_PROPERTIES, "required": ["summary", "type"],
              "anyOf": [{"required": ["epic"]}, {"required": ["parent"]}], "additionalProperties": True}},
    description="Validated ordered rows: mode=task requires epic (key or null); mode=subtask requires parent. Run bulk_validate first.")]


class CreateInput(ChildBody):
    model_config = ConfigDict(extra="forbid")
    clientMutationId: MutationId
    type: str = Field(min_length=1, description="Actual issue type name from list_options(task_types/child_types), not a type ID.")
    summary: str = Field(min_length=1, description="Non-empty ticket title.")
    assignee: str | None = Field(default=None, description="Verified Jira user ID from search_users; not a display name.")
    duedate: str | None = Field(default=None, description="YYYY-MM-DD; omit, null or empty for no due date.")
    parentIsEpic: bool | None = Field(default=None, description="Managed by the tool; omit and use epic_key or parent_key instead.")
    descriptionHtml: str | None = Field(default=None, description=HTML_DESCRIPTION)


class EpicInput(EpicBody):
    model_config = ConfigDict(extra="forbid")
    clientMutationId: MutationId
    summary: str = Field(min_length=1, description="Non-empty Epic title.")
    epicName: str | None = Field(default=None, description="Epic short name; defaults to summary when omitted or empty.")
    assignee: str | None = Field(default=None, description="Verified Jira user ID from search_users; not a display name.")
    duedate: str | None = Field(default=None, description="YYYY-MM-DD; omit, null or empty for no due date.")
    descriptionHtml: str | None = Field(default=None, description=HTML_DESCRIPTION)


class FieldChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: str | None = Field(default=None, description="Actual priority name; null preserves.")
    labels: list[str] | None = Field(default=None, description="Replaces all labels; [] clears, null preserves.")
    components: list[str] | None = Field(default=None, description="Replaces all component names; [] clears, null preserves.")
    summary: str | None = Field(default=None, min_length=1, description="New non-empty ticket title (rename). Omit or null to preserve.")
    assignee: str | None = Field(default=None, description="Verified Jira user ID; empty unassigns, null preserves.")
    reporter: str | None = Field(default=None, description="Verified Jira user ID; requires Jira MODIFY_REPORTER permission.")
    duedate: str | None = Field(default=None, description="YYYY-MM-DD; empty clears, null preserves.")
    epic: str | None = Field(default=None, description="Existing Epic issue key; empty removes membership, null preserves.")
    descriptionHtml: str | None = Field(default=None, description=(
        HTML_DESCRIPTION + " Replaces the entire body; null leaves unchanged and empty clears it."))


class FieldsInput(FieldChanges):
    clientMutationId: MutationId


class TransitionInput(TransitionBody):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, description="Actual transition id from list_options(kind=transitions, key=key).")
    days: int = Field(default=0, ge=0, description=(
        "Worklog days. If worklog is required, at least one of days/hours/minutes must be positive. "
        "Use the user's actual duration; one day uses Jira's configured working day, not 24 hours."))
    hours: int = Field(default=0, ge=0, description="Worklog hours; combine with days/minutes. Required worklog cannot be all zero.")
    minutes: int = Field(default=0, ge=0, description="Worklog minutes; combine with days/hours. Do not supply timeSpent or worklog instead.")
    assignee: str = Field(default="", description="Verified Jira user ID when the transition screen requires/supports assignee.")
    resolution: str = Field(default="", description="Resolution allowedValues.name (not its id), if present on the transition screen.")
    clientMutationId: MutationId
    commentHtml: str = Field(default="", description=HTML_DESCRIPTION + (
        " Required and non-empty for Done transitions or commentRequired=true; otherwise empty adds no comment."))


def register_writes(register, runtime):
    commands = runtime.commands

    def check_bulk_scope(mode, items):
        for item in items:
            parent = item.get("parent") if mode == "subtask" else item.get("epic")
            if isinstance(parent, str) and parent:
                runtime.check_key(parent)

    def create_task(body: CreateInput, epic_key: str = "") -> dict:
        """Create a Task-tier ticket (discover type via list_options), optionally under an Epic.
        Reporter is always Jira's current user. Reuse clientMutationId only for retrying this exact request.
        epic_key selects an existing Epic; empty creates independent work. body.descriptionHtml is HTML.
        Read get_content_guide(skill=ltm-task-authoring) for rich content, people and links.
        New-ticket images require create -> add_attachment -> update_description using the new key.
        Returns the created key and possible cascade suggestion; a suggestion is not an executed transition.
        """
        if epic_key:
            runtime.check_key(epic_key)
        return commands.create(body.model_copy(update={"parentIsEpic": bool(epic_key)}), epic_key or None)

    def create_subtask(parent_key: str, body: CreateInput) -> dict:
        """Create a SubTask under an existing Task-tier parent_key, not directly under an Epic.
        Discover type via list_options(kind=child_types,key=parent_key); pass HTML in body.descriptionHtml.
        Use get_content_guide(skill=ltm-task-authoring) for mentions, links and rich-element examples.
        Returns created key/cascade suggestion. Keep body.clientMutationId and content identical on retry.
        """
        runtime.check_key(parent_key)
        return commands.create(body.model_copy(update={"parentIsEpic": False}), parent_key)

    def create_epic(body: EpicInput) -> dict:
        """Create an Epic with validated Epic Name and Jira's current user as Reporter.
        body.taskKeys optionally links existing Task-tier tickets after creation. Returns key and link result;
        creation and subsequent linking are distinct outcomes. Keep body.clientMutationId on retry.
        body.descriptionHtml is editor HTML; get_content_guide(skill=ltm-task-authoring) explains authoring.
        """
        for key in body.taskKeys or []:
            runtime.check_key(key)
        return commands.create_epic(body)

    def update_fields(key: str, body: FieldsInput) -> dict:
        """Edit allowed fields using current editmeta. Done tickets must be reopened first.
        Reporter requires actual Jira MODIFY_REPORTER permission. Null means unchanged; empty clears.
        labels and components accept string arrays; [] clears all labels/components respectively.
        Those arrays replace the whole field, not append: read current data before preserving other values.
        descriptionHtml replaces the full editor HTML; read descriptionEditHtml and the Task authoring guide.
        """
        runtime.check_key(key)
        return commands.update_fields(key, body)

    def bulk_validate(mode: Literal["task", "subtask"], items: BulkDraftItems) -> dict:
        """Validate up to 100 ordered items with the exact UI bulk rules and live Jira options.
        Each item needs summary/type. task needs epic (key or explicit null); subtask needs an
        existing parent Task key. Optional: priority, duedate, assignee, components, labels,
        description (Markdown). Returns normalized items, indexed errors and warnings; performs no writes.
        Read get_content_guide(skill=ltm-bulk-task-create): description is not editor HTML,
        and plain @name is not a verified mention. Advanced HTML/images need follow-up per-ticket writes.
        """
        check_bulk_scope(mode, items)
        return commands.bulk_validate(mode, items)

    def bulk_create(mode: Literal["task", "subtask"], items: BulkCreateItems, client_mutation_id: MutationId) -> dict:
        """Create up to 100 tasks/subtasks through LTM's existing bulk validation/execution path.
        Inspect bulk_validate first. task items must include epic:null or an existing Epic key;
        subtask items must reference existing parent keys, so create parents in a prior batch.
        Items execute in order. created/failed retain original indices; Jira has no rollback.
        Retry the SAME complete ordered items and SAME mutation ID: confirmed items replay,
        failed items resume, unknown outcomes reconcile without blind duplicate POSTs.
        Never retry only failed rows under the same batch ID or replace an unknown batch ID.
        description is Markdown; the bulk authoring guide covers supported elements and follow-up edits.
        """
        runtime.check_mutation_id(client_mutation_id)
        check_bulk_scope(mode, items)
        return commands.bulk_create(BulkBody(mode=mode, items=items, clientMutationId=client_mutation_id))

    def update_description(key: str, html: Annotated[str, Field(description=HTML_DESCRIPTION)],
                           client_mutation_id: MutationId) -> dict:
        """Replace a ticket's description/body HTML using the same editor serializer as the UI.
        get_ticket returns descriptionEditHtml for editing. Create bodies use body.descriptionHtml;
        comment bodies use add_comment/edit_comment. Done tickets must be reopened before body editing.
        This replaces the complete body; preserve unrelated existing content and reuse the mutation ID on retry.
        Rich markup examples: get_content_guide(skill=ltm-task-authoring).
        """
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.update_fields(key, FieldsInput(descriptionHtml=html, clientMutationId=client_mutation_id))

    def add_comment(key: str, html: Annotated[str, Field(description=HTML_DESCRIPTION)],
                    client_mutation_id: MutationId) -> dict:
        """Publish a new HTML comment on key through the UI's Jira serializer, including Done tickets.
        This does not change status or description. Keep client_mutation_id/content identical on retry;
        verify the published discussion with get_comments rather than treating a timeout as failure proof.
        Read get_content_guide(skill=ltm-comment-authoring) for mentions, links, tables and uploaded images.
        """
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.comment(key, html, mutation_id=client_mutation_id)

    def edit_comment(key: str, comment_id: str, html: Annotated[str, Field(description=HTML_DESCRIPTION)],
                     client_mutation_id: MutationId) -> dict:
        """Replace one existing comment's full HTML, subject to Jira permissions.
        Obtain comment_id from get_comments/get_evidence_page, then load get_comment_source.html.
        Preserve unrelated content from this editor source, not the rendered preview; no new comment is created.
        Reuse the same client_mutation_id and content if authentication recovery requires retrying.
        Read get_content_guide(skill=ltm-comment-authoring) before changing rich elements or images.
        """
        runtime.check_key(key)
        runtime.check_object_id(comment_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.comment(key, html, comment_id=comment_id, mutation_id=client_mutation_id)

    def delete_comment(key: str, comment_id: str, client_mutation_id: MutationId) -> dict:
        """Delete one comment identified by key/comment_id from get_comments, subject to Jira permission.
        Reuse client_mutation_id for the same deletion; unknown outcomes do not justify a replacement ID.
        """
        runtime.check_key(key)
        runtime.check_object_id(comment_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.comment(key, comment_id=comment_id, delete=True, mutation_id=client_mutation_id)

    def transition_ticket(key: str, body: TransitionInput) -> dict:
        """Run an actual Jira transition ID from list_options(kind=transitions,key=key), not a status ID/name.
        Use fields.fields required values: assignee is a verified user ID and resolution an allowedValues name.
        Required worklog uses body.days/hours/minutes, with at least one positive value; do not invent
        a duration or send timeSpent/worklog. Discover fields[].inputFields for the exact argument mapping.
        A Done transition always requires non-empty body.commentHtml (commentRequired=true), even when
        Jira omits the comment screen field. Do not send assignee/resolution/worklog absent from the screen.
        Unknown outcomes must be retried with the same clientMutationId, never a replacement ID.
        Returns ok and a possible cascade suggestion. It does not automatically transition related tickets.
        body.commentHtml is HTML, with the same element rules as the comment authoring guide.
        """
        runtime.check_key(key)
        return commands.transition(key, body)

    def add_issue_link(key: str, other_key: str, client_mutation_id: MutationId,
                       type_name: str = "Relates", direction: Literal["outward", "inward"] = "outward") -> dict:
        """Create a formal Jira issue link between key and other_key within configured projects.
        Obtain type_name from list_options(kind=link_types); direction=outward means key points to other_key,
        inward reverses it. Use get_ticket_context to inspect links; this does not set parent/Epic membership.
        """
        runtime.check_key(key)
        runtime.check_key(other_key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.link(key, other_key, type_name, direction, client_mutation_id)

    def delete_issue_link(key: str, other_key: str, link_id: str, client_mutation_id: MutationId) -> dict:
        """Remove a formal issue link by its numeric link_id and both endpoint keys.
        Both ticket caches are invalidated; the tickets themselves and parent/Epic membership remain intact.
        """
        runtime.check_key(key)
        runtime.check_object_id(link_id)
        runtime.check_key(other_key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_link(key, link_id, other_key, client_mutation_id)

    def add_document(key: str, url: str, client_mutation_id: MutationId, title: str = "") -> dict:
        """Register/update an HTTP(S) document or external URL on key, with optional display title.
        This stores a Jira remote link, not a document body or Confluence edit. No URL content is fetched.
        Inspect stored links/IDs with get_documents; use add_issue_link for formal ticket relationships.
        """
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.document(key, url, title, client_mutation_id)

    def delete_document(key: str, link_id: str, client_mutation_id: MutationId) -> dict:
        """Delete one Jira remote link using link_id from get_documents and the UI's edit permission.
        The linked document/website itself is not deleted. Reuse the same mutation ID on retry.
        """
        runtime.check_key(key)
        runtime.check_object_id(link_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_document(key, link_id, client_mutation_id)

    def add_attachment(key: str, filename: str, base64_data: str, client_mutation_id: MutationId,
                       content_type: str = "application/octet-stream") -> dict:
        """Upload encoded bytes (maximum 10MiB), never a filesystem path. Reuse mutation ID on retry.
        Supply filename, base64_data and optional content_type. Returns attachment id/filename/path;
        its path can be embedded in editor HTML. Upload alone does not publish a comment or alter the body.
        Verify metadata with get_attachments and bytes with read_attachment after upload.
        Use the returned path as img src for an inline image; see the matching authoring guide.
        """
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        if len(base64_data) > 14 * 1024 * 1024:
            raise ValueError("첨부는 10MiB까지 지원합니다.")
        try:
            data = base64.b64decode(base64_data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("base64_data가 올바르지 않습니다.") from None
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("첨부는 10MiB까지 지원합니다.")
        return commands.attachment(key, filename, data, content_type, client_mutation_id)

    def delete_attachment(key: str, attachment_id: str, client_mutation_id: MutationId) -> dict:
        """Delete attachment_id from key, subject to the UI's edit permission; obtain ID from get_attachments.
        Existing body/comment references may then point to a missing file. Reuse the mutation ID on retry.
        """
        runtime.check_key(key)
        runtime.check_object_id(attachment_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_attachment(key, attachment_id, client_mutation_id)

    def delete_ticket(key: str, client_mutation_id: MutationId) -> dict:
        """Permanently delete key under the same Jira/LTM permissions as the UI.
        This is not a status transition or archive action. Preserve client_mutation_id for retries;
        an outcome_unknown response does not authorize repeating deletion with a new ID.
        """
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_ticket(key, client_mutation_id)

    register(bulk_validate, read_only=True)
    for fn in (create_task, create_subtask, create_epic, bulk_create, update_fields, update_description, add_comment, edit_comment,
               delete_comment, transition_ticket, add_issue_link, delete_issue_link, add_document,
               delete_document, add_attachment, delete_attachment, delete_ticket):
        register(fn, read_only=False)
    from app.local_mcp.bulk_update_tools import register_bulk_updates
    register_bulk_updates(register, runtime)
