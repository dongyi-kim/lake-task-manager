"""Content-free failure provenance; no external sockets or model calls."""
import hashlib
import json

import httpx
import pytest

from app.agent.maxis.client import DifyClient, DifyClientConfig
from app.agent.maxis.errors import MaxisErrorCode, MaxisGatewayError, evaluation_failure_diagnostics
from app.agent.maxis.registry import load_registry
from app.agent.maxis.telemetry import RemoteRequestTelemetryV1, request_observation


SECRET = "synthetic-secret-not-for-artifacts"
WORKFLOW = "11111111-1111-4111-8111-111111111111"


def _client(http):
    return DifyClient(DifyClientConfig(profile="test", stage="test", base_url="http://dify.test/v1",
        require_https=False, verify_tls=False), client=http)


def test_local_input_rejection_does_not_claim_http_attempt_or_zero_model_calls():
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("unexpected HTTP"))) as http:
        with pytest.raises(MaxisGatewayError) as failure:
            _client(http)._request_validated_chat_bytes(load_registry().resolve_role("editor_author"),
                api_key=SECRET, query="", user="ltm_test", inputs={})
    result = evaluation_failure_diagnostics(failure.value, stage="gateway", correlation_id=SECRET)
    assert result["classification"] == "bad_request"
    assert result["stage"] == "local_validation" and result["transportStatus"] == "not_attempted"
    assert result["httpStatus"] is None and result["nativeModelCallCount"] is None
    assert result["requestIdSha256"] == hashlib.sha256(SECRET.encode()).hexdigest()
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("status,code", [(400, "bad_request"), (422, "bad_request"),
    (401, "authentication"), (403, "forbidden"), (429, "rate_limited"), (503, "upstream")])
def test_http_status_preserved_without_error_body_or_headers(status, code):
    def respond(request):
        return httpx.Response(status, json={"message": SECRET, "reasoning": SECRET},
                              headers={"x-request-id": SECRET})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(MaxisGatewayError) as failure:
            _client(http)._request_bytes("POST", "/chat-messages", api_key=SECRET,
                payload={}, response_limit=1024, requested_workflow_id=WORKFLOW)
    result = evaluation_failure_diagnostics(failure.value, stage="gateway")
    assert result["stage"] == "http_response" and result["httpStatus"] == status
    assert result["classification"] == code and result["transportStatus"] == "failed"
    assert result["requestedWorkflowIdSha256"] == hashlib.sha256(WORKFLOW.encode()).hexdigest()
    assert result["nativeModelCallCount"] is None
    assert SECRET not in json.dumps(result) and WORKFLOW not in json.dumps(result)


@pytest.mark.parametrize("error_type,code", [(httpx.ConnectError, "network"), (httpx.ReadTimeout, "timeout")])
def test_transport_failure_preserved_without_exception_text(error_type, code):
    def fail(request):
        raise error_type(SECRET, request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as http:
        with pytest.raises(MaxisGatewayError) as failure:
            _client(http)._request_bytes("POST", "/chat-messages", api_key=SECRET,
                payload={}, response_limit=1024)
    result = evaluation_failure_diagnostics(failure.value, stage="gateway")
    assert result["stage"] == "transport" and result["classification"] == code
    assert result["httpStatus"] is None and result["transportStatus"] == "failed"
    assert result["nativeModelCallCount"] is None and SECRET not in json.dumps(result)


def test_postresponse_guard_failure_keeps_only_correlation_digests():
    observation = request_observation(transport_status="succeeded", http_status=200, latency_ms=10,
        workflow_id=WORKFLOW, raw={"workflow_run_id": WORKFLOW, "message_id": SECRET,
        "task_id": SECRET, "answer": SECRET, "metadata": {"reasoning": SECRET}})
    result = evaluation_failure_diagnostics(ValueError(SECRET), stage="response_guard",
                                           response_telemetry=observation)
    assert result["stage"] == "response_guard" and result["httpStatus"] == 200
    assert result["transportStatus"] == "succeeded" and result["nativeModelCallCount"] is None
    assert result["messageIdSha256"] == result["taskIdSha256"] == hashlib.sha256(SECRET.encode()).hexdigest()
    assert SECRET not in json.dumps(result) and WORKFLOW not in json.dumps(result)


def test_unknown_gateway_exception_does_not_infer_no_http_or_calls():
    result = evaluation_failure_diagnostics(RuntimeError(SECRET), stage="gateway")
    assert result["stage"] == "gateway" and result["transportStatus"] == "unknown"
    assert result["httpStatus"] is None and result["nativeModelCallCount"] is None
    assert SECRET not in json.dumps(result)


@pytest.mark.filterwarnings("error")
def test_forged_telemetry_and_arbitrary_error_attributes_do_not_escape():
    forged = RemoteRequestTelemetryV1().model_copy(update={"message_id_sha256": SECRET,
        "native_model_call_count": SECRET, "unexpected_secret": SECRET})
    error = MaxisGatewayError(MaxisErrorCode.NETWORK, telemetry=forged)
    error.code, error.status_code = SECRET, SECRET
    result = evaluation_failure_diagnostics(error, stage="gateway")
    assert result["classification"] == "evaluation_failed" and result["httpStatus"] is None
    assert result["messageIdSha256"] == "" and result["nativeModelCallCount"] is None
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('calls', [None, 0, 2])
def test_native_call_count_is_only_taken_from_validated_observation(calls):
    observation = RemoteRequestTelemetryV1(native_model_call_count=calls)
    result = evaluation_failure_diagnostics(ValueError(), stage='response_guard', response_telemetry=observation)
    assert result['nativeModelCallCount'] == calls


def test_unknown_stage_is_not_written_to_artifact():
    with pytest.raises(ValueError, match="unknown evaluation stage"):
        evaluation_failure_diagnostics(ValueError(SECRET), stage=SECRET)
