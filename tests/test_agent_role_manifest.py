from __future__ import annotations

from pathlib import Path

from app.agent.workflow.role_manifest import ROLE_SPECS
from app.agent.workflow.state import AgentState


def test_role_ids_runtime_names_and_prompt_assets_are_unique():
    assert len(ROLE_SPECS) == len(set(ROLE_SPECS))
    names = [spec.name for spec in ROLE_SPECS.values()]
    assert len(names) == len(set(names))
    prompt_dir = Path(__file__).resolve().parents[1] / "app/agent/prompts/roles"
    for spec in ROLE_SPECS.values():
        if spec.prompt_asset:
            assert (prompt_dir / spec.prompt_asset).is_file(), spec


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


def test_required_redesigned_capabilities_have_dedicated_roles():
    required = {"request_architect", "query_specialist", "research_analyst",
                "people_advisor", "editor_author", "auditor", "action_executor"}
    assert required <= set(ROLE_SPECS)
