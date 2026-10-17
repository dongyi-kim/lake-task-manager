"""Offline behavior and wiring of the synthetic-only native Agent capability slice."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / "deploy/maxis-dify/taskflow_synthetic"


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"synthetic_{name}", DIRECTORY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle():
    return load_module("bundle")


@pytest.fixture
def runtime():
    return load_module("runtime")


@pytest.fixture
def ids():
    return {name: f"00000000-0000-4000-8000-00000000000{i}" for i, name in enumerate(
        ("dataset", "document", "glossary_lookup", "evidence_lookup", "domain_retrieve"), 1)}


@pytest.fixture
def observation():
    result = {
        "providers": [{
            "identity": {"name": "fixture/agent/agent"},
            "plugin_unique_identifier": "fixture/agent:1.0.0@" + "a" * 64,
            "strategies": [{
                "identity": {"name": "function_calling", "provider": "fixture/agent/agent", "label": {"en_US": "FunctionCalling"}},
                "parameters": [{"name": name, "type": kind, "required": True} for name, kind in (
                    ("model", "model-selector"), ("tools", "array[tools]"),
                    ("instruction", "string"), ("query", "string"), ("maximum_iterations", "number"))],
            }],
        }],
        "core_published": {"models": [{"title": "Contract LLM", "model": {
            "provider": "fixture/model/model", "name": "fixture-model", "mode": "chat",
            "completion_params": {"max_tokens": 2048, "temperature": 0.1},
        }}]},
    }
    react = copy.deepcopy(result["providers"][0]["strategies"][0])
    react["identity"].update(name="ReAct", label={"en_US": "ReAct"})
    result["providers"][0]["strategies"].append(react)
    return result


def nodes(dsl):
    return {node["id"]: node["data"] for node in dsl["workflow"]["graph"]["nodes"]}


def run_code(dsl, node_id, **kwargs):
    scope = {}
    exec(compile(nodes(dsl)[node_id]["code"], "<synthetic-code-node>", "exec"), scope)
    return scope["main"](**kwargs)


def test_glossary_is_exact_and_closed(runtime):
    found = json.loads(runtime.glossary_lookup("synthetic-research-v1", "SYN-CHECKPOINT")["result"])
    assert found["status"] == "found"
    assert found["source_ids"] == ["syn-glossary-checkpoint"]
    assert json.loads(runtime.glossary_lookup("synthetic-research-v1", "checkpoint")["result"])["status"] == "not_found"
    with pytest.raises(ValueError, match="fixture"):
        runtime.glossary_lookup("internal-project", "SYN-CHECKPOINT")


def test_upload_document_is_exact_fixture(runtime):
    assert (DIRECTORY / "ltm-synthetic-research-rules-v1.txt").read_text(encoding="utf-8").strip() == runtime.KNOWLEDGE_TEXT
    assert len(runtime.KNOWLEDGE_TEXT.encode("utf-8")) < 768


@pytest.mark.parametrize("fixture_id,case_id", [("internal", "checkpoint_research"),
    ("synthetic-research-v1", "private research"), ("synthetic-research-v1", None)])
def test_research_gate_rejects_unknown_input(runtime, fixture_id, case_id):
    with pytest.raises(ValueError):
        runtime.research_query(fixture_id, case_id)


def test_evidence_is_fixed_snapshot_not_user_supplied_context(runtime):
    result = json.loads(runtime.evidence_lookup("synthetic-research-v1", "syn-task-001")["result"])
    assert result["source_ids"] == ["syn-task-001"]
    assert "합성" in result["text"]
    with pytest.raises(ValueError):
        runtime.evidence_lookup("synthetic-research-v1", "REAL-123")


@pytest.mark.parametrize("value", [None, True, {}, [], "x" * 10000], ids=["null", "bool", "object", "array", "oversized"])
def test_runtime_rejects_non_string_or_oversized_selectors(runtime, value):
    with pytest.raises(ValueError):
        runtime.glossary_lookup("synthetic-research-v1", value)
    with pytest.raises(ValueError):
        runtime.evidence_lookup("synthetic-research-v1", value)


def test_domain_gate_and_admission_are_exact(runtime, ids):
    assert runtime.domain_query("synthetic-research-v1", "draft_rules")["query"] == "SYN_LTM_TASK_RULE_V1"
    hit = {"content": runtime.KNOWLEDGE_TEXT, "metadata": {
        "dataset_id": ids["dataset"], "document_id": ids["document"],
    }}
    result = runtime.admit_domain([hit], ids["dataset"], ids["document"])
    assert json.loads(result["result"])["source_ids"] == ["syn-domain-rules"]
    for altered in (
        [{**hit, "content": "private internal information"}],
        [{**hit, "metadata": {"dataset_id": ids["dataset"], "document_id": ids["glossary_lookup"]}}],
        [hit, hit], {"result": [hit]},
    ):
        with pytest.raises(ValueError):
            runtime.admit_domain(altered, ids["dataset"], ids["document"])
    assert json.loads(runtime.admit_domain([], ids["dataset"], ids["document"])["result"])["status"] == "not_found"


def test_three_atomic_workflows_use_actual_knowledge_node(bundle, ids):
    dsls = bundle.build_tool_dsls(ids["dataset"], ids["document"])
    assert set(dsls) == {"glossary_lookup", "evidence_lookup", "domain_retrieve"}
    for name, dsl in dsls.items():
        assert dsl["version"] == "0.6.0"
        assert dsl["app"]["mode"] == "workflow"
        assert dsl["workflow"]["conversation_variables"] == []
        assert dsl["workflow"]["environment_variables"] == []
        assert not any(n["type"] in {"llm", "agent", "http-request", "tool"} for n in nodes(dsl).values())
        assert set(n["variable"] for n in nodes(dsl)["start"]["variables"]) == {
            "fixture_id", {"glossary_lookup": "term", "evidence_lookup": "source_id", "domain_retrieve": "query_key"}[name]}
    domain = dsls["domain_retrieve"]
    assert nodes(domain)["retrieve"]["type"] == "knowledge-retrieval"
    assert nodes(domain)["retrieve"]["dataset_ids"] == [ids["dataset"]]
    assert nodes(domain)["retrieve"]["query_variable_selector"] == ["gate", "query"]
    assert nodes(domain)["retrieve"]["multiple_retrieval_config"]["top_k"] == 1
    assert run_code(dsls["glossary_lookup"], "lookup", fixture_id="synthetic-research-v1", term="SYN-CHECKPOINT")["result"]
    with pytest.raises(ValueError):
        run_code(domain, "gate", fixture_id="internal", query_key="draft_rules")
    run_code(domain, "admit", hits=[])


def test_registration_parameters_keep_fixture_manual(bundle, ids):
    payload = bundle.tool_registration("evidence_lookup", ids["evidence_lookup"])
    assert payload["workflow_app_id"] == ids["evidence_lookup"]
    assert payload["icon"] == {"content": "🧪", "background": "#EAF3FF"}
    assert {p["name"]: p["form"] for p in payload["parameters"]} == {
        "fixture_id": "form", "source_id": "llm"}


def test_release_names_match_registration_and_agent_binding(bundle, observation, ids):
    dsl = bundle.build_research_dsl(observation, {name: ids[name] for name in bundle.TOOL_NAMES},
        model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64)
    agent = nodes(dsl)["research"]
    tools = agent["agent_parameters"]["tools"]["value"]
    for logical, tool in zip(bundle.TOOL_NAMES, tools):
        registration = bundle.tool_registration(logical, ids[logical])
        assert registration["name"] == bundle.REGISTERED_TOOL_NAMES[logical] == tool["tool_name"]
        assert registration["name"].startswith("ltm_syn_20261017_v1_")
        assert registration["name"] != logical
        assert len(registration["name"]) <= 40
        assert registration["name"] in agent["agent_parameters"]["instruction"]["value"]
        assert tool["provider_name"] == ids[logical]


def test_native_agent_uses_live_observation_and_only_three_tools(bundle, observation, ids):
    dsl = bundle.build_research_dsl(observation, {name: ids[name] for name in bundle.TOOL_NAMES},
        model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64)
    agent = nodes(dsl)["research"]
    assert agent["type"] == "agent"
    assert agent["agent_strategy_provider_name"] == "fixture/agent/agent"
    assert agent["plugin_unique_identifier"] == observation["providers"][0]["plugin_unique_identifier"]
    assert agent["agent_parameters"]["model"]["value"]["model"] == "fixture-model"
    assert agent["agent_parameters"]["maximum_iterations"]["value"] == 4
    assert not agent.get("memory")
    tools = agent["agent_parameters"]["tools"]["value"]
    assert {t["tool_name"] for t in tools} == set(bundle.REGISTERED_TOOL_NAMES.values())
    for logical, tool in zip(bundle.TOOL_NAMES, tools):
        assert tool["type"] == "workflow"
        assert tool["provider_name"] == ids[logical]
        assert tool["parameters"]["fixture_id"] == {"auto": 0, "value": {"type": "constant", "value": "synthetic-research-v1"}}
    assert nodes(dsl)["research"]["agent_parameters"]["query"] == {"type": "variable", "value": ["gate", "query"]}
    assert nodes(dsl)["end"]["outputs"] == [{"variable": "research_candidate", "value_selector": ["research", "text"], "value_type": "string"}]
    assert "TaskFlowResult.v1" not in yaml.safe_dump(dsl)


def test_default_research_strategy_and_app_identity_are_function_calling(bundle, observation, ids):
    dsl = bundle.build_research_dsl(observation, {name: ids[name] for name in bundle.TOOL_NAMES},
        model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64)
    assert nodes(dsl)["research"]["agent_strategy_name"] == "function_calling"
    assert "FunctionCalling" in dsl["app"]["name"]
    react = bundle.build_research_dsl(observation, {name: ids[name] for name in bundle.TOOL_NAMES},
        model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64, strategy="ReAct")
    assert nodes(react)["research"]["agent_strategy_name"] == "ReAct"
    assert "ReAct" in react["app"]["name"]
    assert dsl["app"]["name"] != react["app"]["name"]


def test_cli_default_research_strategy_is_function_calling(bundle, observation, ids, tmp_path):
    observation_file = tmp_path / "observation.json"
    observation_file.write_text(json.dumps(observation), encoding="utf-8")
    bindings_file = tmp_path / "bindings.json"
    bindings_file.write_text(json.dumps({name: ids[name] for name in bundle.TOOL_NAMES}), encoding="utf-8")
    output = tmp_path / "research"
    bundle.main(["--output", str(output), "research", "--observation", str(observation_file),
        "--tool-bindings", str(bindings_file), "--model-plugin", "fixture/model:1.0.0@" + "b" * 64])
    dsl = yaml.safe_load((output / "research.yml").read_text(encoding="utf-8"))
    assert nodes(dsl)["research"]["agent_strategy_name"] == "function_calling"


def test_builder_rejects_unbound_invalid_or_aliasing_ids(bundle, observation, ids):
    with pytest.raises(ValueError):
        bundle.build_tool_dsls("replace-me", ids["document"])
    for providers in ({}, {name: ids["dataset"] for name in bundle.TOOL_NAMES}):
        with pytest.raises(ValueError):
            bundle.build_research_dsl(observation, providers, model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64)
    with pytest.raises(ValueError):
        bundle.build_research_dsl(observation, {n: ids[n] for n in bundle.TOOL_NAMES}, model_plugin_unique_identifier="placeholder")


def test_live_strategy_schema_drift_is_not_silently_accepted(bundle, observation, ids):
    altered = copy.deepcopy(observation)
    altered["providers"][0]["strategies"][0]["parameters"][0]["type"] = "string"
    with pytest.raises(ValueError):
        bundle.build_research_dsl(altered, {n: ids[n] for n in bundle.TOOL_NAMES}, model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64)


@pytest.mark.parametrize("temperature", [True, float("nan"), float("inf"), -1])
def test_model_binding_rejects_invalid_parameter_values(bundle, observation, ids, temperature):
    observation["core_published"]["models"][0]["model"]["completion_params"]["temperature"] = temperature
    with pytest.raises(ValueError):
        bundle.build_research_dsl(observation, {n: ids[n] for n in bundle.TOOL_NAMES}, model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64)


def test_strategy_iteration_capacity_checked(bundle, observation, ids):
    observation["providers"][0]["strategies"][0]["parameters"][-1]["max"] = 3
    with pytest.raises(ValueError):
        bundle.build_research_dsl(observation, {n: ids[n] for n in bundle.TOOL_NAMES}, model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64)


def test_graph_selectors_and_edges_reference_existing_nodes(bundle, observation, ids):
    dsls = list(bundle.build_tool_dsls(ids["dataset"], ids["document"]).values())
    dsls.append(bundle.build_research_dsl(observation, {n: ids[n] for n in bundle.TOOL_NAMES}, model_plugin_unique_identifier="fixture/model:1.0.0@" + "b" * 64))
    for dsl in dsls:
        graph = dsl["workflow"]["graph"]
        indexed = nodes(dsl)
        for edge in graph["edges"]:
            assert edge["source"] in indexed and edge["target"] in indexed
            assert edge["data"]["sourceType"] == indexed[edge["source"]]["type"]
            assert edge["data"]["targetType"] == indexed[edge["target"]]["type"]
        assert len(indexed) == len(graph["nodes"])
        assert all(not n.get("retry_config", {}).get("retry_enabled", False) for n in indexed.values())


def test_cli_is_offline_and_does_not_overwrite(bundle, ids, tmp_path):
    output = tmp_path / "generated"
    args = ["--output", str(output), "tools", "--dataset-id", ids["dataset"], "--document-id", ids["document"]]
    bundle.main(args)
    assert sorted(p.name for p in output.iterdir()) == ["domain_retrieve.yml", "evidence_lookup.yml", "glossary_lookup.yml"]
    original = (output / "glossary_lookup.yml").read_bytes()
    with pytest.raises(FileExistsError):
        bundle.main(args)
    assert (output / "glossary_lookup.yml").read_bytes() == original
