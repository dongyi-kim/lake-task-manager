from __future__ import annotations

import json

import pytest
from jsonschema.validators import validator_for

from app.agent.workflow.agents import base
from app.agent.workflow.agents.auditor import Auditor
from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator
from app.agent.workflow.agents.people_advisor import PeopleAdvisor
from app.agent.workflow.agents.portfolio_analyst import PortfolioAnalyst
from app.agent.workflow.agents.query_specialist import QuerySpecialist
from app.agent.workflow.agents.request_architect import RequestArchitect
from app.agent.workflow.agents.research_analyst import ResearchAnalyst
from app.agent.workflow.agents.work_architect import WorkArchitect


def _role_schema_cases():
    roles = [
        RequestArchitect(), QuerySpecialist(), ResearchAnalyst(), PortfolioAnalyst(),
        PeopleAdvisor(), KnowledgeCurator(), Auditor(),
    ]
    cases = [(role.name, role.schema_for({})) for role in roles]
    work = WorkArchitect()
    work_states = {
        "work_create": {},
        "work_comment": {
            "continuation_contract": {"version": "continuation.v1", "action": "comment"},
        },
        "work_update": {
            "continuation_contract": {"version": "continuation.v1", "action": "update"},
        },
        "work_mixed": {
            "continuation_contract": {"version": "continuation.v1", "action": "mixed"},
        },
        "work_multi_outcome": {
            "request_plan": {"tasks": [
                {"id": "one", "kind": "ticket", "write_intent": True,
                 "instruction": "첫 번째 산출물 생성"},
                {"id": "two", "kind": "ticket", "write_intent": True,
                 "instruction": "두 번째 산출물 생성"},
            ]},
        },
    }
    cases.extend((name, work.schema_for(state)) for name, state in work_states.items())
    return cases


@pytest.mark.parametrize(
    ("_name", "schema"), _role_schema_cases(), ids=lambda value: value if isinstance(value, str) else None,
)
def test_compact_transport_is_the_exact_same_json_schema(_name, schema):
    compact = base._compact_schema_text(schema)
    legacy = json.dumps(schema, ensure_ascii=False)

    assert compact == json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    assert json.loads(compact) == schema
    assert len(compact) < len(legacy)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "number", "maximum": float("nan")},
        {"type": "object", "description": b"not-json"},
    ],
    ids=("nonfinite", "bytes"),
)
def test_compact_transport_rejects_values_that_would_be_silently_normalized(schema):
    with pytest.raises((TypeError, ValueError)):
        base._compact_schema_text(schema)


def _error_coordinates(schema, instance):
    validator_type = validator_for(schema)
    validator_type.check_schema(schema)
    return [
        (
            error.validator,
            tuple(error.absolute_path),
            tuple(error.absolute_schema_path),
            error.message,
        )
        for error in validator_type(schema).iter_errors(instance)
    ]


@pytest.mark.parametrize(
    "instance",
    [
        {"items": [{"kind": "exact", "value": "ok"}]},
        {},
        {"items": [{"kind": "wrong", "value": "ok"}]},
        {"items": [{"kind": "exact", "value": "ok", "extra": True}]},
    ],
    ids=("valid", "required", "enum", "additional_properties"),
)
def test_compact_round_trip_preserves_ref_validation_and_error_diagnostics(instance):
    schema = {
        "$defs": {
            "item": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["exact"]},
                    "value": {"type": "string"},
                },
                "required": ["kind", "value"],
                "additionalProperties": False,
            },
        },
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/item"}},
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    transported = json.loads(base._compact_schema_text(schema))

    assert transported == schema
    assert _error_coordinates(transported, instance) == _error_coordinates(schema, instance)
