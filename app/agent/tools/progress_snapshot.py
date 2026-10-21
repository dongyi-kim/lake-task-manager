"""Internal acquisition metadata, not evidence authority or a freshness certificate.

One collector per call; only the calling thread publishes worker-local observations.
Pending means no outcome collected, not that no service call has started. Returned
means the local function returned, never that the provider supplied complete data.
Raw source dates remain source dates; no wall-clock timestamp is substituted.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

State = Literal["pending", "returned", "failed", "skipped"]
POLICIES = {
    "target": {},
    "changes": {"rows": "last 8"},
    "comments": {"rows": "service comment_limit; reversed", "text_chars": 400},
    "children": {"rows": "all returned; conditional badge lookup"},
    "links": {"rows": "first 8, then require key"},
    "documents": {"rows": "first 3", "text_chars": 900, "text": "whitespace-normalized"},
}
FIELDS = {
    "ticket_field_history": "id date author field from to",
    "issue_comments": "id date created updated author authorId",
    "ticket_children": "id key type issuetype parent statusCat statusCategory updated",
    "ticket_related": "id key type rel updated",
    "ticket_documents": "id linkId space url title updated",
    "ticket_badge": "id key type issuetype updated assigneeId",
    "confluence_page": "id type space version history",
}


def _pick(value, keys):
    return deepcopy({k: value[k] for k in keys.split() if k in value})


def _metadata(operation, value):
    if operation == "get_issue" and isinstance(value, dict):
        result = _pick(value, "id key")
        if isinstance(value.get("fields"), dict):
            result["fields"] = _pick(value["fields"], "issuetype parent created updated duedate")
        return result
    keys = FIELDS.get(operation, "")
    if isinstance(value, list):
        return [_pick(row, keys) if isinstance(row, dict) else None for row in value]
    return _pick(value, keys) if isinstance(value, dict) else None


@dataclass
class ReadObservation:
    operation: str
    arguments: tuple
    state: State
    metadata: Any = None
    returned_type: str | None = None
    returned_count: int | None = None
    error_type: str | None = None


@dataclass
class BranchObservation:
    state: State = "pending"
    reads: list[ReadObservation] = field(default_factory=list)
    projection_policy: dict = field(default_factory=dict)
    projected_count: int | None = None
    reason: str | None = None
    error_type: str | None = None


@dataclass
class ProgressAcquisition:
    requested_key: str | None = None
    branches: dict[str, BranchObservation] = field(default_factory=lambda: {
        name: BranchObservation(projection_policy=deepcopy(policy))
        for name, policy in POLICIES.items()})
    started: bool = False
    finished: bool = False
    provider_coverage: Literal["unknown"] = "unknown"
    cache_provenance: Literal["unknown"] = "unknown"
    source_freshness: Literal["unknown"] = "unknown"

    def begin(self, key):
        if self.started:
            raise ValueError("ProgressAcquisition is single-use")
        self.started, self.requested_key = True, key

    def stop(self, reason):
        for branch in self.branches.values():
            if branch.state == "pending":
                branch.state, branch.reason = "skipped", reason
        self.finished = True


def observe(branch, operation, call, *arguments):
    """Invoke exactly once. Metadata failure cannot replace the service result."""
    if branch is None:
        return call(*arguments)
    try:
        value = call(*arguments)
    except Exception as exc:
        branch.reads.append(ReadObservation(operation, arguments, "failed", error_type=type(exc).__name__))
        raise
    observation = ReadObservation(operation, arguments, "returned", returned_type=type(value).__name__,
                                  returned_count=len(value) if isinstance(value, list) else None)
    try:
        observation.metadata = _metadata(operation, value)
    except Exception as exc:
        observation.error_type = type(exc).__name__
    branch.reads.append(observation)
    return value


class ObservedFailure(Exception):
    """Worker-local observation follows the original map cancellation path."""
    def __init__(self, observation):
        super().__init__(observation.error_type)
        self.observation = observation


def run_branch(name, fn, capture, *, reraise=False):
    branch = BranchObservation(projection_policy=deepcopy(POLICIES[name])) if capture else None
    try:
        value = fn(branch)
    except Exception as exc:
        if branch is None:
            raise
        branch.state, branch.error_type = "failed", type(exc).__name__
        if reraise:
            raise ObservedFailure(branch) from exc
        return None, branch
    if branch is not None:
        if branch.state != "failed":
            branch.state = "returned"
            branch.projected_count = len(value[0] if name == "children" else value)
    return value, branch
