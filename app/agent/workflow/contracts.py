"""Agent v2 역할 간 계약. Runtime schema와 문서/테스트의 단일 source of truth."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


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


class RequestedUpdateEffect(StrictModel):
    """One exact scalar mutation grounded to the current user turn by runtime code."""

    target: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$", max_length=32)
    field: Literal["priority", "duedate", "summary"]
    value: str = Field(min_length=1, max_length=240)
    literal: str = Field(min_length=1, max_length=240)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_field_value(self):
        if self.field == "priority" and self.value not in {
                "P0-Blocker", "P1-Critical", "P2-Major", "P3-Minor", "P4-Trivial"}:
            raise ValueError("priority must use one canonical Jira value")
        if self.field == "duedate":
            try:
                parsed = date.fromisoformat(self.value)
            except ValueError as exc:
                raise ValueError("duedate must be one valid ISO date") from exc
            if parsed.isoformat() != self.value:
                raise ValueError("duedate must use YYYY-MM-DD")
        if self.value != self.value.strip() or self.literal != self.literal.strip():
            raise ValueError("requested effect values and literals must be trimmed")
        return self


class RequestPlan(StrictModel):
    goal: str
    tasks: list[AtomicTask]
    request_questions: list[RequestQuestion] = Field(default_factory=list, max_length=3)
    blocking_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    requested_effects: list[RequestedUpdateEffect] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unique_requested_effects(self):
        identities = [(row.target, row.field) for row in self.requested_effects]
        if len(identities) != len(set(identities)):
            raise ValueError("requested effects must be unique by target and field")
        return self


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


class _StrictReceiptModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)


QuestionIdentity = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class QuestionAnswer(_StrictReceiptModel):
    """One opaque question identity and user-authored value on the public wire."""

    question_id: QuestionIdentity
    value: (
        Annotated[str, Field(min_length=1, max_length=1000)]
        | Annotated[
            list[Annotated[str, Field(min_length=1, max_length=240)]],
            Field(min_length=1, max_length=5),
        ]
    )

    @model_validator(mode="after")
    def bounded_literal_value(self):
        values = self.value if isinstance(self.value, list) else [self.value]
        if isinstance(self.value, list) and not (1 <= len(self.value) <= 5):
            raise ValueError("multi answer must contain one to five values")
        if len(values) != len(set(values)):
            raise ValueError("answer values must be unique")
        if any(value != value.strip() or any(ord(char) < 32 for char in value)
               for value in values):
            raise ValueError("answer values must be trimmed control-free literals")
        return self


class QuestionAnswerReceipt(_StrictReceiptModel):
    """Client transport only; field/kind/required authority stays on the server."""

    contract: Literal["question_answer.receipt.v1"]
    challenge_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32,96}$")
    answers: list[QuestionAnswer] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_question_answers(self):
        identities = [row.question_id for row in self.answers]
        if len(identities) != len(set(identities)):
            raise ValueError("question answers must be unique by identity")
        return self


class QuestionChallengeIdentity(_StrictReceiptModel):
    question_id: QuestionIdentity


class QuestionAnswerChallenge(_StrictReceiptModel):
    contract: Literal["question-answer-challenge.v1"] = "question-answer-challenge.v1"
    challenge_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32,96}$")
    questions: list[QuestionChallengeIdentity] = Field(min_length=1, max_length=3)
    expires_at: int = Field(gt=0)


class QuestionReceiptProjectedAnswer(_StrictReceiptModel):
    question_id: QuestionIdentity
    field: Literal["duedate", "phase"]
    value: str = Field(min_length=1, max_length=1000)


class QuestionReceiptProjection(_StrictReceiptModel):
    """Non-secret, turn-derived authority consumed only by RequestArchitect."""

    contract: Literal["question-answer-projection.v1"]
    authority: Literal["session.question-answer-receipt.v1"]
    checkpoint_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    continuation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    answered: list[QuestionReceiptProjectedAnswer] = Field(min_length=1, max_length=3)
    remaining: list[QuestionIdentity] = Field(default_factory=list, max_length=3)
    complete: Literal[True]
    request_refinement: dict[Literal["duedate", "phase"], str]

    @model_validator(mode="after")
    def exact_projector_coverage(self):
        answer_ids = [row.question_id for row in self.answered]
        if len(answer_ids) != len(set(answer_ids)):
            raise ValueError("projected answer identities must be unique")
        if len(self.remaining) != len(set(self.remaining)) or set(answer_ids) & set(self.remaining):
            raise ValueError("remaining identities must be unique and disjoint")
        if self.remaining:
            raise ValueError("a complete projection cannot have remaining questions")
        projected = {row.field: row.value for row in self.answered}
        if len(projected) != len(self.answered) or projected != self.request_refinement:
            raise ValueError("request refinement must exactly equal projected answers")
        due = projected.get("duedate", "")
        if due:
            try:
                parsed_due = date.fromisoformat(due)
            except ValueError as exc:
                raise ValueError("duedate must be one canonical ISO date") from exc
            if parsed_due.isoformat() != due:
                raise ValueError("duedate must be one canonical ISO date")
        phase = projected.get("phase", "")
        ordinal = phase[:-1] if phase.endswith("차") else ""
        if phase and (not ordinal.isdecimal() or not 1 <= int(ordinal) <= 999
                      or phase != f"{int(ordinal)}차"):
            raise ValueError("phase must be one canonical numeric ordinal")
        return self


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


class KnowledgeConcept(_ExtensibleRoleModel):
    term: str = Field(default_factory=str)
    explanation: str = Field(
        default_factory=str,
        description="One concise Korean sentence: what it is and why it matters here.")


class KnowledgeReference(_ExtensibleRoleModel):
    ref: str = Field(
        default_factory=str,
        description="Only a ticket key, document title, or URL in the input.")
    why: str = Field(
        default_factory=str, description="Why this source is worth opening.")


class KnowledgeBrief(_ExtensibleRoleModel):
    model_config = ConfigDict(title="knowledge_curator")
    concepts: list[KnowledgeConcept] = Field(
        description="Two to five concepts required to understand the subject.")
    our_context: str = Field(description=(
        "Verified internal work, decisions, and attempts. Cite a ticket key or document "
        "title for each claim. If absent, write the Korean phrase 사내 이력 없음."))
    references: list[KnowledgeReference] = Field(default_factory=list)
    gaps: list[str] = Field(
        description="Unknown or undecided points and the follow-up verification needed.")


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


ROLE_OUTPUT_MODELS = {
    "request_architect": RequestPlan,
    "query_specialist": QueryPlan,
    "research_analyst": ResearchReport,
    "work_architect": WorkPlan,
    "people_advisor": PeopleAdvice,
    "knowledge_curator": KnowledgeBrief,
    "auditor": AuditResult,
    "result_integrator": IntegratedResult,
}
ROLE_CONTRACTS = ROLE_OUTPUT_MODELS  # compatibility name; do not create a second registry

# Model-facing wire shapes are not necessarily the persisted/runtime state shape.  Query
# Specialist deliberately emits a compact semantic AST and the server compiles it to QueryPlan;
# keeping that distinction explicit prevents a model from authoring ids, paging or compiler
# provenance merely because QueryPlan remains the runtime compatibility contract above.
ROLE_WIRE_MODELS = {
    "query_specialist": CompactQueryPlan,
    "people_advisor": PeopleAdvice,
    "knowledge_curator": KnowledgeBrief,
}
PYDANTIC_WIRE_ROLES = frozenset(ROLE_WIRE_MODELS)
_ROLE_WIRE_ADAPTERS = {
    role_id: TypeAdapter(model) for role_id, model in ROLE_WIRE_MODELS.items()
}


def _role_output_adapter(role_id: str) -> TypeAdapter:
    try:
        return _ROLE_WIRE_ADAPTERS[role_id]
    except KeyError as exc:
        raise ValueError(f"공용 Pydantic wire boundary 미등록 role: {role_id}") from exc


def role_output_schema(role_id: str) -> dict:
    return _role_output_adapter(role_id).json_schema()


def validate_role_output(role_id: str, value: object) -> dict:
    # Pydantic trusts an instance of the target model by default.  A model_construct() or
    # persistence boundary can therefore carry unvalidated nested values despite ``strict``.
    # Class identity is not wire authority: project public model data, then revalidate it.
    candidate = (value.model_dump(exclude_unset=True, warnings="none")
                 if isinstance(value, BaseModel) else value)
    model = _role_output_adapter(role_id).validate_python(candidate, strict=True)
    return model.model_dump(exclude_unset=True)


__all__ = ["ArtifactRef", "AtomicTask", "RequestQuestion", "RequestedUpdateEffect",
           "RequestPlan", "ContinuationDecision",
           "ContinuationContract", "QuestionContract", "QuestionAnswer",
           "QuestionAnswerReceipt", "QuestionAnswerChallenge",
           "QuestionChallengeIdentity", "QuestionReceiptProjectedAnswer",
           "QuestionReceiptProjection", "ResolvedSlot", "QuerySpec", "QueryPlan",
           "QueryIntent", "CompactQueryPlan",
           "ResearchReport", "WorkPlan", "PeopleAdvice", "KnowledgeConcept",
           "KnowledgeReference", "KnowledgeBrief", "AuthoredArtifact",
           "AuditResult", "IntegratedResult", "ROLE_OUTPUT_MODELS", "ROLE_CONTRACTS",
           "ROLE_WIRE_MODELS", "PYDANTIC_WIRE_ROLES",
           "role_output_schema", "validate_role_output"]
