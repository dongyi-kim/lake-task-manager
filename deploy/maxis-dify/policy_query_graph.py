"""Mechanical Core/PMO policy-query unit; canonical vocabulary lives in LTM.

Only the Role Knowledge search input changes. Knowledge Guard still receives
sys.query for exact EvidencePack binding; ACL, evidence and model input do not.
"""
from __future__ import annotations

import copy
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.maxis.context_guard import MAX_QUERY_UTF8_BYTES
from app.agent.maxis.retrieval_query import (
    _POLICY_SUFFIX_PREFIX, _POLICY_TERMS, select_policy_query,
)

NODE_ID = "1909000000001023"
WORKFLOWS = frozenset({"agent_core", "pmo_brief"})


def source() -> str:
    """Emit a dependency-free copy of the actual application selector."""
    return (
        "# Generated from app.agent.maxis.retrieval_query; do not edit inline.\n"
        + "_POLICY_TERMS = " + json.dumps(dict(_POLICY_TERMS), ensure_ascii=False, sort_keys=True)
        + "\n_POLICY_SUFFIX_PREFIX = " + repr(_POLICY_SUFFIX_PREFIX)
        + "\nMAX_QUERY_UTF8_BYTES = " + str(MAX_QUERY_UTF8_BYTES)
        + "\n\n" + inspect.getsource(select_policy_query).rstrip()
        + '\n\ndef main(query, role_id):\n    return {"query": select_policy_query(query, role_id)}'
    )


def node_data(start_id: str = "start") -> dict:
    return {
        "code": source(), "code_language": "python3",
        "desc": "Select exact LTM policy suffix for retrieval only; preserve original guard/model request",
        "outputs": {"query": {"children": None, "type": "string"}},
        "selected": False, "title": "Policy Query", "type": "code",
        "variables": [
            {"value_selector": ["sys", "query"], "variable": "query"},
            {"value_selector": [start_id, "role_id"], "variable": "role_id"},
        ],
    }


def validate_node(nodes: list, workflow_key: str, start_id: str, error_type=ValueError) -> None:
    matched = [node for node in nodes if node.get("data", {}).get("title") == "Policy Query"]
    if workflow_key not in WORKFLOWS:
        if matched:
            raise error_type("Policy Query is forbidden outside Core/PMO")
        return
    if (len(matched) != 1 or matched[0].get("id") != NODE_ID
            or matched[0].get("data") != node_data(start_id)):
        raise error_type("Policy Query source or binding drifted")


def rebuild(dsl: dict, workflow_key: str) -> dict:
    """Idempotently replace only the reviewed Core/PMO retrieval unit."""
    if workflow_key not in WORKFLOWS:
        raise ValueError("Policy Query is only supported in Core/PMO")
    result = copy.deepcopy(dsl)
    graph = result["workflow"]["graph"]
    titles = {node["data"]["title"]: node for node in graph["nodes"]}
    start = next(node for node in graph["nodes"] if node["data"]["type"] == "start")
    acl, retrieval = titles["Role ACL Key"], titles["Role Knowledge"]
    node = copy.deepcopy(titles["Knowledge Guard"])
    node.update(id=NODE_ID, data=node_data(start["id"]))
    node["position"] = {"x": 520, "y": 660}
    node["positionAbsolute"] = dict(node["position"])
    graph["nodes"] = [item for item in graph["nodes"] if item["id"] != NODE_ID] + [node]
    retrieval["data"]["query_variable_selector"] = [NODE_ID, "query"]
    graph["edges"] = [edge for edge in graph["edges"]
                      if NODE_ID not in {edge["source"], edge["target"]}
                      and (edge["source"], edge["target"]) != (acl["id"], retrieval["id"])]
    for src, dst in ((acl, node), (node, retrieval)):
        graph["edges"].append({
            "data": {"isInIteration": False, "isInLoop": False,
                     "sourceType": src["data"]["type"], "targetType": dst["data"]["type"]},
            "id": f'{src["id"]}-source-{dst["id"]}-target',
            "source": src["id"], "sourceHandle": "source",
            "target": dst["id"], "targetHandle": "target", "type": "custom", "zIndex": 0,
        })
    return result


if __name__ == "__main__":
    import yaml
    for key, filename in (("agent_core", "ltm-agent-core.yml"),
                          ("pmo_brief", "ltm-pmo-brief.yml")):
        path = ROOT / "deploy/maxis-dify/dsl" / filename
        rebuilt = rebuild(yaml.safe_load(path.read_text(encoding="utf-8")), key)
        path.write_text(yaml.safe_dump(rebuilt, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f'{key}: {len(rebuilt["workflow"]["graph"]["nodes"])} nodes')
