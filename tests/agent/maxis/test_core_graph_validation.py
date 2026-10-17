"""Adversarial raw-artifact checks for both Core model branches; no provider calls."""
import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DSL_PATH = ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml"
ENV_PATH = ROOT / "deploy/maxis-dify/environments/dev.yml"


@pytest.fixture(scope="module")
def modules():
    spec = importlib.util.spec_from_file_location("core_test_materializer", ROOT / "deploy/maxis-dify/materialize_environment.py")
    materializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(materializer)
    return materializer, materializer._VALIDATOR


@pytest.fixture
def raw_source(monkeypatch):
    text = DSL_PATH.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    original = yaml.safe_load
    monkeypatch.setattr(yaml, "safe_load", lambda value: raw if value == text else original(value))
    return raw


def _nodes(raw):
    return {node["data"]["title"]: node["data"] for node in raw["workflow"]["graph"]["nodes"]}


def _reject_both(modules, raw):
    materializer, validator = modules
    before = copy.deepcopy(raw)
    with pytest.raises(validator.BundleValidationError):
        validator.validate_bundle(ROOT)
    environment = materializer.load_environment(ENV_PATH)
    with pytest.raises(materializer.EnvironmentBindingError):
        materializer._materialize_dsl(environment, "agent_core")
    assert raw == before, "raw validation must precede every repair or deployment mutation"


@pytest.mark.parametrize("title", ["Contract LLM", "Control LLM", "Query LLM"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("mutation", ["model", "system", "schema", "retry", "fallback", "memory", "vision",
                                      "context", "raw_user", "extra_prompt", "variables", "structured_disabled"])
def test_each_raw_core_llm_is_fully_validated_before_materialization(modules, raw_source, title, reverse, mutation):
    if reverse:
        raw_source["workflow"]["graph"]["nodes"].reverse()
    llm = _nodes(raw_source)[title]
    if mutation == "model":
        llm["model"]["name"] = "unreviewed-model"
    elif mutation == "system":
        llm["prompt_template"][0]["text"] += "\nchanged system"
    elif mutation == "schema":
        llm["structured_output"]["schema"] = {"type": "object"}
    elif mutation in {"retry", "fallback"}:
        llm["retry_config" if mutation == "retry" else "fallback_model"] = {"retry_enabled": True}
    elif mutation == "memory":
        llm["memory"]["window"]["enabled"] = True
    elif mutation == "vision":
        llm["vision"]["enabled"] = True
    elif mutation == "context":
        llm["context"]["variable_selector"] = ["start", "context_pack"]
    elif mutation == "raw_user":
        llm["prompt_template"][1]["text"] = "{{#sys.query#}}"
    elif mutation == "extra_prompt":
        llm["prompt_template"].append({"id": "extra", "role": "assistant", "text": "ready"})
    elif mutation == "variables":
        llm["variables"] = [{"variable": "raw", "value_selector": ["sys", "query"]}]
    else:
        llm["structured_output_enabled"] = False
    _reject_both(modules, raw_source)


@pytest.mark.parametrize("mutation", ["classifier_source", "classifier_selector", "classifier_output", "stage_value",
    "stage_selector", "merge_order", "merge_output", "merge_group", "candidate_bypass", "policy_value",
    "knowledge_source", "guard_source", "extra_edge", "deny_to_control", "missing_edge", "unknown_node"])
def test_raw_core_route_merge_guards_and_topology_cannot_be_repaired(modules, raw_source, mutation):
    nodes = _nodes(raw_source)
    classifier, stage, merge = nodes["Core Control Route"], nodes["Core Stage"], nodes["Core Candidate"]
    graph = raw_source["workflow"]["graph"]
    if mutation == "classifier_source":
        classifier["code"] += "\n# drift"
    elif mutation == "classifier_selector":
        classifier["variables"][0]["value_selector"] = ["start", "context_pack"]
    elif mutation == "classifier_output":
        classifier["outputs"]["candidate"] = {"children": None, "type": "string"}
    elif mutation in {"stage_value", "stage_selector"}:
        stage["cases"][0]["conditions"][0].update(
            {"value": "semantic"} if mutation == "stage_value" else {"variable_selector": ["sys", "query"]})
    elif mutation == "merge_order":
        merge["variables"].reverse()
    elif mutation == "merge_output":
        merge["variables"][0][1] = "structured_output"
    elif mutation == "merge_group":
        merge["advanced_settings"]["group_enabled"] = True
    elif mutation == "candidate_bypass":
        next(row for row in nodes["Contract Guard"]["variables"] if row["variable"] == "candidate")[
            "value_selector"] = ["1909000000001026", "text"]
    elif mutation == "policy_value":
        nodes["Policy Decision"]["cases"][0]["conditions"][0]["value"] = "deny"
    elif mutation in {"knowledge_source", "guard_source"}:
        nodes["Knowledge Guard" if mutation == "knowledge_source" else "Contract Guard"]["code"] += "\n# drift"
    elif mutation == "missing_edge":
        graph["edges"].pop()
    elif mutation == "unknown_node":
        graph["nodes"][-1]["id"] = "unknown"
    else:
        edge = next(row for row in graph["edges"] if row["sourceHandle"] == "false" and row["source"] == "1909000000001007")
        changed = copy.deepcopy(edge)
        changed.update(target="1909000000001026", id="1909000000001007-false-1909000000001026-target")
        changed["data"]["targetType"] = "llm"
        if mutation == "extra_edge":
            graph["edges"].append(changed)
        else:
            edge.update(changed)
    _reject_both(modules, raw_source)


@pytest.mark.parametrize("mutation", ["open_empty_branch", "string_boolean", "query_to_generic", "query_missing_merge"])
def test_query_branch_cannot_weaken_types_or_bypass_its_selected_output(modules, raw_source, mutation):
    nodes = _nodes(raw_source)
    payload = nodes["Query LLM"]["structured_output"]["schema"]["properties"]["payload"]
    if mutation == "open_empty_branch":
        payload["anyOf"][0] = {"type": "object"}
    elif mutation == "string_boolean":
        payload["anyOf"][1]["properties"]["reads"]["items"]["properties"]["exhaustive"] = {"type": "string"}
    elif mutation == "query_to_generic":
        nodes["Core Stage"]["cases"][1]["conditions"][0]["value"] = "semantic"
    else:
        nodes["Core Candidate"]["variables"].pop()
    _reject_both(modules, raw_source)


def test_canonical_query_contract_change_requires_independent_review(modules, raw_source, monkeypatch):
    from app.agent.workflow import contracts
    original = contracts.role_output_schema

    def changed(role):
        schema = original(role)
        schema["additionalProperties"] = True
        return schema

    monkeypatch.setattr(contracts, "role_output_schema", changed)
    _reject_both(modules, raw_source)


@pytest.mark.parametrize("asset", ["code/core_control_route.py", "prompts/core_control.md", "prompts/core_query.md"])
def test_asset_and_embedded_content_cannot_drift_together(modules, raw_source, monkeypatch, asset):
    path = ROOT / "deploy/maxis-dify" / asset
    original = Path.read_text
    changed = original(path, encoding="utf-8").rstrip() + "\nchanged"
    monkeypatch.setattr(Path, "read_text", lambda self, *args, **kwargs: changed if self == path else original(self, *args, **kwargs))
    nodes = _nodes(raw_source)
    if asset.startswith("code/"):
        nodes["Core Control Route"]["code"] = changed
    else:
        common = (ROOT / "deploy/maxis-dify/prompts/common.md").read_text(encoding="utf-8").strip()
        nodes["Query LLM" if asset.endswith("core_query.md") else "Control LLM"]["prompt_template"][0]["text"] = common + "\n\n" + changed
    _reject_both(modules, raw_source)


@pytest.mark.parametrize("mutation", ["nodes", "edges", "node", "data", "edge", "node_id", "variables", "prompt_scalar", "prompt_null"])
def test_malformed_raw_graph_raises_declared_validation_error(modules, raw_source, mutation):
    graph = raw_source["workflow"]["graph"]
    if mutation in {"nodes", "edges"}:
        graph[mutation] = None
    elif mutation in {"node", "edge"}:
        graph["nodes" if mutation == "node" else "edges"][0] = "invalid"
    elif mutation in {"data", "node_id"}:
        graph["nodes"][0]["data" if mutation == "data" else "id"] = []
    elif mutation == "variables":
        _nodes(raw_source)["Core Control Route"]["variables"] = "invalid"
    else:
        _nodes(raw_source)["Control LLM"]["prompt_template"] = None if mutation == "prompt_null" else "invalid"
    _reject_both(modules, raw_source)


@pytest.mark.parametrize("title,value", [("Core Control Route", []), ("Sentinel Query", {}), ("Role Knowledge", [])])
def test_malformed_titles_raise_declared_errors_in_both_entrypoints(modules, raw_source, title, value):
    _nodes(raw_source)[title]["title"] = value
    _reject_both(modules, raw_source)


def test_materializer_preflights_exact_raw_object_before_any_mutation(modules, raw_source, monkeypatch, tmp_path):
    materializer, validator = modules
    before = copy.deepcopy(raw_source)
    def reject(root, bundle, workflows, documents, *, source_dsls):
        assert set(workflows) == set(source_dsls) == {"agent_core"}
        assert source_dsls["agent_core"] is raw_source and raw_source == before
        raise validator.BundleValidationError("raw preflight stop")
    monkeypatch.setattr(validator, "_validate_dify_dsls", reject)
    output = tmp_path / "materialized"
    output.mkdir()
    existing = output / DSL_PATH.name
    existing.write_text("preserved output", encoding="utf-8")
    with pytest.raises(materializer.EnvironmentBindingError, match="raw preflight stop"):
        materializer.materialize(ENV_PATH, output)
    assert raw_source == before
    assert existing.read_text(encoding="utf-8") == "preserved output"
    assert list(output.iterdir()) == [existing]


def test_materialization_binds_all_models_independently_of_node_order(modules, raw_source):
    materializer, validator = modules
    raw_source["workflow"]["graph"]["nodes"].reverse()
    raw_source["workflow"]["graph"]["edges"].reverse()
    validator.validate_bundle(ROOT)
    environment = materializer.load_environment(ENV_PATH)
    environment["model"]["name"] = "target-model"
    environment["workflows"]["agent_core"]["app_name"] += " Target"
    _, result = materializer._materialize_dsl(environment, "agent_core")
    nodes = _nodes(result)
    for title in ("Contract LLM", "Control LLM", "Query LLM"):
        assert nodes[title]["model"]["name"] == "target-model"
        assert nodes[title]["context"]["variable_selector"] == ["1909000000001003", "result"]
        assert nodes[title]["prompt_template"][1]["text"].endswith("{{#1909000000001006.model_input#}}")
    assert next(row["value_selector"] for row in nodes["Contract Guard"]["variables"]
                if row["variable"] == "candidate") == ["1909000000001027", "output"]
