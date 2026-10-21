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


@pytest.fixture(scope="module")
def _graph_template():
    value = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml")
                           .read_text(encoding="utf-8"))["workflow"]["graph"]
    assert len(value["nodes"]) == 35 and len(value["edges"]) == 42
    return value


@pytest.fixture
def graph(_graph_template):
    return deepcopy(_graph_template)


def test_graph_fixture_does_not_share_nested_mutations(_graph_template, graph):
    before = deepcopy(_graph_template)
    assert graph == _graph_template
    work = next(row["data"] for row in graph["nodes"] if row["data"]["title"] == "Work LLM")
    work["structured_output"]["schema"]["properties"].clear()
    work["prompt_template"].append({"role": "assistant", "text": "case-local mutation"})
    graph["edges"].clear()
    assert graph != before
    assert _graph_template == before


@pytest.mark.parametrize("change", (
    "classifier", "stage", "merge", "control_system", "control_input",
    "semantic_input", "guard_input", "edge", "recovery", "query_schema", "query_prompt", "query_assembler",
    "query_projection", "query_projection_selector", "query_input",
    "request_schema", "request_prompt", "request_input", "request_assembler",
    "request_assembler_input", "request_stage", "request_merge", "request_edge",
    "request_projection", "request_projection_selector",
))
def test_core_receipt_detects_execution_changes(graph, change):
    altered = deepcopy(graph)
    nodes = {node["id"]: node["data"] for node in altered["nodes"]}
    if change == "request_schema":
        nodes["1909000000001031"]["structured_output"]["schema"]["properties"]["payload"] = {"type": "object"}
    elif change in {"request_prompt", "request_input"}:
        nodes["1909000000001031"]["prompt_template"][int(change == "request_input")]["text"] += " changed"
    elif change == "request_assembler":
        nodes["1909000000001032"]["code"] += "\n# changed assembler"
    elif change == "request_projection":
        nodes["1909000000001033"]["code"] += "\n# changed projection"
    elif change == "request_projection_selector":
        nodes["1909000000001033"]["variables"][0]["value_selector"] = ["start", "context_pack"]
    elif change == "request_assembler_input":
        nodes["1909000000001032"]["variables"][0]["value_selector"] = ["sys", "query"]
    elif change == "request_stage":
        case = next(row for row in nodes["1909000000001025"]["cases"] if row["case_id"] == "request")
        case["conditions"][0]["value"] = "semantic"
    elif change == "request_merge":
        nodes["1909000000001027"]["variables"][3] = ["1909000000001031", "text"]
    elif change == "request_edge":
        altered["edges"] = [edge for edge in altered["edges"] if edge["source"] != "1909000000001031"]
    elif change == "query_projection":
        nodes["1909000000001030"]["code"] += "\n# changed projection"
    elif change == "query_projection_selector":
        nodes["1909000000001030"]["variables"][0]["value_selector"] = ["start", "context_pack"]
    elif change == "query_input":
        nodes["1909000000001028"]["prompt_template"][1]["text"] = "{{#1909000000001006.model_input#}}"
    elif change == "query_assembler":
        nodes["1909000000001029"]["code"] += "\n# changed assembler"
    elif change == "classifier":
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


@pytest.mark.parametrize("change", ["schema", "system", "user", "input_source", "input_selector",
    "assembler_source", "assembler_input", "assembler_candidate", "stage", "merge", "edge", "memory"])
@pytest.mark.parametrize("route,ids", [
    ("work", ("1909000000001034", "1909000000001035", "1909000000001036")),
    ("audit", ("1909000000001044", "1909000000001045", "1909000000001046")),
])
def test_core_receipt_detects_draft_execution_boundaries(graph, change, route, ids):
    altered = deepcopy(graph)
    nodes = {node["id"]: node["data"] for node in altered["nodes"]}
    work, assembler, projection = (nodes[key] for key in ids)
    if change == "schema": work["structured_output"]["schema"] = {"type": "object"}
    elif change in {"system", "user"}:
        work["prompt_template"][int(change == "user")]["text"] += " changed"
    elif change == "memory": work["memory"] = {}
    elif change == "input_source": projection["code"] += "\n# changed"
    elif change == "input_selector": projection["variables"][0]["value_selector"] = ["sys", "query"]
    elif change == "assembler_source": assembler["code"] += "\n# changed"
    elif change in {"assembler_input", "assembler_candidate"}:
        assembler["variables"][int(change == "assembler_candidate")]["value_selector"] = ["sys", "query"]
    elif change == "stage":
        next(row for row in nodes["1909000000001025"]["cases"] if row["case_id"] == route)[
            "conditions"][0]["value"] = "semantic"
    elif change == "merge":
        variables = nodes["1909000000001027"]["variables"]
        variables[variables.index([ids[1], "candidate"])] = [ids[0], "text"]
    else: altered["edges"] = [row for row in altered["edges"] if row["source"] != ids[0]]
    assert RECEIPT.graph_sha256(altered) != RECEIPT.graph_sha256(graph)
