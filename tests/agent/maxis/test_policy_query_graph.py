"""Retrieval-only separation never substitutes the guarded user request."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil

import pytest
import yaml

from app.agent.maxis.context_guard import MAX_QUERY_UTF8_BYTES
from app.agent.maxis.retrieval_query import (
    _POLICY_SUFFIX_PREFIX, _POLICY_TERMS, select_policy_query,
)

ROOT = Path(__file__).resolve().parents[3]


def _load(name):
    spec = importlib.util.spec_from_file_location(
        "test_" + name, ROOT / "deploy/maxis-dify" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


graph = _load("policy_query_graph")


@pytest.mark.parametrize("role", _POLICY_TERMS)
@pytest.mark.parametrize("prefix", ("원문 요청", "variant 사용자 요청 [[person:opaque-ref]]\n  ",
                                     "No invented facts; preserve 2 hours / 30 minutes."))
def test_selector_is_retrieval_only_and_matches_embedded_unit(role, prefix):
    query = prefix + _POLICY_SUFFIX_PREFIX + _POLICY_TERMS[role]
    embedded = {}
    exec(graph.source(), embedded)
    assert select_policy_query(query, role) == _POLICY_TERMS[role]
    assert embedded["main"](query, role) == {"query": _POLICY_TERMS[role]}
    assert query.startswith(prefix)  # immutable request used by Guard/model


@pytest.mark.parametrize("role", _POLICY_TERMS)
@pytest.mark.parametrize("mutation", (
    "wrong_role", "alias", "unknown_role", "not_suffix", "extra_space", "different_terms",
    "prefix_only", "no_prefix", "empty_prefix", "oversize", "invalid_utf8",
))
def test_unrecognized_or_unbounded_queries_are_byte_for_byte_unchanged(role, mutation):
    suffix = _POLICY_SUFFIX_PREFIX + _POLICY_TERMS[role]
    query = "원문" + suffix
    supplied_role = role
    if mutation == "wrong_role":
        supplied_role = next(item for item in _POLICY_TERMS if item != role)
    elif mutation == "alias":
        supplied_role = role + " "
    elif mutation == "unknown_role":
        supplied_role = "request_architect"
    elif mutation == "not_suffix":
        query += "\n원문 추가"
    elif mutation == "extra_space":
        query += " "
    elif mutation == "different_terms":
        query = "원문" + suffix.replace("Policy search terms:", "Policy Search terms:")
    elif mutation == "prefix_only":
        query = "원문 그대로"
    elif mutation == "no_prefix":
        query = suffix
    elif mutation == "empty_prefix":
        query = "  \n " + suffix
    elif mutation == "oversize":
        query = "가" * MAX_QUERY_UTF8_BYTES + suffix
    elif mutation == "invalid_utf8":
        query = "\ud800" + suffix
    embedded = {}
    exec(graph.source(), embedded)
    assert select_policy_query(query, supplied_role) == query
    assert embedded["main"](query, supplied_role) == {"query": query}


def test_editor_and_existing_knowledge_controls_are_unchanged():
    from app.agent.maxis.knowledge_eval import build_synthetic_pack, load_suite
    query = "사용자 요청\n\nPolicy search terms: HTML TipTap Description"
    assert select_policy_query(query, "editor_author") == query
    suite = load_suite()
    for case in suite.cases:
        pack = build_synthetic_pack(case, suite_sha256=suite.sha256,
                                    knowledge_manifest_sha256="a" * 64)
        assert select_policy_query(pack.query, case.role_id) == pack.query


def _dsl(key):
    name = {"agent_core": "ltm-agent-core.yml", "pmo_brief": "ltm-pmo-brief.yml"}[key]
    return yaml.safe_load((ROOT / "deploy/maxis-dify/dsl" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", ("agent_core", "pmo_brief"))
def test_built_graph_matches_source_and_only_changes_retrieval_unit(key):
    dsl = _dsl(key)
    before = deepcopy(dsl)
    rebuilt = graph.rebuild(dsl, key)
    assert rebuilt == graph.rebuild(rebuilt, key)
    assert dsl == before
    if key == "agent_core":
        # The two mechanical builders use different list order; every object is identical.
        for field in ("nodes", "edges"):
            rebuilt["workflow"]["graph"][field].sort(key=lambda row: row["id"])
            before["workflow"]["graph"][field].sort(key=lambda row: row["id"])
    assert rebuilt == before
    nodes = {node["data"]["title"]: node for node in dsl["workflow"]["graph"]["nodes"]}
    assert len(nodes) == (18 if key == "agent_core" else 12)
    assert len(dsl["workflow"]["graph"]["edges"]) == (20 if key == "agent_core" else 12)
    graph.validate_node(list(nodes.values()), key, "start")
    assert nodes["Policy Query"]["data"]["code"] == graph.source()
    assert nodes["Role Knowledge"]["data"]["query_variable_selector"] == [graph.NODE_ID, "query"]
    guard_vars = {item["variable"]: item["value_selector"]
                  for item in nodes["Knowledge Guard"]["data"]["variables"]}
    assert guard_vars["query"] == ["sys", "query"]
    assert guard_vars["context_pack"] == ["start", "context_pack"]
    condition = nodes["Role Knowledge"]["data"]["metadata_filtering_conditions"]["conditions"][0]
    assert condition["value"] == '{{#' + nodes["Role ACL Key"]["id"] + '.output#}}'
    assert nodes["Release Sentinel"]["data"]["query_variable_selector"] == [nodes["Sentinel Query"]["id"], "output"]


@pytest.mark.parametrize("mutation", ("source", "query_binding", "role_binding", "output", "duplicate", "node_id"))
def test_policy_node_validator_rejects_source_or_binding_drift(mutation):
    nodes = _dsl("agent_core")["workflow"]["graph"]["nodes"]
    node = next(node for node in nodes if node["id"] == graph.NODE_ID)
    if mutation == "source":
        node["data"]["code"] += "\n# drift"
    elif mutation == "query_binding":
        node["data"]["variables"][0]["value_selector"] = ["start", "context_pack"]
    elif mutation == "role_binding":
        node["data"]["variables"][1]["value_selector"] = ["sys", "query"]
    elif mutation == "output":
        node["data"]["outputs"]["query"]["type"] = "object"
    elif mutation == "duplicate":
        nodes.append(deepcopy(node))
    elif mutation == "node_id":
        node["id"] += "wrong"
    with pytest.raises(ValueError, match="Policy Query"):
        graph.validate_node(nodes, "agent_core", "start")


def test_core_and_pmo_selectors_are_identical_and_editor_has_none():
    sources = []
    for key in ("agent_core", "pmo_brief"):
        nodes = _dsl(key)["workflow"]["graph"]["nodes"]
        sources.append(next(node["data"]["code"] for node in nodes if node["id"] == graph.NODE_ID))
    assert sources[0] == sources[1] == graph.source()
    editor = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-editor-author.yml").read_text(encoding="utf-8"))
    graph.validate_node(editor["workflow"]["graph"]["nodes"], "editor_author", "start")


@pytest.mark.parametrize("key", ("agent_core", "pmo_brief"))
@pytest.mark.parametrize("mutation", (
    "source", "retrieval_selector", "guard_query", "edge", "edge_targetHandle",
    "edge_loop", "edge_iteration", "edge_source_type", "edge_target_type", "edge_id",
    "edge_type", "edge_zindex", "edge_extra_metadata",
))
def test_bundle_and_materializer_reject_policy_shortcuts(tmp_path, monkeypatch, key, mutation):
    materializer, validator = _load("materialize_environment"), _load("validate_bundle")
    environment = materializer.load_environment(ROOT / "deploy/maxis-dify/environments/dev.yml")
    target = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy/maxis-dify", target / "deploy/maxis-dify")
    shutil.copytree(ROOT / "knowledge", target / "knowledge")
    (target / "config").mkdir()
    for filename in ("maxis-workflows.yml", "maxis-knowledge.yml"):
        shutil.copy2(ROOT / "config" / filename, target / "config" / filename)
    path = target / environment["workflows"][key]["source_dsl"]
    dsl = yaml.safe_load(path.read_text(encoding="utf-8"))
    nodes = {node["data"]["title"]: node for node in dsl["workflow"]["graph"]["nodes"]}
    if mutation == "source":
        nodes["Policy Query"]["data"]["code"] += "\n# drift"
    elif mutation == "retrieval_selector":
        nodes["Role Knowledge"]["data"]["query_variable_selector"] = ["sys", "query"]
    elif mutation == "guard_query":
        variable = next(item for item in nodes["Knowledge Guard"]["data"]["variables"]
                        if item["variable"] == "query")
        variable["value_selector"] = [graph.NODE_ID, "query"]
    elif mutation == "edge":
        dsl["workflow"]["graph"]["edges"] = [edge for edge in dsl["workflow"]["graph"]["edges"]
                                               if edge["source"] != graph.NODE_ID]
    elif mutation.startswith("edge_"):
        edge = next(item for item in dsl["workflow"]["graph"]["edges"]
                    if item["source"] == graph.NODE_ID)
        if mutation == "edge_targetHandle":
            edge["targetHandle"] = "invalid"
        elif mutation == "edge_loop":
            edge["data"]["isInLoop"] = True
        elif mutation == "edge_iteration":
            edge["data"]["isInIteration"] = True
        elif mutation == "edge_source_type":
            edge["data"]["sourceType"] = "llm"
        elif mutation == "edge_target_type":
            edge["data"]["targetType"] = "code"
        elif mutation == "edge_id":
            edge["id"] += "-drift"
        elif mutation == "edge_type":
            edge["type"] = "invalid"
        elif mutation == "edge_zindex":
            edge["zIndex"] = 1
        elif mutation == "edge_extra_metadata":
            edge["data"]["unreviewed"] = "extra"
    path.write_text(yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError):
        validator.validate_bundle(target)
    monkeypatch.setattr(materializer, "ROOT", target)
    with pytest.raises(materializer.EnvironmentBindingError):
        materializer._materialize_dsl(environment, key)
