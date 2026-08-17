from __future__ import annotations

import importlib
from pathlib import Path

from app.agent.workflow.role_manifest import ROLE_SPECS
from app.agent.workflow.state import AgentState, Node


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
    assert {"messages", "request_text"} <= set(ROLE_SPECS["query_specialist"].input_keys)
    assert {"query_artifacts", "topic_dossier", "web_context"} <= \
        set(ROLE_SPECS["research_analyst"].input_keys)
    assert {"group_activity", "ticket_progress", "person_work_snapshot", "daily_priority_snapshot",
            "change_plan", "questions"} <= set(ROLE_SPECS["result_integrator"].input_keys)
