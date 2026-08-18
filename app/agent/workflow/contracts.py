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


class RequestQuestion(StrictModel):
    """Request-stage missing slot."""

    question: str = Field(min_length=1, max_length=240)
    field: Literal["target", "action", "scope", "acceptance", "other"]


class RequestPlan(StrictModel):
    goal: str
    tasks: list[AtomicTask]
    request_questions: list[RequestQuestion] = Field(default_factory=list, max_length=3)
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


class QuestionContract(StrictModel):
    """Runtime-owned classification of one user-facing question.

    Semantic projection may suggest a question, but only the runtime decides whether the
    missing slot is owned by the user or can be satisfied by verified retrieval/a reversible
    default.  Keeping both classes in one typed envelope prevents a preference question from
    accidentally becoming a graph blocker after a later normalizer.
    """

    contract: Literal["question.v1"] = "question.v1"
    question: str = Field(min_length=1, max_length=1000)
    kind: Literal["text", "choice", "multi", "date"] = "text"
    options: list[Annotated[str, Field(max_length=240)]] = Field(
        default_factory=list, max_length=5)
    field: str = Field(default="", max_length=120)
    ownership: Literal["user_required", "runtime_optional"]
    required_input: bool
    why_required: str = Field(default="", max_length=500)
    fallback: str = Field(default="", max_length=500)


class ResolvedSlot(StrictModel):
    """One runtime-resolved execution slot with immutable decision provenance.

    A semantic role may request retrieval, but only verified runtime material can populate
    this envelope.  It deliberately carries opaque outcome/item identities instead of prose
    matching so graph routing, Work projection and final-effect sealing share one authority.
    """

    contract: Literal["resolved-slot.v1"] = "resolved-slot.v1"
    field: Literal["parent"]
    outcome_id: str = Field(default="", max_length=120)
    item_id: str = Field(default="", max_length=120)
    request: Literal["select_existing"]
    required: bool
    status: Literal["resolved", "unresolved"]
    value: str = Field(default="", max_length=120)
    resolution: Literal["verified_candidate", "top_level", "unresolved"]
    provenance: Literal[
        "materialized_parent_candidates", "explicit_safe_fallback"
    ]
    evidence: list[Annotated[str, Field(max_length=240)]] = Field(
        default_factory=list, max_length=8)
    decision_digest: str = Field(min_length=64, max_length=64)


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


class _ExtensibleRoleModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class AssignmentAlternative(_ExtensibleRoleModel):
    user: str = Field(default_factory=str)
    why: str = Field(default_factory=str, max_length=180, description="Korean explanation of both evidence and limitation.")


class ChildAssignment(_ExtensibleRoleModel):
    index: int = Field(default_factory=int, description="Zero-based child index within this item.")
    user: str = Field(default_factory=str, description="Jira user id")
    why: str = Field(default_factory=str, max_length=180, description="Korean assignment reason containing a metric or ticket key.")


class AssignmentAdvice(_ExtensibleRoleModel):
    index: int = Field(description="Zero-based draft item index.")
    user: str = Field(description="Jira user ID in skcc.x1042 form; empty if unresolved.")
    reasons: list[Annotated[str, Field(max_length=180)]] = Field(
        min_length=1, max_length=3, description="Korean recommendation reasons grounded in supplied evidence; each includes a metric or ticket key, never a generic suitability claim.")
    alternates: list[AssignmentAlternative] = Field(
        default_factory=list, max_length=2,
        description="One or two alternatives, including why each is not first choice.")
    children: list[ChildAssignment] = Field(
        default_factory=list, max_length=30,
        description="Assignments for each child Sub-Task. Never assign a person rejected for excessive workload; empty when there are no children.")


class PeopleAdvice(_ExtensibleRoleModel):
    model_config = ConfigDict(title="people_advisor")
    assignments: list[AssignmentAdvice] = Field(max_length=30)
    caution: str = Field(default_factory=str, max_length=240, description="Korean assignment caution such as overload or role mismatch; empty when none.")


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


__all__ = ["ArtifactRef", "AtomicTask", "RequestQuestion", "RequestPlan", "ContinuationDecision",
           "ContinuationContract", "QuestionContract", "ResolvedSlot", "QuerySpec", "QueryPlan",
           "QueryIntent", "CompactQueryPlan",
           "ResearchReport", "WorkPlan", "PeopleAdvice", "AuthoredArtifact",
           "AuditResult", "IntegratedResult", "ROLE_CONTRACTS"]
