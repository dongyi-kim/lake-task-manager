"""Agent v2 역할 간 계약. Runtime schema와 문서/테스트의 단일 source of truth."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactRef(StrictModel):
    id: str
    kind: Literal["ticket", "person", "document", "external"]
    key: str = ""
    user_id: str = ""
    page_id: str = ""
    url: str = ""
    label: str = ""


class AtomicTask(StrictModel):
    id: str
    kind: Literal["query", "research", "analyze", "plan", "ticket", "comment", "write", "respond"]
    instruction: str
    depends_on: list[str] = Field(default_factory=list)
    write_intent: bool = False
    completion_criteria: list[str] = Field(default_factory=list)


class RequestPlan(StrictModel):
    goal: str
    tasks: list[AtomicTask]
    blocking_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ContinuationDecision(StrictModel):
    """One user-owned answer/refinement captured at a typed turn boundary."""

    field: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=1000)
    source: Literal["interview_answer", "explicit_refinement"]


class ContinuationContract(StrictModel):
    """Bounded authority that may cross a conversational interview turn.

    ``request_plan`` remains the sole outcome DAG.  This contract records its stable root,
    action family and outcome ids plus only user-authored field decisions, so a short answer
    cannot silently become a new request or force downstream roles to replay arbitrary history.
    """

    version: Literal["continuation.v1"] = "continuation.v1"
    root_request: str = Field(min_length=1, max_length=12000)
    intent: Literal[
        "ask", "plan_work", "my_day", "progress", "activity", "modify", "chitchat"
    ]
    action: Literal["read", "create", "comment", "update", "mixed", "respond"]
    target_keys: list[Annotated[str, Field(max_length=32)]] = Field(
        default_factory=list, max_length=16)
    outcome_ids: list[Annotated[str, Field(max_length=80)]] = Field(
        default_factory=list, max_length=6)
    decisions: list[ContinuationDecision] = Field(default_factory=list, max_length=16)


class QuerySpec(StrictModel):
    id: str
    source: Literal["jira", "confluence", "comments", "people", "web", "github"]
    query: str = ""
    where: str = ""
    order_by: str = "updated DESC"
    fields: list[str] = Field(default_factory=list)
    completeness: Literal["page", "all", "count"] = "page"
    page_size: int = Field(default=50, ge=1, le=100)
    # Reserved for persisted legacy plans. QueryRunner currently executes independent reads;
    # non-empty dependencies are rejected rather than silently ignored.
    depends_on: list[str] = Field(default_factory=list)


class QueryPlan(StrictModel):
    queries: list[QuerySpec]
    # Reserved legacy field; non-empty joins are unsupported and fail before execution.
    joins: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    # Runtime compiler provenance. CompactQueryPlan deliberately cannot emit this field;
    # QuerySpecialist strips it from legacy/runtime inputs and sets it only after applying
    # deterministic creation-subject guards.
    compiler_guard: Literal["creation_target_required"] | None = None


class QueryIntent(StrictModel):
    # Model-facing semantic instruction. Operational IDs, projection, ordering and
    # pagination remain compiler-owned. Cross-read dependencies are not supported.
    source: Literal["jira", "confluence", "comments", "people", "web", "github"]
    subject: str = Field(default="", max_length=240)
    where: str = Field(default="", max_length=240)
    exhaustive: bool = False


class CompactQueryPlan(StrictModel):
    # Model-facing AST; Query Specialist compiles this to the runtime QueryPlan.
    reads: list[QueryIntent] = Field(default_factory=list, max_length=8)
    uncertainty: list[Annotated[str, Field(max_length=240)]] = Field(
        default_factory=list, max_length=8)


class Claim(StrictModel):
    text: str
    reference_ids: list[str] = Field(default_factory=list)
    inference: bool = False


class ResearchReport(StrictModel):
    executive_summary: str
    internal_findings: list[Claim] = Field(default_factory=list)
    external_findings: list[Claim] = Field(default_factory=list)
    recommendations: list[Claim] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    references: list[ArtifactRef] = Field(default_factory=list)


class WorkItem(StrictModel):
    temp_id: str
    tier: Literal["epic", "task", "subtask"]
    issue_type: str
    parent_ref: str = ""
    summary: str
    components: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = ""


class WorkPlan(StrictModel):
    destination_project: str
    items: list[WorkItem]
    questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class AssignmentAdvice(StrictModel):
    temp_id: str
    primary_user_id: str = ""
    candidate_user_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    evidence_reference_ids: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class PeopleAdvice(StrictModel):
    assignments: list[AssignmentAdvice]


class AuthoredArtifact(StrictModel):
    target_id: str
    summary: str = ""
    mode: str = ""
    content_template: str
    definition_of_done: list[str] = Field(default_factory=list)
    references: list[ArtifactRef] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


class AuditResult(StrictModel):
    ok: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)
    grounding_ok: bool
    schema_ok: bool
    references_ok: bool


class IntegratedResult(StrictModel):
    answer_template: str
    references: list[ArtifactRef] = Field(default_factory=list)
    pending_actions: list[str] = Field(default_factory=list)
    blocking_questions: list[str] = Field(default_factory=list)


ROLE_CONTRACTS = {
    "request_architect": RequestPlan,
    "query_specialist": QueryPlan,
    "research_analyst": ResearchReport,
    "work_architect": WorkPlan,
    "people_advisor": PeopleAdvice,
    "auditor": AuditResult,
    "result_integrator": IntegratedResult,
}


__all__ = ["ArtifactRef", "AtomicTask", "RequestPlan", "ContinuationDecision",
           "ContinuationContract", "QuerySpec", "QueryPlan",
           "QueryIntent", "CompactQueryPlan",
           "ResearchReport", "WorkPlan", "PeopleAdvice", "AuthoredArtifact",
           "AuditResult", "IntegratedResult", "ROLE_CONTRACTS"]
