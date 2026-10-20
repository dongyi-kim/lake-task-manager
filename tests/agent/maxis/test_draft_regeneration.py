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
def runtime(monkeypatch):
    monkeypatch.setattr(backend, "resolve_role", lambda *_: SimpleNamespace(route="maxis"))
    monkeypatch.setattr(backend, "role_payload", lambda *_a, **_k: {"fixed": "guarded"})
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
    assert len(calls) == 2 and calls[0] == calls[1]
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


def test_editor_unknown_selection_regenerates_once_without_repair(runtime):
    calls = runtime([failure("invalid_editor_selection_id"), success()])
    output = ask("editor_author")
    assert len(calls) == 2 and calls[0] == calls[1]
    assert role_usage_observation(output.metadata)["failedCalls"] == 1


def test_editor_selection_marker_cannot_retry_other_roles_or_bad_inputs(runtime):
    calls = runtime([failure("invalid_editor_selection_id")])
    with pytest.raises(backend.MaxisRuntimeError):
        ask()
    assert len(calls) == 1
    error = classify_dify_error(400, {"message": "invalid_editor_selection_id",
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
