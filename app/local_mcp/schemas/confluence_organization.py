"""Organization tool data contracts, including partial outcomes."""

from typing import Literal
from pydantic import JsonValue
from app.local_mcp.schemas.common import OpenModel
from app.local_mcp.schemas.confluence_reads import ConfluenceList


class StructureResult(OpenModel):
    id: str
    version: int | None = None
    status: str | None = None
    parentId: str | None = None
    verified: bool | None = None
    verificationPending: bool | None = None
    draftState: Literal["unknown"] | None = None


class RestrictionRow(OpenModel):
    operation: Literal["read", "update"]
    users: list[str]
    groups: list[str]


class RestrictionsResult(OpenModel):
    id: str
    restrictions: list[RestrictionRow]
    complete: bool
    fingerprint: str | None
    inheritedRestrictionsIncluded: bool


class RestrictionsChanged(OpenModel):
    id: str
    operations: list[Literal["read", "update"]]
    inheritedRestrictionsUnchanged: bool
    concurrencyProtection: Literal["preflight_only"]


class OperationResult(OpenModel):
    operationId: str
    status: Literal["validated", "completed", "verification_pending", "skipped", "failed", "outcome_unknown"]
    result: dict[str, JsonValue] | None = None
    code: str | None = None
    message: str | None = None


class BatchResult(OpenModel):
    ok: bool
    dryRun: bool
    results: list[OperationResult]
    updated: list[OperationResult]
    failed: list[OperationResult]
    pending: list[OperationResult]
    partial: bool


DATA_MODELS = {
    "list_confluence_trashed_pages": ConfluenceList,
    "move_confluence_document": StructureResult,
    "trash_confluence_document": StructureResult,
    "restore_confluence_document": StructureResult,
    "get_confluence_restrictions": RestrictionsResult,
    "set_confluence_restrictions": RestrictionsChanged,
    "bulk_confluence_changes": BatchResult,
}
