"""Native Jira hierarchy actions; separate discovery from the final mutation."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.local_mcp.schemas.common import OpenModel
from app.local_mcp.write_tools import MutationId
from app.services.ticket_commands import CommandError
from app.services.ticket_hierarchy import HierarchyCommands


Action = Literal["convert_to_subtask", "convert_to_task", "change_parent"]
FieldValues = dict[str, str | list[str]]


class HierarchyChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    parent_key: str = Field(default="", description="Existing Task-tier in the same project; required except convert_to_task.")
    issue_type_id: str = Field(default="", pattern=r"^(?:[0-9]{1,30})?$", description="Exact target type ID, not its name. Empty only for discovery/change_parent.")
    target_status_id: str = Field(default="", pattern=r"^(?:[0-9]{1,30})?$", description="Use only if Jira's wizard requires a workflow status mapping.")
    field_values: FieldValues = Field(default_factory=dict, description="Only input IDs returned by the wizard. Never send id, guid, atl_token or guessed field names.")

    @model_validator(mode="after")
    def valid_target(self):
        if (self.action != "convert_to_task") != bool(self.parent_key):
            raise ValueError("변환 작업에 맞는 parent_key를 지정하세요.")
        if self.action == "change_parent" and (self.issue_type_id or self.target_status_id or self.field_values):
            raise ValueError("부모 변경은 parent_key만 받습니다.")
        return self


class HierarchyResult(OpenModel):
    ok: Literal[True]
    key: str
    action: Action
    issueTypeId: str
    parentKey: str | None
    previousParentKey: str | None
    epicKey: str | None
    previousEpicKey: str | None


class HierarchyInput(OpenModel):
    id: str
    label: str
    type: str
    required: bool
    options: list[dict[str, str]]


class HierarchyOptions(OpenModel):
    key: str
    action: Action
    ready: bool
    stage: str
    inputs: list[HierarchyInput]
    removedFields: list[str]
    changes: list[dict[str, str]]
    previewHash: str
    securityLevel: dict | None
    parentSecurityLevel: dict | None


def register_hierarchy_writes(register, runtime):
    commands = HierarchyCommands(runtime.commands)

    def check(key, body):
        runtime.check_key(key)
        if body.parent_key:
            runtime.check_key(body.parent_key)

    def get_hierarchy_options(key: str, body: HierarchyChange) -> dict:
        """Inspect Jira 8.20's native conversion/reparent wizard without committing a ticket change.
        action is convert_to_subtask, convert_to_task or change_parent. Start without issue_type_id to
        discover exact type IDs, then repeat with the selected ID and any required target_status_id/field_values.
        Returns ready, current stage, typed inputs/options, removedFields, changes and previewHash; no form/session tokens.
        The server advances temporary wizard forms but never submits the final mutation. No arbitrary REST is exposed.
        Done tickets require a separately requested reopen; a Task with children cannot become a SubTask.
        Conversion requires Jira edit permission; reparenting requires Move Issues and a valid editable parent.
        Jira-specific custom widgets may require the Jira UI. An unknown required value must not be invented.
        Read get_content_guide(skill=ltm-task-authoring,path=references/ticket-actions.md) for the full flow.
        """
        check(key, body)
        result = commands.options(key, body.model_dump())
        if len(json.dumps(result, ensure_ascii=False).encode()) > 6 * 1024:
            raise CommandError("변환 화면의 선택 목록이 너무 큽니다. Jira 화면에서 확인하세요.", 413)
        return result

    def change_ticket_hierarchy(key: str, body: HierarchyChange, client_mutation_id: MutationId,
        preview_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        acknowledged_removed_fields: Annotated[list[str], Field(max_length=50)] = []) -> dict:
        """Convert Task to SubTask, SubTask to Task-tier, or change a SubTask's parent through Jira's native wizard.
        First call get_hierarchy_options until ready; pass its exact previewHash as preview_hash.
        Review changes including security-level inheritance before execution; changed previews require rediscovery.
        Conversion requires the chosen issue_type_id, not a name.
        parent_key is required for convert_to_subtask/change_parent, forbidden for convert_to_task.
        This is not Epic membership: use update_fields(body.epic) to change a Task's Epic.
        Done tickets must be explicitly reopened first. Conversions require Jira edit permission; changing
        parent requires Move Issues. The target parent must be another Task-tier in the same project;
        a Task with children cannot become a SubTask. Jira enforces workflow and field restrictions.
        If removedFields is non-empty, obtain the user's explicit field-removal intent and pass that exact list
        as acknowledged_removed_fields. Never silently accept field loss or invent required values/status IDs.
        Keep the SAME mutation ID and exact payload on retries. A lost final response is reconciled, never
        blindly re-submitted. Changes involving custom fields/removal may remain outcome_unknown and need
        a fresh Jira inspection. Returns verified type/parent and invalidates both old/new hierarchy caches.
        """
        check(key, body)
        if body.action != "change_parent" and not body.issue_type_id:
            raise ValueError("get_hierarchy_options에서 선택한 issue_type_id가 필요합니다.")
        return commands.execute(key, body.model_dump(), client_mutation_id, preview_hash, acknowledged_removed_fields)

    register(get_hierarchy_options, read_only=True, output_type=HierarchyOptions)
    register(change_ticket_hierarchy, read_only=False, output_type=HierarchyResult)
