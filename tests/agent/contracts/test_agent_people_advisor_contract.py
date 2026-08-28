"""PeopleAdvisor's Pydantic contract is the only structured-output authority."""

import pytest
from jsonschema import Draft202012Validator, ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

from app.agent.workflow.agents.people_advisor import PeopleAdvisor, SCHEMA
from app.agent.workflow.contracts import role_output_schema, validate_role_output


def test_people_advisor_contract_drives_prompt_schema_and_typed_wire_projection():
    schema = role_output_schema("people_advisor")
    Draft202012Validator.check_schema(schema)
    assert schema["title"] == "people_advisor"
    assert SCHEMA == schema

    agent = PeopleAdvisor()
    assert agent.schema() == schema

    wire = {
        "assignments": [{
            "index": 0,
            "user": "skcc.x1042",
            "reasons": ["진행중 2건"],
            "provider_extension": {"retained": True},
        }],
        "provider_extension": "retained",
    }
    Draft202012Validator(schema).validate(wire)
    projected = agent.pre_validate_structured_output(
        {}, wire, output_contract="structured", execution_stage="synthesis",
    )

    # The old JSON Schema allowed extensions and did not synthesize omitted optional fields.
    assert projected == wire
    assert validate_role_output("people_advisor", projected)["assignments"][0]["index"] == 0


@pytest.mark.parametrize(
    "invalid",
    [
        {"assignments": [{"index": "0", "user": "skcc.x1042", "reasons": ["진행중 2건"]}]},
        {"assignments": [{"index": 0, "user": "skcc.x1042", "reasons": []}]},
        {"assignments": [{"index": 0, "user": "skcc.x1042", "reasons": ["x" * 181]}]},
    ],
)
def test_people_advisor_contract_strictly_rejects_the_same_invalid_wire_shapes(invalid):
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(role_output_schema("people_advisor")).validate(invalid)
    with pytest.raises(PydanticValidationError):
        PeopleAdvisor().pre_validate_structured_output(
            {}, invalid, output_contract="structured", execution_stage="synthesis",
        )
