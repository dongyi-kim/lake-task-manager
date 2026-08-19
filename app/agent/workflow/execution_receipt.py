"""Signed, typed execution receipts shared by write producers and Result consumers.

The legacy ``result`` mapping is display data.  It is intentionally not an authority: write
adapters may normalize provider responses and a checkpoint may retain it after its approval
capability has been consumed.  This module instead binds the approved payload to action-scoped
target ids at the write boundary, validates the raw adapter response, and signs a self-contained
terminal outcome ledger.  ResultIntegrator consumes only that ledger.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from app.agent.workflow.safe_render import sanitize_external_scalar


EXECUTION_RECEIPT_CONTRACT = "execution-receipt.v1"
EXECUTION_RECEIPT_AUTHORITY = "action-executor.approved-dispatch.v1"
EXECUTION_RECEIPT_ACTIONS = frozenset({
    "create_tickets", "create_epic", "update_ticket", "update_tickets",
})

ExecutionAction = Literal[
    "create_tickets", "create_epic", "update_ticket", "update_tickets",
]
ExecutionPhase = Literal["primary"]
ExecutionScope = Literal["item", "child"]
ExecutionStatus = Literal["success", "failure"]
ExecutionChangeField = Literal[
    "assignee", "components", "description", "duedate", "labels", "priority", "summary",
]
ExecutionValueKind = Literal["clear", "list", "scalar", "text"]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TicketKey = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]*-[0-9]+$")]
NonNegativeInt = Annotated[int, Field(ge=0)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExecutionApprovalV1(_StrictFrozenModel):
    phase: ExecutionPhase
    action: ExecutionAction
    payload_digest: Sha256Digest
    capability_digest: Sha256Digest


class ExecutionEffectV1(_StrictFrozenModel):
    field: ExecutionChangeField
    value_kind: ExecutionValueKind
    display: str = Field(min_length=1, max_length=240)


class ExecutionTargetV1(_StrictFrozenModel):
    ordinal: NonNegativeInt
    phase: ExecutionPhase
    action: ExecutionAction
    scope: ExecutionScope
    index: NonNegativeInt
    target_id: str = Field(min_length=1, max_length=160)
    effect_digest: Sha256Digest
    target_key: TicketKey | None = None
    summary: str = Field(default="", max_length=500)
    parent_index: NonNegativeInt | None = None
    effects: tuple[ExecutionEffectV1, ...] = ()

    @model_validator(mode="after")
    def _canonical_target_id(self):
        if self.target_id != execution_target_id(
                self.action, self.scope, self.index, phase=self.phase):
            raise ValueError("target id is not the canonical action-scoped identity")
        if self.scope == "child" and (self.action != "create_tickets"
                                      or self.parent_index is None):
            raise ValueError("child targets need one approved create parent index")
        if self.scope == "item" and self.parent_index is not None:
            raise ValueError("non-child targets cannot carry a parent index")
        if self.action.startswith("update_") and not self.effects:
            raise ValueError("update targets need a typed approved effect display")
        if self.action.startswith("create_") and self.effects:
            raise ValueError("create targets cannot carry update effects")
        return self


class ExecutionOutcomeV1(_StrictFrozenModel):
    ordinal: NonNegativeInt
    phase: ExecutionPhase
    action: ExecutionAction
    scope: ExecutionScope
    index: NonNegativeInt
    target_id: str = Field(min_length=1, max_length=160)
    effect_digest: Sha256Digest
    status: ExecutionStatus
    key: TicketKey | None = None
    summary: str = Field(default="", max_length=500)
    error: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _terminal_shape(self):
        if self.target_id != execution_target_id(
                self.action, self.scope, self.index, phase=self.phase):
            raise ValueError("outcome target id is not canonical")
        if self.status == "success" and (self.key is None or self.error):
            raise ValueError("successful execution needs one key and no error")
        if self.status == "failure" and (self.key is not None or not self.error.strip()):
            raise ValueError("failed execution needs one non-empty error and no key")
        return self


class ExecutionReceiptV1(_StrictFrozenModel):
    contract: Literal["execution-receipt.v1"] = EXECUTION_RECEIPT_CONTRACT
    authority: Literal["action-executor.approved-dispatch.v1"] = EXECUTION_RECEIPT_AUTHORITY
    thread_id: str = Field(min_length=1, max_length=300)
    action: ExecutionAction
    payload_digest: Sha256Digest
    capability_digest: Sha256Digest
    approvals: tuple[ExecutionApprovalV1, ...]
    cardinality: int = Field(ge=1, le=200)
    expected: tuple[ExecutionTargetV1, ...]
    outcomes: tuple[ExecutionOutcomeV1, ...]
    note: str = Field(default="", max_length=1000)
    complete: Literal[True] = True
    signature: Sha256Digest

    @model_validator(mode="after")
    def _exact_coverage(self):
        if not self.approvals or self.approvals[0].phase != "primary":
            raise ValueError("receipt needs one primary approval")
        primary = self.approvals[0]
        if (self.action, self.payload_digest, self.capability_digest) != (
                primary.action, primary.payload_digest, primary.capability_digest):
            raise ValueError("top-level authority must equal the primary approval")
        if len({row.phase for row in self.approvals}) != len(self.approvals):
            raise ValueError("approval phases must be unique")
        if self.cardinality != len(self.expected) or self.cardinality != len(self.outcomes):
            raise ValueError("receipt cardinality must cover every expected target")
        expected_ids = [row.target_id for row in self.expected]
        outcome_ids = [row.target_id for row in self.outcomes]
        if len(set(expected_ids)) != len(expected_ids) or outcome_ids != expected_ids:
            raise ValueError("terminal outcomes must bind every ordered target exactly once")
        for ordinal, (expected, outcome) in enumerate(zip(self.expected, self.outcomes)):
            identity = (expected.ordinal, expected.phase, expected.action, expected.scope,
                        expected.index, expected.target_id, expected.effect_digest)
            actual = (outcome.ordinal, outcome.phase, outcome.action, outcome.scope,
                      outcome.index, outcome.target_id, outcome.effect_digest)
            if expected.ordinal != ordinal or outcome.ordinal != ordinal or identity != actual:
                raise ValueError("outcome identity does not equal its expected target")
            if (outcome.status == "success" and expected.target_key is not None
                    and outcome.key != expected.target_key):
                raise ValueError("mutation result key does not equal the approved target")
        outcomes_by_id = {row.target_id: row for row in self.outcomes}
        for target, outcome in zip(self.expected, self.outcomes):
            if target.scope != "child" or outcome.status != "success":
                continue
            parent_id = execution_target_id(
                "create_tickets", "item", target.parent_index, phase=target.phase,
            )
            if (parent_id not in outcomes_by_id
                    or outcomes_by_id[parent_id].status != "success"):
                raise ValueError("a child cannot succeed without its approved parent succeeding")
        create_keys = [row.key for row in self.outcomes
                       if row.status == "success" and row.action.startswith("create_")]
        if len(set(create_keys)) != len(create_keys):
            raise ValueError("two create targets cannot claim the same created key")
        return self

    def as_dict(self) -> dict:
        return self.model_dump(mode="python")


_ACTION_ADAPTER = TypeAdapter(ExecutionAction)
_PHASE_ADAPTER = TypeAdapter(ExecutionPhase)
_SCOPE_ADAPTER = TypeAdapter(ExecutionScope)
_CHANGE_FIELD_ADAPTER = TypeAdapter(ExecutionChangeField)
_RECEIPT_ADAPTER = TypeAdapter(ExecutionReceiptV1)
_PROCESS_KEY = secrets.token_bytes(32)


def _canonical_digest(value) -> str:
    try:
        blob = json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def execution_effect_digest(value) -> str:
    """Digest one exact approved operation without exposing its body in the receipt id."""
    return _canonical_digest(value)


def execution_target_id(
    action: str,
    scope: str,
    index: int,
    *,
    phase: str = "primary",
) -> str:
    """Return the canonical action-scoped id; invalid inputs fail rather than coerce."""
    checked_action = _ACTION_ADAPTER.validate_python(action, strict=True)
    checked_phase = _PHASE_ADAPTER.validate_python(phase, strict=True)
    checked_scope = _SCOPE_ADAPTER.validate_python(scope, strict=True)
    if type(index) is not int or index < 0:
        raise ValueError("execution target index must be a non-negative integer")
    return f"{checked_phase}:{checked_action}:{checked_scope}:{index}"


def bind_execution_rows(
    rows,
    *,
    action: str,
    items: list,
    scope: str = "item",
    source_indices: list[int] | None = None,
    phase: str = "primary",
) -> list[dict]:
    """Attach ids after a write call, using only its explicit result index.

    ``source_indices`` maps a filtered child batch's provider index back to the original approved
    child index.  It is kept out of the Jira rows and used only after the provider returns.
    Invalid/no-index rows remain unbound so a receipt cannot claim complete coverage.
    """
    if not isinstance(rows, list):
        return [{"unbound_execution_row": type(rows).__name__}]
    bound: list[dict] = []
    for value in rows:
        if not isinstance(value, dict):
            bound.append({"unbound_execution_row": type(value).__name__})
            continue
        row = dict(value)
        # Provider-controlled fields cannot mint authority.  Only this helper writes them.
        row.pop("target_id", None)
        row.pop("effect_digest", None)
        provider_index = row.get("index")
        source_index = provider_index
        if source_indices is not None:
            if (type(provider_index) is not int
                    or not 0 <= provider_index < len(source_indices)):
                bound.append(row)
                continue
            source_index = source_indices[provider_index]
        if (type(source_index) is not int or not 0 <= source_index < len(items)
                or not isinstance(items[source_index], dict)):
            bound.append(row)
            continue
        digest = execution_effect_digest(items[source_index])
        if not digest:
            bound.append(row)
            continue
        row.update(index=source_index,
                   target_id=execution_target_id(
                       action, scope, source_index, phase=phase,
                   ),
                   effect_digest=digest)
        bound.append(row)
    return bound


def bind_single_execution_result(raw, *, action: str, payload: dict) -> dict:
    """Add one write-boundary identity to a singleton adapter response."""
    out = dict(raw or {}) if isinstance(raw, dict) else {}
    digest = execution_effect_digest(payload)
    if digest:
        out.update(index=0,
                   target_id=execution_target_id(action, "item", 0),
                   effect_digest=digest)
    return out


def scrub_execution_sidecars(result: dict, *, secrets_to_remove=()) -> dict:
    """Remove internal ids and sanitize external prose before the public/API boundary."""
    clean = dict(result or {}) if isinstance(result, dict) else {}
    for collection in ("created", "updated", "failed"):
        if collection not in clean:
            continue
        rows = []
        for value in clean.get(collection) or []:
            if isinstance(value, dict):
                row = {key: item for key, item in value.items()
                       if key not in {"target_id", "effect_digest"}}
                for field in ("summary", "error"):
                    if field in row:
                        row[field] = sanitize_external_scalar(
                            row[field], secrets_to_remove=secrets_to_remove,
                        )
                rows.append(row)
        clean[collection] = rows
    if "note" in clean:
        clean["note"] = sanitize_external_scalar(
            clean.get("note"), secrets_to_remove=secrets_to_remove,
        )
    clean.pop("_execution_raw", None)
    return clean


def _ticket_key(value) -> str:
    try:
        return TypeAdapter(TicketKey).validate_python(value, strict=True)
    except ValidationError:
        return ""


def _effect_projection(changes) -> tuple[ExecutionEffectV1, ...] | None:
    if not isinstance(changes, dict) or not changes:
        return None
    projected = []
    for field, value in changes.items():
        try:
            checked_field = _CHANGE_FIELD_ADAPTER.validate_python(field, strict=True)
        except ValidationError:
            return None
        if value is None or value == "":
            kind, display = "clear", "비움"
        elif isinstance(value, list):
            if (len(value) > 20 or any(
                    not isinstance(item, (str, int, float, bool)) or isinstance(item, float)
                    and (item != item or item in (float("inf"), float("-inf")))
                    for item in value)):
                return None
            kind = "list"
            display = ", ".join(str(item) for item in value) if value else "비움"
        elif isinstance(value, (str, int, float, bool)):
            if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
                return None
            kind = "text" if checked_field in {"summary", "description"} else "scalar"
            display = str(value)
        else:
            return None
        display = sanitize_external_scalar(display, limit=200)
        if not display:
            display = "비움"
        try:
            projected.append(ExecutionEffectV1(
                field=checked_field, value_kind=kind, display=display,
            ))
        except ValidationError:
            return None
    return tuple(projected)


def _expected_targets(record: dict, *, phase: str) -> tuple[ExecutionTargetV1, ...] | None:
    try:
        action = _ACTION_ADAPTER.validate_python(record.get("action"), strict=True)
        checked_phase = _PHASE_ADAPTER.validate_python(phase, strict=True)
    except ValidationError:
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or _canonical_digest(payload) != record.get("fp"):
        return None
    specs: list[tuple[str, int, dict, str | None, int | None]] = []
    if action == "create_tickets":
        items = payload.get("items")
        children = payload.get("children") or []
        if not isinstance(items, list) or not items or not isinstance(children, list):
            return None
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not str(item.get("summary") or "").strip():
                return None
            specs.append(("item", index, item, None, None))
        for index, child in enumerate(children):
            parent_index = child.get("parent_index") if isinstance(child, dict) else None
            if (not isinstance(child, dict) or not str(child.get("summary") or "").strip()
                    or type(parent_index) is not int or not 0 <= parent_index < len(items)):
                return None
            specs.append(("child", index, child, None, parent_index))
    elif action == "create_epic":
        if not str(payload.get("summary") or "").strip():
            return None
        specs.append(("item", 0, payload, None, None))
    elif action == "update_ticket":
        key = _ticket_key(payload.get("key"))
        changes = payload.get("changes")
        if not key or not isinstance(changes, dict) or not changes:
            return None
        specs.append(("item", 0, payload, key, None))
    else:  # update_tickets
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return None
        seen: set[str] = set()
        for index, item in enumerate(items):
            key = _ticket_key((item or {}).get("key") if isinstance(item, dict) else None)
            changes = (item or {}).get("changes") if isinstance(item, dict) else None
            if not key or key in seen or not isinstance(changes, dict) or not changes:
                return None
            seen.add(key)
            specs.append(("item", index, item, key, None))
    targets = []
    for ordinal, (scope, index, effect, target_key, parent_index) in enumerate(specs):
        effects = _effect_projection(effect.get("changes")) if action.startswith("update_") else ()
        if action.startswith("update_") and effects is None:
            return None
        targets.append(ExecutionTargetV1(
            ordinal=ordinal, phase=checked_phase, action=action, scope=scope, index=index,
            target_id=execution_target_id(action, scope, index, phase=checked_phase),
            effect_digest=execution_effect_digest(effect), target_key=target_key,
            summary=str(effect.get("summary") or target_key or "")[:500],
            parent_index=parent_index,
            effects=effects,
        ))
    return tuple(targets)


def _strict_rows(rows, expected_by_id, *, success: bool, action: str):
    if not isinstance(rows, list):
        return None
    outcomes = []
    if action.startswith("create_"):
        allowed = ({"index", "key", "summary", "target_id", "effect_digest"}
                   if success else
                   {"index", "summary", "error", "target_id", "effect_digest"})
    else:
        allowed = ({"index", "key", "fields", "target_id", "effect_digest"}
                   if success else
                   {"index", "summary", "error", "target_id", "effect_digest"})
    for value in rows:
        if not isinstance(value, dict) or not set(value).issubset(allowed):
            return None
        target = expected_by_id.get(value.get("target_id"))
        if (target is None or type(value.get("index")) is not int
                or value.get("index") != target.index
                or value.get("effect_digest") != target.effect_digest):
            return None
        if success:
            key = _ticket_key(value.get("key"))
            if not key:
                return None
            if target.target_key is not None and key != target.target_key:
                return None
            if action.startswith("create_") and str(value.get("summary") or "") != target.summary:
                return None
            if action.startswith("update_"):
                fields = value.get("fields")
                if (not isinstance(fields, list)
                        or any(not isinstance(field, str) for field in fields)
                        or len(fields) != len(set(fields))):
                    return None
            outcomes.append((target, "success", key, ""))
        else:
            error = value.get("error")
            if not isinstance(error, str) or not error.strip():
                return None
            summary = str(value.get("summary") or "")
            if summary and summary != target.summary:
                return None
            outcomes.append((target, "failure", None, error[:1000]))
    return outcomes


def _raw_outcomes(action: str, payload: dict, raw: dict, expected):
    if not isinstance(raw, dict) or type(raw.get("ok")) is not bool:
        return None
    if raw.get("error") or raw.get("errors") or raw.get("incomplete") not in (None, False):
        return None
    expected_by_id = {row.target_id: row for row in expected}
    if action in {"create_tickets", "create_epic"}:
        if not set(raw).issubset({"ok", "created", "failed"}):
            return None
        success = _strict_rows(raw.get("created"), expected_by_id, success=True, action=action)
        failed = _strict_rows(raw.get("failed"), expected_by_id, success=False, action=action)
    elif action == "update_tickets":
        if not set(raw).issubset({"ok", "updated", "failed"}):
            return None
        success = _strict_rows(raw.get("updated"), expected_by_id, success=True, action=action)
        failed = _strict_rows(raw.get("failed"), expected_by_id, success=False, action=action)
        if success is not None:
            for target, _status, _key, _error in success:
                row = next(value for value in raw["updated"]
                           if value.get("target_id") == target.target_id)
                approved = payload["items"][target.index]["changes"]
                if set(row.get("fields") or []) != set(approved):
                    return None
    else:  # update_ticket singleton raw adapter
        allowed = {"ok", "key", "updated", "skipped", "index", "target_id", "effect_digest"}
        if not raw.get("ok") or not set(raw).issubset(allowed):
            return None
        target = expected[0]
        if (raw.get("target_id") != target.target_id
                or raw.get("effect_digest") != target.effect_digest
                or raw.get("index") != 0 or _ticket_key(raw.get("key")) != target.target_key
                or raw.get("skipped") not in (None, [])):
            return None
        fields = raw.get("updated")
        if (not isinstance(fields, list) or set(fields) != set(payload.get("changes") or {})
                or len(fields) != len(set(fields))
                or any(not isinstance(field, str) for field in fields)):
            return None
        success, failed = [(target, "success", target.target_key, "")], []
    if success is None or failed is None or bool(failed) == bool(raw.get("ok")):
        return None
    combined = success + failed
    if action.startswith("create_"):
        created_keys = [key for _target, status, key, _error in combined
                        if status == "success"]
        if len(created_keys) != len(set(created_keys)):
            return None
    by_id = {target.target_id: (target, status, key, error)
             for target, status, key, error in combined}
    if len(by_id) != len(combined) or set(by_id) != set(expected_by_id):
        return None
    for target in expected:
        if target.scope != "child" or by_id[target.target_id][1] != "success":
            continue
        parent_id = execution_target_id(
            "create_tickets", "item", target.parent_index, phase=target.phase,
        )
        if parent_id not in by_id or by_id[parent_id][1] != "success":
            return None
    return [by_id[target.target_id] for target in expected]


def execution_raw_complete(action: str, payload: dict, raw: dict) -> bool:
    """Whether one registered raw adapter response exactly covers its approved payload."""
    try:
        record = {"action": action, "payload": payload, "fp": _canonical_digest(payload)}
        expected = _expected_targets(record, phase="primary")
        return bool(expected is not None
                    and _raw_outcomes(action, payload, raw, expected) is not None)
    except (ValidationError, TypeError, ValueError):
        return False


def _normalized_result_matches(action: str, payload: dict, raw: dict, result: dict,
                               reconciled) -> bool:
    """Require the legacy projection to be the same terminal ledger as the raw authority."""
    if (not isinstance(result, dict)
            or set(result) != {"created", "updated", "failed", "note"}
            or not isinstance(result.get("note"), str)):
        return False
    actual: dict[str, dict[str, dict]] = {}
    for collection in ("created", "updated", "failed"):
        rows = result.get(collection)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return False
        by_id = {row.get("target_id"): row for row in rows}
        if len(by_id) != len(rows) or any(not target_id for target_id in by_id):
            return False
        actual[collection] = by_id

    expected: dict[str, dict[str, dict]] = {"created": {}, "updated": {}, "failed": {}}
    if action == "update_ticket":
        target, status, key, _error = reconciled[0]
        if status != "success":
            return False
        expected["updated"][target.target_id] = {
            "index": target.index, "key": key, "fields": list(raw.get("updated") or []),
            "target_id": target.target_id, "effect_digest": target.effect_digest,
        }
    else:
        success_collection = "created" if action.startswith("create_") else "updated"
        raw_success = raw.get(success_collection)
        raw_failed = raw.get("failed")
        if not isinstance(raw_success, list) or not isinstance(raw_failed, list):
            return False
        for row in raw_success:
            if not isinstance(row, dict) or not row.get("target_id"):
                return False
            expected[success_collection][row["target_id"]] = row
        for row in raw_failed:
            if not isinstance(row, dict) or not row.get("target_id"):
                return False
            expected["failed"][row["target_id"]] = row

    reconciled_ids = {target.target_id for target, *_rest in reconciled}
    expected_ids = {target_id for rows in expected.values() for target_id in rows}
    actual_ids = {target_id for rows in actual.values() for target_id in rows}
    return (expected_ids == reconciled_ids == actual_ids
            and all(actual[collection] == expected[collection]
                    for collection in ("created", "updated", "failed")))


def issue_execution_receipt(
    *,
    record: dict,
    token: str,
    result: dict,
    raw: dict,
    consumption_attestation: dict,
    phase: str = "primary",
) -> dict | None:
    """Finalize one consumed approval into a signed exact terminal ledger.

    A one-use positive consumption attestation is required.  Token absence, rejection or expiry
    is never execution proof; raw action reconciliation above is the only result authority.
    """
    from app.agent import approval

    frozen_record = deepcopy(record) if isinstance(record, dict) else {}
    if not token:
        return None
    try:
        expected = _expected_targets(frozen_record, phase=phase)
    except (ValidationError, TypeError, ValueError):
        return None
    payload = frozen_record.get("payload")
    if expected is None or not isinstance(payload, dict):
        return None
    try:
        action = _ACTION_ADAPTER.validate_python(frozen_record.get("action"), strict=True)
    except ValidationError:
        return None
    reconciled = _raw_outcomes(action, payload, raw, expected)
    if reconciled is None:
        return None
    if not _normalized_result_matches(action, payload, raw, result, reconciled):
        return None
    if not approval.verify_consumption_attestation(
            consumption_attestation, token=token,
            thread_id=str(frozen_record.get("thread") or ""),
            action=action, payload=payload):
        return None
    try:
        outcomes = tuple(ExecutionOutcomeV1(
            ordinal=target.ordinal, phase=target.phase, action=target.action,
            scope=target.scope, index=target.index, target_id=target.target_id,
            effect_digest=target.effect_digest, status=status, key=key,
            summary=target.summary,
            error=sanitize_external_scalar(
                error, limit=1000, secrets_to_remove=(token,),
            ),
        ) for target, status, key, error in reconciled)
    except (ValidationError, TypeError, ValueError):
        return None
    payload_digest = str(frozen_record.get("fp") or "")
    capability_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        approval_row = ExecutionApprovalV1(
            phase=phase, action=action, payload_digest=payload_digest,
            capability_digest=capability_digest,
        )
    except (ValidationError, TypeError, ValueError):
        return None
    unsigned = {
        "thread_id": str(frozen_record.get("thread") or ""),
        "action": action,
        "payload_digest": payload_digest,
        "capability_digest": capability_digest,
        "approvals": (approval_row,),
        "cardinality": len(expected),
        "expected": expected,
        "outcomes": outcomes,
        "note": sanitize_external_scalar(
            result.get("note"), limit=1000, secrets_to_remove=(token,),
        ),
        "complete": True,
    }
    try:
        seal_payload = ExecutionReceiptV1(
            signature="0" * 64, **unsigned,
        ).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        return None
    seal_payload.pop("signature", None)
    signature = hmac.new(
        _PROCESS_KEY,
        json.dumps(seal_payload, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    try:
        return ExecutionReceiptV1(signature=signature, **unsigned).as_dict()
    except (ValidationError, TypeError, ValueError):
        return None


def parse_execution_receipt(value, *, thread_id: str, token: str) -> ExecutionReceiptV1 | None:
    """Verify schema, seal, current thread and current approval capability digest."""
    # JSON/checkpointer transports represent tuples as lists.  Normalize only these three known
    # containers, then retain strict validation for every scalar and nested record.
    wire = dict(value) if isinstance(value, dict) else value
    if isinstance(wire, dict):
        for field in ("approvals", "expected", "outcomes"):
            if isinstance(wire.get(field), list):
                wire[field] = tuple(wire[field])
        expected_rows = []
        for value in wire.get("expected") or ():
            row = dict(value) if isinstance(value, dict) else value
            if isinstance(row, dict) and isinstance(row.get("effects"), list):
                row["effects"] = tuple(row["effects"])
            expected_rows.append(row)
        if isinstance(wire.get("expected"), tuple):
            wire["expected"] = tuple(expected_rows)
    try:
        receipt = _RECEIPT_ADAPTER.validate_python(wire, strict=True)
    except ValidationError:
        return None
    sealed = receipt.model_dump(mode="json")
    signature = sealed.pop("signature", "")
    expected_signature = hmac.new(
        _PROCESS_KEY,
        json.dumps(sealed, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    if receipt.thread_id != str(thread_id or ""):
        return None
    current_capability = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    if not token or not hmac.compare_digest(receipt.capability_digest, current_capability):
        return None
    return receipt


def safe_inline_literal(value, *, limit: int = 500) -> str:
    """Render untrusted provider text as one inert, prefixed Markdown code literal."""
    compact = sanitize_external_scalar(value, limit=limit) or "(empty)"
    return f"`external: {compact}`"


def render_execution_receipt(receipt: ExecutionReceiptV1) -> str:
    """Render only signed outcome rows; legacy result prose is never read."""
    lines = ["### 실행 결과", ""]
    expected_by_id = {row.target_id: row for row in receipt.expected}
    for outcome in receipt.outcomes:
        expected = expected_by_id[outcome.target_id]
        badge_key = outcome.key if outcome.status == "success" else expected.target_key
        badge = f"{{{{ticket-inline:{badge_key}}}}}" if badge_key else ""
        subject = badge or safe_inline_literal(expected.summary)
        if outcome.status == "success":
            if expected.effects:
                labels = {
                    "assignee": "담당자", "components": "컴포넌트", "description": "설명",
                    "duedate": "마감", "labels": "라벨", "priority": "우선순위",
                    "summary": "제목",
                }
                changes = "; ".join(
                    f"{labels[effect.field]} {safe_inline_literal(effect.display, limit=240)}"
                    for effect in expected.effects
                )
                lines.append(f"- 성공 · {subject} · {changes}")
            else:
                lines.append(f"- 성공 · {subject} · {safe_inline_literal(expected.summary)}")
        else:
            lines.append(f"- 실패 · {subject} · {safe_inline_literal(outcome.error)}")
    if receipt.note:
        lines.extend(["", "참고 · " + safe_inline_literal(receipt.note)])
    return "\n".join(lines)
