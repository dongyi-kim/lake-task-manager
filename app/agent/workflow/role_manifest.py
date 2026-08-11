"""Lake Task Manager Agent 역할 경계의 단일 manifest.

표시 이름은 역할의 업무 책임을, ``runtime``은 현재 호환 class/node를 뜻한다. 기존 class 이름은
checkpoint와 trace 호환 때문에 유지하지만 새 prompt와 문서는 이 manifest의 역할 이름을 쓴다.
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
    runtime: str
    prompt_asset: str
    model_tier: ModelTier
    purpose: str
    input_keys: tuple[str, ...]
    output_keys: tuple[str, ...]
    tool_groups: tuple[str, ...] = ()
    effect: Effect = "read"


ROLE_SPECS: dict[str, RoleSpec] = {
    "request_architect": RoleSpec(
        "request_architect", "Request Architect", "Planner", "planner.md", "simple",
        "단일·복합 요청을 atomic task DAG와 routing intent로 분해한다.",
        ("messages", "user_identity", "request_plan", "draft", "approval_token"),
        ("intent", "keywords", "module", "mentioned_keys", "request_plan", "request_text"),
    ),
    "query_specialist": RoleSpec(
        "query_specialist", "Query Specialist", "QuerySpecialist", "query_specialist.md", "simple",
        "atomic read task를 typed QueryPlan으로 변환하며 직접 조회하지 않는다.",
        ("request_plan", "keywords", "mentioned_keys", "messages"),
        ("query_plan",),
    ),
    "query_runner": RoleSpec(
        "query_runner", "Query Runner", "QueryRunner", "", "deterministic",
        "QueryPlan을 scope·pagination 계약에 따라 실행하고 전체 결과를 artifact로 보존한다.",
        ("query_plan", "thread_id"), ("query_results", "query_artifacts"),
        ("query", "web"),
    ),
    "research_analyst": RoleSpec(
        "research_analyst", "Research Analyst", "Historian", "historian.md", "complex",
        "내부·외부 자료를 취합해 사실, inference, gap이 구분된 조사 결과를 만든다.",
        ("request_plan", "query_plan", "query_results", "pre_survey", "seed_map",
         "topic_dossier", "web_context"),
        ("situation", "evidence", "related_docs", "epic_candidate", "already_exists"),
        ("search", "web"),
    ),
    "knowledge_curator": RoleSpec(
        "knowledge_curator", "Knowledge Curator", "Curator", "curator.md", "complex",
        "조사 결과를 개념·사내 맥락·출처·공백 구조의 재사용 가능한 브리프로 정리한다.",
        ("situation", "evidence", "related_docs", "web_context", "topic_dossier"),
        ("knowledge_brief",),
    ),
    "portfolio_analyst": RoleSpec(
        "portfolio_analyst", "Portfolio Analyst", "PMO", "pmo.md", "complex",
        "진척·업무량·정체·활동 자료를 PMO 관점의 위험과 우선순위로 해석한다.",
        ("intent", "mentioned_keys", "module", "group_activity", "ticket_progress"),
        ("pmo_findings", "pmo_caution"),
        ("pmo", "people"),
    ),
    "work_architect": RoleSpec(
        "work_architect", "Work Architect · Draft Author", "Refiner", "refiner.md", "complex",
        "조사 결과를 Epic→Task→SubTask 구조와 생성·변경 draft로 변환한다.",
        ("request_text", "situation", "evidence", "query_artifacts", "structure_plan",
         "structure_notes", "draft", "change_plan"),
        ("interpretation", "structure_plan", "structure_ok", "questions", "draft", "change_plan"),
        effect="draft",
    ),
    "people_advisor": RoleSpec(
        "people_advisor", "People Advisor", "Assigner", "assigner.md", "complex",
        "실제 roster·이력·workload 근거로 담당 후보와 대안을 제안한다.",
        ("draft", "evidence", "query_results"), ("assignments",), effect="draft",
    ),
    "auditor": RoleSpec(
        "auditor", "Auditor", "Reviewer", "reviewer.md", "complex",
        "schema·계층·근거·참조·요청 충족을 검사하고 차단 문제와 경고를 분리한다.",
        ("request_text", "request_plan", "draft", "change_plan", "evidence"),
        ("review", "revisions"), ("review",), effect="draft",
    ),
    "action_executor": RoleSpec(
        "action_executor", "Action Executor", "Operator", "operator.md", "deterministic",
        "승인 fingerprint와 정확히 일치하는 write payload만 한 번 실행한다.",
        ("thread_id", "approval_token", "comment_token", "draft", "change_plan"),
        ("result",), ("write",), effect="write",
    ),
    "result_integrator": RoleSpec(
        "result_integrator", "Result Integrator", "Responder", "responder.md", "complex",
        "검증된 결과와 미해결 항목을 하나의 한국어 사용자 답변으로 통합한다.",
        ("request_plan", "situation", "knowledge_brief", "pmo_findings", "draft", "review",
         "approval_token", "result", "error"),
        ("reply",), effect="respond",
    ),
    "editor_author": RoleSpec(
        "editor_author", "Editor Ticket · Comment Author", "compose", "composer.md", "complex",
        "에디터의 기존 본문·ticket context를 보존하며 description 또는 comment 초안을 작성한다.",
        ("ticket_key", "kind", "prompt", "seed_html", "user_id"),
        ("html", "note", "references"), effect="draft",
    ),
}


def role_specs() -> tuple[RoleSpec, ...]:
    return tuple(ROLE_SPECS.values())


__all__ = ["RoleSpec", "ROLE_SPECS", "role_specs"]
