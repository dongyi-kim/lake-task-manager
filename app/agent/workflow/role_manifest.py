"""Lake Task Manager Agent 역할 경계의 단일 manifest.

역할은 alias 없이 canonical ``id`` 하나로 식별한다. 같은 id가 graph node, Python module,
prompt filename에 그대로 쓰이고, runtime class 이름은 id의 PascalCase 변환이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ModelTier = Literal["simple", "complex", "deterministic"]
Effect = Literal["read", "draft", "write", "respond"]


@dataclass(frozen=True)
class RoleSpec:
    id: str
    name: str
    model_tier: ModelTier
    purpose: str
    input_keys: tuple[str, ...]
    output_keys: tuple[str, ...]
    tool_groups: tuple[str, ...] = ()
    effect: Effect = "read"
    has_prompt: bool = True

    @property
    def prompt_asset(self) -> str:
        """Prompt를 쓰는 역할의 canonical asset 이름. 별도 alias table을 두지 않는다."""
        return f"{self.id}.md" if self.has_prompt else ""


ROLE_SPECS: dict[str, RoleSpec] = {
    "request_architect": RoleSpec(
        "request_architect", "Request Architect", "simple",
        "Decomposes single and compound requests into an atomic task DAG and routing intent.",
        ("messages", "user_identity", "request_plan", "draft", "approval_token"),
        ("intent", "keywords", "module", "mentioned_keys", "request_plan", "request_text"),
    ),
    "query_specialist": RoleSpec(
        "query_specialist", "Query Specialist", "simple",
        "Translates atomic read tasks into a typed QueryPlan without executing retrieval.",
        ("request_plan", "request_text", "keywords", "mentioned_keys", "messages"),
        ("query_plan",),
    ),
    "query_runner": RoleSpec(
        "query_runner", "Query Runner", "deterministic",
        "Executes QueryPlan under scope and pagination contracts and preserves complete artifacts.",
        ("query_plan", "thread_id"),
        ("query_results", "query_artifacts", "assignment_completion"),
        ("query", "web"), has_prompt=False,
    ),
    "research_analyst": RoleSpec(
        "research_analyst", "Research Analyst", "complex",
        "Synthesizes internal and external evidence while separating facts, inference, and gaps.",
        ("messages", "request_text", "request_plan", "query_plan", "query_results",
         "query_artifacts", "pre_survey", "seed_map", "topic_dossier", "web_context"),
        ("situation", "evidence", "related_docs", "epic_candidate", "already_exists"),
        ("search", "web"),
    ),
    "knowledge_curator": RoleSpec(
        "knowledge_curator", "Knowledge Curator", "complex",
        "Curates research into a reusable brief of concepts, internal context, sources, and gaps.",
        ("situation", "evidence", "related_docs", "web_context", "topic_dossier"),
        ("knowledge_brief",),
    ),
    "portfolio_analyst": RoleSpec(
        "portfolio_analyst", "Portfolio Analyst", "complex",
        "Interprets progress, workload, staleness, and activity as PMO risks and priorities.",
        ("messages", "intent", "mentioned_keys", "module", "user_id", "user_role",
         "pre_survey", "query_results", "group_activity", "ticket_progress"),
        ("pmo_findings", "pmo_caution", "person_work_snapshot", "daily_priority_snapshot"),
        ("pmo", "people"),
    ),
    "work_architect": RoleSpec(
        "work_architect", "Work Architect", "complex",
        "Converts verified findings into Epic-to-Task-to-SubTask structures and mutation drafts.",
        ("messages", "request_text", "intent", "mentioned_keys", "situation", "evidence",
         "related_docs", "pre_survey", "query_artifacts", "structure_plan",
         "structure_notes", "draft", "change_plan"),
        ("interpretation", "structure_plan", "structure_ok", "questions", "draft", "change_plan"),
        effect="draft",
    ),
    "people_advisor": RoleSpec(
        "people_advisor", "People Advisor", "complex",
        "Recommends assignment candidates and alternatives from verified roster, history, and workload.",
        ("draft", "evidence", "query_results"), ("assignments",), effect="draft",
    ),
    "auditor": RoleSpec(
        "auditor", "Auditor", "complex",
        "Audits schema, hierarchy, evidence, references, and request coverage; separates errors from warnings.",
        ("request_text", "request_plan", "draft", "change_plan", "evidence"),
        ("review", "revisions"), ("review",), effect="draft",
    ),
    "action_executor": RoleSpec(
        "action_executor", "Action Executor", "deterministic",
        "Executes exactly once only the write payload that matches the approved fingerprint.",
        ("thread_id", "approval_token", "comment_token", "draft", "change_plan"),
        ("result",), ("write",), effect="write",
    ),
    "result_integrator": RoleSpec(
        "result_integrator", "Result Integrator", "complex",
        "Integrates verified results and unresolved items into one Korean user response.",
        ("messages", "request_text", "intent", "answer_depth", "request_plan", "situation",
         "evidence", "related_docs", "pre_survey", "topic_dossier", "knowledge_brief",
         "pmo_findings", "pmo_caution", "group_activity", "ticket_progress",
         "person_work_snapshot", "daily_priority_snapshot", "interpretation", "questions",
         "draft", "assignments", "change_plan", "review", "approval_token", "result", "error"),
        ("reply",), effect="respond",
    ),
    "editor_author": RoleSpec(
        "editor_author", "Editor Author", "complex",
        "Drafts a description or comment while preserving existing editor content and ticket context.",
        ("ticket_key", "kind", "prompt", "seed_html", "user_id"),
        ("html", "note", "references"), effect="draft",
    ),
}


def role_specs() -> tuple[RoleSpec, ...]:
    return tuple(ROLE_SPECS.values())


__all__ = ["RoleSpec", "ROLE_SPECS", "role_specs"]
