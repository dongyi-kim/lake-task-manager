"""Validate the executed Core graph without rebuilding or normalizing its input."""
from __future__ import annotations

import hashlib

SEMANTIC_ID = "1909000000001004"
CONTROL_ID = "1909000000001026"
CLASSIFIER_ID = "1909000000001024"
STAGE_ID = "1909000000001025"
MERGE_ID = "1909000000001027"
KNOWLEDGE_ID = "1909000000001006"
POLICY_ID = "1909000000001007"
GUARD_ID = "1909000000001005"
# Independent review pins: matching a changed artifact to a changed asset is insufficient.
ROUTE_SHA256 = "2c2b97b1de58d91e676e05d3da9516f1347370d80ae7094513cbd17a9a5d4859"
CONTROL_SYSTEM_SHA256 = "7017b30253765eab4e41723de73a3d5014c1f5c2b820fedcbaab80cf08ddae60"
NODE_IDENTITIES = {
    "start": ("시작", "start"), "answer": ("Allowed Answer", "answer"),
    "denied_answer": ("Denied Answer", "answer"),
    "1909000000001001": ("Sentinel Query", "template-transform"),
    "1909000000001002": ("Release Sentinel", "knowledge-retrieval"),
    "1909000000001003": ("Role Knowledge", "knowledge-retrieval"),
    "1909000000001010": ("Role ACL Key", "template-transform"),
    "1909000000001023": ("Policy Query", "code"),
    SEMANTIC_ID: ("Contract LLM", "llm"), CONTROL_ID: ("Control LLM", "llm"),
    KNOWLEDGE_ID: ("Knowledge Guard", "code"), GUARD_ID: ("Contract Guard", "code"),
    POLICY_ID: ("Policy Decision", "if-else"), CLASSIFIER_ID: ("Core Control Route", "code"),
    STAGE_ID: ("Core Stage", "if-else"), MERGE_ID: ("Core Candidate", "variable-aggregator"),
}
EDGES = {
    ("start", "source", "1909000000001001"), ("1909000000001001", "source", "1909000000001002"),
    ("start", "source", "1909000000001010"), ("1909000000001010", "source", "1909000000001023"),
    ("1909000000001023", "source", "1909000000001003"),
    ("1909000000001002", "source", KNOWLEDGE_ID), ("1909000000001003", "source", KNOWLEDGE_ID),
    (KNOWLEDGE_ID, "source", POLICY_ID), (POLICY_ID, "false", "denied_answer"),
    (POLICY_ID, "true", CLASSIFIER_ID), (CLASSIFIER_ID, "source", STAGE_ID),
    (STAGE_ID, "true", CONTROL_ID), (STAGE_ID, "false", SEMANTIC_ID),
    (SEMANTIC_ID, "source", MERGE_ID), (CONTROL_ID, "source", MERGE_ID),
    (MERGE_ID, "source", GUARD_ID), (GUARD_ID, "source", "answer"),
}


def validate_core_graph(root, nodes, *, checks, schema, semantic_prompts,
                        semantic_hashes, model_provider, max_tokens,
                        max_system_bytes, error_type):
    """The caller validates shared guards, edge metadata and exact EDGES separately."""
    def fail(message):
        raise error_type(f"agent_core {message}")

    if set(nodes) != set(NODE_IDENTITIES) or any(
        (nodes[key]["data"].get("title"), nodes[key]["data"].get("type")) != identity
        for key, identity in NODE_IDENTITIES.items()
    ):
        fail("Dify Core node identities drifted")
    try:
        source = (root / "deploy/maxis-dify/code/core_control_route.py").read_text(encoding="utf-8").rstrip()
        prompt = "\n\n".join((root / "deploy/maxis-dify/prompts" / name).read_text(encoding="utf-8").strip()
                             for name in ("common.md", "core_control.md"))
    except (OSError, UnicodeError) as exc:
        raise error_type("reviewed Core control assets cannot be loaded") from exc
    if any(hashlib.sha256(value.encode("utf-8")).hexdigest() != pin
           for value, pin in ((source, ROUTE_SHA256), (prompt, CONTROL_SYSTEM_SHA256))):
        fail("reviewed Core control asset digest drifted")
    classifier = nodes[CLASSIFIER_ID]["data"]
    if classifier != {
        "title": "Core Control Route", "type": "code", "selected": False,
        "desc": "원래 model_input의 activation 형태만 분류; 권한 판정 아님", "code_language": "python3",
        "code": source, "variables": [{"variable": "model_input", "value_selector": [KNOWLEDGE_ID, "model_input"]}],
        "outputs": {"route": {"children": None, "type": "string"}},
    } or classifier.get("selected") is not False:
        fail("Dify Core classifier source or binding drifted")
    stage = nodes[STAGE_ID]["data"]
    if stage != {
        "title": "Core Stage", "type": "if-else", "selected": False,
        "desc": "검증된 activation만 전용 모델 경로로 분기",
        "cases": [{"case_id": "true", "id": "true", "logical_operator": "and", "conditions": [{
            "comparison_operator": "is", "id": "3918d5de-3625-41ba-a218-5630ca1724e7",
            "value": "control", "varType": "string", "variable_selector": [CLASSIFIER_ID, "route"]}]}],
    } or stage.get("selected") is not False:
        fail("Dify Core stage route or selector drifted")
    merge = nodes[MERGE_ID]["data"]
    if merge != {
        "title": "Core Candidate", "type": "variable-aggregator", "selected": False,
        "desc": "실행된 단일 모델의 text를 기존 Contract Guard에 전달", "output_type": "string",
        "variables": [[CONTROL_ID, "text"], [SEMANTIC_ID, "text"]],
        "advanced_settings": {"group_enabled": False, "groups": []},
    } or merge.get("selected") is not False or merge["advanced_settings"].get("group_enabled") is not False:
        fail("Dify Core candidate merge drifted")
    for node_id, prompts, hashes in (
        (SEMANTIC_ID, semantic_prompts, semantic_hashes),
        (CONTROL_ID, (prompt, semantic_prompts[1]),
         {"system": CONTROL_SYSTEM_SHA256, "user": semantic_hashes["user"]}),
    ):
        checks.validate_llm(
            nodes[node_id]["data"], label=f"agent_core.{node_id}", schema=schema,
            prompts=prompts, hashes=hashes, knowledge_id="1909000000001003",
            model_provider=model_provider, model_name="gpt-5.4-mini", max_tokens=max_tokens,
            max_system_bytes=max_system_bytes, error_type=error_type,
        )
