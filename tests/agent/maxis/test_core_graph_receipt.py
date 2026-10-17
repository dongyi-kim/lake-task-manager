"""The Core publication receipt includes every new execution boundary."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "core_receipt_test", ROOT / "deploy/maxis-dify/graph_receipt.py")
RECEIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECEIPT)


@pytest.fixture
def graph():
    value = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml")
                           .read_text(encoding="utf-8"))["workflow"]["graph"]
    assert len(value["nodes"]) == 17 and len(value["edges"]) == 19
    return value


@pytest.mark.parametrize("change", (
    "classifier", "stage", "merge", "control_system", "control_input",
    "semantic_input", "guard_input", "edge", "recovery", "query_schema", "query_prompt",
))
def test_core_receipt_detects_execution_changes(graph, change):
    altered = deepcopy(graph)
    nodes = {node["id"]: node["data"] for node in altered["nodes"]}
    if change == "classifier":
        nodes["1909000000001024"]["code"] += "\n# changed classifier"
    elif change == "stage":
        nodes["1909000000001025"]["cases"][0]["conditions"][0]["value"] = "semantic"
    elif change == "merge":
        nodes["1909000000001027"]["variables"][0] = ["start", "context_pack"]
    elif change in {"control_system", "control_input", "semantic_input"}:
        node_id = "1909000000001004" if change == "semantic_input" else "1909000000001026"
        index = 0 if change == "control_system" else 1
        nodes[node_id]["prompt_template"][index]["text"] += " changed"
    elif change == "query_schema":
        nodes["1909000000001028"]["structured_output"]["schema"]["properties"]["payload"] = {"type": "object"}
    elif change == "query_prompt":
        nodes["1909000000001028"]["prompt_template"][0]["text"] += " changed"
    elif change == "guard_input":
        row = next(row for row in nodes["1909000000001005"]["variables"]
                   if row["variable"] == "candidate")
        row["value_selector"] = ["1909000000001026", "text"]
    elif change == "edge":
        altered["edges"] = [edge for edge in altered["edges"]
                            if edge["source"] != "1909000000001024"]
    else:
        nodes["1909000000001026"]["retry_config"] = {"retry_enabled": True}
    assert RECEIPT.graph_sha256(altered) != RECEIPT.graph_sha256(graph)


def test_core_receipt_ignores_only_presentation_and_list_order(graph):
    altered = deepcopy(graph)
    altered["nodes"].reverse()
    altered["edges"].reverse()
    for node in altered["nodes"]:
        node["position"] = {"x": -100, "y": 999}
        node["positionAbsolute"] = {"x": -100, "y": 999}
        node["data"]["desc"] = "display only"
        node["data"]["selected"] = True
        if node["data"]["type"] == "llm":
            for index, row in enumerate(node["data"]["prompt_template"]):
                row["id"] = f"editor-local-{index}"
    assert RECEIPT.graph_sha256(altered) == RECEIPT.graph_sha256(graph)
