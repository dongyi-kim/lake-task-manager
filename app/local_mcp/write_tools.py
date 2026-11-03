"""Explicit direct-write catalog; built-in Agent HITL remains a separate caller boundary."""

import base64
import binascii

from pydantic import Field

from app.services.ticket_models import ChildBody, EpicBody, FieldsBody, TransitionBody


class CreateInput(ChildBody):
    clientMutationId: str = Field(min_length=8, max_length=128)


class EpicInput(EpicBody):
    clientMutationId: str = Field(min_length=8, max_length=128)


class FieldsInput(FieldsBody):
    clientMutationId: str = Field(min_length=8, max_length=128)


class TransitionInput(TransitionBody):
    clientMutationId: str = Field(min_length=8, max_length=128)


def register_writes(register, runtime):
    commands = runtime.commands

    def create_task(body: CreateInput, epic_key: str = "") -> dict:
        """Create a Task-tier ticket (discover type via list_options), optionally under an Epic.
        Reporter is always Jira's current user. Reuse clientMutationId only for retrying this exact request.
        """
        if epic_key:
            runtime.check_key(epic_key)
        return commands.create(body.model_copy(update={"parentIsEpic": bool(epic_key)}), epic_key or None)

    def create_subtask(parent_key: str, body: CreateInput) -> dict:
        """Create a SubTask under a Task-tier parent; use an actual child_types option."""
        runtime.check_key(parent_key)
        return commands.create(body.model_copy(update={"parentIsEpic": False}), parent_key)

    def create_epic(body: EpicInput) -> dict:
        """Create an Epic using Jira's validated Epic Name field and default current-user Reporter."""
        for key in body.taskKeys or []:
            runtime.check_key(key)
        return commands.create_epic(body)

    def update_fields(key: str, body: FieldsInput) -> dict:
        """Edit allowed fields using current editmeta. Done tickets must be reopened first.
        Reporter requires actual Jira MODIFY_REPORTER permission. Null means unchanged; empty clears.
        """
        runtime.check_key(key)
        return commands.update_fields(key, body)

    def update_description(key: str, html: str, client_mutation_id: str) -> dict:
        """Replace a ticket's description/body HTML using the same editor serializer as the UI.
        get_ticket returns descriptionEditHtml for editing. Create bodies use body.descriptionHtml;
        comment bodies use add_comment/edit_comment. Done tickets must be reopened before body editing.
        """
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.update_fields(key, FieldsInput(descriptionHtml=html, clientMutationId=client_mutation_id))

    def add_comment(key: str, html: str, client_mutation_id: str) -> dict:
        """Publish comment HTML through LTM's Jira serializer. Keep mutation ID when retrying."""
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.comment(key, html, mutation_id=client_mutation_id)

    def edit_comment(key: str, comment_id: str, html: str, client_mutation_id: str) -> dict:
        """Edit an existing comment subject to Jira permissions; HTML uses the UI serializer."""
        runtime.check_key(key)
        runtime.check_object_id(comment_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.comment(key, html, comment_id=comment_id, mutation_id=client_mutation_id)

    def delete_comment(key: str, comment_id: str, client_mutation_id: str) -> dict:
        """Delete one comment, retaining a receipt to prevent unsafe repeated writes."""
        runtime.check_key(key)
        runtime.check_object_id(comment_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.comment(key, comment_id=comment_id, delete=True, mutation_id=client_mutation_id)

    def transition_ticket(key: str, body: TransitionInput) -> dict:
        """Run an actual Jira transition ID and its required fields. Consult list_options first.
        Unknown outcomes must be retried with the same clientMutationId, never a replacement ID.
        """
        runtime.check_key(key)
        return commands.transition(key, body)

    def add_issue_link(key: str, other_key: str, client_mutation_id: str,
                       type_name: str = "Relates", direction: str = "outward") -> dict:
        """Link two tickets using an actual Jira link type (list_options/link_types)."""
        runtime.check_key(key)
        runtime.check_key(other_key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.link(key, other_key, type_name, direction, client_mutation_id)

    def delete_issue_link(key: str, other_key: str, link_id: str, client_mutation_id: str) -> dict:
        """Remove an issue link by ID and invalidate both endpoint ticket caches."""
        runtime.check_key(key)
        runtime.check_object_id(link_id)
        runtime.check_key(other_key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_link(key, link_id, other_key, client_mutation_id)

    def add_document(key: str, url: str, client_mutation_id: str, title: str = "") -> dict:
        """Add/update a remote HTTP(S) document link. No URL fetch or local file read is performed."""
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.document(key, url, title, client_mutation_id)

    def delete_document(key: str, link_id: str, client_mutation_id: str) -> dict:
        """Delete a remote document link subject to the same LTM edit permission as the UI."""
        runtime.check_key(key)
        runtime.check_object_id(link_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_document(key, link_id, client_mutation_id)

    def add_attachment(key: str, filename: str, base64_data: str, client_mutation_id: str,
                       content_type: str = "application/octet-stream") -> dict:
        """Upload encoded bytes (maximum 10MiB), never a filesystem path. Reuse mutation ID on retry.
        The returned attachment path can be used in editor HTML before publishing.
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

    def delete_attachment(key: str, attachment_id: str, client_mutation_id: str) -> dict:
        """Delete an attachment by ID subject to the UI's LTM edit permission."""
        runtime.check_key(key)
        runtime.check_object_id(attachment_id)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_attachment(key, attachment_id, client_mutation_id)

    def delete_ticket(key: str, client_mutation_id: str) -> dict:
        """Permanently delete a ticket under the same Jira/LTM permissions as the UI."""
        runtime.check_key(key)
        runtime.check_mutation_id(client_mutation_id)
        return commands.delete_ticket(key, client_mutation_id)

    for fn in (create_task, create_subtask, create_epic, update_fields, update_description, add_comment, edit_comment,
               delete_comment, transition_ticket, add_issue_link, delete_issue_link, add_document,
               delete_document, add_attachment, delete_attachment, delete_ticket):
        register(fn, read_only=False)
