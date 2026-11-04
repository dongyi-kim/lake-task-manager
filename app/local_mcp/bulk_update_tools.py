"""Bounded bulk edits reuse the same commands, durable receipts and caches as single edits."""

import hashlib
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.local_mcp.write_tools import FieldChanges, FieldsInput, MutationId
from app.services.ticket_commands import _preflight_read


class BatchActorChanged(ValueError):
    """A new row cannot be started by a different account from the batch manifest."""


class BulkUpdateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*-[0-9]+$",
                     description="Exact Jira key within configured projects; each key may occur once.")
    fields: FieldChanges

    @model_validator(mode="after")
    def has_changes(self):
        if not self.fields.model_dump(exclude_none=True):
            raise ValueError("각 행에는 하나 이상의 변경 필드가 필요합니다.")
        return self


def _failure(runtime, index, key, exc):
    error = dict(runtime._error(exc))
    message = str(error.pop("message", error.pop("error", "요청을 완료하지 못했습니다.")))
    preview = message.encode("utf-8")[:160].decode("utf-8", errors="ignore")
    # Do not repeat arbitrary Jira bodies/field values in a bulk response. Full discovery is
    # available through list_options, and the unmodified payload remains in the caller's batch.
    kept = {name: error[name] for name in ("code", "status", "retryable", "needLogin",
        "repeatWrite", "writeExecuted", "clientMutationId", "reason", "nextAction") if name in error}
    if isinstance(exc, BatchActorChanged):
        kept.update(code="actor_changed", retryable=False, writeExecuted=False)
    action = ("restore_original_user" if isinstance(exc, BatchActorChanged) else
              "verify_outcome" if kept.get("code") == "outcome_unknown" else
              "authenticate" if kept.get("needLogin") else
              "reopen_or_check_conflict" if kept.get("status") == 409 else
              "check_permissions" if kept.get("status") == 403 else
              "retry_same_batch" if kept.get("retryable") else "check_inputs")
    return {"index": index, "key": key, **kept, "message": preview,
            "messageTruncated": preview != message, "action": action}


def register_bulk_updates(register, runtime):
    def bulk_update(items: Annotated[list[BulkUpdateItem], Field(min_length=1, max_length=10)],
                    client_mutation_id: MutationId) -> dict:
        """Edit 1..10 distinct tickets in order; returns indexed updated/failed rows, without rollback.
        Each row is {key, fields}; fields match update_fields, including summary (rename), labels,
        components, assignee, descriptionHtml and epic. Arrays REPLACE all values; [] clears.
        Null/omitted fields preserve values. Use list_options(edit_fields,key) for current permissions.
        Done tickets must be explicitly reopened using an available transition before a NEW edit batch;
        this tool never reopens implicitly. Reporter requires MODIFY_REPORTER; missing editmeta fields
        may reflect Jira permissions, field screens or workflow restrictions. Recheck metadata instead of
        authentication retries. A 403 never bypasses permissions; nextAction preserves specific discovery guidance.
        This does not convert Task/SubTask types or change a SubTask's parent; epic is Epic membership only.
        Retry the SAME complete ordered items and SAME batch ID: successes replay, confirmed failures
        resume, outcome_unknown is never blindly written again. Never replace an unknown batch ID or
        retry a subset under the old ID. Corrected content needs a new ID after resolving unknown outcomes.
        Authentication silently recovers once; unresolved login errors require authenticate then the same batch.
        Failed rows include a short message preview and action: inspect permissions/inputs, reopen separately,
        authenticate, restore_original_user, retry_same_batch, or verify_outcome. A changed Jira account
        cannot resume unfinished rows; restore the original account and keep the batch ID. Split larger work.
        """
        keys = [item.key.upper() for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("한 일괄 수정에 같은 티켓을 여러 번 넣을 수 없습니다.")
        for item in items:
            runtime.check_key(item.key)
            if item.fields.epic:
                runtime.check_key(item.fields.epic)
        commands, client = runtime.commands, runtime.client
        commands._once("bulk-update-manifest", client_mutation_id,
            {"items": [item.model_dump() for item in items]}, lambda: {"ok": True})
        # A resumed partial batch must not start new rows under a different Jira account.
        actor, _ = client._mutation_actor_for("command-bulk-update-manifest", client_mutation_id)
        updated, failed = [], []
        with client._mutation_batch_scope():
            for index, item in enumerate(items):
                row_id = "bulk-edit:" + hashlib.sha256(f"{client_mutation_id}:{index}".encode()).hexdigest()
                body = FieldsInput(**item.fields.model_dump(), clientMutationId=row_id)
                try:
                    # Existing receipts can only replay a confirmed result or report uncertainty
                    # (commands._once never rewrites an ambiguous field edit). Only a new receipt
                    # can start a PUT; fence every such row, including after silent authentication.
                    if not client._mutation_receipts.receipt("command-fields", row_id):
                        try:
                            _preflight_read(lambda: client.assert_mutation_actor_identity(actor))
                        except ValueError as exc:
                            raise BatchActorChanged(str(exc)) from exc
                    commands.update_fields(item.key, body)
                    updated.append({"index": index, "key": item.key,
                                    "fields": sorted(item.fields.model_dump(exclude_none=True))})
                except Exception as exc:
                    failed.append(_failure(runtime, index, item.key, exc))
        return {"ok": not failed, "updated": updated, "failed": failed}

    register(bulk_update, read_only=False)
