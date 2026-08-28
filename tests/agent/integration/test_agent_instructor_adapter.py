from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from app.agent import instructor_adapter


SCHEMA = {
    "title": "BoundedOutput",
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _validate(value):
    from jsonschema import validate

    validate(instance=value, schema=SCHEMA)
    return dict(value)


@pytest.mark.parametrize(
    "invalid",
    [
        "not JSON",
        'prefix {"value":"must-not-pass"}',
        '```json\n{"value":"must-not-pass"}\n```',
        '{"value":"wrong"}<END_JSON> trailing prose',
        '{"value":"partial"<END_JSON>',
    ],
    ids=("plain", "prefix", "fence", "trailing", "partial"),
)
def test_instructor_adapter_retries_strict_whole_document_json_once(invalid):
    calls = []
    repair_inputs = []

    def initial():
        calls.append("initial")
        return AIMessage(content=invalid, response_metadata={"finish_reason": "stop"})

    def repair(raw_text, validation_error, diagnostic):
        calls.append("repair")
        repair_inputs.append((raw_text, validation_error, diagnostic))
        return AIMessage(content='{"value":"ok"}<END_JSON>',
                         response_metadata={"finish_reason": "stop"})

    result = instructor_adapter.invoke_prompt_json(
        schema=SCHEMA,
        model_name="BoundedOutput",
        initial_call=initial,
        repair_call=repair,
        validate_output=_validate,
        validation_diagnostic=lambda _exc: {
            "category": "schema", "keyword": "contract", "path": "$"
        },
        end_token="<END_JSON>",
    )

    assert result == {"value": "ok"}
    assert calls == ["initial", "repair"]
    assert repair_inputs[0][0] == invalid
    assert repair_inputs[0][2]["category"] == "parse"


def test_instructor_adapter_uses_role_prevalidation_before_schema_validation():
    calls = []

    def validate(value):
        value = dict(value)
        value.pop("runtime_owned", None)
        return _validate(value)

    result = instructor_adapter.invoke_prompt_json(
        schema=SCHEMA,
        model_name="BoundedOutput",
        initial_call=lambda: calls.append("initial") or AIMessage(
            content=json.dumps({"value": "ok", "runtime_owned": "drop"})),
        repair_call=lambda *_args: calls.append("repair"),
        validate_output=validate,
        validation_diagnostic=lambda _exc: {},
        end_token="<END_JSON>",
    )

    assert result == {"value": "ok"}
    assert calls == ["initial"]


@pytest.mark.parametrize("selected_backend", ["instructor", "legacy"])
def test_single_missing_root_field_uses_a_bounded_patch_and_preserves_valid_material(
        monkeypatch, selected_backend):
    """A large valid evidence payload must not be regenerated to add one missing summary."""
    monkeypatch.setenv(instructor_adapter.BACKEND_ENV, selected_backend)
    schema = {
        "type": "object",
        "properties": {
            "situation": {"type": "string", "maxLength": 1600},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "observations": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["key", "observations"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["situation", "evidence"],
        "additionalProperties": False,
    }
    evidence = [{"key": "DL-100", "observations": ["kept exactly", "still exact"]}]
    calls = []
    patch_schemas = []

    def validate(value):
        from jsonschema import validate

        validate(instance=value, schema=schema)
        return dict(value)

    result = instructor_adapter.invoke_prompt_json(
        schema=schema,
        model_name="ResearchOutput",
        initial_call=lambda: calls.append("initial") or AIMessage(
            content=json.dumps({"evidence": evidence}, ensure_ascii=False)),
        repair_call=lambda *_args: calls.append("full-repair") or AIMessage(
            content=json.dumps({"situation": "wrong path", "evidence": []})),
        required_patch_call=lambda _raw, _error, _diagnostic, patch_schema: (
            calls.append("required-patch")
            or patch_schemas.append(patch_schema)
            or AIMessage(content=json.dumps({"situation": "검증된 현재 상황"},
                                             ensure_ascii=False))
        ),
        validate_output=validate,
        validation_diagnostic=lambda exc: {
            "category": "schema",
            "keyword": str(getattr(exc, "validator", "")),
            "path": "$",
            "missing": "situation",
        },
        end_token="<END_JSON>",
    )

    assert result == {"situation": "검증된 현재 상황", "evidence": evidence}
    assert calls == ["initial", "required-patch"]
    assert patch_schemas == [{
        "type": "object",
        "properties": {"situation": {"type": "string", "maxLength": 1600}},
        "required": ["situation"],
        "additionalProperties": False,
    }]


def test_missing_root_patch_is_not_used_when_existing_material_has_another_violation():
    schema = {
        "type": "object",
        "properties": {
            "situation": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["situation", "evidence"],
        "additionalProperties": False,
    }
    calls = []

    def validate(value):
        from jsonschema import validate

        validate(instance=value, schema=schema)
        return dict(value)

    result = instructor_adapter.invoke_prompt_json(
        schema=schema,
        model_name="ResearchOutput",
        initial_call=lambda: calls.append("initial") or AIMessage(
            content=json.dumps({"evidence": "not-an-array"})),
        repair_call=lambda *_args: calls.append("full-repair") or AIMessage(
            content=json.dumps({"situation": "fixed", "evidence": []})),
        required_patch_call=lambda *_args: calls.append("required-patch"),
        validate_output=validate,
        validation_diagnostic=lambda exc: {
            "category": "schema",
            "keyword": str(getattr(exc, "validator", "")),
            "path": "$",
            "missing": "situation",
        },
        end_token="<END_JSON>",
    )

    assert result == {"situation": "fixed", "evidence": []}
    assert calls == ["initial", "full-repair"]


def test_instructor_adapter_never_retries_empty_or_transport_failures():
    for outcome in (AIMessage(content="   "), ConnectionError("offline")):
        calls = []

        def initial(value=outcome):
            calls.append("initial")
            if isinstance(value, BaseException):
                raise value
            return value

        with pytest.raises(instructor_adapter.InstructorAdapterError) as caught:
            instructor_adapter.invoke_prompt_json(
                schema=SCHEMA,
                model_name="BoundedOutput",
                initial_call=initial,
                repair_call=lambda *_args: calls.append("repair"),
                validate_output=_validate,
                validation_diagnostic=lambda _exc: {},
                end_token="<END_JSON>",
            )

        assert caught.value.wire_attempts == 1
        assert caught.value.kind in {"empty", "transport"}
        assert calls == ["initial"]


def test_instructor_adapter_length_gate_preserves_projection_fail_closed():
    calls = []

    with pytest.raises(instructor_adapter.InstructorAdapterError) as caught:
        instructor_adapter.invoke_prompt_json(
            schema=SCHEMA,
            model_name="BoundedOutput",
            initial_call=lambda: calls.append("initial") or AIMessage(
                content='{"value":"partial',
                response_metadata={"finish_reason": "length"}),
            repair_call=lambda *_args: calls.append("repair"),
            validate_output=_validate,
            validation_diagnostic=lambda _exc: {},
            end_token="<END_JSON>",
            fail_on_length=True,
        )

    assert caught.value.kind == "length"
    assert caught.value.wire_attempts == 1
    assert calls == ["initial"]


def test_backend_gate_is_explicit_and_defaults_to_instructor(monkeypatch):
    monkeypatch.delenv("LTM_AGENT_STRUCTURED_OUTPUT_BACKEND", raising=False)
    assert instructor_adapter.backend() == "instructor"

    monkeypatch.setenv("LTM_AGENT_STRUCTURED_OUTPUT_BACKEND", "legacy")
    assert instructor_adapter.backend() == "legacy"

    monkeypatch.setenv("LTM_AGENT_STRUCTURED_OUTPUT_BACKEND", "unknown")
    with pytest.raises(ValueError, match="structured output backend"):
        instructor_adapter.backend()

    monkeypatch.delenv(instructor_adapter.FALLBACK_POLICY_ENV, raising=False)
    assert instructor_adapter.fallback_policy() == "allow"
    monkeypatch.setenv(instructor_adapter.FALLBACK_POLICY_ENV, "forbid")
    assert instructor_adapter.fallback_policy() == "forbid"
    monkeypatch.setenv(instructor_adapter.FALLBACK_POLICY_ENV, "unknown")
    with pytest.raises(ValueError, match="fallback policy"):
        instructor_adapter.fallback_policy()


def test_instructor_initialization_failure_rolls_back_before_any_wire_call(monkeypatch):
    import instructor

    monkeypatch.delenv(instructor_adapter.BACKEND_ENV, raising=False)
    def fail_patch(**_kwargs):
        raise RuntimeError("unsupported runtime")

    monkeypatch.setattr(instructor, "patch", fail_patch)
    calls = []

    result = instructor_adapter.invoke_prompt_json(
        schema=SCHEMA,
        model_name="BoundedOutput",
        initial_call=lambda: calls.append("initial") or AIMessage(
            content='{"value":"ok"}'),
        repair_call=lambda *_args: calls.append("repair"),
        validate_output=_validate,
        validation_diagnostic=lambda _exc: {},
        end_token="<END_JSON>",
    )

    assert result == {"value": "ok"}
    assert calls == ["initial"]


def test_instructor_initialization_failure_is_not_hidden_when_fallback_forbidden(
        monkeypatch):
    import instructor

    monkeypatch.delenv(instructor_adapter.BACKEND_ENV, raising=False)
    monkeypatch.setenv(instructor_adapter.FALLBACK_POLICY_ENV, "forbid")
    monkeypatch.setattr(
        instructor, "patch",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("unsupported runtime")),
    )
    calls = []

    with pytest.raises(instructor_adapter.InstructorAdapterError) as caught:
        instructor_adapter.invoke_prompt_json(
            schema=SCHEMA,
            model_name="BoundedOutput",
            initial_call=lambda: calls.append("initial"),
            repair_call=lambda *_args: calls.append("repair"),
            validate_output=_validate,
            validation_diagnostic=lambda _exc: {},
            end_token="<END_JSON>",
        )

    assert caught.value.kind == "backend_unavailable"
    assert caught.value.wire_attempts == 0
    assert calls == []


@pytest.mark.parametrize(
    ("scenario", "expected", "expected_calls"),
    [
        ("valid", ("ok", {"value": "ok"}), ["initial"]),
        ("valid_length", ("ok", {"value": "ok"}), ["initial"]),
        ("invalid", ("ok", {"value": "ok"}), ["initial", "repair"]),
        ("schema_invalid", ("ok", {"value": "ok"}), ["initial", "repair"]),
        ("empty", ("error", "empty", 1), ["initial"]),
        ("transport", ("error", "transport", 1), ["initial"]),
        ("partial_length", ("error", "length", 1), ["initial"]),
        ("schema_invalid_length", ("error", "length", 1), ["initial"]),
    ],
)
def test_default_and_legacy_backends_have_the_same_wire_trace(
        monkeypatch, scenario, expected, expected_calls):
    def run(selected_backend):
        if selected_backend == "default":
            monkeypatch.delenv(instructor_adapter.BACKEND_ENV, raising=False)
        else:
            monkeypatch.setenv(instructor_adapter.BACKEND_ENV, selected_backend)
        calls = []

        def initial():
            calls.append("initial")
            if scenario == "transport":
                raise ConnectionError("offline")
            if scenario == "empty":
                return AIMessage(content=" ")
            if scenario == "invalid":
                return AIMessage(content="not-json")
            if scenario in {"schema_invalid", "schema_invalid_length"}:
                return AIMessage(
                    content='{"wrong":"value"}',
                    response_metadata={
                        "finish_reason": (
                            "length" if scenario.endswith("_length") else "stop")
                    },
                )
            if scenario == "partial_length":
                return AIMessage(
                    content='{"value":"partial',
                    response_metadata={"finish_reason": "length"},
                )
            finish_reason = "length" if scenario == "valid_length" else "stop"
            return AIMessage(
                content='{"value":"ok"}<END_JSON>',
                response_metadata={"finish_reason": finish_reason},
            )

        def repair(*_args):
            calls.append("repair")
            return AIMessage(content='{"value":"ok"}<END_JSON>')

        try:
            result = instructor_adapter.invoke_prompt_json(
                schema=SCHEMA,
                model_name="BoundedOutput",
                initial_call=initial,
                repair_call=repair,
                validate_output=_validate,
                validation_diagnostic=lambda _exc: {
                    "category": "schema", "keyword": "contract", "path": "$"
                },
                end_token="<END_JSON>",
                fail_on_length=True,
            )
            outcome = ("ok", result)
        except instructor_adapter.InstructorAdapterError as exc:
            outcome = ("error", exc.kind, exc.wire_attempts)
        return outcome, calls

    default_trace = run("default")
    legacy_trace = run("legacy")

    assert default_trace == legacy_trace
    assert default_trace == (expected, expected_calls)
