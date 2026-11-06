"""Explicit structure changes, bounded batches and page access restrictions."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.local_mcp.write_tools import MutationId
from app.local_mcp.confluence_read_tools import _page
from app.services.confluence_commands import ConfluenceCommands
from app.services.confluence_organization import ConfluenceOrganization


PageId = Annotated[str, Field(pattern=r"^[0-9]{1,30}$")]
Version = Annotated[int, Field(ge=1)]


class RestrictionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["read", "update"]
    users: list[str] = Field(description="Exact Confluence userKeys. Empty clears direct users for this operation.")
    groups: list[str] = Field(description="Exact group names. Empty clears direct groups. Omitted operations remain unchanged.")

    @model_validator(mode="after")
    def valid_subjects(self):
        if any(not value.strip() for value in self.users + self.groups):
            raise ValueError("빈 사용자·그룹 이름은 지정할 수 없습니다.")
        return self


class OrganizationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    action: Literal["move", "update", "trash", "restore"]
    page_id: PageId
    expected_version: Version
    parent_id: str = Field(default="", pattern=r"^(?:[0-9]{1,30})?$", description="move destination; empty moves to Space root.")
    position: int | None = Field(default=None, ge=-1, description="Sibling position; -1 removes explicit order.")
    title: str | None = None
    body: str | None = None
    format: Literal["storage", "text"] = "storage"
    depends_on: list[str] = Field(default_factory=list, max_length=100,
        description="Earlier operation IDs; dependent writes are skipped after failure or uncertain verification.")

    @model_validator(mode="after")
    def valid_fields(self):
        if self.action == "update" and self.title is None and self.body is None:
            raise ValueError("update에는 title 또는 body가 필요합니다.")
        if self.action != "update" and (self.title is not None or self.body is not None):
            raise ValueError("title/body는 update 작업에서만 지원합니다.")
        if self.action != "move" and (self.parent_id or self.position is not None):
            raise ValueError("parent_id/position은 move 작업에서만 지원합니다.")
        return self


def register_confluence_organization(register, runtime):
    commands = ConfluenceCommands(runtime.get_client, runtime.settings)
    organization = ConfluenceOrganization(commands)

    def list_confluence_trashed_pages(space_key: str, start_at: Annotated[int, Field(ge=0)] = 0,
                                     max_results: Annotated[int, Field(ge=1, le=20)] = 20) -> dict:
        """List accessible trashed pages in one configured Space with exact IDs and versions for restore.
        Follow nextStartAt until hasMore=false. This never searches other Spaces, restores or purges.
        Missing rows may be permission-hidden; only returned pages are known. Use restore_confluence_document
        with the selected page's version.number and a unique mutation ID.
        """
        commands.check_space(space_key)
        data = commands.request("GET", "/rest/api/content", params={"spaceKey": space_key, "type": "page",
            "status": "trashed", "start": start_at, "limit": max_results, "expand": "space,version"})
        result = _page(data, start_at, max_results)
        result["results"] = [{"id": str(row["id"]), "title": row.get("title", ""),
            "space": {"key": (row.get("space") or {}).get("key")}, "status": "trashed",
            "version": {"number": (row.get("version") or {}).get("number"), "when": (row.get("version") or {}).get("when")}}
            for row in result["results"]]
        return result

    def move_confluence_document(page_id: PageId, parent_id: str, expected_version: Version,
                                 client_mutation_id: MutationId, position: Annotated[int | None, Field(ge=-1)] = None) -> dict:
        """Move/reorder an existing page within its CURRENT Space; never copy or move across Spaces.
        parent_id is a Confluence page ID, or empty for a Space root. position is sibling order,
        -1 removes explicit order; omitted keeps the server default. expected_version is mandatory.
        Read the tree first. Cycles are rejected. Access inherited from the new parent can change.
        Production requires recorded 9.2.4 collaborative-editing verification. Published version checks
        cannot detect every unpublished draft. verified=false means re-read the tree before dependent work.
        Keep the same mutation ID and arguments on retry; unknown results are never blindly rewritten.
        """
        return organization.move(page_id, parent_id, expected_version, client_mutation_id, position)

    def trash_confluence_document(page_id: PageId, expected_version: Version, client_mutation_id: MutationId) -> dict:
        """Move ONE leaf page to the Confluence trash, never permanently delete it.
        Refuses pages with children; move children first or explicitly trash them bottom-up.
        Version is checked immediately before deletion; the REST DELETE has no atomic version precondition.
        Use only when deletion is requested, keep the mutation ID unchanged on retry.
        """
        return organization.trash(page_id, expected_version, client_mutation_id)

    def restore_confluence_document(page_id: PageId, expected_version: Version, client_mutation_id: MutationId) -> dict:
        """Restore a trashed page by its exact ID and trashed version. Never copies or purges content.
        Title conflicts or missing permission are returned as errors. Re-read the restored tree before
        moving it. The same mutation ID reuses a confirmed restoration instead of publishing twice.
        """
        return organization.restore(page_id, expected_version, client_mutation_id)

    def get_confluence_restrictions(page_id: PageId) -> dict:
        """Read current DIRECT read/update restrictions and a fingerprint for conditional modification.
        Inherited restrictions are not expanded; absence of direct restrictions does not mean public access.
        complete=false/fingerprint=null means incomplete pagination; do not replace restrictions from it.
        User identifiers are Confluence userKeys, not Jira usernames. This check goes to the server.
        """
        return organization.restrictions(page_id)

    def set_confluence_restrictions(page_id: PageId, restrictions: Annotated[list[RestrictionChange], Field(min_length=1, max_length=2)],
            expected_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")], client_mutation_id: MutationId) -> dict:
        """Replace only explicitly supplied direct read/update restrictions after checking their fingerprint.
        Empty users AND groups clears the named direct restriction; omitted operations remain unchanged.
        Read get_confluence_restrictions first. Ancestor/Space restrictions still apply; this cannot grant
        more access than Confluence allows. Fingerprint protection is preflight-only, not atomic server CAS.
        Do not broaden access unless the user requests it. Use confirmed Confluence userKeys/group names.
        """
        return organization.set_restrictions(page_id, [row.model_dump() for row in restrictions], expected_fingerprint, client_mutation_id)

    def bulk_confluence_changes(operations: Annotated[list[OrganizationChange], Field(min_length=1, max_length=100)],
                                client_mutation_id: MutationId, dry_run: bool = True) -> dict:
        """Validate or execute up to 100 explicit page updates/moves/trash/restores in order.
        Default dry_run=true checks current versions/relationships without writes; it does not prove edit
        permission or lock the tree. Set false only for user-authorized changes. Each operation needs a
        unique operation_id, exact page_id and expected_version; depends_on references earlier operations.
        Preserve the entire batch and mutation ID on retry. Successful operations reuse receipts; failed
        dependencies are skipped. There is no all-or-nothing transaction or automatic rollback. Inspect
        every result, including verification_pending/outcome_unknown, before reporting completion.
        """
        return organization.bulk([row.model_dump() for row in operations], client_mutation_id, dry_run)

    for fn in (move_confluence_document, trash_confluence_document, restore_confluence_document,
               set_confluence_restrictions, bulk_confluence_changes):
        register(fn, read_only=False)
    register(get_confluence_restrictions, read_only=True)
    register(list_confluence_trashed_pages, read_only=True)
