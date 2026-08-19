from __future__ import annotations

import importlib
from pathlib import Path

from app.agent.workflow.contracts import ContinuationContract, ContinuationDecision
from app.agent.workflow.role_manifest import (ROLE_SPECS, role_specs, tools_for_role,
                                               validate_role_tool_groups)
from app.agent.workflow.state import AgentState, Node, RequestRefinement


def test_role_ids_names_and_prompt_assets_are_canonical_and_unique():
    assert len(ROLE_SPECS) == len(set(ROLE_SPECS))
    names = [spec.name for spec in ROLE_SPECS.values()]
    assert len(names) == len(set(names))
    prompt_dir = Path(__file__).resolve().parents[1] / "app/agent/prompts/roles"
    for role_id, spec in ROLE_SPECS.items():
        assert spec.id == role_id
        assert not hasattr(spec, "runtime"), "runtime alias를 다시 만들지 않는다"
        if spec.prompt_asset:
            prompt = prompt_dir / spec.prompt_asset
            assert spec.prompt_asset == f"{role_id}.md"
            assert prompt.is_file(), spec
            assert prompt.read_text(encoding="utf-8").splitlines()[0] == f"# {spec.name}"


def test_role_module_class_and_graph_node_use_the_same_canonical_id():
    """Role lookup에 alias table이나 legacy fallback이 다시 생기지 않게 한다."""
    node_values = {value for key, value in vars(Node).items()
                   if key.isupper() and isinstance(value, str)}
    for role_id in ROLE_SPECS:
        module_name = ("app.agent.editor_author" if role_id == "editor_author"
                       else f"app.agent.workflow.agents.{role_id}")
        module = importlib.import_module(module_name)
        class_name = "".join(part.title() for part in role_id.split("_"))
        role_class = getattr(module, class_name)
        assert role_class.name == role_id
        if role_id != "editor_author":
            assert role_id in node_values


def test_legacy_role_alias_modules_and_prompts_do_not_return():
    root = Path(__file__).resolve().parents[1] / "app/agent"
    legacy = {"planner", "historian", "curator", "pmo", "refiner", "assigner",
              "reviewer", "operator", "responder", "composer"}
    agent_modules = root / "workflow/agents"
    prompt_dir = root / "prompts/roles"
    assert not [agent_modules / f"{name}.py" for name in legacy
                if (agent_modules / f"{name}.py").exists()]
    assert not [prompt_dir / f"{name}.md" for name in legacy
                if (prompt_dir / f"{name}.md").exists()]


def test_graph_role_state_contracts_name_real_state_keys():
    state_keys = set(AgentState.__annotations__)
    external_keys = {"ticket_key", "kind", "prompt", "seed_html"}
    for spec in ROLE_SPECS.values():
        unknown_inputs = set(spec.input_keys) - state_keys - external_keys
        unknown_outputs = set(spec.output_keys) - state_keys - {"html", "note", "references"}
        assert not unknown_inputs, (spec.id, unknown_inputs)
        assert not unknown_outputs, (spec.id, unknown_outputs)


def test_only_action_executor_has_write_tools_and_write_effect():
    writers = [spec.id for spec in ROLE_SPECS.values()
               if spec.effect == "write" or "write" in spec.tool_groups]
    assert writers == ["action_executor"]


def test_semantic_projection_is_explicit_and_initially_limited_to_work_architect():
    projected = [spec.id for spec in ROLE_SPECS.values()
                 if spec.semantic_contract == "semantic_projection"]
    assert projected == ["work_architect"]
    assert all(spec.semantic_contract in {"direct", "semantic_projection"}
               for spec in ROLE_SPECS.values())


def test_required_redesigned_capabilities_have_dedicated_roles():
    required = {"request_architect", "query_specialist", "research_analyst",
                "people_advisor", "editor_author", "auditor", "action_executor"}
    assert required <= set(ROLE_SPECS)


def test_role_manifest_documents_cross_turn_and_final_response_inputs():
    assert {"intent", "request_text", "request_plan", "turn_continuation", "situation",
            "materialized_ticket_sources"} <= set(
                ROLE_SPECS["request_architect"].input_keys)
    assert {"sufficient", "playbook", "answer_depth", "trace"} <= set(
        ROLE_SPECS["request_architect"].output_keys)
    assert set(RequestRefinement.__annotations__) == {"parent", "phase", "duedate"}
    assert "request_refinement" in ROLE_SPECS["request_architect"].output_keys
    assert "request_refinement" in ROLE_SPECS["work_architect"].input_keys
    assert [spec.id for spec in ROLE_SPECS.values()
            if "request_refinement" in spec.output_keys] == ["request_architect"]
    assert set(ContinuationContract.model_fields) == {
        "version", "root_request", "intent", "action", "target_keys", "outcome_ids", "decisions",
    }
    assert set(ContinuationDecision.model_fields) == {"field", "value", "source"}
    assert "continuation_contract" in ROLE_SPECS["request_architect"].input_keys
    assert "continuation_contract" in ROLE_SPECS["request_architect"].output_keys
    assert "continuation_contract" in ROLE_SPECS["research_analyst"].input_keys
    assert "continuation_contract" in ROLE_SPECS["work_architect"].input_keys
    assert "continuation_contract" in ROLE_SPECS["auditor"].input_keys
    assert "continuation_contract" in ROLE_SPECS["result_integrator"].input_keys
    assert [spec.id for spec in ROLE_SPECS.values()
            if "continuation_contract" in spec.output_keys] == ["request_architect"]
    assert {"messages", "request_text"} <= set(ROLE_SPECS["query_specialist"].input_keys)
    assert {"messages", "intent", "request_text", "request_plan", "query_plan", "keywords",
            "turn_continuation", "materialized_ticket_sources"} <= set(
                ROLE_SPECS["query_runner"].input_keys)
    assert {"query_artifacts", "materialized_ticket_sources", "topic_dossier", "web_context"} <= \
        set(ROLE_SPECS["research_analyst"].input_keys)
    assert "materialized_ticket_sources" in ROLE_SPECS["query_runner"].output_keys
    assert "materialized_ticket_sources" in ROLE_SPECS["work_architect"].input_keys
    assert {"messages", "request_text", "request_plan", "keywords", "turn_continuation",
            "materialized_ticket_sources", "structure_ok", "draft", "evidence", "revisions"} <= \
        set(ROLE_SPECS["auditor"].input_keys)
    assert {"query_plan", "query_results", "group_activity", "ticket_progress",
            "person_work_snapshot", "daily_priority_snapshot", "change_plan", "questions"} <= \
        set(ROLE_SPECS["result_integrator"].input_keys)


def test_manifest_tool_groups_resolve_to_exact_role_catalogs():
    """Manifest is the single permission/catalog source for every model-facing tool Role."""
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.portfolio_analyst import PortfolioAnalyst
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    validate_role_tool_groups()
    for role_id, role_class in {
        "research_analyst": ResearchAnalyst,
        "portfolio_analyst": PortfolioAnalyst,
        "action_executor": ActionExecutor,
    }.items():
        expected = {tool.name for tool in tools_for_role(role_id, include_dynamic=False)}
        actual = {tool.name for tool in role_class().tools
                  if not str(tool.name).startswith("mcp_")}
        assert actual == expected, (role_id, sorted(actual), sorted(expected))


def test_research_model_catalog_is_minimal_read_only_gap_filling_set():
    from app.agent import tools as T

    names = {tool.name for tool in tools_for_role("research_analyst", include_dynamic=False)}
    assert names == {
        "get_ticket", "read_document", "search_documents", "search_comments", "query_people",
        "list_attachments", "read_attachment", "search_web", "search_github",
    }
    assert not (names & {tool.name for tool in T.WRITE_TOOLS})
    assert len(names) < len({tool.name for tool in
                             (T.SEARCH_TOOLS + T.WEB_TOOLS + T.PEOPLE_TOOLS + T.RULE_TOOLS)})


def test_optional_mcp_catalog_is_granted_only_by_the_manifest(monkeypatch):
    from types import SimpleNamespace
    from app.agent import mcp_client

    external = SimpleNamespace(
        name="mcp_fetch_fetch",
        metadata={"ltm_source": "mcp", "ltm_capability": "read"},
    )
    monkeypatch.setattr(mcp_client, "tools", lambda: [external])
    assert external in tools_for_role("research_analyst")
    assert external not in tools_for_role("portfolio_analyst")


def test_manifest_denies_unclassified_external_tool_even_when_name_looks_read_only(monkeypatch):
    from types import SimpleNamespace
    from app.agent import mcp_client

    monkeypatch.setattr(
        mcp_client, "tools", lambda: [SimpleNamespace(name="mcp_fetch_read_document")]
    )

    import pytest
    with pytest.raises(RuntimeError, match="unclassified external MCP tool denied"):
        tools_for_role("research_analyst")


def test_deterministic_query_capabilities_use_real_read_groups_and_auditor_has_no_catalog():
    assert "query" not in {group for spec in ROLE_SPECS.values() for group in spec.tool_groups}
    assert ROLE_SPECS["query_runner"].tool_groups == ("search", "web")
    assert ROLE_SPECS["auditor"].tool_groups == ()


def test_contract_registry_contains_only_canonical_roles():
    from app.agent.workflow.contracts import ROLE_CONTRACTS

    assert set(ROLE_CONTRACTS) <= set(ROLE_SPECS)
    assert not ({"ticket_author", "comment_author"} & set(ROLE_CONTRACTS))


def test_role_kind_separates_semantic_service_and_guardrail_boundaries():
    services = {spec.id for spec in ROLE_SPECS.values() if spec.kind == "service"}
    guardrails = {spec.id for spec in ROLE_SPECS.values() if spec.kind == "guardrail"}
    semantics = {spec.id for spec in ROLE_SPECS.values() if spec.kind == "semantic"}

    assert services == {"query_runner", "action_executor"}
    assert guardrails == {"auditor"}
    assert semantics == set(ROLE_SPECS) - services - guardrails
    assert all(ROLE_SPECS[role_id].model_tier == "deterministic" for role_id in services)
    assert all(ROLE_SPECS[role_id].model_tier != "deterministic" for role_id in semantics)
    assert all(ROLE_SPECS[role_id].effect != "write" for role_id in guardrails)


def test_execution_layers_separate_formatting_from_semantic_judgment():
    expected = {
        "request_architect": "lightweight_semantic",
        "query_specialist": "lightweight_semantic",
        "query_runner": "deterministic",
        "research_analyst": "deep_semantic",
        "knowledge_curator": "deep_semantic",
        "portfolio_analyst": "deep_semantic",
        "work_architect": "deep_semantic",
        "people_advisor": "deep_semantic",
        "auditor": "deep_semantic",
        "action_executor": "deterministic",
        "result_integrator": "deep_semantic",
        "editor_author": "deep_semantic",
    }
    assert {role_id: spec.execution_layer for role_id, spec in ROLE_SPECS.items()} == expected
    assert all(spec.execution_layer != "projection" for spec in ROLE_SPECS.values())
    assert ROLE_SPECS["research_analyst"].decision_layer == "lightweight_semantic"
    assert ROLE_SPECS["portfolio_analyst"].decision_layer == "lightweight_semantic"


def test_manifest_model_tier_is_the_safe_fallback_not_an_unqualified_simple_route():
    assert ROLE_SPECS["request_architect"].model_tier == "complex"
    assert ROLE_SPECS["query_specialist"].model_tier == "complex"
    assert ROLE_SPECS["work_architect"].model_tier == "complex"


def test_role_specs_api_keeps_manifest_order_and_objects():
    assert role_specs() == tuple(ROLE_SPECS.values())


def test_agent_guide_requires_evidence_before_adding_a_semantic_role():
    guide = (Path(__file__).resolve().parents[1] / "app/agent/AGENT.md").read_text(
        encoding="utf-8"
    )
    for token in ("`semantic`", "`service`", "`guardrail`", "단계마다 Role을 추가하지 않는다",
                  "versioned battery", "Role별 regression 없음"):
        assert token in guide
