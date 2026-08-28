from __future__ import annotations

import json

import pytest
from pydantic import ValidationError


def _knowledge_module():
    from app.agent.workflow.agents import knowledge_curator

    return knowledge_curator


def test_knowledge_projection_adapter_is_the_prompt_schema_source_of_truth():
    module = _knowledge_module()
    from app.agent.workflow.contracts import (
        PYDANTIC_WIRE_ROLES, ROLE_CONTRACTS, ROLE_OUTPUT_MODELS, role_output_schema,
    )

    schema = module.KnowledgeCurator().schema()
    assert schema == role_output_schema("knowledge_curator")
    assert ROLE_OUTPUT_MODELS["knowledge_curator"].__name__ == "KnowledgeBrief"
    assert ROLE_CONTRACTS is ROLE_OUTPUT_MODELS
    assert {"knowledge_curator", "people_advisor"} <= PYDANTIC_WIRE_ROLES
    assert set(schema["required"]) == {"concepts", "our_context", "gaps"}
    assert "references" not in schema["required"]

    concept_ref = schema["properties"]["concepts"]["items"]["$ref"]
    concept = schema["$defs"][concept_ref.rsplit("/", 1)[-1]]
    reference_ref = schema["properties"]["references"]["items"]["$ref"]
    reference = schema["$defs"][reference_ref.rsplit("/", 1)[-1]]
    assert "required" not in concept
    assert "required" not in reference
    assert "why it matters here" in concept["properties"]["explanation"]["description"]
    assert "Only a ticket key" in reference["properties"]["ref"]["description"]


def test_knowledge_projection_keeps_legacy_optional_and_extra_field_semantics():
    from app.agent.workflow.contracts import validate_role_output

    payload = {
        "concepts": [{"term": "CDC", "provider_extension": "kept"}],
        "our_context": "DL-1에서 검증",
        "gaps": [],
        "provider_extension": {"kept": True},
    }

    projected = validate_role_output("knowledge_curator", payload)

    assert projected == payload
    assert "references" not in projected


def test_knowledge_projection_rejects_the_same_wrong_json_shapes():
    from app.agent.workflow.contracts import validate_role_output

    payloads = [
        {"our_context": "DL-1", "gaps": []},
        {"concepts": {}, "our_context": "DL-1", "gaps": []},
        {"concepts": [], "our_context": 7, "gaps": []},
        {"concepts": [], "our_context": "DL-1", "gaps": "none"},
        {"concepts": ["CDC"], "our_context": "DL-1", "gaps": []},
    ]
    accepted = []
    for payload in payloads:
        try:
            validate_role_output("knowledge_curator", payload)
        except ValidationError:
            continue
        accepted.append(payload)

    assert not accepted, accepted


def test_knowledge_projection_keeps_empty_nested_objects_legal():
    from app.agent.workflow.contracts import validate_role_output

    payload = {
        "concepts": [{}], "our_context": "사내 이력 없음", "gaps": [],
        "references": [{}],
    }

    assert validate_role_output("knowledge_curator", payload) == payload


@pytest.mark.parametrize("role_id", ["unknown_role", "request_architect"])
def test_role_output_registry_fails_closed_for_an_unregistered_wire_role(role_id):
    from app.agent.workflow.contracts import role_output_schema, validate_role_output

    with pytest.raises(ValueError, match="공용 Pydantic wire boundary 미등록 role"):
        role_output_schema(role_id)
    with pytest.raises(ValueError, match="공용 Pydantic wire boundary 미등록 role"):
        validate_role_output(role_id, {})


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"our_context": "DL-1", "gaps": []},
            {"category": "schema", "keyword": "required", "path": "$", "missing": "concepts"},
        ),
        (
            {"concepts": [], "our_context": 7, "gaps": []},
            {"category": "schema", "keyword": "type", "path": "$.our_context"},
        ),
        (
            {"concepts": [{"term": 7}], "our_context": "DL-1", "gaps": []},
            {"category": "schema", "keyword": "type", "path": "$.concepts[0].term"},
        ),
    ],
)
def test_common_pydantic_boundary_keeps_safe_json_schema_diagnostics(payload, expected):
    from app.agent.workflow.agents.base import _validate_output, _validation_diagnostic

    agent = _knowledge_module().KnowledgeCurator()
    candidate = agent.pre_validate_structured_output(
        {}, payload, output_contract="structured", execution_stage="synthesis",
    )
    with pytest.raises(Exception) as caught:
        _validate_output(candidate, agent.schema())

    diagnostic = _validation_diagnostic(caught.value)
    assert diagnostic == expected
    assert "DL-1" not in json.dumps(diagnostic, ensure_ascii=False)


def test_agent_apply_uses_the_adapter_projection_without_model_calls(monkeypatch):
    module = _knowledge_module()
    agent = module.KnowledgeCurator()
    payload = {
        "concepts": [{"term": "CDC", "explanation": "변경 데이터 캡처"}],
        "our_context": "DL-1에서 검토",
        "references": [{"ref": "DL-1", "why": "검토 기록"}],
        "gaps": ["운영 주기 확인 필요"],
    }
    monkeypatch.setattr(agent, "llm", lambda **_kwargs: pytest.fail("LLM called"))

    assert agent.apply({}, payload)["knowledge_brief"] == payload
