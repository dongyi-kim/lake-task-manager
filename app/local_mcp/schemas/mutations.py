"""Actual command results, including partial batches and authentication progress."""

from typing import Literal

from .common import JsonValue, OpenModel, ToolError


class OkResult(OpenModel):
    ok: Literal[True]
    note: str | None = None


class SuggestedTransition(OpenModel):
    id: str
    name: str
    to: str
    toId: str
    toCategory: str
    needsScreen: bool
    fields: dict[str, JsonValue] | None


class CascadeSuggestion(OpenModel):
    rule: Literal["done", "inprogress", "reopen"]
    parentKey: str
    parentSummary: str
    parentStatus: str
    parentType: str
    parentAssignee: str | None
    transition: SuggestedTransition


class CreatedTicket(OpenModel):
    ok: Literal[True]
    key: str | None
    cascade: CascadeSuggestion | None = None


class LinkFailure(OpenModel):
    key: str
    error: str


class EpicLinkResult(OpenModel):
    ok: bool
    linked: list[str]
    failed: list[LinkFailure]


class CreatedEpic(OpenModel):
    ok: Literal[True]
    key: str | None
    link: EpicLinkResult | None


class ValidationMessage(OpenModel):
    index: int | None
    field: str | None
    message: str


class BulkValidation(OpenModel):
    ok: bool
    errors: list[ValidationMessage]
    warnings: list[ValidationMessage]
    # Rejected field values are echoed too; do not claim they satisfy creation inputs.
    items: list[dict[str, JsonValue]] | None = None


class BulkCreatedItem(OpenModel):
    index: int
    key: str | None
    summary: str


class BulkFailedItem(OpenModel):
    index: int
    summary: str
    error: str
    code: str | None = None
    clientMutationId: str | None = None
    retryable: bool | None = None
    repeatWrite: bool | None = None
    needLogin: bool | None = None
    service: Literal["jira"] | None = None


class BulkCreated(OpenModel):
    ok: bool
    created: list[BulkCreatedItem]
    failed: list[BulkFailedItem]
    warnings: list[ValidationMessage]
    errors: list[ValidationMessage] | None = None


class BulkUpdatedItem(OpenModel):
    index: int
    key: str
    fields: list[str]


class BulkUpdateFailure(ToolError):
    index: int
    key: str
    reason: str | None = None
    messageTruncated: bool
    action: Literal["verify_outcome", "authenticate", "reopen_or_check_conflict",
                    "check_permissions", "retry_same_batch", "check_inputs", "restore_original_user"]


class BulkUpdated(OpenModel):
    ok: bool
    updated: list[BulkUpdatedItem]
    failed: list[BulkUpdateFailure]


class CommentAuthor(OpenModel):
    name: str | None = None
    key: str | None = None
    accountId: str | None = None
    displayName: str | None = None
    active: bool | None = None
    avatarUrls: dict[str, str] | None = None


class EditedComment(OpenModel):
    """Jira PUT may return a comment object or a successful empty 204 body."""
    id: str | int | None = None
    body: str | None = None
    renderedBody: str | None = None
    created: str | None = None
    updated: str | None = None
    author: CommentAuthor | None = None
    updateAuthor: CommentAuthor | None = None


class AddedComment(EditedComment):
    # The create/reconciliation path validates this ID before confirming the receipt.
    id: str | int


class TransitionedTicket(OpenModel):
    ok: Literal[True]
    cascade: CascadeSuggestion | None


class AddedDocument(OpenModel):
    """Jira remote-link response; not the content of the linked document."""
    id: str | int
    self: str | None = None


class AddedAttachment(OpenModel):
    id: str
    filename: str
    path: str


class AuthenticationArguments(OpenModel):
    service: Literal["jira", "confluence"]
    interactive: bool | None = None


class AuthenticationAction(OpenModel):
    tool: Literal["authenticate", "get_auth_status"]
    arguments: AuthenticationArguments


class AuthenticationState(OpenModel):
    service: Literal["jira", "confluence"]
    state: Literal["authenticated", "login_required", "checking", "unavailable", "login_in_progress"]
    authenticated: bool
    needLogin: bool
    pending: bool
    recovered: bool
    message: str
    loginStarted: bool | None = None
    nextAction: AuthenticationAction | None = None


DATA_MODELS = {
    "bulk_validate": BulkValidation,
    "create_task": CreatedTicket,
    "create_subtask": CreatedTicket,
    "create_epic": CreatedEpic,
    "bulk_create": BulkCreated,
    "bulk_update": BulkUpdated,
    "update_fields": OkResult,
    "update_description": OkResult,
    "add_comment": AddedComment,
    "edit_comment": EditedComment,
    "delete_comment": OkResult,
    "transition_ticket": TransitionedTicket,
    "add_issue_link": OkResult,
    "delete_issue_link": OkResult,
    "add_document": AddedDocument | OkResult,
    "delete_document": OkResult,
    "add_attachment": AddedAttachment,
    "delete_attachment": OkResult,
    "delete_ticket": OkResult,
    "get_auth_status": AuthenticationState,
    "authenticate": AuthenticationState,
}
