"""Exclusive internal units keep one model call and one authoritative guard path."""
import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"deploy/maxis-dify/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build():
    spec, validator = _load("editor_graph"), _load("validate_bundle")
    dsl = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-editor-author-2026.09.yml").read_text(encoding="utf-8"))
    return spec, spec.rebuild(ROOT, dsl, validator)


def test_builder_is_idempotent_and_preserves_common_guards():
    spec, dsl = _build()
    assert spec.rebuild(ROOT, dsl, _load("validate_bundle")) == dsl
    graph = dsl["workflow"]["graph"]
    assert len(graph["nodes"]) == 17
    assert len(graph["edges"]) == 19
    nodes = {node["data"]["title"]: node for node in graph["nodes"]}
    assert nodes["Editor Candidate"]["data"]["variables"] == [[spec.BRANCH_NODE_IDS[b], "text"] for b in spec.BRANCHES]
    assert nodes["Contract Guard"]["data"]["variables"][0]["value_selector"] == [nodes["Editor Assembler"]["id"], "candidate"]
    assert nodes["Allowed Answer"]["data"]["answer"] == "{{#1909000000001005.result#}}"


@pytest.mark.parametrize("branch", ["description", "comment", "control"])
def test_each_path_has_one_model_and_no_other_writer(branch):
    spec, dsl = _build()
    graph = dsl["workflow"]["graph"]
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}
    reached, pending = set(), [spec.ROUTE_NODE_ID]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        expected_handle = {"description": "true", "comment": "comment", "control": "false"}[branch]
        pending.extend(e["target"] for e in graph["edges"] if e["source"] == current
                       and (current != spec.ROUTE_NODE_ID or e["sourceHandle"] == expected_handle))
    assert [key for key in reached if nodes[key]["type"] == "llm"] == [spec.BRANCH_NODE_IDS[branch]]
    assert {"Editor Candidate", "Editor Assembler", "Contract Guard", "Allowed Answer"} <= {nodes[key]["title"] for key in reached}
    assert not any(nodes[key]["type"] in {"tool", "agent", "http-request"} for key in reached)


@pytest.mark.parametrize("branch,sections", [("description", ["background", "scope", "work", "dod"]), ("comment", ["comment"])])
def test_writer_contract_contains_no_control_envelope(branch, sections):
    spec, dsl = _build()
    node = next(n["data"] for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == spec.BRANCH_NODE_IDS[branch])
    schema = node["structured_output"]["schema"]
    assert "oneOf" not in schema
    assert schema["properties"]["contract"]["enum"] == ["editor-plan.v2"]
    assert schema["properties"]["sections"]["items"]["properties"]["section"]["enum"] == sections
    prompt = node["prompt_template"][0]["text"]
    assert "remote-role-result" not in prompt
    assert "payload_schema_sha256" not in prompt
    assert len(prompt.encode("utf-8")) < 8192


def test_builder_does_not_change_its_input():
    spec, dsl = _build()
    original = copy.deepcopy(dsl)
    spec.rebuild(ROOT, dsl, _load("validate_bundle"))
    assert dsl == original


@pytest.mark.parametrize("mutation", ["prompt", "schema", "selector", "retry", "extra_edge", "model"])
def test_publish_comparison_retains_all_execution_semantics(mutation):
    spec, dsl = _build()
    receipt = _load("graph_receipt")
    graph = dsl["workflow"]["graph"]
    changed = copy.deepcopy(graph)
    data = next(n["data"] for n in changed["nodes"] if n["id"] == spec.COMMENT_NODE_ID)
    if mutation == "prompt":
        data["prompt_template"][0]["text"] += " weakened policy"
    elif mutation == "schema":
        data["structured_output"]["schema"]["additionalProperties"] = True
    elif mutation == "selector":
        data["context"]["variable_selector"] = ["start", "context_pack"]
    elif mutation == "retry":
        data["retry_config"] = {"retry_enabled": True}
    elif mutation == "extra_edge":
        changed["edges"].append({"source": spec.CONTROL_NODE_ID, "sourceHandle": "source",
                                  "target": "answer", "targetHandle": "target", "data": {}})
    else:
        data["model"]["name"] = "unreviewed-model"
    assert receipt.graph_sha256(changed) != receipt.graph_sha256(graph)


def test_publish_comparison_ignores_only_presentation_and_local_uuid():
    _, dsl = _build()
    receipt = _load("graph_receipt")
    graph = dsl["workflow"]["graph"]
    changed = copy.deepcopy(graph)
    for node in changed["nodes"]:
        node["position"] = {"x": 0, "y": 0}
        node["data"]["selected"] = True
        node["data"]["desc"] = "UI annotation"
        if node["data"]["type"] == "llm":
            node["data"]["prompt_template"][0]["id"] = "new-editor-id"
        if node["data"]["title"] == "Role Knowledge":
            node["data"]["metadata_filtering_conditions"]["conditions"][0]["id"] = "d8bf897d-8b8c-490f-95f4-88eb9aeea400"
    assert receipt.graph_sha256(changed) == receipt.graph_sha256(graph)


def test_publication_reservation_blocks_concurrent_and_repeated_attempts(tmp_path):
    receipt = _load("graph_receipt")
    path = tmp_path / "attempt.json"
    with receipt.publication_receipt(path) as record:
        assert path.with_suffix(".json.claim").exists()
        with pytest.raises(FileExistsError):
            with receipt.publication_receipt(path):
                pytest.fail("duplicate must not enter an external operation")
        record["status"] = "published"
    assert '"published"' in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        with receipt.publication_receipt(path):
            pytest.fail("completed attempt must never run again")


@pytest.mark.parametrize("error", [RuntimeError, SystemExit, KeyboardInterrupt])
def test_publication_failure_records_phase_without_exception_text(tmp_path, error):
    receipt = _load("graph_receipt")
    path = tmp_path / "attempt.json"
    with pytest.raises(error):
        with receipt.publication_receipt(path) as record:
            record["phase"] = "login"
            raise error("private failure detail")
    result = path.read_text(encoding="utf-8")
    assert '"failed"' in result and '"login"' in result
    assert "private failure detail" not in result
    assert path.with_suffix(".json.claim").exists()
