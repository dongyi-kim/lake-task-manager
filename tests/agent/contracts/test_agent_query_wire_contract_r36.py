"""QuerySpecialist's compact model wire and runtime QueryPlan stay distinct."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.agent.workflow.agents.query_specialist import (
    QuerySpecialist,
    _compile_compact_query_plan,
)
from app.agent.workflow.contracts import (
    CompactQueryPlan,
    PYDANTIC_WIRE_ROLES,
    QueryPlan,
    ROLE_CONTRACTS,
    ROLE_OUTPUT_MODELS,
    ROLE_WIRE_MODELS,
    role_output_schema,
    validate_role_output,
)


VALID_WIRE = {
    "reads": [
        {
            "source": "jira",
            "subject": "Puffin NDV",
            "where": "status != Done",
            "exhaustive": True,
        },
        {
            "source": "confluence",
            "subject": "Puffin 설계",
            "where": "space = DATA",
            "exhaustive": False,
        },
    ],
    "uncertainty": ["reader version is unknown"],
}


def _minified(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_query_wire_registry_is_separate_from_runtime_contract_with_zero_schema_delta():
    legacy_schema = CompactQueryPlan.model_json_schema()
    common_schema = role_output_schema("query_specialist")

    assert ROLE_OUTPUT_MODELS["query_specialist"] is QueryPlan
    assert ROLE_CONTRACTS is ROLE_OUTPUT_MODELS
    assert ROLE_WIRE_MODELS["query_specialist"] is CompactQueryPlan
    assert {"query_specialist", "people_advisor", "knowledge_curator"} \
        <= PYDANTIC_WIRE_ROLES
    # Byte-identical minified schemas imply zero token delta for every tokenizer without
    # requiring a tokenizer asset or network lookup in the offline test suite.
    assert _minified(common_schema) == _minified(legacy_schema)
    assert QuerySpecialist().schema() == common_schema


@pytest.mark.parametrize(
    "invalid",
    [
        {
            "reads": [{
                "source": "jira", "subject": "Puffin", "where": "",
                "exhaustive": "false",
            }],
            "uncertainty": [],
        },
        {
            "reads": [{
                "source": "jira", "subject": "Puffin", "where": "",
                "exhaustive": False, "id": "forged-model-id",
            }],
            "uncertainty": [],
        },
        {
            "reads": [{
                "source": "jira", "subject": "Puffin", "where": "",
                "exhaustive": False, "page_size": 5,
            }],
            "uncertainty": [],
        },
        {
            "reads": [{
                "source": "jira", "subject": "Puffin", "where": "",
                "exhaustive": False, "completeness": "all",
            }],
            "uncertainty": [],
        },
        {"reads": [], "uncertainty": [], "compiler_guard": "creation_target_required"},
        {
            "reads": [
                {"source": "jira", "subject": str(index), "where": "",
                 "exhaustive": False}
                for index in range(9)
            ],
            "uncertainty": [],
        },
        {
            "reads": [{
                "source": "jira", "subject": "x" * 241, "where": "",
                "exhaustive": False,
            }],
            "uncertainty": [],
        },
        {"reads": [], "uncertainty": ["x" * 241]},
    ],
    ids=(
        "strict-bool", "runtime-id", "runtime-page-size", "runtime-completeness",
        "runtime-compiler-guard", "read-limit", "subject-limit", "uncertainty-limit",
    ),
)
def test_query_wire_strictly_rejects_coercion_runtime_fields_and_limits(invalid):
    with pytest.raises(ValidationError):
        validate_role_output("query_specialist", invalid)

    # Direct compact callers cross the same strict boundary before compilation.
    with pytest.raises(ValidationError):
        QuerySpecialist().apply({}, invalid)


def test_common_wire_boundary_revalidates_untrusted_pydantic_instances():
    # BaseModel instances can originate from model_construct or another persistence layer.
    # Pydantic normally trusts same-class instances, so the common boundary must project
    # them back to data and validate rather than treating the class identity as authority.
    invalid = CompactQueryPlan.model_construct(
        reads=[{
            "source": "jira", "subject": "Puffin", "where": "",
            "exhaustive": "false",
        }],
        uncertainty=[],
    )

    with pytest.raises(ValidationError):
        validate_role_output("query_specialist", invalid)
    with pytest.raises(ValidationError):
        QuerySpecialist().apply({}, invalid)


def test_query_compiler_owns_operational_ids_paging_order_and_completeness():
    projected = validate_role_output("query_specialist", VALID_WIRE)
    plan = _compile_compact_query_plan(projected)

    assert [row["id"] for row in plan["queries"]] == [
        "read-1-jira", "read-2-confluence",
    ]
    assert [row["page_size"] for row in plan["queries"]] == [50, 50]
    assert [row["completeness"] for row in plan["queries"]] == ["all", "page"]
    assert all(row["order_by"] == "updated DESC" for row in plan["queries"])
    assert all(row["fields"] == [] and row["depends_on"] == []
               for row in plan["queries"])
    assert plan["joins"] == []
    assert "compiler_guard" not in plan


def test_legacy_runtime_plan_remains_direct_apply_compatible_but_is_not_model_wire():
    legacy = {
        "queries": [{
            "id": "legacy-jira", "source": "jira", "query": "Puffin NDV",
            "where": "", "order_by": "updated DESC", "fields": [],
            "completeness": "all", "page_size": 50, "depends_on": [],
        }],
        "joins": [],
        "uncertainty": ["legacy checkpoint"],
    }

    result = QuerySpecialist().apply({"request_text": "Puffin NDV 조회"}, legacy)
    assert result["query_plan"]["queries"][0]["id"] == "legacy-jira"
    assert result["query_plan"]["uncertainty"] == ["legacy checkpoint"]

    with pytest.raises(ValidationError):
        validate_role_output("query_specialist", legacy)


def test_empty_compact_wire_uses_model_defaults_instead_of_legacy_plan_parsing():
    assert validate_role_output("query_specialist", {}) == {}
    result = QuerySpecialist().apply({}, {})
    assert result["query_plan"] == {"queries": [], "joins": [], "uncertainty": []}


class _PromptSequence:
    def __init__(self, *contents: str):
        self.contents = list(contents)
        self.configs = []

    def invoke(self, _messages, **kwargs):
        self.configs.append(kwargs.get("config"))
        return AIMessage(content=self.contents.pop(0))


def test_query_strict_failure_keeps_existing_single_repair_trace(monkeypatch):
    from app.agent import capabilities

    monkeypatch.setattr(capabilities, "get", lambda _tier="simple": {
        "checked": {"json_schema": False, "json_object": False},
    })
    monkeypatch.setattr(capabilities, "record", lambda *_args, **_kwargs: None)

    invalid = {
        "reads": [{
            "source": "jira", "subject": "Puffin", "where": "",
            "exhaustive": "false",
        }],
        "uncertainty": [],
    }
    valid = {
        "reads": [{
            "source": "jira", "subject": "Puffin", "where": "",
            "exhaustive": False,
        }],
        "uncertainty": [],
    }
    transport = _PromptSequence(
        json.dumps(invalid, ensure_ascii=False),
        json.dumps(valid, ensure_ascii=False),
    )
    agent = QuerySpecialist()
    monkeypatch.setattr(agent, "llm", lambda **_kwargs: transport)

    out = agent._invoke_structured_transport(
        {}, [HumanMessage(content="Puffin 조회")],
        output_contract="structured", capability_tier="simple",
        execution_layer="lightweight_semantic", execution_stage="synthesis",
    )

    assert out == valid
    assert len(transport.configs) == 2
    assert transport.configs[0]["metadata"]["ltm_output_contract"] == "structured"
    assert transport.configs[1]["metadata"] == {
        "ltm_role_id": "query_specialist",
        "ltm_output_contract": "structured_repair",
        "ltm_execution_layer": "projection",
        "ltm_execution_stage": "repair",
        "ltm_validation_category": "schema",
        "ltm_validation_keyword": "type",
        "ltm_validation_path": "$.reads[0].exhaustive",
    }
