"""Lake Task Manager Agent 역할 경계의 단일 manifest.

역할은 alias 없이 canonical ``id`` 하나로 식별한다. 같은 id가 graph node, Python module,
prompt filename에 그대로 쓰이고, runtime class 이름은 id의 PascalCase 변환이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from app.agent.model_profiles import ExecutionLayer, TaskProfile


ModelTier = Literal["simple", "complex", "deterministic"]
Effect = Literal["read", "draft", "write", "respond"]
SemanticContract = Literal["direct", "semantic_projection"]
RoleKind = Literal["semantic", "service", "guardrail"]


@dataclass(frozen=True)
class RoleSpec:
    id: str
    name: str
    model_tier: ModelTier
    task_profile: TaskProfile
    purpose: str
    input_keys: tuple[str, ...]
    output_keys: tuple[str, ...]
    tool_groups: tuple[str, ...] = ()
    effect: Effect = "read"
    has_prompt: bool = True
    # ``semantic_projection`` keeps semantic judgment on the Role's complex model and
    # delegates only the final typed projection when the model profile declares a
    # projection tier.  It is opt-in so each Role can be characterized before migration.
    semantic_contract: SemanticContract = "direct"
    # ``semantic`` delegates judgment to a model; ``service`` executes a typed deterministic
    # operation; ``guardrail`` accepts/rejects an artifact without authoring or executing it.
    # Defaults remain after required fields so existing positional RoleSpec construction stays compatible.
    kind: RoleKind = "semantic"
    # Runtime model routing source of truth. ``model_tier`` remains as the conservative
    # fallback/API compatibility field; qualified fast lanes are resolved from model profiles.
    execution_layer: ExecutionLayer = "deep_semantic"
    # ToolAgent may use a cheaper qualified lane to choose tools while keeping evidence
    # synthesis on ``execution_layer``. None means the main layer is used for both stages.
    decision_layer: ExecutionLayer | None = None

    def __post_init__(self) -> None:
        expected = "deterministic" if self.execution_layer == "deterministic" else "complex"
        if self.model_tier != expected:
            raise ValueError(
                f"Role {self.id} model_tier={self.model_tier!r} drifts from "
                f"execution_layer={self.execution_layer!r}; safe fallback must be {expected!r}."
            )
        if self.kind == "service" and self.execution_layer != "deterministic":
            raise ValueError(f"Service Role {self.id} must be deterministic.")

    @property
    def prompt_asset(self) -> str:
        """Prompt를 쓰는 역할의 canonical asset 이름. 별도 alias table을 두지 않는다."""
        return f"{self.id}.md" if self.has_prompt else ""


ROLE_SPECS: dict[str, RoleSpec] = {
    "request_architect": RoleSpec(
        "request_architect", "Request Architect", "complex", "fast_structured",
        "Decomposes single and compound requests into an atomic task DAG and routing intent.",
        ("messages", "user_identity", "intent", "request_text", "request_plan", "playbook",
         "keywords", "module", "mentioned_keys", "sufficient", "answer_depth",
         "turn_continuation", "situation", "evidence", "materialized_ticket_sources",
         "structure_plan", "draft", "questions", "approval_token", "continuation_contract"),
        ("intent", "keywords", "module", "mentioned_keys", "sufficient", "playbook",
         "answer_depth", "request_plan", "request_refinement", "request_text", "questions", "trace",
         "continuation_contract"),
        execution_layer="lightweight_semantic",
    ),
    "query_specialist": RoleSpec(
        "query_specialist", "Query Specialist", "complex", "fast_structured",
        "Translates atomic read tasks into a typed QueryPlan without executing retrieval.",
        ("request_plan", "request_text", "keywords", "mentioned_keys", "messages"),
        ("query_plan",),
        execution_layer="lightweight_semantic",
    ),
    "query_runner": RoleSpec(
        "query_runner", "Query Runner", "deterministic", "fast_structured",
        "Executes QueryPlan under scope and pagination contracts and preserves complete artifacts.",
        ("messages", "intent", "request_text", "request_plan", "query_plan", "keywords",
         "turn_continuation", "materialized_ticket_sources", "thread_id"),
        ("query_results", "query_artifacts", "materialized_ticket_sources",
         "assignment_completion"),
        ("search", "web"), has_prompt=False, kind="service",
        execution_layer="deterministic",
    ),
    "research_analyst": RoleSpec(
        "research_analyst", "Research Analyst", "complex", "reasoning",
        "Synthesizes internal and external evidence while separating facts, inference, and gaps.",
        ("messages", "request_text", "request_plan", "query_plan", "query_results",
         "query_artifacts", "materialized_ticket_sources", "pre_survey", "seed_map",
         "topic_dossier", "web_context", "continuation_contract"),
        ("situation", "evidence", "related_docs", "epic_candidate", "already_exists"),
        ("research", "mcp"), decision_layer="lightweight_semantic",
    ),
    "knowledge_curator": RoleSpec(
        "knowledge_curator", "Knowledge Curator", "complex", "balanced",
        "Curates research into a reusable brief of concepts, internal context, sources, and gaps.",
        ("situation", "evidence", "related_docs", "web_context", "topic_dossier"),
        ("knowledge_brief",),
    ),
    "portfolio_analyst": RoleSpec(
        "portfolio_analyst", "Portfolio Analyst", "complex", "reasoning",
        "Interprets progress, workload, staleness, and activity as PMO risks and priorities.",
        ("messages", "intent", "mentioned_keys", "module", "user_id", "user_role",
         "pre_survey", "query_results", "group_activity", "ticket_progress"),
        ("pmo_findings", "pmo_caution", "person_work_snapshot", "daily_priority_snapshot",
         "portfolio_snapshot", "group_activity", "ticket_progress"),
        ("portfolio",), decision_layer="lightweight_semantic",
    ),
    "work_architect": RoleSpec(
        "work_architect", "Work Architect", "complex", "reasoning",
        "Converts verified findings into Epic-to-Task-to-SubTask structures and mutation drafts.",
        ("messages", "request_text", "request_plan", "intent", "mentioned_keys", "situation", "evidence",
         "related_docs", "pre_survey", "query_artifacts", "materialized_ticket_sources", "structure_plan",
         "structure_notes", "request_refinement", "continuation_contract", "draft", "change_plan"),
        ("interpretation", "structure_plan", "structure_ok", "questions", "draft", "change_plan"),
        effect="draft", semantic_contract="semantic_projection",
    ),
    "people_advisor": RoleSpec(
        "people_advisor", "People Advisor", "complex", "balanced",
        "Recommends assignment candidates and alternatives from verified roster, history, and workload.",
        ("draft", "evidence", "query_results"), ("assignments",), effect="draft",
    ),
    "auditor": RoleSpec(
        "auditor", "Auditor", "complex", "reasoning",
        "Audits schema, hierarchy, evidence, references, and request coverage; separates errors from warnings.",
        ("messages", "request_text", "request_plan", "keywords", "turn_continuation",
         "materialized_ticket_sources", "continuation_contract", "structure_ok", "draft",
         "change_plan", "evidence", "revisions"),
        ("review", "revisions"), effect="draft", kind="guardrail",
    ),
    "action_executor": RoleSpec(
        "action_executor", "Action Executor", "deterministic", "fast_structured",
        "Executes exactly once only the write payload that matches the approved fingerprint.",
        ("thread_id", "approval_token", "comment_token", "draft", "change_plan"),
        ("result",), ("write",), effect="write", kind="service",
        execution_layer="deterministic",
    ),
    "result_integrator": RoleSpec(
        "result_integrator", "Result Integrator", "complex", "balanced",
        "Integrates verified results and unresolved items into one Korean user response.",
        ("messages", "request_text", "intent", "answer_depth", "request_plan", "query_plan",
         "query_results", "situation",
         "evidence", "related_docs", "pre_survey", "topic_dossier", "knowledge_brief",
         "materialized_ticket_sources", "pmo_findings", "pmo_caution", "portfolio_snapshot",
         "group_activity", "ticket_progress",
         "person_work_snapshot", "daily_priority_snapshot", "interpretation", "questions",
         "draft", "assignments", "change_plan", "continuation_contract", "review",
         "approval_token", "result", "error"),
        ("reply",), effect="respond",
    ),
    "editor_author": RoleSpec(
        "editor_author", "Editor Author", "complex", "balanced",
        "Drafts a description or comment while preserving existing editor content and ticket context.",
        ("ticket_key", "kind", "prompt", "seed_html", "user_id"),
        ("html", "note", "references"), effect="draft",
    ),
}


def role_specs() -> tuple[RoleSpec, ...]:
    return tuple(ROLE_SPECS.values())


def execution_layer_for_role(role_id: str, stage: str = "synthesis") -> ExecutionLayer:
    """Resolve one Role/stage layer and fail loudly for manifest drift or unknown Roles."""
    spec = ROLE_SPECS.get(str(role_id or ""))
    if spec is None:
        raise KeyError(f"알 수 없는 Role manifest id: {role_id}")
    if stage == "decision" and spec.decision_layer:
        return spec.decision_layer
    return spec.execution_layer


def tools_for_role(role_id: str, *, include_dynamic: bool = True) -> list:
    """Resolve one canonical Role's effective tool catalog from ``ROLE_SPECS``.

    Native tool calling and prompt-JSON fallback must receive this same list. Static groups live in
    :mod:`app.agent.tools`; dynamic external MCP tools are attached only when the manifest explicitly
    grants the ``mcp`` group. Unknown groups and name collisions fail loudly so a typo cannot silently
    broaden or empty a Role's runtime permissions.
    """
    spec = ROLE_SPECS.get(str(role_id or ""))
    if spec is None:
        raise KeyError(f"unknown role id: {role_id}")

    from app.agent import tools as registry

    rows, by_name = [], {}

    def add(group: str, tool_rows) -> None:
        for tool_obj in tool_rows or []:
            name = str(getattr(tool_obj, "name", "") or "")
            if not name:
                raise RuntimeError(f"unnamed tool in role={spec.id}, group={group}")
            if group == "mcp":
                metadata = getattr(tool_obj, "metadata", None)
                if (not isinstance(metadata, dict)
                        or metadata.get("ltm_source") != "mcp"
                        or metadata.get("ltm_capability") != "read"):
                    raise RuntimeError(
                        f"unclassified external MCP tool denied for role={spec.id}: {name}"
                    )
            previous = by_name.get(name)
            if previous is not None and previous is not tool_obj:
                raise RuntimeError(f"tool name collision for role={spec.id}: {name}")
            if previous is None:
                by_name[name] = tool_obj
                rows.append(tool_obj)

    for group in spec.tool_groups:
        if group == "mcp":
            if include_dynamic:
                try:
                    from app.agent import mcp_client
                    external = mcp_client.tools()
                except Exception:
                    # External read-only MCP is optional and remains fail-soft.
                    external = []
                add(group, external)
            continue
        if group not in registry.TOOL_GROUPS:
            raise RuntimeError(f"unknown tool group for role={spec.id}: {group}")
        add(group, registry.TOOL_GROUPS[group])

    write_names = {tool.name for tool in registry.WRITE_TOOLS}
    leaked = write_names & set(by_name)
    if leaked and spec.effect != "write":
        raise RuntimeError(
            f"non-write role={spec.id} received write tools: {', '.join(sorted(leaked))}"
        )
    return rows


def validate_role_tool_groups() -> None:
    """Validate every static manifest group without starting optional MCP processes."""
    for spec in ROLE_SPECS.values():
        tools_for_role(spec.id, include_dynamic=False)


__all__ = ["RoleSpec", "RoleKind", "SemanticContract", "ExecutionLayer", "ROLE_SPECS",
           "role_specs", "execution_layer_for_role", "tools_for_role",
           "validate_role_tool_groups"]
