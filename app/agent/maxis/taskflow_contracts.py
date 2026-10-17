"""Versioned research-to-draft protocol; never a Jira execution capability."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.agent.maxis.context_guard import canonical_evidence_pack_json
from app.agent.maxis.contracts import (
    CompactQueryPlanV1, DraftQuestionV1, EvidencePackV1, RemoteRoleResultV1,
    StrictFrozenModel, WorkDraftV1,
)
from app.agent.maxis.public_metadata import require_safe_public_text

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]


class TaskFlowPinV1(StrictFrozenModel):
    """All nested deployments are bound, not just a mutable parent app name."""

    profile_fingerprint: Identifier
    workflow_release: Identifier
    workflow_sha256: Digest
    research_release: Identifier
    research_sha256: Digest
    knowledge_manifest_sha256: Digest

    @model_validator(mode="after")
    def validate_labels(self):
        for value in (self.profile_fingerprint, self.workflow_release, self.research_release):
            require_safe_public_text(value, "taskflow release label")
        return self


def evidence_digest(pack: EvidencePackV1) -> str:
    return hashlib.sha256(canonical_evidence_pack_json(pack).encode("utf-8")).hexdigest()


class TaskFlowRequestV1(StrictFrozenModel):
    contract: Literal["taskflow-request.v1"] = "taskflow-request.v1"
    request_id: Identifier
    revision: int = Field(ge=0, le=2)
    pin: TaskFlowPinV1
    evidence: EvidencePackV1
    evidence_sha256: Digest

    @model_validator(mode="after")
    def validate_binding(self):
        require_safe_public_text(self.request_id, "taskflow request id")
        if self.evidence.request_id != self.request_id:
            raise ValueError("request identity mismatch")
        if self.evidence_sha256 != evidence_digest(self.evidence):
            raise ValueError("evidence digest mismatch")
        if self.evidence.knowledge_manifest_sha256 != self.pin.knowledge_manifest_sha256:
            raise ValueError("knowledge release mismatch")
        return self


class TaskFlowResultV1(StrictFrozenModel):
    """Recovery control is separate from the legacy non-ok empty-payload contract.

    Source-backed partial text is deliberately not a free-text escape hatch. Until
    a separately validated finding contract is deployed, recovery returns questions
    and a bounded reason only. Completed results still pass the existing Role guard.
    """

    contract: Literal["taskflow-result.v1"] = "taskflow-result.v1"
    request_id: Identifier
    revision: int = Field(ge=0, le=2)
    pin: TaskFlowPinV1
    evidence_sha256: Digest
    status: Literal["completed", "needs_evidence", "needs_input", "blocked"]
    query_plan: CompactQueryPlanV1 | None = None
    questions: tuple[DraftQuestionV1, ...] = Field(default=(), max_length=3)
    draft_result: RemoteRoleResultV1 | None = None
    reason: Literal[
        "", "insufficient_evidence", "source_conflict", "incomplete_coverage",
        "budget_exhausted", "access_denied", "release_changed", "invalid_response",
        "cancelled", "transport_error", "input_required", "deployment_unverified",
    ] = ""

    @model_validator(mode="after")
    def validate_outcome(self):
        require_safe_public_text(self.request_id, "taskflow request id")
        if self.status == "completed":
            if (self.draft_result is None or self.draft_result.status != "ok"
                    or self.draft_result.role_id != "work_architect"):
                raise ValueError("completion requires a successful work draft envelope")
            if self.draft_result.workflow_version != self.pin.workflow_release:
                raise ValueError("draft workflow release mismatch")
            draft = WorkDraftV1.model_validate_json(json.dumps(self.draft_result.payload))
            if not draft.items or any(q.required_input for q in draft.questions):
                raise ValueError("completion cannot contain an incomplete work draft")
            if self.query_plan is not None or self.questions or self.reason:
                raise ValueError("completion cannot request more work")
        elif self.draft_result is not None:
            raise ValueError("incomplete outcomes cannot carry an executable draft")
        if self.status == "needs_evidence":
            if not self.query_plan or not self.query_plan.reads or self.questions:
                raise ValueError("additional evidence requires one nonempty query plan")
            if any(r.source not in {"jira", "confluence", "comments"}
                   for r in self.query_plan.reads):
                raise ValueError("source class has no taskflow authority contract")
        elif self.query_plan is not None:
            raise ValueError("query plan only belongs to needs_evidence")
        if self.status == "needs_input":
            if not self.questions or not all(q.required_input for q in self.questions):
                raise ValueError("input request needs concrete required questions")
        elif self.questions:
            raise ValueError("questions only belong to needs_input")
        if self.status != "completed" and not self.reason:
            raise ValueError("incomplete outcome requires a reason")
        return self
