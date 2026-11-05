"""Confluence command result contracts; upstream content is not a write instruction."""

from typing import Literal

from .common import OpenModel


class ConfluenceMutation(OpenModel):
    id: str
    operation: str
    status: Literal["success"]
    version: int | None = None
    title: str | None = None
    parentId: str | None = None
    draftState: Literal["unknown"] | None = None
    filename: str | None = None
    mimeType: str | None = None
    size: int | None = None
    labels: list[str] | None = None


class ConfluenceAttachmentBody(OpenModel):
    id: str
    pageId: str
    filename: str
    version: int
    mimeType: str | None
    base64: str
    totalBytes: int
    offset: int
    hasMore: bool
    nextOffset: int | None
    downloadUrl: str


class ConfluenceCommentBody(OpenModel):
    id: str
    pageId: str
    version: int
    format: Literal["storage", "text"]
    content: str
    offset: int
    totalCharacters: int
    hasMore: bool
    nextOffset: int | None


DATA_MODELS = {name: ConfluenceMutation for name in (
    "create_confluence_page", "update_confluence_page", "create_confluence_comment",
    "update_confluence_comment", "delete_confluence_comment", "upload_confluence_attachment",
    "delete_confluence_attachment", "add_confluence_labels", "remove_confluence_label")}
DATA_MODELS["read_confluence_attachment"] = ConfluenceAttachmentBody
DATA_MODELS["read_confluence_comment"] = ConfluenceCommentBody
