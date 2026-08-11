"""Agent v2 역할 간 계약. Runtime schema와 문서/테스트의 단일 source of truth."""

from __future__ import annotations

from typing import Literal

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


class QuerySpec(StrictModel):
    id: str
    source: Literal["jira", "confluence", "comments", "people", "web", "github"]
    query: str = ""
    where: str = ""
    order_by: str = "updated DESC"
    fields: list[str] = Field(default_factory=list)
    completeness: Literal["page", "all", "count"] = "page"
    page_size: int = Field(default=50, ge=1, le=100)
    depends_on: list[str] = Field(default_factory=list)


class QueryPlan(StrictModel):
    queries: list[QuerySpec]
    joins: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)


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
    "ticket_author": AuthoredArtifact,
    "comment_author": AuthoredArtifact,
    "auditor": AuditResult,
    "result_integrator": IntegratedResult,
}


__all__ = ["ArtifactRef", "AtomicTask", "RequestPlan", "QuerySpec", "QueryPlan",
           "ResearchReport", "WorkPlan", "PeopleAdvice", "AuthoredArtifact",
           "AuditResult", "IntegratedResult", "ROLE_CONTRACTS"]
