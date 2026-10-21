"""At most one native regeneration; no local fallback or discarded paid attempt."""
from types import SimpleNamespace
import pytest
from app.agent.maxis import backend
from app.agent.maxis.errors import classify_dify_error, MaxisGatewayError, MaxisErrorCode
from app.agent.maxis.telemetry import RemoteRequestTelemetryV1, role_usage_observation


def observed(status="failed"):
    return RemoteRequestTelemetryV1(
        transport_status="failed" if status == "failed" else "succeeded",
        http_status=400 if status == "failed" else 200,
        result_status=status, latency_ms=10,
        **({} if status == "failed" else {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}),
    ).model_dump(mode="json")


def failure(marker="invalid_core_work_draft", telemetry=True):
    cause = classify_dify_error(400, {"message": marker})
    error = backend.MaxisRuntimeError("synthetic rejection")
    error.__cause__ = cause
    error.metadata = {"requestTelemetry": observed()} if telemetry else {}
    return error


@pytest.fixture
def runtime(monkeypatch, evidence):
    monkeypatch.setattr(backend, "resolve_role", lambda *_: SimpleNamespace(route="maxis"))
    monkeypatch.setattr(backend, "role_payload", lambda role, *_a, **_k: {
        "roleId": role, "runtime": {"fixed": "guarded"},
        "evidencePack": evidence.model_copy(update={"use_case": role}).model_dump(mode="json"),
    })
    calls = []
    def install(outcomes):
        def invoke(*args, **kwargs):
            calls.append((args, kwargs))
            result = outcomes.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        monkeypatch.setattr(backend, "invoke", invoke)
        return calls
    return install


def ask(role="work_architect"):
    return backend.invoke_role(role, {"thread_id": "thread", "correlation_id": "corr"}, [], {},
                               output_kind="structured")


def success():
    return {"output": {"value": "typed"}, "metadata": {"requestTelemetry": observed("completed")}}


def test_fresh_generation_is_bounded_and_preserves_failed_attempt(runtime):
    calls = runtime([failure(), success()])
    result = ask()
    assert len(calls) == 2
    first, second = (call[0][1] for call in calls)
    assert first["runtime"] == second["runtime"]
    assert {k: v for k, v in first["evidencePack"].items() if k != "role_context"} == {
        k: v for k, v in second["evidencePack"].items() if k != "role_context"}
    assert not first["evidencePack"]["role_context"]
    assert second["evidencePack"]["role_context"][-1]["name"] == "native_draft_feedback"
    assert result == {"value": "typed"}
    assert len(result.metadata["requestAttempts"]) == 2
    usage = role_usage_observation(result.metadata)
    assert (usage["calls"], usage["failedCalls"], usage["unknownUsageCalls"], usage["totalTokens"]) == (2, 1, 1, 10)


def test_second_rejection_is_not_hidden_or_retried(runtime):
    calls = runtime([failure(), failure()])
    with pytest.raises(backend.MaxisRuntimeError) as error:
        ask()
    assert len(calls) == 2
    assert role_usage_observation(error.value.metadata)["failedCalls"] == 2


@pytest.mark.parametrize("second_fails", [False, True])
def test_audit_draft_uses_one_fixed_feedback_attempt_only(runtime, second_fails):
    import json
    rejected = "invalid_core_audit_draft native_draft_schema"
    calls = runtime([failure(rejected), failure(rejected) if second_fails else success()])
    if second_fails:
        with pytest.raises(backend.MaxisRuntimeError) as caught:
            ask("auditor")
        metadata = caught.value.metadata
    else:
        metadata = ask("auditor").metadata
    assert len(calls) == 2
    first, second = (call[0][1] for call in calls)
    assert {k: v for k, v in first["evidencePack"].items() if k != "role_context"} == {
        k: v for k, v in second["evidencePack"].items() if k != "role_context"}
    hint = json.loads(second["evidencePack"]["role_context"][-1]["content_json"])
    assert hint["reason"] == "schema" and "Preserve adverse findings" in hint["role_contract"]
    assert role_usage_observation(metadata)["failedCalls"] == (2 if second_fails else 1)


@pytest.mark.parametrize("marker,role", [
    ("invalid_core_audit_input", "auditor"),
    ("invalid_core_audit_draft invalid_core_audit_input", "auditor"),
    ("invalid_core_audit_draft", "work_architect"),
])
def test_audit_input_failures_and_wrong_role_never_regenerate(runtime, marker, role):
    calls = runtime([failure(marker)])
    with pytest.raises(backend.MaxisRuntimeError):
        ask(role)
    assert len(calls) == 1


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("role", ["auditor", "work_architect"])
def test_mixed_audit_and_other_role_markers_never_authorize_retry(runtime, nested, role):
    payload = ({"message": "invalid_core_audit_draft", "error": {"message": "invalid_core_work_draft"}}
               if nested else {"message": "invalid_core_audit_draft invalid_core_work_draft"})
    cause = classify_dify_error(400, payload)
    assert cause.code == MaxisErrorCode.WORKFLOW_CONTRACT and not cause.draft_regeneration_allowed
    error = backend.MaxisRuntimeError("synthetic rejection")
    error.__cause__ = cause
    error.metadata = {"requestTelemetry": observed()}
    calls = runtime([error])
    with pytest.raises(backend.MaxisRuntimeError):
        ask(role)
    assert len(calls) == 1


def local_numeric_failure(code="unverified_numeric_claim", telemetry=True, binding_valid=True):
    from app.agent.maxis.response_guard import ResponseGuardError
    error = backend.MaxisRuntimeError("synthetic local rejection")
    error.__cause__ = ResponseGuardError(code, "PRIVATE_SENTINEL")
    error.metadata = {"requestTelemetry": observed("blocked")} if telemetry else {}
    error._numeric_regeneration_allowed = binding_valid
    return error


@pytest.mark.parametrize("role", ["query_specialist", "request_architect", "work_architect",
                                 "knowledge_curator", "result_integrator"])
@pytest.mark.parametrize("second_fails", [False, True])
def test_local_numeric_guard_gets_one_fresh_draft_without_weakening_guard(runtime, role, second_fails):
    import json
    calls = runtime([local_numeric_failure(), local_numeric_failure() if second_fails else success()])
    if second_fails:
        with pytest.raises(backend.MaxisRuntimeError) as caught:
            ask(role)
        metadata = caught.value.metadata
    else:
        metadata = ask(role).metadata
    assert len(calls) == 2
    first, second = (call[0][1] for call in calls)
    assert {k: v for k, v in first["evidencePack"].items() if k != "role_context"} == {
        k: v for k, v in second["evidencePack"].items() if k != "role_context"}
    hint = json.loads(second["evidencePack"]["role_context"][-1]["content_json"])
    assert hint["reason"] == "numeric_grounding" and "PRIVATE_SENTINEL" not in str(hint)
    usage = role_usage_observation(metadata)
    assert (usage["calls"], usage["failedCalls"], usage["totalTokens"]) == (2, 2 if second_fails else 1, 20)


@pytest.mark.parametrize("code,role,telemetry", [
    ("unverified_numeric_claim", "auditor", True),
    ("unverified_numeric_claim", "editor_author", True),
    ("unverified_numeric_claim", "query_specialist", False),
    ("unverified_url", "query_specialist", True),
    ("citation_assertion_mismatch", "query_specialist", True),
    ("intent_source_fact_forbidden", "query_specialist", True),
    ("knowledge_manifest_mismatch", "query_specialist", True),
])
def test_local_retry_does_not_expand_to_other_guards_or_roles(runtime, code, role, telemetry):
    calls = runtime([local_numeric_failure(code, telemetry)])
    with pytest.raises(backend.MaxisRuntimeError):
        ask(role)
    assert len(calls) == 1


def test_local_numeric_result_text_regenerates_once(runtime):
    calls = runtime([local_numeric_failure(), success()])
    result = backend.invoke_role("result_integrator", {}, [], {}, output_kind="text")
    assert len(calls) == 2
    assert role_usage_observation(result.metadata)["failedCalls"] == 1


def test_local_numeric_shadow_does_not_regenerate(runtime, monkeypatch):
    monkeypatch.setattr(backend, "resolve_role", lambda *_: SimpleNamespace(route="shadow"))
    calls = runtime([local_numeric_failure()])
    with pytest.raises(backend.MaxisRuntimeError):
        ask("query_specialist")
    assert len(calls) == 1


def test_local_numeric_and_binding_mismatch_cannot_regenerate(runtime):
    calls = runtime([local_numeric_failure(binding_valid=False)])
    with pytest.raises(backend.MaxisRuntimeError):
        ask("query_specialist")
    assert len(calls) == 1


def test_local_numeric_retry_rechecks_profile_and_preserves_paid_failure(runtime):
    calls = runtime([local_numeric_failure(), backend.RuntimeProfileChanged("profile changed")])
    with pytest.raises(backend.RuntimeProfileChanged) as caught:
        ask("query_specialist")
    assert len(calls) == 2
    usage = role_usage_observation(caught.value.metadata)
    assert (usage["calls"], usage["failedCalls"], usage["totalTokens"]) == (1, 1, 10)


def test_unsupported_query_source_regenerates_without_dropping_reads(runtime):
    calls = runtime([failure("invalid_core_query_source"), success()])
    ask("query_specialist")
    assert len(calls) == 2
    hint = calls[1][0][1]["evidencePack"]["role_context"][-1]["content_json"]
    assert "task.allowed_sources" in hint and "uncertainty" in hint
    assert calls[0][0][1]["evidencePack"]["sources"] == calls[1][0][1]["evidencePack"]["sources"]
    mixed = classify_dify_error(400, {"message": "invalid_core_query_source invalid_core_query_input"})
    assert not mixed.draft_regeneration_allowed

@pytest.mark.parametrize("second_fails", [False, True])
def test_result_text_api_regenerates_native_structured_draft_once(runtime, second_fails):
    calls = runtime([failure("invalid_core_result_draft"),
                     failure("invalid_core_result_draft") if second_fails else success()])
    def invoke():
        return backend.invoke_role("result_integrator", {}, [], {}, output_kind="text")
    if second_fails:
        with pytest.raises(backend.MaxisRuntimeError) as error:
            invoke()
        assert role_usage_observation(error.value.metadata)["failedCalls"] == 2
    else:
        assert role_usage_observation(invoke().metadata)["failedCalls"] == 1
    assert len(calls) == 2
    assert calls[1][0][1]["evidencePack"]["role_context"][-1]["name"] == "native_draft_feedback"


def test_other_text_outputs_do_not_gain_native_result_regeneration(runtime):
    calls = runtime([failure("invalid_core_result_draft")])
    with pytest.raises(backend.MaxisRuntimeError):
        backend.invoke_role("knowledge_curator", {}, [], {}, output_kind="text")
    assert len(calls) == 1


@pytest.mark.parametrize("marker", ["invalid_editor_selection_id", "invalid_editor_selection_limit"])
def test_editor_unknown_selection_regenerates_once_without_repair(runtime, marker):
    calls = runtime([failure(marker), success()])
    output = ask("editor_author")
    assert len(calls) == 2 and calls[0] == calls[1]
    assert role_usage_observation(output.metadata)["failedCalls"] == 1


@pytest.mark.parametrize("marker", ["invalid_editor_selection_id", "invalid_editor_selection_limit"])
def test_editor_selection_marker_cannot_retry_other_roles_or_bad_inputs(runtime, marker):
    calls = runtime([failure(marker)])
    with pytest.raises(backend.MaxisRuntimeError):
        ask()
    assert len(calls) == 1
    error = classify_dify_error(400, {"message": marker,
                                    "error": {"message": "invalid_editor_input"}})
    assert not error.draft_regeneration_allowed


@pytest.mark.parametrize("marker", [
    "invalid_core_work_input", "invalid_editor_draft_spec", "invalid_editor_plan",
    "invalid_core_work_draft invalid_core_work_input", "not_invalid_core_work_draft_suffix",
    "Unsupported parameter: temperature", "ProviderTokenNotInitError",
])
def test_input_configuration_and_non_native_errors_never_regenerate(runtime, marker):
    calls = runtime([failure(marker)])
    with pytest.raises(backend.MaxisRuntimeError):
        ask()
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["editor_author", "auditor"])
def test_other_roles_are_not_retried(runtime, role):
    calls = runtime([failure()])
    with pytest.raises(backend.MaxisRuntimeError):
        ask(role)
    assert len(calls) == 1


def test_no_network_observation_means_no_regeneration(runtime):
    calls = runtime([failure(telemetry=False)])
    with pytest.raises(backend.MaxisRuntimeError):
        ask()
    assert len(calls) == 1


def test_second_preflight_failure_keeps_first_attempt(runtime):
    calls = runtime([failure(), backend.RuntimeProfileChanged("profile changed")])
    with pytest.raises(backend.RuntimeProfileChanged) as error:
        ask()
    assert len(calls) == 2
    assert role_usage_observation(error.value.metadata)["calls"] == 1


def test_feedback_generation_failure_preserves_original_paid_attempt(runtime, monkeypatch):
    from app.agent.maxis import draft_feedback
    def reject(*_, **__):
        raise ValueError("invalid feedback input")
    monkeypatch.setattr(draft_feedback, "with_native_feedback", reject)
    calls = runtime([failure()])
    with pytest.raises(backend.MaxisRuntimeError) as error:
        ask()
    assert len(calls) == 1
    assert role_usage_observation(error.value.metadata)["failedCalls"] == 1


def test_feedback_is_fixed_non_citable_and_does_not_restamp_evidence(evidence):
    import copy
    import json
    from app.agent.maxis.draft_feedback import with_native_feedback
    pack = evidence.model_copy(update={"use_case": "work_architect"})
    payload = {"roleId": "work_architect", "evidencePack": pack.model_dump(mode="json"),
               "runtime": {"profile": "synthetic-profile"}}
    original = copy.deepcopy(payload)
    result = with_native_feedback(payload, "work_architect")
    assert payload == original
    row = result["evidencePack"]["role_context"][-1]
    assert row["observed_at"] == payload["evidencePack"]["requested_at"]
    assert json.loads(row["content_json"])["previous_result"] == "rejected_native_draft"
    assert row["context_id"] not in {source["source_id"] for source in result["evidencePack"]["sources"]}
    assert {k: v for k, v in result["evidencePack"].items() if k != "role_context"} == {
        k: v for k, v in payload["evidencePack"].items() if k != "role_context"}
    assert with_native_feedback(result, "work_architect") is result


@pytest.mark.parametrize("reason", ["", "json_syntax", "json_structure", "schema", "source_id", "fact_selection", "numeric_grounding"])
def test_work_feedback_matches_single_question_channel_for_every_rejection(evidence, reason):
    import json
    from app.agent.maxis.draft_feedback import with_native_feedback
    from app.agent.maxis.contracts import canonical_payload_schema
    from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
    schema, digest = canonical_payload_schema(CREATE_SCHEMA)
    pack = evidence.model_copy(update={"use_case": "work_architect", "payload_schema_json": schema,
                                       "payload_schema_sha256": digest})
    payload = {"roleId": "work_architect", "evidencePack": pack.model_dump(mode="json")}
    result = with_native_feedback(payload, "work_architect", reason=reason)
    content = result["evidencePack"]["role_context"][-1]["content_json"]
    hint = json.loads(content)
    assert "Root keys are contract,status,payload." in hint["role_contract"]
    assert "payload.questions with status=ok and items=[]" in hint["role_contract"]
    assert "work-draft.v3" in hint["role_contract"]
    assert "missing_inputs" not in content and "needs_input" not in content


@pytest.mark.parametrize("schema_name", ["update", "comment", "extended_create"])
@pytest.mark.parametrize("reason", ["", "numeric_grounding", "schema", "json_syntax"])
def test_work_feedback_never_routes_other_schemas_into_native_create(evidence, schema_name, reason):
    import json
    from app.agent.maxis.draft_feedback import with_native_feedback
    from app.agent.maxis.contracts import canonical_payload_schema
    from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA, UPDATE_SCHEMA, COMMENT_SCHEMA
    schemas = {"update": UPDATE_SCHEMA, "comment": COMMENT_SCHEMA,
               "extended_create": {**CREATE_SCHEMA, "properties": {
                   **CREATE_SCHEMA["properties"], "outcome": {"type": "string"}}}}
    schema, digest = canonical_payload_schema(schemas[schema_name])
    pack = evidence.model_copy(update={"use_case": "work_architect", "payload_schema_json": schema,
                                       "payload_schema_sha256": digest})
    payload = {"roleId": "work_architect", "evidencePack": pack.model_dump(mode="json")}
    result = with_native_feedback(payload, "work_architect", reason=reason)
    hint = json.loads(result["evidencePack"]["role_context"][-1]["content_json"])
    assert "bound payload schema and published output contract" in hint["role_contract"]
    assert "work-draft.v3" not in str(hint) and "payload.questions" not in str(hint)
    assert result["evidencePack"]["payload_schema_json"] == schema
    assert result["evidencePack"]["payload_schema_sha256"] == digest


@pytest.mark.parametrize("blocked_by", ["synthetic_only", "full_context", "budget"])
def test_feedback_never_displaces_context_or_changes_fixed_fixture(evidence, blocked_by, monkeypatch):
    import hashlib
    from app.agent.maxis import draft_feedback
    from app.agent.maxis.contracts import RoleContextV1
    from app.agent.maxis.context_guard import ContextGuardError
    pack = evidence.model_copy(update={"use_case": "work_architect"})
    if blocked_by == "synthetic_only":
        pack = pack.model_copy(update={"flags": ("synthetic_only",)})
    elif blocked_by == "full_context":
        pack = pack.model_copy(update={"role_context": tuple(RoleContextV1(
            context_id=f"ctx-{index}", name=f"context_{index}", content_json="{}",
            content_sha256=hashlib.sha256(b"{}").hexdigest(), observed_at=pack.requested_at,
        ) for index in range(8))})
    else:
        real = draft_feedback.validate_pack_safety
        def bounded(candidate):
            real(candidate)
            if candidate.role_context:
                raise ContextGuardError("context_too_large", "evidence_pack")
        monkeypatch.setattr(draft_feedback, "validate_pack_safety", bounded)
    payload = {"roleId": "work_architect", "evidencePack": pack.model_dump(mode="json")}
    assert draft_feedback.with_native_feedback(payload, "work_architect") is payload


def test_local_post_guard_rejection_marks_only_the_last_attempt(runtime):
    from app.agent.workflow.agents.base import Agent
    runtime([failure(), success()])
    result = ask()
    error = Agent._remote_local_rejection(SimpleNamespace(name="work_architect"), result, ValueError())
    usage = role_usage_observation(error.metadata)
    assert (usage["calls"], usage["failedCalls"]) == (2, 2)
    assert result.metadata["requestAttempts"][-1]["result_status"] == "completed"


@pytest.mark.parametrize("attempts", [[], [{}] * 3, "bad"])
def test_attempt_list_is_bounded_and_typed(attempts):
    with pytest.raises(ValueError):
        role_usage_observation({"requestAttempts": attempts})


def test_final_observation_cannot_disagree_with_attempt_history():
    with pytest.raises(ValueError, match="does not match"):
        role_usage_observation({"requestAttempts": [observed()], "requestTelemetry": observed("completed")})


def test_native_draft_regeneration_is_not_a_transport_retry():
    error = classify_dify_error(400, {"message": "invalid_core_work_draft"})
    assert error.draft_regeneration_allowed and not error.retryable
    assert not MaxisGatewayError(MaxisErrorCode.AUTHENTICATION).draft_regeneration_allowed


@pytest.mark.parametrize("reason", ["json_syntax", "json_structure", "schema", "source_id", "fact_selection"])
def test_fixed_reason_reaches_one_regeneration_without_remote_text(runtime, reason):
    calls = runtime([failure("invalid_core_work_draft native_draft_" + reason + " PRIVATE_SENTINEL"), success()])
    ask()
    import json
    first, second = (call[0][1] for call in calls)
    hint = json.loads(second["evidencePack"]["role_context"][-1]["content_json"])
    assert hint["reason"] == reason and "PRIVATE_SENTINEL" not in str(hint)
    assert first["evidencePack"]["sources"] == second["evidencePack"]["sources"]
    assert len(calls) == 2


@pytest.mark.parametrize("marker", ["native_draft_schema", "native_draft_schema_extra",
    "invalid_core_work_input native_draft_schema", "Unsupported parameter: x native_draft_schema"])
def test_diagnostic_never_grants_regeneration_without_an_allowed_draft_error(marker):
    error = classify_dify_error(400, {"message": marker})
    assert not error.draft_regeneration_allowed
    assert not error.draft_regeneration_reason


def test_unknown_and_ambiguous_diagnostics_are_not_echoed():
    for suffix in ("native_draft_arbitrary", "native_draft_schema native_draft_json_syntax"):
        error = classify_dify_error(400, {"message": "invalid_core_work_draft " + suffix})
        assert error.draft_regeneration_allowed and error.draft_regeneration_reason == ""


@pytest.mark.parametrize("veto", [
    "invalid_core_work_input", "ProviderTokenNotInitError",
    "Unsupported parameter: temperature", "ModelNotFoundError",
])
@pytest.mark.parametrize("reversed_order", [False, True])
def test_nested_error_vetoes_regeneration_independently_of_order(veto, reversed_order):
    outer, inner = ("invalid_core_work_draft", veto)
    if reversed_order:
        outer, inner = inner, outer
    error = classify_dify_error(400, {"message": outer, "error": {"message": inner}})
    assert not error.draft_regeneration_allowed
