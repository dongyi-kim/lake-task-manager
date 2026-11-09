"""Pydantic is the single source of truth for model-facing Role contracts."""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError


MODEL_BACKED_ROLES = {
    "request_architect",
    "query_specialist",
    "research_analyst",
    "knowledge_curator",
    "portfolio_analyst",
    "work_architect",
    "people_advisor",
    "auditor",
}


def test_every_structured_semantic_role_uses_the_shared_pydantic_wire_registry():
    from app.agent.workflow.contracts import PYDANTIC_WIRE_ROLES

    assert MODEL_BACKED_ROLES <= PYDANTIC_WIRE_ROLES


@pytest.mark.parametrize(
    ("module_name", "class_name", "role_id"),
    [
        ("request_architect", "RequestArchitect", "request_architect"),
        ("research_analyst", "ResearchAnalyst", "research_analyst"),
        ("portfolio_analyst", "PortfolioAnalyst", "portfolio_analyst"),
        ("auditor", "Auditor", "auditor"),
    ],
)
def test_static_role_prompt_schema_comes_from_the_shared_contract(
        module_name, class_name, role_id):
    from importlib import import_module

    from app.agent.workflow.contracts import role_output_schema

    module = import_module(f"app.agent.workflow.agents.{module_name}")
    schema = getattr(module, class_name)().schema()

    assert schema == role_output_schema(role_id)
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    ("variant", "constant", "payload"),
    [
        ("legacy", "SCHEMA", {"questions": [], "mode": "task", "items": []}),
        ("create", "CREATE_SCHEMA", {"questions": [], "mode": "task", "items": []}),
        ("update", "UPDATE_SCHEMA", {"questions": []}),
        ("comment", "COMMENT_SCHEMA", {"questions": []}),
    ],
)
def test_work_architect_variants_come_from_pydantic_models(variant, constant, payload):
    from app.agent.workflow.agents import work_architect
    from app.agent.workflow.contracts import role_output_schema, validate_role_output

    schema = getattr(work_architect, constant)

    assert schema == role_output_schema("work_architect", variant=variant, inline=True)
    Draft202012Validator.check_schema(schema)
    assert validate_role_output("work_architect", payload, variant=variant) == payload


@pytest.mark.parametrize(
    ("role_id", "payload"),
    [
        (
            "request_architect",
            {
                "intent": "ask",
                "keywords": ["CDC"],
                "sufficient": True,
                "request_questions": [],
                "requested_effects": [],
                "target_selectors": [],
                "provider_extension": {"kept": True},
            },
        ),
        (
            "research_analyst",
            {
                "situation": "DL-1에서 확인",
                "evidence": [{"key": "DL-1", "provider_extension": "kept"}],
                "provider_extension": {"kept": True},
            },
        ),
        (
            "portfolio_analyst",
            {
                "headline": "지연 1건",
                "findings": [{"key": "DL-1", "provider_extension": "kept"}],
                "provider_extension": {"kept": True},
            },
        ),
        (
            "auditor",
            {
                "grounded": True,
                "rule_compliant": True,
                "answers_request": True,
                "problems": [{"provider_extension": "kept"}],
                "provider_extension": {"kept": True},
            },
        ),
    ],
)
def test_migrated_role_contracts_preserve_legacy_extension_semantics(role_id, payload):
    from app.agent.workflow.contracts import validate_role_output

    assert validate_role_output(role_id, payload) == payload


@pytest.mark.parametrize(
    ("role_id", "payload"),
    [
        (
            "request_architect",
            {
                "intent": "ask",
                "keywords": "CDC",
                "sufficient": True,
                "request_questions": [],
                "requested_effects": [],
                "target_selectors": [],
            },
        ),
        ("research_analyst", {"situation": "확인", "evidence": {}}),
        ("portfolio_analyst", {"headline": "확인", "findings": {}}),
        (
            "auditor",
            {
                "grounded": "yes",
                "rule_compliant": True,
                "answers_request": True,
                "problems": [],
            },
        ),
    ],
)
def test_migrated_role_contracts_reject_wrong_wire_types(role_id, payload):
    from app.agent.workflow.contracts import validate_role_output

    with pytest.raises(ValidationError):
        validate_role_output(role_id, payload)


def test_compact_work_contracts_remain_closed_to_unknown_fields():
    from app.agent.workflow.contracts import validate_role_output

    with pytest.raises(ValidationError):
        validate_role_output(
            "work_architect",
            {"questions": [], "mode": "task", "items": [], "unknown": True},
            variant="create",
        )
