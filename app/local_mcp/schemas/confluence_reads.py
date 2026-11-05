"""Published revision metadata is deliberately separate from collaborative drafts."""
from typing import Literal

from .common import JsonValue, OpenModel


class ConfluenceRevision(OpenModel):
    pageId: str
    latestPublishedVersion: int
    lastModifiedAt: str | None
    lastModifiedBy: dict[str, JsonValue] | None
    checkedAt: float | None
    isLatest: bool | None
    draftState: Literal["unknown"]


class ConfluenceBodyCache(OpenModel):
    bodyVersion: int
    bodyFetchedAt: float | None
    latestPublishedVersion: int
    headCheckedAt: float | None
    isLatest: bool | None


class ConfluenceList(OpenModel):
    results: list[dict[str, JsonValue]]
    startAt: int
    returned: int
    hasMore: bool
    nextStartAt: int | None
    coverage: Literal["accessible_only"]


class ConfluenceContext(OpenModel):
    pageId: str
    kind: Literal["ancestors", "comment", "attachment", "label", "restriction"]
    results: list[dict[str, JsonValue]] | None = None
    data: dict[str, JsonValue] | None = None
    hasMore: bool | None = None
    nextStartAt: int | None = None


OUTPUT_MODELS = {"check_confluence_revision": ConfluenceRevision,
                 "list_confluence_spaces": ConfluenceList,
                 "list_confluence_pages": ConfluenceList,
                 "get_confluence_context": ConfluenceContext}
