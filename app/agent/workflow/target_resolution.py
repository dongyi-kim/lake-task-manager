"""Typed relative mutation targets resolved by complete Jira relationship snapshots.

``ContinuationContract.target_keys`` deliberately represents exact user-written Jira keys.
That is insufficient for requests such as "the one remaining direct child under DL-9090":
the written key is an anchor, not the mutation target.  This module keeps those two identity
classes separate.  RequestArchitect can emit one bounded selector, QuerySpecialist compiles a
server-only read, QueryRunner binds the complete child snapshot to a signed receipt, and every
mutation consumer reads the same resolved target set.

No prose is reparsed after the RequestPlan boundary and no receipt can mint a Jira identity that
was absent from the provider-owned direct-child snapshot.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.agent.workflow.canonical_digest import digest_value
from app.agent.workflow.contracts import TargetSelector
from app.agent.workflow.state import request_text


TARGET_RESOLUTION_ARTIFACT = "target-resolution.v1"
TARGET_RESOLUTION_AUTHORITY = "query-runner.direct-child-resolution.v1"

_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d+$")
_KEY_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9-])([A-Za-z][A-Za-z0-9]{1,9}-\d+)(?![A-Za-z0-9-])")
_DIGEST = r"^[0-9a-f]{64}$"
_SIGNING_KEY = os.urandom(32)


class _StrictFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DirectChildV1(_StrictFrozen):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$", max_length=32)
    summary: str = Field(min_length=1, max_length=1000)
    status: str = Field(min_length=1, max_length=240)
    statusCategory: Literal["todo", "inprogress", "done"]
    type: str = Field(min_length=1, max_length=120)
    parentKey: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$", max_length=32)
    assignee: str | None = Field(default=None, max_length=240)
    assigneeId: str | None = Field(default=None, max_length=240)
    updated: str | None = Field(default=None, max_length=100)


class DirectChildrenSnapshotV1(_StrictFrozen):
    contract: Literal["jira-direct-children-snapshot.v1"]
    parentKey: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$", max_length=32)
    parentType: str = Field(min_length=1, max_length=120)
    children: tuple[DirectChildV1, ...] = Field(max_length=100)
    expectedKeys: tuple[
        Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$")], ...
    ] = Field(max_length=100)
    returned: int = Field(ge=0, le=100)
    total: int = Field(ge=0, le=100)
    complete: bool
    remaining: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def exact_coverage(self):
        child_keys = tuple(row.key for row in self.children)
        if child_keys != self.expectedKeys or len(child_keys) != len(set(child_keys)):
            raise ValueError("child snapshot identities must be exact, ordered, and unique")
        if any(row.parentKey != self.parentKey for row in self.children):
            raise ValueError("every child must bind to the exact parent")
        if self.returned != len(self.children) or self.total != len(self.expectedKeys):
            raise ValueError("child snapshot cardinality mismatch")
        if self.complete:
            if self.returned != self.total or self.remaining != 0:
                raise ValueError("complete child snapshot must cover the full provider set")
        elif self.remaining == 0:
            raise ValueError("incomplete child snapshot cannot claim zero remaining")
        return self


class TargetResolutionReceiptV1(_StrictFrozen):
    contract: Literal["target-resolution-receipt.v1"] = "target-resolution-receipt.v1"
    authority: Literal["query-runner.direct-child-resolution.v1"] = TARGET_RESOLUTION_AUTHORITY
    selector_id: str = Field(min_length=1, max_length=60)
    anchor_key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$", max_length=32)
    attempt_digest: str = Field(pattern=_DIGEST)
    thread_digest: str = Field(pattern=_DIGEST)
    request_plan_digest: str = Field(pattern=_DIGEST)
    query_plan_digest: str = Field(pattern=_DIGEST)
    selector_digest: str = Field(pattern=_DIGEST)
    snapshot_digest: str = Field(pattern=_DIGEST)
    result_digest: str = Field(pattern=_DIGEST)
    resolved_keys: tuple[
        Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$")], ...
    ] = Field(max_length=1)
    complete: bool
    signature: str = Field(pattern=_DIGEST)

    @model_validator(mode="after")
    def exact_resolution(self):
        if len(self.resolved_keys) != len(set(self.resolved_keys)):
            raise ValueError("resolved target keys must be unique")
        if self.complete is not (len(self.resolved_keys) == 1):
            raise ValueError("exactly-one resolution completeness mismatch")
        return self


_SELECTOR_ADAPTER = TypeAdapter(TargetSelector)
_SNAPSHOT_ADAPTER = TypeAdapter(DirectChildrenSnapshotV1)
_RECEIPT_ADAPTER = TypeAdapter(TargetResolutionReceiptV1)


def _boundary_digest(boundary: str) -> str:
    return digest_value({"request_text": str(boundary or "").strip()})


def _boundary_keys(boundary: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _KEY_IN_TEXT.finditer(str(boundary or "")):
        key = match.group(1).upper()
        if key not in found:
            found.append(key)
    return tuple(found)


def _write_tasks(tasks) -> list[dict]:
    return [row for row in (tasks or []) if isinstance(row, dict) and (
        row.get("write_intent") is True
        or str(row.get("kind") or "").strip().casefold() in {
            "plan", "ticket", "comment", "write", "modify",
        }
    )]


def ground_target_selectors(value, tasks, boundary: str) -> list[dict]:
    """Bind one model selector to one current write task and one written anchor.

    Multi-write selector mapping remains fail-closed until RequestPlan carries an explicit
    per-effect target contract.  This avoids converting a useful read relation into authority
    for a sibling mutation merely because both tasks mention the same anchor.
    """
    rows = list(value or []) if isinstance(value, (list, tuple)) else []
    writes = _write_tasks(tasks)
    keys = _boundary_keys(boundary)
    if len(rows) != 1 or len(writes) != 1 or not keys:
        return []
    raw = rows[0]
    if not isinstance(raw, dict):
        return []
    candidate = {
        "contract": "target-selector.v1",
        "task_id": raw.get("task_id"),
        "anchor_key": str(raw.get("anchor_key") or "").strip().upper(),
        "relation": raw.get("relation"),
        "state": raw.get("state"),
        "cardinality": raw.get("cardinality"),
        "source_digest": _boundary_digest(boundary),
    }
    try:
        selector = _SELECTOR_ADAPTER.validate_python(candidate, strict=True)
    except Exception:
        return []
    if selector.task_id != str(writes[0].get("id") or ""):
        return []
    if selector.anchor_key not in keys:
        return []
    return [selector.model_dump(mode="python")]


def current_target_selectors(state) -> tuple[TargetSelector, ...]:
    plan = state.get("request_plan") or {}
    if not isinstance(plan, dict):
        return ()
    raw = plan.get("target_selectors") or []
    writes = _write_tasks(plan.get("tasks") or [])
    boundary = request_text(state).strip() or str(state.get("request_text") or "").strip()
    if not raw:
        return ()
    if len(raw) != 1 or len(writes) != 1 or not boundary:
        return ()
    try:
        selector = _SELECTOR_ADAPTER.validate_python(raw[0], strict=True)
    except Exception:
        return ()
    if (selector.task_id != str(writes[0].get("id") or "")
            or selector.source_digest != _boundary_digest(boundary)
            or selector.anchor_key not in _boundary_keys(boundary)):
        return ()
    return (selector,)


def target_resolution_requested(state) -> bool:
    plan = state.get("request_plan") or {}
    return isinstance(plan, dict) and bool(plan.get("target_selectors"))


def selector_query_specs(state) -> list[dict]:
    return [{
        "id": f"target-resolution-{row.task_id}",
        "source": "jira",
        "query": "",
        "where": f"parent = {row.anchor_key}",
        "order_by": "",
        "fields": ["key", "summary", "status", "issuetype", "assignee", "updated"],
        "completeness": "all",
        "page_size": 100,
        "depends_on": [],
        "target_selector_id": row.task_id,
    } for row in current_target_selectors(state)]


def _selector_spec(state, selector: TargetSelector) -> dict | None:
    expected = selector_query_specs(state)
    if len(expected) != 1:
        return None
    matches = [row for row in ((state.get("query_plan") or {}).get("queries") or [])
               if isinstance(row, dict)
               and str(row.get("target_selector_id") or "") == selector.task_id]
    return matches[0] if len(matches) == 1 and matches[0] == expected[0] else None


def _normalize_snapshot(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    row = dict(value)
    if isinstance(row.get("children"), list):
        row["children"] = tuple(row["children"])
    if isinstance(row.get("expectedKeys"), list):
        row["expectedKeys"] = tuple(row["expectedKeys"])
    return row


def _validate_snapshot(value, selector: TargetSelector) -> DirectChildrenSnapshotV1 | None:
    try:
        snapshot = _SNAPSHOT_ADAPTER.validate_python(_normalize_snapshot(value), strict=True)
    except Exception:
        return None
    return snapshot if snapshot.parentKey == selector.anchor_key else None


def _resolution_result(selector: TargetSelector, snapshot: DirectChildrenSnapshotV1 | None) -> dict:
    children = [row.model_dump(mode="python") for row in snapshot.children] if snapshot else []
    resolved = [row.key for row in (snapshot.children if snapshot else ())
                if row.statusCategory != "done"]
    complete = bool(snapshot and snapshot.complete and len(resolved) == 1)
    return {
        "tickets": children,
        "returned": len(children),
        "total": snapshot.total if snapshot else None,
        "complete": complete,
        **({"incomplete": True,
            "incompleteReason": (
                "snapshot_invalid" if snapshot is None
                else "relationship_coverage_incomplete" if not snapshot.complete
                else "target_cardinality_mismatch"
            )} if not complete else {}),
        "targetResolution": {
            "contract": "target-resolution-result.v1",
            "selectorId": selector.task_id,
            "anchorKey": selector.anchor_key,
            "candidateKeys": [row.key for row in (snapshot.children if snapshot else ())],
            "resolvedKeys": resolved if complete else [],
            "complete": complete,
        },
    }


def _result_authority_projection(value) -> dict:
    """Bind only producer-owned resolution fields, not later materialization overlays."""
    if not isinstance(value, dict):
        return {}
    return {field: value.get(field) for field in (
        "tickets", "returned", "total", "complete", "incomplete", "incompleteReason",
        "targetResolution",
    ) if field in value}


def _unsigned(value: TargetResolutionReceiptV1 | dict) -> dict:
    row = value.model_dump(mode="json") if isinstance(value, TargetResolutionReceiptV1) \
        else dict(value)
    row.pop("signature", None)
    return row


def _signature(value: dict) -> str:
    return hmac.new(
        _SIGNING_KEY, digest_value(value).encode("ascii"), hashlib.sha256,
    ).hexdigest()


def build_target_resolution_result(state, query_spec, snapshot_value) -> tuple[dict, dict | None]:
    """Project one snapshot and mint a receipt bound to current plan/result identity."""
    selectors = current_target_selectors(state)
    selector_id = str((query_spec or {}).get("target_selector_id") or "")
    selector = next((row for row in selectors if row.task_id == selector_id), None)
    if selector is None or _selector_spec(state, selector) != query_spec:
        return {
            "tickets": [], "returned": 0, "total": None, "complete": False,
            "incomplete": True, "incompleteReason": "selector_query_unbound",
        }, None
    snapshot = _validate_snapshot(snapshot_value, selector)
    result = _resolution_result(selector, snapshot)
    if snapshot is None:
        return result, None
    # A process-local signature is not a turn boundary by itself.  Refuse to mint even an
    # incomplete receipt unless Session supplied both durable identities; otherwise the same
    # anonymous state shape could be replayed across direct callers/checkpoints.
    attempt_id = str(state.get("turn_attempt_id") or "").strip()
    thread_id = str(state.get("thread_id") or "").strip()
    if not attempt_id or not thread_id:
        return {**result, "complete": False, "incomplete": True,
                "incompleteReason": "turn_identity_missing"}, None
    try:
        resolved = tuple(result["targetResolution"]["resolvedKeys"])
        unsigned = {
            "contract": "target-resolution-receipt.v1",
            "authority": TARGET_RESOLUTION_AUTHORITY,
            "selector_id": selector.task_id,
            "anchor_key": selector.anchor_key,
            "attempt_digest": digest_value({
                "turn_attempt_id": attempt_id,
            }),
            "thread_digest": digest_value({
                "thread_id": thread_id,
            }),
            "request_plan_digest": digest_value(state.get("request_plan")),
            "query_plan_digest": digest_value(state.get("query_plan")),
            "selector_digest": digest_value(selector.model_dump(mode="json")),
            "snapshot_digest": digest_value(snapshot.model_dump(mode="json")),
            "result_digest": digest_value(_result_authority_projection(result)),
            "resolved_keys": resolved,
            "complete": result["complete"],
        }
        receipt = TargetResolutionReceiptV1(
            **unsigned,
            signature=_signature({**unsigned, "resolved_keys": list(resolved)}),
        )
        return result, {
            "receipt": receipt.model_dump(mode="python"),
            "snapshot": snapshot.model_dump(mode="python"),
        }
    except Exception:
        return {**result, "complete": False, "incomplete": True,
                "incompleteReason": "receipt_issue_failed"}, None


def _normalize_receipt(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    row = dict(value)
    if isinstance(row.get("resolved_keys"), list):
        row["resolved_keys"] = tuple(row["resolved_keys"])
    return row


def _receipt_for_selector(state, selector: TargetSelector) -> TargetResolutionReceiptV1 | None:
    try:
        artifact_root = (state.get("query_artifacts") or {}).get(TARGET_RESOLUTION_ARTIFACT)
        artifact = artifact_root.get(selector.task_id) if isinstance(artifact_root, dict) else None
        if not isinstance(artifact, dict):
            return None
        receipt = _RECEIPT_ADAPTER.validate_python(
            _normalize_receipt(artifact.get("receipt")), strict=True,
        )
        if not hmac.compare_digest(receipt.signature, _signature(_unsigned(receipt))):
            return None
        if (receipt.selector_id != selector.task_id
                or receipt.anchor_key != selector.anchor_key
                or receipt.attempt_digest != digest_value({
                    "turn_attempt_id": str(state.get("turn_attempt_id") or "").strip(),
                })
                or receipt.thread_digest != digest_value({
                    "thread_id": str(state.get("thread_id") or "").strip(),
                })
                or receipt.request_plan_digest != digest_value(state.get("request_plan"))
                or receipt.query_plan_digest != digest_value(state.get("query_plan"))
                or receipt.selector_digest != digest_value(selector.model_dump(mode="json"))):
            return None
        snapshot = _validate_snapshot(artifact.get("snapshot"), selector)
        if snapshot is None or receipt.snapshot_digest != digest_value(
                snapshot.model_dump(mode="json")):
            return None
        spec = _selector_spec(state, selector)
        if spec is None:
            return None
        rows = [row for row in (state.get("query_results") or []) if isinstance(row, dict)
                and str(row.get("id") or "") == str(spec.get("id") or "")
                and row.get("source") == "jira" and isinstance(row.get("result"), dict)]
        if (len(rows) != 1 or receipt.result_digest != digest_value(
                _result_authority_projection(rows[0]["result"]))):
            return None
        expected = _resolution_result(selector, snapshot)
        if digest_value(_result_authority_projection(expected)) != receipt.result_digest:
            return None
        return receipt if receipt.complete else None
    except Exception:
        return None


def resolved_target_keys(state) -> tuple[str, ...]:
    selectors = current_target_selectors(state)
    if not selectors:
        return ()
    receipts = [_receipt_for_selector(state, selector) for selector in selectors]
    if any(receipt is None for receipt in receipts):
        return ()
    keys = tuple(key for receipt in receipts for key in receipt.resolved_keys)
    return keys if len(keys) == len(set(keys)) else ()


def authoritative_mutation_targets(state) -> tuple[str, ...]:
    """Return the sole mutation target authority consumed by Work/Research/Auditor."""
    if target_resolution_requested(state):
        return resolved_target_keys(state)
    contract = state.get("continuation_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != "continuation.v1":
        return ()
    keys = tuple(
        str(value or "").strip().upper()
        for value in (contract.get("target_keys") or [])
        if _KEY.fullmatch(str(value or "").strip().upper())
    )
    return keys if len(keys) == len(set(keys)) else ()


__all__ = [
    "TARGET_RESOLUTION_ARTIFACT", "TARGET_RESOLUTION_AUTHORITY",
    "DirectChildV1", "DirectChildrenSnapshotV1", "TargetResolutionReceiptV1",
    "authoritative_mutation_targets", "build_target_resolution_result",
    "current_target_selectors", "ground_target_selectors", "resolved_target_keys",
    "selector_query_specs", "target_resolution_requested",
]
