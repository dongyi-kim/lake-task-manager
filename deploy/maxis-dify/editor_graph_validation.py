"""Shared source-graph validation for the three exclusive Editor branches.

Validate the expanded graph as it is executed, never a collapsed legacy graph.
Environment materialization calls this before changing any model bindings.
"""
from __future__ import annotations

import hashlib
from typing import Any


def reject_recovery_settings(value: Any, *, path: str,
                             error_type: type[Exception]) -> None:
    """Node failures must terminate, never synthesize a value or alternate route."""
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).lower().replace("-", "_")
            if key in {"error_strategy", "retry_config", "retry_enabled"} or "fallback" in key:
                raise error_type(f"{path} Dify recovery or fallback setting is forbidden")
            reject_recovery_settings(nested, path=f"{path}.{raw_key}", error_type=error_type)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_recovery_settings(nested, path=f"{path}[{index}]", error_type=error_type)


def validate_llm(data: dict, *, label: str, schema: dict, prompts: tuple[str, str],
                 hashes: dict, knowledge_id: str, model_provider: str,
                 model_name: str, max_tokens: int, max_system_bytes: int,
                 error_type: type[Exception]) -> None:
    def fail(message: str) -> None:
        raise error_type(f"{label} Dify LLM {message}")

    if set(data) != {
        "context", "desc", "memory", "model", "prompt_template", "selected",
        "structured_output", "structured_output_enabled", "title", "type",
        "variables", "vision",
    }:
        fail("fields or recovery/fallback settings drifted")
    if data.get("context") != {
        "enabled": True, "variable_selector": [knowledge_id, "result"],
    }:
        fail("context must select the guarded Role Knowledge result")
    if (
        data.get("model") != {
            "provider": model_provider, "name": model_name, "mode": "chat",
            "completion_params": {"temperature": 0.1, "max_tokens": max_tokens},
        }
        or data.get("memory") != {
            "query_prompt_template": "", "role_prefix": {"assistant": "", "user": ""},
            "window": {"enabled": False, "size": 1},
        }
        or data.get("structured_output_enabled") is not True
        or data.get("structured_output") != {"schema": schema}
        or data.get("variables") != []
        or data.get("vision") != {"enabled": False}
        or data.get("selected") is not False
        or data.get("type") != "llm"
    ):
        fail("model or structured output drifted (including memory/vision/state)")
    if (data["memory"]["window"]["enabled"] is not False
            or data["vision"]["enabled"] is not False
            or data["context"]["enabled"] is not True):
        fail("memory, vision, or context enablement drifted")
    rows = data.get("prompt_template")
    if (
        not isinstance(rows, list) or len(rows) != 2
        or any(not isinstance(row, dict) or set(row) != {"id", "role", "text"}
               for row in rows)
        or [row.get("role") for row in rows] != ["system", "user"]
        or any(not isinstance(row.get("id"), str) or not row["id"] for row in rows)
        or rows[0]["id"] == rows[1]["id"]
        or any(not isinstance(row.get("text"), str) for row in rows)
    ):
        fail("prompt must contain exactly canonical system and user messages")
    canonical_hashes = {
        role: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        for role, prompt in zip(("system", "user"), prompts)
    }
    if hashes != canonical_hashes:
        fail("canonical prompt SHA-256 drifted; review complete messages before updating pins")
    if len(rows[0]["text"].encode("utf-8")) > max_system_bytes:
        fail("system prompt exceeds the UTF-8 budget")
    if tuple(row["text"] for row in rows) != prompts:
        fail("prompt SHA-256 drifted; canonical prompt content differs")


def validate_editor_graph(root, nodes: list[dict], edges: list[dict], *, spec,
                          remote_schema: dict, project, assembler_source: str,
                          input_id: str, input_entrypoint: str, model_provider: str,
                          model_name: str, max_tokens: int, max_system_bytes: int,
                          error_type: type[Exception]) -> dict[str, Any]:
    def fail(message: str) -> None:
        raise error_type(f"editor_author {message}")

    if not isinstance(nodes, list) or not isinstance(edges, list):
        fail("Dify graph nodes and edges must be lists")
    by_id, by_title, counts = {}, {}, {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("data"), dict):
            fail("Dify node binding is invalid")
        node_id, data = node.get("id"), node["data"]
        title, kind = data.get("title"), data.get("type")
        if (not isinstance(node_id, str) or not node_id or node_id in by_id
                or not isinstance(title, str) or title in by_title
                or not isinstance(kind, str)):
            fail("Dify node ids and titles must be unique strings")
        reject_recovery_settings(data, path=f"editor_author.{node_id}.data", error_type=error_type)
        by_id[node_id], by_title[title] = node, node
        counts[kind] = counts.get(kind, 0) + 1
    if counts != {"start": 1, "template-transform": 2, "knowledge-retrieval": 2,
                  "llm": 3, "code": 4, "if-else": 2, "answer": 2,
                  "variable-aggregator": 1}:
        fail("Dify node topology drifted")

    def titled(title: str, kind: str) -> dict:
        node = by_title.get(title)
        if node is None or node["data"].get("type") != kind:
            fail(f"Dify {title} node binding drifted")
        return node

    start = next(node for node in nodes if node["data"]["type"] == "start")
    knowledge = titled("Knowledge Guard", "code")
    role = titled("Role Knowledge", "knowledge-retrieval")
    policy = titled("Policy Decision", "if-else")
    guard = titled("Contract Guard", "code")
    editor_input, assembler = titled("Editor Input", "code"), titled("Editor Assembler", "code")
    sentinel_query = titled("Sentinel Query", "template-transform")
    acl = titled("Role ACL Key", "template-transform")
    sentinel = titled("Release Sentinel", "knowledge-retrieval")
    allowed, denied = titled("Allowed Answer", "answer"), titled("Denied Answer", "answer")
    branch_ids = dict(spec.BRANCH_NODE_IDS)
    if {node["id"] for node in nodes if node["data"]["type"] == "llm"} != set(branch_ids.values()):
        fail("Dify Editor branch identities drifted")
    for branch in spec.BRANCHES:
        node = titled(spec.BRANCH_TITLES[branch], "llm")
        if node["id"] != branch_ids[branch]:
            fail("Dify Editor branch identities drifted")
        try:
            schema = spec.branch_schema(root, branch, remote_schema, project)
            prompts = spec.branch_prompts(root, branch, input_id)
        except (OSError, UnicodeError, ValueError) as exc:
            raise error_type("reviewed Editor branch schema or prompt cannot be loaded") from exc
        validate_llm(node["data"], label=f"editor_author.{branch}", schema=schema,
                     prompts=prompts, hashes=spec.BRANCH_PROMPT_SHA256[branch],
                     knowledge_id=role["id"], model_provider=model_provider,
                     model_name=model_name, max_tokens=max_tokens,
                     max_system_bytes=max_system_bytes, error_type=error_type)
    code_fields = {"code", "code_language", "desc", "outputs", "selected", "title", "type", "variables"}
    data = editor_input["data"]
    if (editor_input["id"] != input_id or set(data) != code_fields
            or data.get("code_language") != "python3"
            or not isinstance(data.get("code"), str)
            or data["code"].rstrip() != (assembler_source + input_entrypoint).rstrip()
            or data.get("outputs") != {name: {"children": None, "type": "string"}
                                       for name in ("model_input", "route")}
            or data.get("variables") != [{"variable": "model_input", "value_selector": [knowledge["id"], "model_input"]}]
            or data.get("selected") is not False):
        fail("Editor Input binding or source drifted")
    data = assembler["data"]
    selectors = [("candidate", [spec.MERGE_NODE_ID, "output"])] + [
        (name, [start["id"], name]) for name in (
            "context_pack", "role_id", "correlation_id", "output_contract",
            "workflow_version", "knowledge_version")
    ] + [("knowledge_proof", [knowledge["id"], "knowledge_proof"])]
    if (set(data) != code_fields or data.get("code_language") != "python3"
            or not isinstance(data.get("code"), str) or data["code"].rstrip() != assembler_source
            or data.get("outputs") != {"candidate": {"children": None, "type": "string"}}
            or data.get("variables") != [{"variable": name, "value_selector": selector} for name, selector in selectors]
            or data.get("selected") is not False):
        fail("Editor Assembler binding or source drifted")
    for node_id, expected in ((spec.ROUTE_NODE_ID, spec.route_data(input_id)),
                              (spec.MERGE_NODE_ID, spec.merge_data(branch_ids))):
        if node_id not in by_id or by_id[node_id]["data"] != expected:
            fail("Dify Editor route or candidate merge binding drifted")

    routes = {
        (start["id"], "source", sentinel_query["id"]),
        (sentinel_query["id"], "source", sentinel["id"]),
        (start["id"], "source", acl["id"]), (acl["id"], "source", role["id"]),
        (sentinel["id"], "source", knowledge["id"]), (role["id"], "source", knowledge["id"]),
        (knowledge["id"], "source", policy["id"]), (policy["id"], "true", input_id),
        (policy["id"], "false", denied["id"]), (assembler["id"], "source", guard["id"]),
        (guard["id"], "source", allowed["id"]),
    } | spec.branch_edges(input_id, branch_ids, assembler["id"])
    actual = set()
    for edge in edges:
        if not isinstance(edge, dict):
            fail("Dify edge binding is invalid")
        source, handle, target = edge.get("source"), edge.get("sourceHandle"), edge.get("target")
        if (not all(isinstance(value, str) for value in (source, handle, target))
                or source not in by_id or target not in by_id
                or (source, handle, target) in actual
                or edge.get("id") != f"{source}-{handle}-{target}-target"
                or edge.get("targetHandle") != "target" or edge.get("type") != "custom"
                or edge.get("zIndex") != 0
                or edge.get("data") != {"isInIteration": False, "isInLoop": False,
                                         "sourceType": by_id[source]["data"]["type"],
                                         "targetType": by_id[target]["data"]["type"]}):
            fail("Dify edge binding or node type drifted")
        if (edge["data"]["isInIteration"] is not False
                or edge["data"]["isInLoop"] is not False):
            fail("Dify edge iteration or loop state drifted")
        actual.add((source, handle, target))
    if actual != routes:
        fail("Dify graph edges / deterministic branch topology drifted")
    return {"branch_ids": branch_ids, "input_id": input_id,
            "assembler_id": assembler["id"], "edges": routes}
