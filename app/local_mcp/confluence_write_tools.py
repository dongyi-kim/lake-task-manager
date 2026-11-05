"""Direct Confluence writes through the running LTM application's command layer."""

import base64
import binascii
from typing import Annotated, Literal

from pydantic import Field

from app.local_mcp.write_tools import MutationId
from app.services.confluence_commands import ConfluenceCommands


Version = Annotated[int, Field(ge=1, description="Published version returned by the latest read; a mismatch aborts the write.")]
Storage = Annotated[str, Field(description=(
    "Confluence storage XHTML (ac:/ri: macros preserved), not Jira editor HTML. "
    "Read get_content_guide(skill='ltm-confluence-authoring'). format=text escapes plain text."))]


def register_confluence_writes(register, runtime):
    commands = ConfluenceCommands(lambda: runtime.client, runtime.settings)

    def call(method, **kwargs):
        try:
            return getattr(commands, method)(**kwargs)
        except Exception as exc:
            exc.service = "confluence"
            raise

    def create_confluence_page(space: str, title: str, body: Storage, client_mutation_id: MutationId,
                               parent_id: str | None = None, format: Literal["storage", "text"] = "storage") -> dict:
        """Create a published page in a configured Space, optionally below a page in that same Space.
        No parent means a Space root, not automatically the homepage. Resolve Space/parent IDs first.
        Storage XHTML preserves Confluence macros; Markdown/Jira HTML are different formats.
        Reuse client_mutation_id for the exact retry; outcome_unknown must never be retried with a new ID.
        This does not copy another document or manipulate another editor's unpublished draft.
        """
        return call("create_page", space=space, title=title, body=body, mutation_id=client_mutation_id,
                    parent_id=parent_id, format=format)

    def update_confluence_page(page_id: str, expected_version: Version, client_mutation_id: MutationId,
                               body: Storage | None = None, title: str | None = None,
                               format: Literal["storage", "text"] = "storage") -> dict:
        """Replace the published body and/or rename a page, preserving omitted fields.
        Read storage and check its published revision first; expected_version is mandatory.
        A version conflict aborts: re-read/review rather than replacing expected_version on an old body.
        Requires this server's DC 9.2.4 collaborative-editing verification. Published-version CAS
        does not prove an unpublished draft is absent; draftState=unknown is explicitly reported.
        No forced overwrite, draft deletion or automatic merge is performed. Existing macros must be retained.
        """
        return call("update_page", page_id=page_id, expected_version=expected_version, mutation_id=client_mutation_id,
                    body=body, title=title, format=format)

    def create_confluence_comment(page_id: str, body: Storage, client_mutation_id: MutationId,
                                  format: Literal["storage", "text"] = "storage") -> dict:
        """Add a footer comment to a page; inline annotated comments are not created by this tool.
        Comments have their own versions; a new comment need not increment the page's body version.
        Supply Confluence storage XHTML or format=text, and retain the mutation ID for retries.
        """
        return call("create_comment", page_id=page_id, body=body, mutation_id=client_mutation_id, format=format)

    def update_confluence_comment(page_id: str, comment_id: str, expected_version: Version, body: Storage,
                                  client_mutation_id: MutationId, format: Literal["storage", "text"] = "storage") -> dict:
        """Replace a comment belonging to page_id, using that comment's expected_version (not the page version).
        Obtain ID/version from get_confluence_context(kind='comment'); read_confluence_comment returns full
        version-pinned source chunks. Do not replace a full comment with a truncated list preview.
        Confluence edit permissions apply.
        """
        return call("update_comment", page_id=page_id, comment_id=comment_id, body=body,
                    expected_version=expected_version, mutation_id=client_mutation_id, format=format)

    def delete_confluence_comment(page_id: str, comment_id: str, expected_version: Version,
                                  client_mutation_id: MutationId) -> dict:
        """Delete one verified page comment. expected_version is the comment's version.
        This is destructive and only for user-authorized deletion. Unknown outcomes are not replayed blindly.
        """
        return call("delete_child", page_id=page_id, child_id=comment_id, kind="comment",
                    expected_version=expected_version, mutation_id=client_mutation_id)

    def upload_confluence_attachment(page_id: str, filename: str, base64_content: str,
                                     client_mutation_id: MutationId,
                                     content_type: str = "application/octet-stream") -> dict:
        """Upload a new attachment (1 byte..10MiB decoded) to a page; filename is a name, never a local path.
        base64_content carries bytes, not a URL. Existing identical filenames are rejected at preflight
        to avoid silently publishing another attachment version. Use the returned filename in an ri:attachment.
        An uncertain upload must be inspected before any new-ID retry; no arbitrary filesystem access is exposed.
        """
        try:
            if len(base64_content) > 14 * 1024 * 1024:
                raise ValueError("첨부는 10MiB 이하이어야 합니다.")
            data = base64.b64decode(base64_content, validate=True)
        except (binascii.Error, ValueError) as exc:
            error = ValueError("유효한 base64로 인코딩한 10MiB 이하 첨부가 필요합니다.")
            error.service = "confluence"
            raise error from exc
        return call("upload_attachment", page_id=page_id, filename=filename, data=data,
                    mutation_id=client_mutation_id, content_type=content_type)

    def read_confluence_attachment(page_id: str, attachment_id: str, offset: int = 0,
                                   max_bytes: Annotated[int, Field(ge=1, le=4096)] = 1024,
                                   version: int | None = None) -> dict:
        """Inspect a small base64 preview and get downloadUrl for a verified attachment (total<=10MiB).
        Fetch downloadUrl with a local HTTP client to save the full binary outside model context;
        do not concatenate thousands of MCP text chunks for normal file downloads.
        No arbitrary URL/local path is accepted. offset/max_bytes count decoded bytes; max_bytes<=4096.
        Follow nextOffset and pass the first response's version for every subsequent chunk.
        The requested historical version is retained; an unavailable version aborts instead of mixing bytes.
        """
        return call("read_attachment", page_id=page_id, attachment_id=attachment_id,
                    offset=offset, max_bytes=max_bytes, version=version)

    def read_confluence_comment(page_id: str, comment_id: str, version: Version | None = None,
                                offset: int = 0, max_chars: Annotated[int, Field(ge=1, le=8000)] = 4000,
                                format: Literal["storage", "text"] = "storage") -> dict:
        """Read a comment's complete evidence/source in bounded version-pinned chunks.
        Use get_confluence_context(kind='comment') to discover IDs; list previews are not full bodies.
        Follow nextOffset, retaining the first response's version and format for every following chunk.
        max_chars caps characters; each content chunk also caps UTF-8 bytes at 8000 without splitting a character.
        storage preserves source XHTML for editing; text is evidence only. The comment must belong to page_id.
        """
        return call("read_comment", page_id=page_id, comment_id=comment_id, version=version,
                    offset=offset, max_chars=max_chars, format=format)

    def delete_confluence_attachment(page_id: str, attachment_id: str, expected_version: Version,
                                     client_mutation_id: MutationId) -> dict:
        """Delete an attachment verified to belong to page_id, after checking its own version.
        This can break images/download links in the page; use only for user-authorized removal.
        """
        return call("delete_child", page_id=page_id, child_id=attachment_id, kind="attachment",
                    expected_version=expected_version, mutation_id=client_mutation_id)

    def add_confluence_labels(page_id: str, labels: list[str], client_mutation_id: MutationId) -> dict:
        """Add global labels while preserving existing labels; no page-body version replacement.
        This is an additive operation. Query the label list separately for the final complete set.
        """
        return call("update_labels", page_id=page_id, labels=labels, mutation_id=client_mutation_id)

    def remove_confluence_label(page_id: str, label: str, client_mutation_id: MutationId) -> dict:
        """Remove exactly one named label and preserve all others. Does not modify the page body."""
        return call("update_labels", page_id=page_id, labels=[label], remove=True, mutation_id=client_mutation_id)

    for tool in (create_confluence_page, update_confluence_page, create_confluence_comment,
                 update_confluence_comment, delete_confluence_comment, upload_confluence_attachment,
                 delete_confluence_attachment, add_confluence_labels, remove_confluence_label):
        register(tool, read_only=False)
    register(read_confluence_attachment, read_only=True)
    register(read_confluence_comment, read_only=True)
